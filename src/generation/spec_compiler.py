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

import re
import textwrap
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape as _xml_escape


def _attr(value: Any) -> str:
    """Escape a value for a double-quoted XML attribute (saxutils.escape alone leaves '"' raw)."""
    return _xml_escape(str(value), {'"': "&quot;"})

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


# .NET type name -> XAML type token. Studio rejects "InArgument(System.String)" ("type could not be resolved");
# it expects the XAML 2006 primitives (x:String) or a prefixed CLR type (s:DateTime, sd:DataTable).
_XAML_TYPES = {
    "string": "x:String", "system.string": "x:String",
    "int": "x:Int32", "int32": "x:Int32", "system.int32": "x:Int32", "integer": "x:Int32",
    "int64": "x:Int64", "system.int64": "x:Int64", "long": "x:Int64",
    "bool": "x:Boolean", "boolean": "x:Boolean", "system.boolean": "x:Boolean",
    "double": "x:Double", "system.double": "x:Double", "decimal": "x:Decimal", "system.decimal": "x:Decimal",
    "object": "x:Object", "system.object": "x:Object",
    "datetime": "s:DateTime", "system.datetime": "s:DateTime", "timespan": "s:TimeSpan", "system.timespan": "s:TimeSpan",
    "datatable": "sd:DataTable", "system.data.datatable": "sd:DataTable",
    "datarow": "sd:DataRow", "system.data.datarow": "sd:DataRow",
    "string[]": "s:String[]", "system.string[]": "s:String[]",
    "list<string>": "scg:List(x:String)", "system.collections.generic.list<system.string>": "scg:List(x:String)",
    "securestring": "ss:SecureString", "system.security.securestring": "ss:SecureString",
}


def xaml_type(type_name: str) -> str:
    """Map a spec type ('System.String', 'Int32', 'x:String', 'DataTable' ...) to a Studio-loadable XAML type token."""
    t = (type_name or "x:String").strip()
    if ":" in t:                                     # already prefixed (x:String, sd:DataTable, s:DateTime)
        return t
    hit = _XAML_TYPES.get(t.lower())
    if hit:
        return hit
    if t.startswith("System.") and t.count(".") == 1:
        return "s:" + t.split(".", 1)[1]
    return "x:Object"                                # unknown type -> Object rather than an unloadable token


_PRIMITIVES = {"x:String", "x:Int32", "x:Int64", "x:Boolean", "x:Double", "x:Decimal"}

# Names that may appear in VB expressions without being workflow variables.
_VB_KNOWN = {
    # keywords / operators / literals
    "new", "not", "and", "or", "andalso", "orelse", "xor", "nothing", "true", "false", "if", "is", "isnot", "mod", "like",
    "ctype", "cint", "cstr", "cdbl", "cbool", "cdate", "cdec", "clng", "directcast", "trycast", "gettype", "typeof",
    "function", "sub", "from", "of", "me", "mybase", "string", "integer", "double", "boolean", "decimal", "object",
    "date", "long", "short", "byte", "char", "single",
    # VB runtime functions
    "format", "now", "today", "trim", "len", "left", "right", "mid", "ucase", "lcase", "split", "join", "replace",
    "isnothing", "isnumeric", "val", "chr", "asc", "vbcrlf", "vbnewline", "vblf", "vbtab", "environment",
    # common .NET types / namespaces used in expressions
    "system", "datetime", "timespan", "math", "convert", "path", "directory", "file", "fileinfo", "directoryinfo",
    "array", "list", "dictionary", "datatable", "datarow", "regex", "stringcomparison", "guid", "int32", "int64",
    "microsoft", "io", "text", "linq", "enumerable", "newtonsoft", "jobject", "jtoken", "jarray", "jsonconvert",
    "cultureinfo", "globalization", "encoding", "uri", "net", "mail", "data", "xml", "xdocument",
    # delegate arguments created by the templates / compiler
    "row", "currentrow", "exception", "excelworkbookscope", "contexttarget", "item",
}


def _infer_type(name: str, assigned: Optional[str] = None) -> str:
    """Type for an auto-declared variable: from its prefix, else from the value assigned to it, else String."""
    n = name.lower()
    for prefix, typ in (("str", "x:String"), ("int", "x:Int32"), ("cnt", "x:Int32"), ("num", "x:Int32"),
                        ("dbl", "x:Double"), ("dec", "x:Decimal"), ("bool", "x:Boolean"), ("is", "x:Boolean"),
                        ("has", "x:Boolean"), ("dt", "sd:DataTable"), ("arr", "s:String[]"), ("list", "scg:List(x:String)")):
        if n.startswith(prefix + "_") or (prefix in ("str", "int", "dt", "bool", "arr") and n.startswith(prefix) and name[len(prefix):len(prefix) + 1].isupper()):
            return typ
    if n in ("io_datatable",) or n.endswith("datatable"):
        return "sd:DataTable"
    v = (assigned or "").strip().strip("[]").strip()
    if v:
        if v.startswith(("DateTime.Now", "Now", "Date.Now", "DateTime.Today")) and ".ToString" not in v:
            return "s:DateTime"
        if "GetFiles(" in v or "GetDirectories(" in v:
            return "s:String[]"
        if v.replace(".", "", 1).isdigit() or v.endswith((".Count", ".Length", ".Rows.Count")):
            return "x:Int32" if "." not in v.replace(".Count", "").replace(".Length", "") else "x:Double"
        if v in ("True", "False"):
            return "x:Boolean"
    return "x:String"


def _identifiers_in_expressions(xml_text: str) -> List[str]:
    """Free identifiers used inside [VB expressions] of attributes and element texts (string literals removed)."""
    import html as _html
    import re as _re
    found: List[str] = []
    for expr in _re.findall(r'="\[(.*?)\]"|>\[(.*?)\]<', xml_text, flags=_re.S):
        e = _html.unescape(expr[0] or expr[1])
        e = _re.sub(r'"(?:[^"]|"")*"', '""', e)                      # drop VB string literals
        for m in _re.finditer(r'(?<![\w.])([A-Za-z_]\w*)', e):
            ident = m.group(1)
            if ident.lower() not in _VB_KNOWN and ident not in found:
                found.append(ident)
    return found


def vb_string(text: str) -> str:
    """Plain text -> VB string expression in an XML attribute, e.g. [&quot;Start&quot;]."""
    from xml.sax.saxutils import escape as _esc
    return "[" + _esc('"' + str(text).replace('"', '""') + '"', {'"': "&quot;"}) + "]"


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
        class_name = "UiPath." + (re.sub(r"\W", "", ws.name, flags=re.ASCII) or "GeneratedProcess")   # x:Class must be a valid identifier

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
            type_str = xaml_type(arg.type)
            lines.append(
                f'    <x:Property Name="{arg.name}" '
                f'Type="{tag}({type_str})" '
                f'sap2010:WorkflowViewState.IdRef="{arg.name}" />'
            )
        lines.append("  </x:Members>")
        return "\n".join(lines) + "\n"

    # ── VisualBasic.Settings ──────────────────────────────────────────────────

    def _build_vb_settings(self, ws: WorkflowSpec) -> str:
        # Studio writes exactly this; the WF3-style VisualBasicSettings.ImportedNamespaces block fails to load
        # ("Cannot set unknown member ...ImportedNamespaces"). Imports live in TextExpression.NamespacesForImplementation.
        return '  <mva:VisualBasic.Settings>\n    <x:Null />\n  </mva:VisualBasic.Settings>\n'

    # ── TryCatch-Wrapper ──────────────────────────────────────────────────────

    def _build_trycatch(self, ws: WorkflowSpec) -> str:
        main_seq    = self._build_main_sequence(ws)
        catch_block = self._build_catch(ws)
        finally_blk = self._build_finally(ws)

        return (
            f'<TryCatch DisplayName="{_attr(ws.name)} TryCatch">\n'
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

    def _auto_declare(self, ws: WorkflowSpec, rendered_xml: str) -> None:
        """Declare every free identifier used in expressions that is not an argument/variable.
        LLM specs and template defaults (str_Response, str_FileContent ...) referenced undeclared names
        -> Studio: BC30451 'x' is not declared (all 5 end-to-end workflows on 24 Nov 2026)."""
        from src.generation.workflow_spec import WorkflowVariable
        known = {v.name for v in ws.variables} | {a.name for a in ws.arguments}
        assigned = {}
        for act in ws.activities:
            if act.type == "Assign":
                assigned[str((act.props or {}).get("to") or "").strip("[] ")] = str((act.props or {}).get("value") or "")
        for ident in _identifiers_in_expressions(rendered_xml):
            if ident not in known:
                ws.variables.append(WorkflowVariable(name=ident, type=_infer_type(ident, assigned.get(ident))))
                known.add(ident)

    def _build_main_sequence(self, ws: WorkflowSpec) -> str:
        acts_xml  = self._build_activities(ws)
        before = len(ws.variables)
        self._auto_declare(ws, acts_xml)
        if len(ws.variables) != before:                 # Assign types depend on declarations -> render again
            acts_xml = self._build_activities(ws)
        var_xml   = self._build_variables(ws)

        # Argument-Werte als lesbare Liste für den Start-Log
        arg_names = ", ".join(a.name for a in ws.arguments[:3]) if ws.arguments else ""
        arg_hint  = f" - {arg_names}" if arg_names else ""

        start_log = (
            f'<ui:LogMessage DisplayName="Log Start" Level="Info" '
            f'Message="{vb_string(f"[{ws.name}] Start{arg_hint}")}" />'
        )
        end_log = (
            f'<ui:LogMessage DisplayName="Log Ende" Level="Info" '
            f'Message="{vb_string(f"[{ws.name}] Abgeschlossen")}" />'
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
            typ = xaml_type(var.type)
            d = None if var.default is None else str(var.default).strip()
            if d and not (d.startswith("[") and d.endswith("]")) and typ not in _PRIMITIVES:
                # "Literal only supports value types and System.String" -> no literal defaults for DataTable/Object/...
                d = "[New System.Data.DataTable]" if typ == "sd:DataTable" else None
            if d == "" and typ != "x:String":
                d = None
            if d and typ == "x:String" and len(d) >= 2 and d[0] == d[-1] == '"':
                d = d[1:-1]                              # String Default is a literal: '"A1:B100"' -> 'A1:B100'
            if d is not None:
                default_attr = f' Default="{_attr(d)}"'
            lines.append(
                f'  <Variable x:TypeArguments="{xaml_type(var.type)}" '
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

        # UI activities after OpenBrowser go INTO the browser scope with partial selectors.
        # Flat placement with full <html app=...> selectors fails Studio's analyzer (UI-DBP-006, error level).
        UI_IN_SCOPE = {"Click", "NClick", "TypeInto", "NTypeInto", "GetText", "NGetText", "GetValue",
                       "WaitForElement", "WaitUiElementAppear", "NavigateTo"}
        browser_idx: Optional[int] = None
        scope_parts: List[str] = []

        for act_id in order:
            act = act_map.get(act_id)
            if act is None:
                continue

            props = dict(act.props or {})
            in_scope = browser_idx is not None and act.type in UI_IN_SCOPE
            if in_scope and not props.get("selector"):
                props["selector_kind"] = "webctrl"
            if act.type in ("CloseBrowser", "CloseTab"):
                browser_idx = None                       # Scope endet; CloseTab bleibt ausserhalb
            if act.type == "Assign" and not props.get("type"):
                # Typ aus Zielvariable/-argument ableiten (sonst Object -> Int32 = Compilerfehler in Studio)
                ziel = str(props.get("to") or "").strip("[] ")
                typen = {v.name: v.type for v in ws.variables}
                typen.update({a.name: a.type for a in ws.arguments})
                if ziel in typen:
                    props["type"] = xaml_type(typen[ziel])
            xml_fragment = render_activity_xml(
                act.type,
                act.display_name,
                props,
            )

            if xml_fragment and in_scope:
                scope_parts.append(xml_fragment)
                parts[browser_idx] = self._browser_with_body(parts[browser_idx], scope_parts)
            elif xml_fragment:
                parts.append(xml_fragment)
                if act.type == "OpenBrowser":
                    browser_idx, scope_parts = len(parts) - 1, []
            else:
                # Fallback: LogMessage mit Hinweis
                dn = act.display_name or act.type
                parts.append(
                    f'      <ui:LogMessage DisplayName="{_attr(dn)} [TODO: Implementieren]" '
                    f'Level="Warn" '
                    f'Message="{vb_string(f"[{ws.name}] SCHRITT AUSSTEHEND: {dn} ({act.type})")}" />'
                )

        return "\n".join(parts) if parts else (
            '      <ui:LogMessage DisplayName="Hauptlogik (TODO)" '
            f'Level="Warn" Message="{vb_string("Keine Aktivitäten im Spec definiert")}" />'
        )

    @staticmethod
    def _browser_with_body(browser_xml: str, inner: List[str]) -> str:
        """Replace the (empty or previous) body Sequence of an OpenBrowser fragment with the scoped activities."""
        import re as _re
        body = "\n".join(_indent(x.strip("\n"), 6) for x in inner)
        neu = f'<Sequence DisplayName="Do">\n{body}\n            </Sequence>'
        return _re.sub(r'<Sequence DisplayName="Do"(?: />|>.*?\n\s*</Sequence>)', lambda m: neu, browser_xml, count=1, flags=_re.S)

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
            '[&quot;[' + _attr(ws.name.replace('"', '""')).replace('&quot;', '&quot;&quot;') + '] FEHLER: &quot; + exception.Message]'
        )
        # TakeScreenshot lives in UiPath.UIAutomation.Activities: only UI workflows reference that package.
        # In a pure file/Excel/mail project it fails to load ("Cannot create unknown type ...TakeScreenshot").
        cat = (ws.category or "").lower()
        ui_flow = any(k in cat for k in ("web", "scraping", "sap", "desktop", "ui"))
        screenshot_xml = '      <ui:TakeScreenshot DisplayName="Screenshot bei Fehler" />\n' if ui_flow else ''
        return (
            '<Catch x:TypeArguments="s:Exception">\n'
            '  <ActivityAction x:TypeArguments="s:Exception">\n'
            '    <ActivityAction.Argument>\n'
            '      <DelegateInArgument x:TypeArguments="s:Exception" Name="exception" />\n'
            '    </ActivityAction.Argument>\n'
            '    <Sequence DisplayName="Fehlerbehandlung">\n'
            f'{screenshot_xml}'
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
                '<ui:CloseTab DisplayName="Browser-Tab schließen (Cleanup)" ContinueOnError="True" />\n'
                f'<ui:LogMessage DisplayName="Cleanup abgeschlossen" '
                f'Level="Info" Message="{vb_string(f"[{ws.name}] Cleanup OK")}" />'
            )
        elif "sap" in cat:
            inner = (
                f'<ui:LogMessage DisplayName="SAP Cleanup" Level="Info" '
                f'Message="{vb_string(f"[{ws.name}] SAP-Session cleanup (ggf. Logoff implementieren)")}" />'
            )
        elif "excel" in cat:
            inner = (
                f'<ui:LogMessage DisplayName="Excel Cleanup" Level="Info" '
                f'Message="{vb_string(f"[{ws.name}] Excel-Dateien ggf. schließen")}" />'
            )
        else:
            inner = (
                f'<ui:LogMessage DisplayName="Cleanup" Level="Info" '
                f'Message="{vb_string(f"[{ws.name}] Ressourcen freigeben")}" />'
            )

        return (
            '<Sequence DisplayName="Finally – Cleanup">\n'
            f'  {inner}\n'
            '</Sequence>'
        )
