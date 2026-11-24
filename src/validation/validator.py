"""
src/validation/validator.py  –  v3
Multi-Layer XAML-Validator

Verbesserungen gegenüber v2:
  NEU        component_scores: Aufschlüsselung des Gesamt-Scores in fünf
             messbare Dimensionen (Struktur, Logging, Benennung, Sicherheit,
             Vollständigkeit). Ermöglicht detaillierte Ablationsanalyse und
             human-readable Evaluation (Masterarbeit Kap. 5).
  NEU        activity_coverage: Anteil der im Prompt erwähnten Aktivitätsnamen,
             die im generierten XAML tatsächlich auftauchen (0.0–1.0).
             Kann mit einer Referenzliste oder der Prompt-Beschreibung berechnet
             werden – nutze validate_with_reference() für diese Metrik.
  NEU        suggestions: Konkrete, umsetzbare Verbesserungsvorschläge als
             separate Liste (nicht vermischt mit errors/warnings).
  NEU        passed als Top-Level-Schlüssel (vorher nur implizit über score).
  FIX        Rethrow-Prüfung: Vorher wurde nur geprüft ob <Rethrow/> irgendwo
             im Dokument vorkommt. Jetzt wird geprüft ob es innerhalb eines
             Catch-Blocks liegt (strukturelle Prüfung über Textkontext).
  FIX        TryCatch-Prüfung erkennt jetzt auch ui:TryCatch und das
             moderne uix:TryCatch Pattern.
  NEU        validate_batch() für effiziente Evaluation mehrerer Workflows.
  NEU        validate_with_reference() für Tree-Edit-Distance-Approximation
             und Jaccard-Ähnlichkeit (für Ablationsstudie).
"""

from __future__ import annotations

import re
import logging
import math
from collections import Counter
from typing import Dict, List, Optional, Set, Tuple
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

# ─── Aktivitäten-Whitelist (~90 valide UiPath-Aktivitäten) ───────────────────
VALID_ACTIVITIES: Set[str] = {
    # Core / System
    "Sequence", "Flowchart", "FlowDecision", "FlowSwitch", "State",
    "StateMachine", "TryCatch", "Catch", "Finally", "Throw", "Rethrow",
    "Assign", "Delay", "DoWhile", "While", "ForEach", "ParallelForEach",
    "If", "Switch", "Pick", "PickBranch", "InvokeMethod", "InvokeWorkflow",
    "InvokeProcess", "LogMessage", "WriteLine", "AddQueueItem",
    "GetQueueItem", "PostponeTransaction", "SetTransactionStatus",
    "GetTransactionItem", "ShouldStop",
    # System.Activities Framework-Konstrukte (für Catch-Handler-Delegates,
    # z. B. vom SpecCompiler generierte Catch-Blöcke mit typisiertem Handler)
    "ActivityAction", "DelegateInArgument", "DelegateOutArgument",
    "ActivityFunc", "Variable", "Activity",
    # UIAutomation
    "Click", "TypeInto", "GetText", "GetFullText", "SetText", "SendHotkey",
    "KeyboardShortcut", "MouseScroll", "DoubleClick", "RightClick",
    "Hover", "FindElement", "FindChildren", "ElementExists", "WaitForReady",
    "WaitElementVanish", "WaitForElement", "Highlight", "TakeScreenshot",
    "TakeRegionScreenshot", "AttachWindow", "OpenApplication",
    "CloseApplication", "OpenBrowser", "CloseBrowser", "NavigateTo",
    "ExtractData", "GetAttribute", "CheckState", "SelectItem",
    "SelectMultipleItems",
    # Excel
    "ExcelApplicationScope", "ReadRange", "WriteRange", "AppendRange",
    "ReadCell", "WriteCell", "ForEachRow", "DeleteSheetRows",
    "InsertSheetRows", "SortRange", "FilterRange", "ClearSheetContent",
    "GetCellColor", "SetCellColor", "CopyPasteRange", "ReadRowCount",
    "ReadColumnCount", "CreateTable",
    # Mail
    "GetMailMessages", "SendMail", "ReplyToMailMessage", "ForwardMail",
    "SaveAttachments", "MoveMail", "DeleteMail", "MarkAsRead",
    "GetOutlookMailMessages", "SendOutlookMail",
    # Orchestrator
    "GetCredential", "GetRobotCredential", "GetAsset",
    "AddTransactionItem", "BulkAddQueueItems",
    "SetAssetValue", "DeleteQueueItems", "UpdateTransactionProgress",
    # SAP
    "SAPLogon", "OpenSAPTransaction", "SAPCallFunction",
    "NSAPLogon", "NSAPCallTransaction", "NSAPSetFieldValue",
    "NSAPGetFieldValue", "NSAPClick", "NSAPTypeInto", "NSAPGetText",
    "NSAPTableGetCell", "NSAPTableSetCell",
    # Database
    "Connect", "ExecuteQuery", "ExecuteNonQuery", "InsertDataTable",
    "Disconnect", "StartTransaction", "CommitTransaction",
    "RollbackTransaction",
    # PDF
    "ReadPDFText", "ExtractPDFPage", "ReadPDFWithOCR", "GetPDFPageCount",
    # WebAPI / HTTP
    "HTTPRequest", "DeserializeJSON", "SerializeJSON",
    "DeserializeXML", "SerializeXML", "BuildDTFromJSON",
    # File / System
    "ReadTextFile", "WriteTextFile", "AppendLine", "AppendText",
    "MoveFile", "CopyFile", "DeleteFile", "PathExists", "CreateDirectory",
    "DeleteDirectory", "CompressZip", "ExtractZip", "GetFiles", "GetFolders",
    # Misc
    "SetVariable", "InvokeVBA", "RunScript", "StartProcess",
}

# ─── Compiled Regex Patterns ──────────────────────────────────────────────────
_ACT_NAME_RE       = re.compile(r'<(?:ui:|uix:|ue:|ueab:|s:|ns)?(\w+)\s')
_DISPLAY_NAME_RE   = re.compile(r'DisplayName="([^"]*)"')
_VARIABLE_RE       = re.compile(
    r'<Variable\s[^>]*x:TypeArguments="([^"]*)"[^>]*Name="([^"]*)"'
)
_ARG_NAME_RE       = re.compile(r'<x:Property\s+Name="(\w+)"\s+Type="[^"]*"')
_SELECTOR_RE       = re.compile(r'Selector="([^"]*)"')
_CREDENTIAL_RE     = re.compile(
    r'(?i)(password|passwort|pwd|kennwort|secret)\s*=\s*"[^"]{3,}"'
)
_HARDCODED_PATH_RE = re.compile(
    r'Value="(?:[A-Z]:\\[^"]{10,}|/home/[^"]{5,})"'
)
# Prüft ob Rethrow innerhalb eines Catch-Blocks vorkommt (Kontext-Prüfung)
# Bugfix: vorheriger Regex <Rethrow\s*/> erkannte nur attributlose Rethrow-Tags
# (<Rethrow/>) und übersah <Rethrow DisplayName="..." />, was der vom
# SpecCompiler generierten Form entspricht. [^>]* erlaubt beliebige Attribute.
_CATCH_RETHROW_RE  = re.compile(
    r'<Catch\b[^>]*>.*?<Rethrow\b[^>]*/>', re.DOTALL
)
# Aktivitätsnamen im Text (für activity_coverage)
_ACTIVITY_TOKEN_RE = re.compile(r'\b([A-Z][A-Za-z]{2,})\b')

# XAML-interne Schlüsselwörter die keine echten Aktivitäten sind
_XAML_KEYWORDS: Set[str] = {
    'Variable', 'Property', 'Members', 'Argument', 'Literal',
    'OutArgument', 'InArgument', 'VariableReference', 'Text',
    'Value', 'Duration', 'Collection', 'VisualBasicSettings',
    'VisualBasicImportReference', 'VisualBasicSetting', 'Activity',
    'TextExpression', 'AssemblyReference',
    'Target', 'Body', 'Result', 'TargetSelector', 'ActivityAction', 'DelegateInArgument'
}

# UI-Automation erkennen (dort gehoert ein Screenshot in den Catch-Block)
UI_ACTIVITY_PATTERN = re.compile(r"<(?:ui:(?:OpenBrowser|AttachBrowser|AttachWindow|OpenApplication|Click|TypeInto|GetValue|"
                                 r"WaitUiElementAppear|NavigateTo|CloseTab)|uix:)\b")

# Scoring-Gewichte pro Schweregrad
_SEVERITY_WEIGHTS: Dict[str, int] = {
    'critical': -30,
    'error':    -15,
    'warning':   -5,
    'info':      -1,
}

# Component-Score-Gewichte (summieren sich zu 100)
_COMPONENT_WEIGHTS: Dict[str, int] = {
    'structure':    35,   # TryCatch, Finally, Rethrow
    'logging':      25,   # Start / Ende / Fehler LogMessage
    'naming':       15,   # Argumente, Variablen, DisplayNames
    'security':     15,   # Credentials, Hardcoded Paths
    'completeness': 10,   # Namespace, Aktivitäts-Whitelist
}


class XAMLValidator:
    """
    Vier-Ebenen-Validierungspipeline für UiPath XAML-Workflows.

    Ebene 1  XML-Syntaxvalidierung (Gate – bei Fehler Abbruch)
    Ebene 2  UiPath-Aktivitäten-Whitelist (Halluzinations-Filter)
    Ebene 3  Best-Practice-Regelwerk (10 Kategorien)
    Ebene 4  Strukturanalyse & Komplexitätsgrenzen

    Rückgabe-Dict von validate():
      total_score     int       0-100
      passed          bool      score >= pass_threshold (Default 70)
      component_scores dict     Aufschlüsselung nach 5 Dimensionen
      layers          dict      Ergebnis je Ebene
      errors          list[str] Kritische Fehler + Fehler
      warnings        list[str] Warnungen
      infos           list[str] Informationen
      suggestions     list[str] Konkrete Verbesserungsvorschläge
      summary         str       Lesbare Zusammenfassung
    """

    def __init__(self, pass_threshold: int = 70):
        self.pass_threshold = pass_threshold

    # ── Öffentliche API ───────────────────────────────────────────────────────

    def validate(self, xaml: str, run_layer4: bool = True) -> Dict:
        """Führt alle Validierungsebenen aus und gibt Gesamt-Bericht zurück."""
        result: Dict = {
            'total_score':      0,
            'passed':           False,
            'component_scores': {k: 0 for k in _COMPONENT_WEIGHTS},
            'layers':           {},
            'errors':           [],
            'warnings':         [],
            'infos':            [],
            'suggestions':      [],
            'summary':          '',
        }

        # ── Ebene 1: XML-Syntax (Gate) ────────────────────────────────────────
        layer1 = self._validate_syntax(xaml)
        result['layers']['xml_syntax'] = layer1
        if not layer1['passed']:
            result['errors'] = layer1['errors']
            result['summary'] = 'XML-Syntaxfehler – weitere Prüfung nicht möglich'
            return result

        try:
            root = ET.fromstring(xaml.encode('utf-8'))
        except ET.ParseError as e:
            result['errors'].append(f"XML Parse-Fehler: {e}")
            result['summary'] = 'XML-Parsing fehlgeschlagen'
            return result

        # ── Ebene 2: Aktivitäten-Whitelist ───────────────────────────────────
        layer2 = self._validate_activities(xaml, root)
        result['layers']['uipath_structure'] = layer2

        # ── Ebene 3: Best-Practice-Regeln ─────────────────────────────────────
        layer3 = self._validate_best_practices(xaml, root)
        result['layers']['best_practices'] = layer3

        # ── Ebene 4: Strukturanalyse ──────────────────────────────────────────
        if run_layer4:
            layer4 = self._validate_structure(xaml, root)
            result['layers']['llm_review'] = layer4
        else:
            layer4 = {'issues': []}

        # ── Score berechnen ───────────────────────────────────────────────────
        score = 100
        all_issues = (
            layer2.get('issues', []) +
            layer3.get('issues', []) +
            layer4.get('issues', [])
        )
        for issue in all_issues:
            sev = issue.get('severity', 'warning')
            score += _SEVERITY_WEIGHTS.get(sev, -5)
            msg = issue['message']
            if sev in ('critical', 'error'):
                result['errors'].append(msg)
            elif sev == 'warning':
                result['warnings'].append(msg)
            else:
                result['infos'].append(msg)

        score = max(0, min(100, score))
        result['total_score'] = score
        result['passed'] = score >= self.pass_threshold

        # ── Component-Scores berechnen ────────────────────────────────────────
        result['component_scores'] = self._compute_component_scores(
            xaml, layer2, layer3, layer4
        )

        # ── Verbesserungsvorschläge ───────────────────────────────────────────
        result['suggestions'] = self._generate_suggestions(result)
        result['summary']     = self._build_summary(score, result)
        return result

    def validate_with_reference(
        self,
        generated_xaml: str,
        reference_xaml: str,
        expected_activities: Optional[List[str]] = None,
    ) -> Dict:
        """
        Erweiterte Validierung mit Referenz-XAML (für Ablationsstudie).

        Berechnet zusätzlich:
          activity_coverage  float  Anteil erwarteter Aktivitäten im generierten XAML
          jaccard_similarity float  Jaccard-Index der Aktivitäts-Sets
          edit_distance_approx int  Approximierte Tree-Edit-Distance (Aktivitätslisten)

        Parameter:
          generated_xaml     – vom Modell generierter Workflow
          reference_xaml     – manuell erstellter oder Template-Workflow
          expected_activities – Liste erwarteter Aktivitätsnamen (optional;
                                wenn None, aus reference_xaml extrahiert)
        """
        base = self.validate(generated_xaml)

        # Aktivitäts-Sets
        gen_acts = set(self._extract_activity_names(generated_xaml))
        ref_acts = set(
            expected_activities or self._extract_activity_names(reference_xaml)
        )

        # Activity Coverage: Wie viele referenz-Aktivitäten tauchen auf?
        coverage = (
            len(gen_acts & ref_acts) / len(ref_acts)
            if ref_acts else 0.0
        )

        # Jaccard-Ähnlichkeit
        union = gen_acts | ref_acts
        jaccard = len(gen_acts & ref_acts) / len(union) if union else 0.0

        # Approximierte Edit-Distance über geordnete Aktivitätslisten
        gen_list = self._extract_activity_names(generated_xaml)
        ref_list = expected_activities or self._extract_activity_names(reference_xaml)
        edit_dist = self._levenshtein_distance(gen_list, ref_list)

        base['activity_coverage']     = round(coverage, 4)
        base['jaccard_similarity']    = round(jaccard, 4)
        base['edit_distance_approx']  = edit_dist
        base['generated_activities']  = sorted(gen_acts)
        base['reference_activities']  = sorted(ref_acts)
        return base

    def validate_batch(
        self,
        xaml_list: List[str],
        run_layer4: bool = False,
    ) -> List[Dict]:
        """
        Validiert eine Liste von XAML-Workflows effizient.
        Layer 4 ist per Default deaktiviert (Performance).
        """
        return [self.validate(xaml, run_layer4=run_layer4) for xaml in xaml_list]

    # ── Ebene 1: XML-Syntax ───────────────────────────────────────────────────

    def _validate_syntax(self, xaml: str) -> Dict:
        errors: List[str] = []

        cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', xaml)
        try:
            ET.fromstring(cleaned.encode('utf-8'))
            xml_ok = True
        except ET.ParseError as e:
            errors.append(f"XML Syntax-Fehler: {e}")
            xml_ok = False

        # Pflicht-Namespaces
        required_ns = [
            ('xmlns="http://schemas.microsoft.com/netfx/2009/xaml/activities"',
             'default XAML activities namespace'),
            ('xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"',
             'XAML language namespace (x:Class, x:TypeArguments)'),
        ]
        for ns, label in required_ns:
            if ns not in xaml:
                errors.append(f"Fehlender Namespace ({label}): {ns[:60]}...")

        # Falsches Root-Element
        if re.search(r'<sap2016:WorkflowViewModel', xaml):
            errors.append(
                "Ungültiges Root-Element <sap2016:WorkflowViewModel> – "
                "verwende <Activity x:Class=\"Namespace.Name\"> stattdessen."
            )

        return {'passed': xml_ok and len(errors) == 0, 'errors': errors}

    # ── Ebene 2: Aktivitäten-Whitelist ────────────────────────────────────────

    def _validate_activities(self, xaml: str, root: ET.Element) -> Dict:
        issues: List[Dict] = []
        found = {m for m in _ACT_NAME_RE.findall(xaml) if m[0].isupper()}
        unknown = (found - VALID_ACTIVITIES) - _XAML_KEYWORDS

        for act in sorted(unknown):
            issues.append({
                'severity': 'error',
                'rule':     'unknown_activity',
                'message':  f"Unbekannte Aktivität '{act}' – mögliche Halluzination. "
                            f"Prüfe ob ein Namespace-Präfix fehlt.",
            })

        return {
            'passed':           not any(i['severity'] == 'error' for i in issues),
            'found_activities': sorted(found & VALID_ACTIVITIES),
            'unknown':          sorted(unknown),
            'issues':           issues,
        }

    # ── Ebene 3: Best-Practice-Regeln ─────────────────────────────────────────

    def _validate_best_practices(self, xaml: str, root: ET.Element) -> Dict:
        issues: List[Dict] = []

        # 3.1 TryCatch vorhanden (erkennt ui:TryCatch, uix:TryCatch, TryCatch)
        has_trycatch = bool(re.search(r'<(?:\w+:)?TryCatch\b', xaml))
        if not has_trycatch:
            issues.append({'severity': 'critical', 'rule': 'no_trycatch',
                           'message': 'Kein TryCatch-Block gefunden. '
                                      'TryCatch ist die Pflicht-Außenstruktur.'})

        # 3.2 Rethrow INNERHALB eines Catch-Blocks (FIX: war vorher nur globale Suche)
        has_rethrow_in_catch = bool(_CATCH_RETHROW_RE.search(xaml))
        has_rethrow_anywhere = '<Rethrow' in xaml or '<ui:Rethrow' in xaml
        if has_trycatch and not has_rethrow_in_catch:
            if has_rethrow_anywhere:
                issues.append({'severity': 'warning', 'rule': 'rethrow_misplaced',
                               'message': '<Rethrow/> gefunden, aber nicht innerhalb '
                                          'eines Catch-Blocks – Exception wird nicht korrekt '
                                          'weitergeleitet.'})
            else:
                issues.append({'severity': 'error', 'rule': 'no_rethrow',
                               'message': 'Kein <Rethrow/> im Catch-Block – '
                                          'Exception wird stillschweigend verschluckt.'})

        # 3.3 Finally-Block (Cleanup)
        if has_trycatch and '<Finally' not in xaml:
            issues.append({'severity': 'info', 'rule': 'no_finally',
                           'message': 'Kein Finally-Block für Ressourcen-Cleanup '
                                      '(Browser schließen, SAP abmelden etc.).'})

        # 3.4 TakeScreenshot im Catch - nur fuer UI-Workflows sinnvoll (TakeScreenshot gehoert zu
        # UiPath.UIAutomation.Activities; in reinen Datei-/API-Workflows wuerde es das Paket erzwingen)
        if has_trycatch and UI_ACTIVITY_PATTERN.search(xaml):
            catch_block = self._extract_catch_content(xaml)
            if catch_block and 'TakeScreenshot' not in catch_block:
                issues.append({'severity': 'info', 'rule': 'no_screenshot_in_catch',
                               'message': 'Kein TakeScreenshot im Catch-Block – '
                                          'erschwert Fehlerdiagnose in Produktion.'})

        # 3.5 Start-LogMessage
        if not re.search(
            r'LogMessage[^>]*(?:Level="Info"[^>]*Message|Message[^>]*"[^"]*Start)',
            xaml, re.IGNORECASE
        ):
            issues.append({'severity': 'error', 'rule': 'no_start_log',
                           'message': 'Kein Start-LogMessage gefunden '
                                      '(Pflicht: erste Aktivität im Workflow).'})

        # 3.6 Ende-LogMessage
        if not re.search(
            r'LogMessage[^>]*"[^"]*(?:Abgeschlossen|Completed|Ende|End|Finish)',
            xaml, re.IGNORECASE
        ):
            issues.append({'severity': 'warning', 'rule': 'no_end_log',
                           'message': 'Kein Abschluss-LogMessage gefunden.'})

        # 3.7 Fehler-LogMessage im Catch
        if has_trycatch:
            catch_content = self._extract_catch_content(xaml)
            has_error_log = (
                catch_content and
                re.search(r'LogMessage', catch_content, re.IGNORECASE) and
                re.search(r'(?:error|fehler|exception|fail)', catch_content, re.IGNORECASE)
            )
            if not has_error_log:
                issues.append({'severity': 'error', 'rule': 'no_error_log',
                               'message': 'Kein Error-LogMessage im Catch-Block.'})

        # 3.8 Argument-Präfixe (in_/out_/io_)
        args = _ARG_NAME_RE.findall(xaml)
        bad_args = [a for a in args if not re.match(r'^(in_|out_|io_)', a)]
        if bad_args:
            issues.append({'severity': 'warning', 'rule': 'arg_naming',
                           'message': f"Argumente ohne Präfix (in_/out_/io_): "
                                      f"{', '.join(bad_args[:4])}"})

        # 3.9 DisplayNames
        display_names = _DISPLAY_NAME_RE.findall(xaml)
        blank_names   = [n for n in display_names if not n.strip()]
        if blank_names:
            issues.append({'severity': 'warning', 'rule': 'blank_display_names',
                           'message': f"{len(blank_names)} leere DisplayName-Werte gefunden."})

        # 3.10 Hardcodierte Credentials
        if _CREDENTIAL_RE.search(xaml):
            issues.append({'severity': 'critical', 'rule': 'hardcoded_credentials',
                           'message': 'Hardcodiertes Passwort/Secret gefunden! '
                                      'Stattdessen GetCredential/GetRobotCredential verwenden.'})

        # 3.11 Hardcodierte Dateipfade
        if _HARDCODED_PATH_RE.search(xaml):
            issues.append({'severity': 'warning', 'rule': 'hardcoded_paths',
                           'message': 'Hardcodierte Dateipfade – stattdessen '
                                      'Orchestrator-Assets oder in_-Argumente verwenden.'})

        # 3.12 Selektor-Platzhalter
        selectors = _SELECTOR_RE.findall(xaml)
        placeholder = [s for s in selectors
                       if 'KONFIGURIEREN' in s or 'TODO' in s or 'SELECTOR' in s.upper()]
        if placeholder and len(placeholder) == len(selectors):
            issues.append({'severity': 'info', 'rule': 'all_selectors_placeholder',
                           'message': f"Alle {len(placeholder)} Selektoren sind Platzhalter – "
                                      f"vor Produktionseinsatz konfigurieren."})

        return {
            'passed': not any(i['severity'] == 'critical' for i in issues),
            'issues': issues,
        }

    # ── Ebene 4: Strukturanalyse ───────────────────────────────────────────────

    def _validate_structure(self, xaml: str, root: ET.Element) -> Dict:
        issues: List[Dict] = []

        # 4.1 Aktivitätszahl (Komplexität)
        activity_count = len(_DISPLAY_NAME_RE.findall(xaml))
        if activity_count > 60:
            issues.append({'severity': 'warning', 'rule': 'high_complexity',
                           'message': f"{activity_count} Aktivitäten – "
                                      f"Workflow in SubWorkflows aufteilen (max. ~40 empfohlen)."})
        elif activity_count < 3:
            issues.append({'severity': 'info', 'rule': 'low_complexity',
                           'message': f"Nur {activity_count} Aktivitäten – "
                                      f"Workflow möglicherweise unvollständig."})

        # 4.2 Verschachtelungstiefe (Heuristik)
        nesting = (xaml.count('<Sequence') + xaml.count('<ForEach')
                   + xaml.count('<If ') + xaml.count('<While'))
        if nesting > 8:
            issues.append({'severity': 'info', 'rule': 'deep_nesting',
                           'message': f"Hohe Verschachtelungstiefe (~{nesting}) – "
                                      f"InvokeWorkflow-Refactoring empfohlen."})

        # 4.3 REFramework-Vollständigkeit
        is_refw = any(kw in xaml for kw in
                      ('GetTransactionItem', 'SetTransactionStatus', 'StateMachine'))
        if is_refw:
            required_rf = ['GetTransactionItem', 'SetTransactionStatus', 'ShouldStop']
            missing_rf  = [r for r in required_rf if r not in xaml]
            if missing_rf:
                issues.append({'severity': 'warning', 'rule': 'reframework_incomplete',
                               'message': f"REFramework erkannt aber unvollständig – "
                                          f"fehlt: {', '.join(missing_rf)}"})

        # 4.4 Variablen-Benennung (camelCase)
        for type_arg, var_name in _VARIABLE_RE.findall(xaml):
            if var_name and not re.match(r'^[a-z][a-zA-Z0-9]*$', var_name):
                issues.append({'severity': 'info', 'rule': 'variable_naming',
                               'message': f"Variable '{var_name}' nicht in camelCase."})
                break  # nur erste Meldung, um Spam zu vermeiden

        # 4.5 Duplikat-DisplayNames
        display_names = _DISPLAY_NAME_RE.findall(xaml)
        counts = Counter(n for n in display_names if n.strip())
        dupes  = [n for n, c in counts.items() if c > 1]
        if dupes:
            issues.append({'severity': 'info', 'rule': 'duplicate_display_names',
                           'message': f"Doppelte DisplayNames: {', '.join(dupes[:3])}"})

        return {'passed': True, 'issues': issues}

    # ── Component-Scores ─────────────────────────────────────────────────────

    def _compute_component_scores(
        self,
        xaml: str,
        layer2: Dict,
        layer3: Dict,
        layer4: Dict,
    ) -> Dict[str, int]:
        """
        Berechnet komponentenweise Scores für die Ablationsstudie.

        Jeder Score ist 0-100 und gewichtet die Regeln seiner Kategorie.
        Die Gewichtung der Komponenten ist in _COMPONENT_WEIGHTS dokumentiert.

        Zweck: Ermöglicht differenziertere Aussagen als ein einzelner Gesamt-Score.
        Beispiel: Ein Modell kann bei 'logging' gut sein aber bei 'security' versagen.
        """
        def issues_of(layer: Dict, rules: List[str]) -> List[Dict]:
            return [i for i in layer.get('issues', []) if i.get('rule') in rules]

        # Struktur: TryCatch, Rethrow, Finally
        struct_issues = issues_of(layer3, ['no_trycatch', 'no_rethrow',
                                            'rethrow_misplaced', 'no_finally'])
        structure = max(0, 100 - sum(_SEVERITY_WEIGHTS.get(i['severity'], -5) * -1
                                      for i in struct_issues))

        # Logging: Start, Ende, Fehler
        log_issues = issues_of(layer3, ['no_start_log', 'no_end_log', 'no_error_log'])
        logging_s  = max(0, 100 - sum(_SEVERITY_WEIGHTS.get(i['severity'], -5) * -1
                                       for i in log_issues))

        # Benennung: Argumente, Variablen, DisplayNames
        name_issues = issues_of(layer3, ['arg_naming', 'blank_display_names',
                                          'missing_display_names'])
        name_issues += issues_of(layer4, ['variable_naming', 'duplicate_display_names'])
        naming = max(0, 100 - sum(_SEVERITY_WEIGHTS.get(i['severity'], -5) * -1
                                   for i in name_issues))

        # Sicherheit: Credentials, Pfade
        sec_issues = issues_of(layer3, ['hardcoded_credentials', 'hardcoded_paths'])
        security   = max(0, 100 - sum(_SEVERITY_WEIGHTS.get(i['severity'], -5) * -1
                                       for i in sec_issues))

        # Vollständigkeit: Namespace, Whitelist
        comp_issues = layer2.get('issues', [])
        completeness = max(0, 100 - len(comp_issues) * 15)

        return {
            'structure':    min(100, structure),
            'logging':      min(100, logging_s),
            'naming':       min(100, naming),
            'security':     min(100, security),
            'completeness': min(100, completeness),
        }

    # ── Suggestions ──────────────────────────────────────────────────────────

    def _generate_suggestions(self, result: Dict) -> List[str]:
        """
        Generiert konkrete, priorisierte Verbesserungsvorschläge.
        Getrennt von errors/warnings damit die UI sie separat anzeigen kann.
        """
        suggestions: List[str] = []
        errors   = result.get('errors', [])
        warnings = result.get('warnings', [])
        comp     = result.get('component_scores', {})

        if comp.get('structure', 100) < 70:
            suggestions.append(
                "Struktur: Umschließe den gesamten Workflow mit einem "
                "<TryCatch>-Block und stelle sicher dass Catch → "
                "LogMessage(Error) → Rethrow enthält."
            )
        if comp.get('logging', 100) < 70:
            suggestions.append(
                "Logging: Füge LogMessage-Aktivitäten am Anfang ('Start'), "
                "Ende ('Abgeschlossen') und im Catch-Block ('FEHLER: {exception.Message}') ein."
            )
        if comp.get('security', 100) < 80:
            suggestions.append(
                "Sicherheit: Ersetze hardcodierte Credentials durch "
                "GetCredential/GetRobotCredential und Pfade durch Orchestrator-Assets."
            )
        if comp.get('naming', 100) < 70:
            suggestions.append(
                "Benennung: Vergib allen Aktivitäten einen beschreibenden DisplayName "
                "und nutze in_/out_-Präfixe für alle Workflow-Argumente."
            )
        if any('Selektor' in e or 'selector' in e.lower() for e in warnings):
            suggestions.append(
                "Selektoren: Öffne den UiPath Studio Selektor-Editor und ersetze "
                "alle KONFIGURIEREN-Platzhalter durch echte Selektoren."
            )
        return suggestions

    # ── Hilfsmethoden ─────────────────────────────────────────────────────────

    def _extract_catch_content(self, xaml: str) -> Optional[str]:
        """Extrahiert den Inhalt des ersten Catch-Blocks (Textbasiert)."""
        m = re.search(r'<Catch\b[^>]*>(.*?)</Catch>', xaml, re.DOTALL)
        return m.group(1) if m else None

    def _extract_activity_names(self, xaml: str) -> List[str]:
        """Extrahiert geordnete Liste aller DisplayName-Werte aus XAML."""
        return _DISPLAY_NAME_RE.findall(xaml)

    @staticmethod
    def _levenshtein_distance(a: List[str], b: List[str]) -> int:
        """
        Levenshtein-Distanz zwischen zwei Listen von Aktivitätsnamen.
        Approximiert die Tree-Edit-Distance für lineare Workflows.
        Komplexität: O(n*m) – für typische Workflow-Längen (< 60) ausreichend.
        """
        n, m = len(a), len(b)
        if n == 0: return m
        if m == 0: return n
        dp = [[0] * (m + 1) for _ in range(n + 1)]
        for i in range(n + 1): dp[i][0] = i
        for j in range(m + 1): dp[0][j] = j
        for i in range(1, n + 1):
            for j in range(1, m + 1):
                cost = 0 if a[i-1] == b[j-1] else 1
                dp[i][j] = min(
                    dp[i-1][j] + 1,
                    dp[i][j-1] + 1,
                    dp[i-1][j-1] + cost,
                )
        return dp[n][m]

    def _build_summary(self, score: int, result: Dict) -> str:
        n_err  = len(result['errors'])
        n_warn = len(result['warnings'])
        status = (
            "Exzellent"          if score >= 90 else
            "Gut"                if score >= 70 else
            "Akzeptabel"         if score >= 50 else
            "Verbesserungsbedarf"
        )
        return (
            f"{status} ({score}/100) – "
            f"{n_err} Fehler, {n_warn} Warnungen"
        )
