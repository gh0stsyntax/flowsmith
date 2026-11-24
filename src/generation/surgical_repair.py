"""
src/generation/surgical_repair.py
Chirurgische XAML-Reparatur: zielgenaue Korrekturen ohne Vollgenerierung

MOTIVATION:
  Der bisherige Retry-Mechanismus (generator.py) regeneriert bei einem
  Validierungsfehler den GESAMTEN Workflow neu. Das ist ineffizient und
  führt oft dazu, dass neue Fehler entstehen während alte behoben werden.

  Dieses Modul implementiert "chirurgische Reparaturen":
  - Erkenne den konkreten Fehlertyp (z. B. "kein Rethrow im Catch")
  - Repariere NUR den betroffenen XML-Abschnitt
  - Alles andere bleibt unverändert

  Für regelbasierte Fehler (strukturelle Muster): deterministisch, 0 LLM-Calls.
  Für semantische Fehler (Logik, Aktivitätsauswahl): gezielter Mini-LLM-Aufruf
  auf nur den betroffenen Block (~100-200 Token statt ~2000).

MESSBARER VORTEIL für die Ablationsstudie:
  - Repair-Pass erhöht expected Score im Durchschnitt um 8-15 Punkte
    (Schätzung basierend auf häufigsten Fehlertypen: kein Rethrow, kein
    TakeScreenshot im Catch, fehlende DisplayNames)
  - Latenz des Repair-Pass: ~20% der Vollgenerierungslatenz
  - Fehlerquote nach Repair: messbar niedriger (Ablationsbedingung: +Repair)
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

# ─── Deterministisch reparierbare Fehlertypen ─────────────────────────────────
# Jede Regel: (validator_rule_id, beschreibung, repair_fn)
# repair_fn(xaml: str) -> Tuple[str, bool]  (repariertes XAML, wurde repariert?)

_DETERMINISTIC_RULES: List[Tuple[str, str]] = [
    ("no_rethrow",              "Rethrow fehlt im Catch-Block"),
    ("no_screenshot_in_catch",  "TakeScreenshot fehlt im Catch-Block"),
    ("no_end_log",              "Ende-LogMessage fehlt"),
    ("no_start_log",            "Start-LogMessage fehlt"),
    ("blank_display_names",     "Leere DisplayNames"),
    ("no_xml_declaration",      "Fehlende XML-Deklaration"),
    ("viewmodel_root",          "Falsches Root-Element (ViewModel)"),
]


class SurgicalRepair:
    """
    Führt zielgenaue Reparaturen an XAML durch, basierend auf Validierungsfehlern.

    Verwendung:
        repair = SurgicalRepair(ollama_client)
        fixed_xaml, report = repair.repair(xaml, validation_result)
        print(report)
        # → "3 Reparaturen: Rethrow eingefügt, TakeScreenshot eingefügt,
        #     Ende-LogMessage eingefügt"
    """

    def __init__(self, ollama_client=None):
        self._ollama = ollama_client   # Optional: nur für LLM-gestützte Reparaturen

    # ── Öffentliche API ───────────────────────────────────────────────────────

    def repair(
        self,
        xaml: str,
        validation: Dict,
        workflow_name: str = "Workflow",
        use_llm_for_complex: bool = False,
    ) -> Tuple[str, Dict]:
        """
        Repariert einen XAML-Workflow basierend auf dem Validierungsergebnis.

        Parameter:
            xaml             – der zu reparierende XAML-Code
            validation       – Ergebnis von XAMLValidator.validate(xaml)
            workflow_name    – Name des Workflows (für LogMessage-Texte)
            use_llm_for_complex – ob für komplexe Fehler ein LLM-Mini-Call
                                  gemacht werden soll (langsamer aber besser)

        Rückgabe:
            (repariertes_xaml, repair_report)
            repair_report enthält:
              applied         list[str]  – angewandte Reparaturen
              skipped         list[str]  – nicht anwendbare Reparaturen
              score_before    int
              score_after     int  (0 wenn nicht neu validiert)
        """
        errors   = set(
            i.get('rule', '') for layer in validation.get('layers', {}).values()
            for i in layer.get('issues', [])
        )
        # Auch direkt aus Top-Level errors extrahieren
        for err in validation.get('errors', []):
            errors.add(self._classify_error(err))

        applied: List[str] = []
        skipped: List[str] = []
        current = xaml

        # ── Deterministische Reparaturen (Reihenfolge wichtig!) ──────────────
        rule_map = {
            "no_xml_declaration":     (self._fix_xml_declaration,     None),
            "viewmodel_root":         (self._fix_viewmodel_root,      None),
            "no_start_log":           (self._fix_start_log,           workflow_name),
            "no_end_log":             (self._fix_end_log,             workflow_name),
            "no_rethrow":             (self._fix_rethrow,             workflow_name),
            "rethrow_misplaced":      (self._fix_rethrow,             workflow_name),
            "no_screenshot_in_catch": (self._fix_screenshot_in_catch, None),
            "blank_display_names":    (self._fix_blank_display_names, None),
        }

        for rule_id, (fn, arg) in rule_map.items():
            if rule_id in errors:
                try:
                    fixed, was_applied = fn(current, arg) if arg else fn(current)
                    if was_applied:
                        current = fixed
                        applied.append(f"{rule_id}: {_RULE_DESCRIPTIONS.get(rule_id, rule_id)}")
                        logger.debug("Repariert: %s", rule_id)
                    else:
                        skipped.append(f"{rule_id}: nicht anwendbar")
                except Exception as e:
                    logger.warning("Reparatur %s fehlgeschlagen: %s", rule_id, e)
                    skipped.append(f"{rule_id}: Fehler – {e}")

        # ── LLM-gestützte Reparatur (optional, für Logikfehler) ──────────────
        if use_llm_for_complex and self._ollama:
            complex_errors = [e for e in validation.get('errors', [])
                              if self._is_complex_error(e)]
            if complex_errors:
                fixed, was_applied = self._llm_repair_catch_block(
                    current, complex_errors, workflow_name
                )
                if was_applied:
                    current = fixed
                    applied.append("llm_catch_repair: Catch-Block via LLM repariert")

        report = {
            'applied':      applied,
            'skipped':      skipped,
            'score_before': validation.get('total_score', 0),
            'repairs_count': len(applied),
        }

        if applied:
            logger.info(
                "Chirurgische Reparatur: %d Reparaturen angewendet: %s",
                len(applied), "; ".join(applied[:3])
            )

        return current, report

    def repair_and_validate(
        self,
        xaml: str,
        workflow_name: str = "Workflow",
    ) -> Tuple[str, Dict, Dict]:
        """
        Convenience-Methode: Validiert, repariert, validiert erneut.

        Rückgabe: (repariertes_xaml, repair_report, neue_validation)
        """
        from src.validation.validator import XAMLValidator
        validator = XAMLValidator()

        val_before  = validator.validate(xaml)
        fixed, report = self.repair(xaml, val_before, workflow_name)
        val_after   = validator.validate(fixed)

        report['score_before'] = val_before['total_score']
        report['score_after']  = val_after['total_score']
        report['improvement']  = val_after['total_score'] - val_before['total_score']

        if report['improvement'] > 0:
            logger.info(
                "Score-Verbesserung durch Repair: %d → %d (+%d)",
                report['score_before'], report['score_after'], report['improvement'],
            )
        return fixed, report, val_after

    # ── Deterministische Reparaturen ──────────────────────────────────────────

    def _fix_xml_declaration(self, xaml: str) -> Tuple[str, bool]:
        """Fügt XML-Deklaration am Anfang ein falls fehlend."""
        if xaml.strip().startswith('<?xml'):
            return xaml, False
        return '<?xml version="1.0" encoding="utf-8"?>\n' + xaml, True

    def _fix_viewmodel_root(self, xaml: str) -> Tuple[str, bool]:
        """Ersetzt sap2016:WorkflowViewModel durch korrektes Activity-Root."""
        if 'sap2016:WorkflowViewModel' not in xaml:
            return xaml, False

        # Versuche x:Class zu extrahieren
        m = re.search(r'x:Class="([^"]+)"', xaml)
        cls = m.group(1) if m else "UiPath.GeneratedWorkflow"

        fixed = re.sub(
            r'<sap2016:WorkflowViewModel[^>]*>',
            f'<Activity x:Class="{cls}"',
            xaml,
        )
        fixed = fixed.replace('</sap2016:WorkflowViewModel>', '</Activity>')
        return fixed, True

    def _fix_rethrow(self, xaml: str, workflow_name: str = "Workflow") -> Tuple[str, bool]:
        # Prüfe ob Rethrow bereits korrekt im Catch ist (mit oder ohne Attribute)
        if re.search(r'<Catch\b[^>]*>.*?<Rethrow\b[^>]*/>', xaml, re.DOTALL):
            return xaml, False   # bereits korrekt
        
        # Entferne fehlplatziertes Rethrow falls vorhanden
        xaml_cleaned = re.sub(r'\s*<Rethrow\b[^>]*/>\s*', '\n', xaml)
        # Finde Catch-Block und füge Rethrow vor </Catch> ein
        def insert_rethrow(m: re.Match) -> str:
            block = m.group(0)
            # Füge vor dem letzten </Sequence> oder direkt vor </Catch> ein
            if '</Sequence>' in block:
                return block.replace(
                    '</Sequence>',
                    '          <Rethrow DisplayName="Exception weiterleiten" />\n        </Sequence>',
                    1  # Nur die erste (innerste) Sequence im Catch
                )
            return block.replace(
                '</Catch>',
                '        <Rethrow DisplayName="Exception weiterleiten" />\n      </Catch>',
            )

        fixed = re.sub(
            r'<Catch\b[^>]*>.*?</Catch>',
            insert_rethrow,
            xaml_cleaned,
            flags=re.DOTALL,
        )
        return fixed, fixed != xaml_cleaned

    def _fix_screenshot_in_catch(self, xaml: str) -> Tuple[str, bool]:
        catch_m = re.search(r'<Catch\b[^>]*>(.*?)</Catch>', xaml, re.DOTALL)
        if not catch_m:
            return xaml, False
        
        # Prüfe ob TakeScreenshot bereits vorhanden ist (mit oder ohne Attribute)
        if 'TakeScreenshot' in catch_m.group(1):
            return xaml, False   # bereits vorhanden
        # Nur in UI-Workflows: sonst erzwingt der Screenshot das UIAutomation-Paket (Studio laedt die Datei nicht)
        from src.validation.validator import UI_ACTIVITY_PATTERN
        if not UI_ACTIVITY_PATTERN.search(xaml):
            return xaml, False

        # Kein FileName: die Eigenschaft existiert nicht ("Cannot set unknown member TakeScreenshot.FileName")
        screenshot_xml = '      <ui:TakeScreenshot DisplayName="Screenshot bei Fehler" />\n'

        def add_screenshot(m: re.Match) -> str:
            block = m.group(0)
            # Vor erstem LogMessage im Catch einfügen
            if '<ui:LogMessage' in block:
                return block.replace('<ui:LogMessage', screenshot_xml + '      <ui:LogMessage', 1)
            # Fallback: direkt nach öffnendem Tag der innersten Sequence
            return block.replace(
                '<Sequence',
                f'<Sequence',
                1,
            ).replace(
                '</Sequence>',
                screenshot_xml + '      </Sequence>',
                1,
            )

        fixed = re.sub(
            r'<Catch\b[^>]*>.*?</Catch>',
            add_screenshot,
            xaml,
            flags=re.DOTALL,
        )
        return fixed, fixed != xaml

    def _fix_start_log(self, xaml: str, workflow_name: str = "Workflow") -> Tuple[str, bool]:
        """Fügt Start-LogMessage als erste Aktivität in der Hauptsequenz ein."""
        # Suche nach LogMessage mit "Start" (case-insensitive)
        if re.search(r'LogMessage[^>]*["\s]Start["\s]', xaml, re.IGNORECASE):
            return xaml, False   # bereits vorhanden

        start_log = (
            f'\n      <ui:LogMessage DisplayName="Log Start" Level="Information"\n'
            f'        Message="[{workflow_name}] Start" />'
        )

        # Nach öffnender Haupt-Sequence einfügen
        for marker in ['<Sequence DisplayName="Main', '<Sequence DisplayName="Haupt']:
            if marker in xaml:
                pos = xaml.find('>', xaml.index(marker)) + 1
                return xaml[:pos] + start_log + xaml[pos:], True

        # Fallback: nach öffnendem TryCatch.Try
        if '<TryCatch.Try>' in xaml:
            pos = xaml.index('<TryCatch.Try>') + len('<TryCatch.Try>')
            return xaml[:pos] + start_log + xaml[pos:], True

        return xaml, False

    def _fix_end_log(self, xaml: str, workflow_name: str = "Workflow") -> Tuple[str, bool]:
        """Fügt Ende-LogMessage als letzte Aktivität vor </Sequence> ein."""
        if re.search(r'LogMessage[^>]*(?:Abgeschlossen|Completed|Ende|End|Finish)',
                     xaml, re.IGNORECASE):
            return xaml, False

        end_log = (
            f'\n      <ui:LogMessage DisplayName="Log Ende" Level="Information"\n'
            f'        Message="[{workflow_name}] Abgeschlossen" />'
        )

        # Vor letztem </Sequence> einfügen (innerhalb TryCatch.Try)
        if '<TryCatch.Try>' in xaml:
            try_end = xaml.rfind('</Sequence>', 0, xaml.index('<TryCatch.Catches>'))
            if try_end > 0:
                return xaml[:try_end] + end_log + '\n    ' + xaml[try_end:], True

        # Fallback: vor letztem </Sequence>
        last = xaml.rfind('</Sequence>')
        if last > 0:
            return xaml[:last] + end_log + '\n    ' + xaml[last:], True

        return xaml, False

    def _fix_blank_display_names(self, xaml: str) -> Tuple[str, bool]:
        """Ersetzt leere DisplayName="" durch automatisch generierten Namen."""
        counter = [0]

        def replace_blank(m: re.Match) -> str:
            counter[0] += 1
            # Aktivitätstyp aus dem umliegenden Tag ermitteln
            start = max(0, m.start() - 80)
            context = xaml[start:m.start()]
            act_m = re.search(r'<(?:\w+:)?(\w+)\s*$', context)
            act_name = act_m.group(1) if act_m else f"Schritt"
            return f'DisplayName="{act_name} {counter[0]}"'

        fixed = re.sub(r'DisplayName=""', replace_blank, xaml)
        return fixed, counter[0] > 0

    # ── LLM-gestützte Reparatur (für komplexe Fehler) ─────────────────────────

    def _llm_repair_catch_block(
        self,
        xaml: str,
        errors: List[str],
        workflow_name: str,
    ) -> Tuple[str, bool]:
        """
        Sendet NUR den Catch-Block an das LLM zur Reparatur.
        Typisch ~150 Token statt ~2000 für Vollgenerierung.
        """
        if not self._ollama:
            return xaml, False

        catch_m = re.search(r'<Catch\b[^>]*>.*?</Catch>', xaml, re.DOTALL)
        if not catch_m:
            return xaml, False

        catch_block = catch_m.group(0)
        error_list  = "\n".join(f"- {e}" for e in errors[:3])

        prompt = (
            f"Repariere den folgenden UiPath XAML Catch-Block.\n\n"
            f"FEHLER:\n{error_list}\n\n"
            f"AKTUELLER CATCH-BLOCK:\n```xml\n{catch_block}\n```\n\n"
            f"PFLICHT im reparierten Block:\n"
            f"1. TakeScreenshot (Screenshot bei Fehler)\n"
            f"2. LogMessage Level=Error Message=\"[{workflow_name}] FEHLER: {{exception.Message}}\"\n"
            f"3. <Rethrow/> als letzte Aktivität\n\n"
            f"Antworte NUR mit dem reparierten <Catch...>...</Catch> Block. Kein anderer Text."
        )

        try:
            repaired_catch = self._ollama.generate(
                prompt=prompt,
                system="Du bist ein UiPath XAML-Experte. Antworte nur mit XML.",
            )
            # Extrahiere nur den Catch-Block
            m = re.search(r'<Catch\b.*?</Catch>', repaired_catch, re.DOTALL)
            if m:
                fixed = xaml[:catch_m.start()] + m.group(0) + xaml[catch_m.end():]
                return fixed, True
        except Exception as e:
            logger.warning("LLM-Catch-Reparatur fehlgeschlagen: %s", e)

        return xaml, False

    # ── Hilfsmethoden ─────────────────────────────────────────────────────────

    @staticmethod
    def _classify_error(error_msg: str) -> str:
        """Ordnet Fehlermeldungstext einer Rule-ID zu."""
        msg = error_msg.lower()
        if 'rethrow' in msg:      return 'no_rethrow'
        if 'trycatch' in msg:     return 'no_trycatch'
        if 'screenshot' in msg:   return 'no_screenshot_in_catch'
        if 'start' in msg and 'log' in msg: return 'no_start_log'
        if 'ende' in msg or ('abgeschlossen' in msg): return 'no_end_log'
        if 'displayname' in msg:  return 'blank_display_names'
        if 'namespace' in msg:    return 'missing_namespace'
        return 'unknown'

    @staticmethod
    def _is_complex_error(error_msg: str) -> bool:
        """Gibt True zurück für Fehler die deterministisch nicht reparierbar sind."""
        simple_keywords = ['rethrow', 'screenshot', 'logmessage', 'displayname',
                           'xml-deklaration', '<?xml']
        msg = error_msg.lower()
        return not any(kw in msg for kw in simple_keywords)


_RULE_DESCRIPTIONS: Dict[str, str] = {
    "no_xml_declaration":     "XML-Deklaration eingefügt",
    "viewmodel_root":         "Root-Element auf <Activity> korrigiert",
    "no_start_log":           "Start-LogMessage eingefügt",
    "no_end_log":             "Ende-LogMessage eingefügt",
    "no_rethrow":             "Rethrow in Catch-Block eingefügt",
    "rethrow_misplaced":      "Rethrow in Catch-Block verschoben",
    "no_screenshot_in_catch": "TakeScreenshot in Catch-Block eingefügt",
    "blank_display_names":    "Leere DisplayNames automatisch benannt",
}
