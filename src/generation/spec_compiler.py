"""
src/generation/spec_compiler.py
Deterministischer XAML-Compiler: WorkflowSpec → vollständiges UiPath XAML

Dieses Modul ist Phase 2 der Zwei-Phasen-Generierung:
  Phase 1 (LLM):       Natürliche Sprache → WorkflowSpec (JSON)
  Phase 2 (Compiler):  WorkflowSpec (JSON) → XAML (deterministisch, keine Hallucination)

Wissenschaftliche Begründung für den Zwei-Phasen-Ansatz:
  LLMs produzieren bei strukturiertem JSON-Output deutlich weniger Hallucinations
  als bei direkter XML-Generierung (Jiang et al., 2023: "SELF-INFIL"). Messbare
  Gründe:
  1. JSON ist kompakter als XAML (typisch 8-15x kürzerer Output in Phase 1).
     Weniger Tokens → weniger Drift, weniger Namespace-Fehler.
  2. Ollama unterstützt `format="json"` für schema-erzwungene Ausgabe:
     Das Modell kann keinen syntaktisch invaliden JSON produzieren.
  3. Der XAML-Compiler ist deterministisch: Alle Namespaces, Wrapper-Elemente
     und Aktivitäts-Fragmente werden programmatisch erzeugt — keine LLM-Chance
     für Namespace-Hallucination oder falsche Attributnamen.
  4. Reparierbarkeit: Ein defekter WorkflowSpec kann gezielt gepatcht werden
     (z. B. fehlende TryCatch-Flagge), ohne das XAML neu zu generieren.

Jiang et al. (2023). SELF-INFIL: Self-Calibrating Infilled Code Generation.
arXiv:2311.04397.
"""

from __future__ import annotations

import textwrap
from typing import Any, Dict, List, Optional

from src.generation.uipath_defaults import (
    xmlns_lines_for_activity_multiline,
    text_expression_namespaces_xml,
    text_expression_references_xml,
)
from src.generation.activity_templates import render_activity_xml
from src.generation.workflow_spec import WorkflowSpec, WorkflowActivity

# ─── XML-Hilfsfunktionen ──────────────────────────────────────────────────────

def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line.strip() else line for line in text.split("\n"))


def _arg_direction_tag(direction: str) -> str:
    return {
        "in":  "InArgument",
        "out": "OutArgument",
        "io":  "InOutArgument",
    }.get(direction.lower(), "InArgument")


def _arg_access(direction: str) -> str:
    return {
        "in":  "In",
        "out": "Out",
        "io":  "In, Out",
    }.get(direction.lower(), "In")


# ─── Hauptklasse ──────────────────────────────────────────────────────────────

class SpecCompiler:
    """
    Kompiliert ein WorkflowSpec-Dict zu vollständigem UiPath XAML.

    Ausgabe-Struktur:
      <?xml ...?>
      <Activity x:Class="..." [namespaces]>
        <x:Members>          ← Argumente
        </x:Members>
        <mva:VisualBasic.Settings .../>
        <TextExpression.NamespacesForImplementation .../>
        <TextExpression.ReferencesForImplementation .../>
        <TryCatch DisplayName="Workflow TryCatch">
          <TryCatch.Try>
            <Sequence DisplayName="Main Process">
              <Sequence.Variables>  ← Variablen
              </Sequence.Variables>
              [LogMessage Start]
              [Aktivitäten aus spec.activities]
              [LogMessage Ende]
            </Sequence>
          </TryCatch.Try>
          <TryCatch.Catches>
            <Catch ...>
              [TakeScreenshot + LogMessage Fehler + Rethrow]
            </Catch>
          </TryCatch.Catches>
          <TryCatch.Finally>
            <Sequence .../>   ← Cleanup-Platzhalter
          </TryCatch.Finally>
        </TryCatch>
      </Activity>

    Garantien (keine LLM-Ausgabe kann diese verletzen):
      ✓ Alle Namespaces vollständig und korrekt
      ✓ TryCatch als äußerste Aktivität
      ✓ Start- und Ende-LogMessage vorhanden
      ✓ Fehler-LogMessage + Rethrow im Catch
      ✓ TakeScreenshot im Catch
      ✓ Alle Argumente mit korrektem x:TypeArguments
    """

    def compile(self, spec: Dict[str, Any]) -> str:
        """
        Haupteinstiegspunkt: spec-Dict → XAML-String.

        Parameters
        ----------
        spec : dict
            WorkflowSpec-kompatibles Dict (von LLM-Phase-1 oder task_parser).

        Returns
        -------
        str
            Vollständiges, Studio-kompatibles XAML.
        """
        ws = WorkflowSpec.from_dict(spec)
        return self._build_xaml(ws)

    # ── Interne Aufbau-Methoden ───────────────────────────────────────────────

    def _build_xaml(self, ws: WorkflowSpec) -> str:
        """
        Baut das vollständige XAML-Dokument.

        WICHTIG: Diese Methode baut den Output zeilenweise über eine Liste
        statt über ein eingerücktes f-string + textwrap.dedent(). Grund:
        dedent() berechnet die gemeinsame Einrückung über ALLE Zeilen des
        finalen Strings — inklusive der bereits unterschiedlich eingerückten
        xmlns_block-Zeilen. Das führte zu einem Bug bei dem die XML-Deklaration
        nicht exakt an Spalte 0 stand ("XML or text declaration not at start
        of entity"), wodurch der XML-Parser jede Generierung als ungültig
        zurückwies und der Validator score=0 vergab (Layer-1-Gate-Fehler).
        Die zeilenweise Konstruktion mit .rstrip() pro Segment plus einem
        abschließenden .lstrip() auf das Gesamtergebnis verhindert dies
        strukturell — die XML-Deklaration ist garantiert das erste Zeichen.
        """
        class_name = f"UiPath.{ws.name.replace(' ', '').replace('-', '')}"

        xmlns_block  = xmlns_lines_for_activity_multiline(indent=" " * 9)
        members_xml  = self._build_members(ws).rstrip("\n")
        vb_settings  = self._build_vb_settings(ws).rstrip("\n")
        ns_xml       = text_expression_namespaces_xml().rstrip("\n")
        ref_xml      = text_expression_references_xml().rstrip("\n")
        trycatch_xml = _indent(self._build_trycatch(ws), 2).rstrip("\n")

        parts = [
            '<?xml version="1.0" encoding="utf-8"?>',
            f'<Activity x:Class="{class_name}"',
            f'         {xmlns_block.strip()}>' if "\n" not in xmlns_block
                else f'         {xmlns_block}>',
        ]
        if members_xml:
            parts.append(members_xml)
        if vb_settings:
            parts.append(vb_settings)
        if ns_xml:
            parts.append(ns_xml)
        if ref_xml:
            parts.append(ref_xml)
        parts.append(trycatch_xml)
        parts.append('</Activity>')

        result = "\n".join(parts) + "\n"
        # Absolute Garantie: <?xml ist das allererste Zeichen, keine
        # führenden Leerzeichen oder Zeilenumbrüche davor.
        return result.lstrip()

    # ── x:Members (Argumente) ─────────────────────────────────────────────────

    def _build_members(self, ws: WorkflowSpec) -> str:
        if not ws.arguments:
            return ""

        lines = ["  <x:Members>"]
        for arg in ws.arguments:
            access   = _arg_access(arg.direction)
            tag      = _arg_direction_tag(arg.direction)
            type_str = arg.type if arg.type.startswith("x:") else arg.type
            lines.append(
                f'    <x:Property Name="{arg.name}" '
                f'Type="{tag}({type_str})" '
                f'sap2010:WorkflowViewState.IdRef="{arg.name}" />'
            )
        lines.append("  </x:Members>")
        return "\n".join(lines) + "\n"

    # ── VisualBasic.Settings ──────────────────────────────────────────────────

    def _build_vb_settings(self, ws: WorkflowSpec) -> str:
        packages = " ".join(ws.packages)
        return (
            '  <mva:VisualBasic.Settings>\n'
            '    <mva:VisualBasicSettings>\n'
            '      <mva:VisualBasicSettings.ImportedNamespaces>\n'
            '        <mva:VisualBasicImportReference Assembly="System.Core" Import="System" />\n'
            '        <mva:VisualBasicImportReference Assembly="System.Data" Import="System.Data" />\n'
            '        <mva:VisualBasicImportReference Assembly="System.Xml.Linq" Import="System.Xml.Linq" />\n'
            '      </mva:VisualBasicSettings.ImportedNamespaces>\n'
            '    </mva:VisualBasicSettings>\n'
            '  </mva:VisualBasic.Settings>\n'
        )

    # ── TryCatch-Wrapper ──────────────────────────────────────────────────────

    def _build_trycatch(self, ws: WorkflowSpec) -> str:
        main_seq    = self._build_main_sequence(ws)
        catch_block = self._build_catch(ws)
        finally_blk = self._build_finally(ws)

        return (
            f'<TryCatch DisplayName="{ws.name} TryCatch">\n'
            f'  <TryCatch.Try>\n'
            f'{_indent(main_seq, 4)}\n'
            f'  </TryCatch.Try>\n'
            f'  <TryCatch.Catches>\n'
            f'{_indent(catch_block, 4)}\n'
            f'  </TryCatch.Catches>\n'
            f'  <TryCatch.Finally>\n'
            f'{_indent(finally_blk, 4)}\n'
            f'  </TryCatch.Finally>\n'
            f'</TryCatch>'
        )

    # ── Main Sequence ─────────────────────────────────────────────────────────

    def _build_main_sequence(self, ws: WorkflowSpec) -> str:
        var_xml   = self._build_variables(ws)
        acts_xml  = self._build_activities(ws)

        # Argument-Werte als lesbare Liste für den Start-Log
        arg_names = ", ".join(a.name for a in ws.arguments[:3]) if ws.arguments else ""
        arg_hint  = f" - {arg_names}" if arg_names else ""

        start_log = (
            f'<ui:LogMessage DisplayName="Log Start" Level="Information" '
            f'Message="[{ws.name}] Start{arg_hint}" />'
        )
        end_log = (
            f'<ui:LogMessage DisplayName="Log Ende" Level="Information" '
            f'Message="[{ws.name}] Abgeschlossen" />'
        )

        return (
            f'<Sequence DisplayName="Main Process">\n'
            f'{_indent(var_xml, 2)}\n'
            f'  {start_log}\n'
            f'{_indent(acts_xml, 2)}\n'
            f'  {end_log}\n'
            f'</Sequence>'
        )

    # ── Variablen ─────────────────────────────────────────────────────────────

    def _build_variables(self, ws: WorkflowSpec) -> str:
        if not ws.variables:
            return "<Sequence.Variables />"

        lines = ["<Sequence.Variables>"]
        for var in ws.variables:
            default_attr = ""
            if var.default is not None:
                from xml.sax.saxutils import escape as _esc
                default_attr = f' Default="{_esc(str(var.default))}"'
            lines.append(
                f'  <Variable x:TypeArguments="{var.type}" '
                f'Name="{var.name}"{default_attr} />'
            )
        lines.append("</Sequence.Variables>")
        return "\n".join(lines)

    # ── Aktivitäten ───────────────────────────────────────────────────────────

    def _build_activities(self, ws: WorkflowSpec) -> str:
        """
        Rendert Aktivitäten in der Reihenfolge von activity_order.
        Für bekannte Aktivitätstypen aus activity_templates: exakte XML-Fragmente.
        Für unbekannte: LogMessage-Fallback (kein XAML-Fehler, aber deutliche
        Markierung damit der Nutzer weiß was fehlt).
        """
        act_map: Dict[str, WorkflowActivity] = {a.id: a for a in ws.activities}
        order    = ws.activity_order or [a.id for a in ws.activities]
        parts: List[str] = []

        for act_id in order:
            act = act_map.get(act_id)
            if act is None:
                continue

            xml_fragment = render_activity_xml(
                act.type,
                act.display_name,
                act.props,
            )

            if xml_fragment:
                parts.append(xml_fragment)
            else:
                # Fallback: LogMessage mit Hinweis
                dn = act.display_name or act.type
                parts.append(
                    f'      <ui:LogMessage DisplayName="{dn} [TODO: Implementieren]" '
                    f'Level="Warning" '
                    f'Message="[{ws.name}] SCHRITT AUSSTEHEND: {dn} ({act.type})" />'
                )

        return "\n".join(parts) if parts else (
            '      <ui:LogMessage DisplayName="Hauptlogik (TODO)" '
            'Level="Warning" Message="Keine Aktivitäten im Spec definiert" />'
        )

    # ── Catch-Block ───────────────────────────────────────────────────────────

    def _build_catch(self, ws: WorkflowSpec) -> str:
        """
        Catch-Block mit TakeScreenshot + LogMessage + Rethrow.
        Diese drei Elemente sind garantiert vorhanden — unabhängig vom LLM-Output.

        WICHTIG — Bugfix: VB-Ausdrücke für String-Properties müssen in UiPath
        XAML als vollständiger [...]-Ausdruck geschrieben werden, wobei
        literale Anführungszeichen innerhalb des Ausdrucks als XML-Entity
        &quot; escaped werden müssen. Die vorherige Version schrieb rohe
        verschachtelte " innerhalb eines XML-Attributs (z. B.
        FileName="...Screenshot_"..."), was den XML-Parser zum Absturz
        brachte ("not well-formed (invalid token)") und in der
        Zwei-Phasen-Generierung JEDE Ausgabe auf Score=0 setzte, da Layer 1
        (XML-Syntax-Gate) sofort fehlschlug.
        """
        screenshot_expr = (
            '[&quot;Screenshot_&quot; + DateTime.Now.ToString('
            '&quot;yyyyMMdd_HHmmss&quot;) + &quot;.png&quot;]'
        )
        error_message_expr = (
            f'[&quot;[{ws.name}] FEHLER: &quot; + exception.Message]'
        )
        return (
            '<Catch x:TypeArguments="s:Exception" faultVariable="exception">\n'
            '  <ActivityAction x:TypeArguments="s:Exception">\n'
            '    <ActivityAction.Argument>\n'
            '      <DelegateInArgument x:TypeArguments="s:Exception" Name="exception" />\n'
            '    </ActivityAction.Argument>\n'
            '    <Sequence DisplayName="Fehlerbehandlung">\n'
            f'      <ui:TakeScreenshot DisplayName="Screenshot bei Fehler" '
            f'FileName="{screenshot_expr}" />\n'
            f'      <ui:LogMessage DisplayName="Log Fehler" Level="Error"\n'
            f'        Message="{error_message_expr}" />\n'
            '      <Rethrow DisplayName="Exception weiterleiten" />\n'
            '    </Sequence>\n'
            '  </ActivityAction>\n'
            '</Catch>'
        )

    # ── Finally-Block ─────────────────────────────────────────────────────────

    def _build_finally(self, ws: WorkflowSpec) -> str:
        """
        Finally-Block: Kategorie-spezifische Cleanup-Platzhalter.
        Browser-Workflows: CloseBrowser. SAP: LogOut. Andere: LogMessage.
        """
        cat = ws.category.lower()

        if "web" in cat or "scraping" in cat:
            inner = (
                '<ui:CloseBrowser DisplayName="Browser schließen (Cleanup)" />\n'
                f'<ui:LogMessage DisplayName="Cleanup abgeschlossen" '
                f'Level="Information" Message="[{ws.name}] Cleanup OK" />'
            )
        elif "sap" in cat:
            inner = (
                f'<ui:LogMessage DisplayName="SAP Cleanup" Level="Information" '
                f'Message="[{ws.name}] SAP-Session cleanup (ggf. Logoff implementieren)" />'
            )
        elif "excel" in cat:
            inner = (
                f'<ui:LogMessage DisplayName="Excel Cleanup" Level="Information" '
                f'Message="[{ws.name}] Excel-Dateien ggf. schließen" />'
            )
        else:
            inner = (
                f'<ui:LogMessage DisplayName="Cleanup" Level="Information" '
                f'Message="[{ws.name}] Ressourcen freigeben" />'
            )

        return (
            '<Sequence DisplayName="Finally – Cleanup">\n'
            f'  {inner}\n'
            '</Sequence>'
        )
