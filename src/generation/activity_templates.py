"""
Minimal XAML snippets for common UiPath activities (classic ui: + modern uix: where typical).
Selectors are placeholders — replace in Studio with real selectors from Recorder.
"""

from __future__ import annotations

import html
from typing import Any, Dict, Optional


def _a(s: str) -> str:
    return html.escape(s, quote=True)


def csharp_to_vb(expr: str) -> str:
    """The generated projects use VB expressions; models often answer in C#. Convert the common forms
    (array initialisers, `new T(...)`, `null`, `&&`/`||`, `!x`, `==`/`!=`, `$"..."`-free). Anything else is left unchanged."""
    import re as _re
    s = str(expr)
    s = _re.sub(r"new\s*\[\s*\]\s*\{", "New String() {", s)                       # new[] { a } -> New String() {a}
    s = _re.sub(r"new\s+(\w+)\s*\[\s*\]\s*\{", lambda m: f"New {m.group(1).capitalize() if m.group(1).islower() else m.group(1)}() {{", s)
    s = _re.sub(r"\bnew\s+(?=[A-Za-z_][\w.]*\s*\()", "New ", s)                  # new Foo(..) -> New Foo(..)
    s = _re.sub(r"\bnull\b", "Nothing", s)
    s = s.replace("&&", " AndAlso ").replace("||", " OrElse ")
    s = _re.sub(r"(?<![=!<>])==(?!=)", " = ", s).replace("!=", " <> ")
    s = _re.sub(r"!(?=[A-Za-z_(])", "Not ", s)
    # VB pitfalls seen in real builds (25 Nov 2026): Format(...) resolves to the UiPath.Core.Format namespace (BC30112);
    # String.Split("x") picks the Char() overload (BC30512 String -> Char).
    s = _re.sub(r'\bFormat\(\s*([A-Za-z_][\w.]*)\s*,\s*("[^"]*")\s*\)', r"\1.ToString(\2)", s)
    s = _re.sub(r'\.Split\(\s*"([^"])"\s*\)', r'.Split("\1"c)', s)
    return _re.sub(r"\s{2,}", " ", s).strip()


def _expr(s: str) -> str:
    """Wrap a VB expression in [ ] unless it already is one (Studio treats [..] as an expression)."""
    s = str(s).strip()
    return s if s.startswith("[") and s.endswith("]") else f"[{s}]"


def _vb(text: str) -> str:
    """Log/message text -> escaped VB string expression.
    * `[expr]` as a whole (one bracket group, e.g. `["Done " + x.ToString]`) is an expression and kept;
    * text with embedded groups becomes a concatenation: `[Workflow] Count: [n] on [DateTime.Now.ToString("d")]` ->
      `"[Workflow] Count: " & n & " on " & DateTime.Now.ToString("d")`. A leading plain `[Name]` is the workflow-name prefix
      and stays literal; later `[identifier]` and any `[a.b(c)]` are expressions. Before, a text that merely began with `[`
      and ended with `]` was taken for one expression (BC30034/BC30198);
    * anything else is a string literal."""
    import re as _re
    s = str(text)
    if _re.fullmatch(r"\[[^\[\]]*\]", s):
        inner = s[1:-1]
        if inner and ('"' in inner or "+" in inner or "." in inner and " " not in inner):
            return _a(csharp_to_vb(s[1:-1]).join("[]"))
    parts = _re.split(r"(\[[^\[\]]+\])", s)
    if len(parts) > 1:
        out, has_expr, first = [], False, True                       # re.split yields '' before a leading group
        for part in parts:
            if part == "":
                continue
            k, first = (0 if first else 1), False                   # k == 0: the first non-empty part (workflow-name prefix)
            m = _re.fullmatch(r"\[([^\[\]]+)\]", part)
            chain = m and _re.fullmatch(r"[A-Za-z_]\w*(?:\.\w+|\([^()]*\))*", m.group(1).strip())
            if chain and not (k == 0 and _re.fullmatch(r"[A-Za-z_]\w*", m.group(1).strip())):
                out.append(csharp_to_vb(m.group(1).strip()))
                has_expr = True
            else:
                out.append('"' + part.replace('"', '""') + '"')
        if has_expr:
            return _a("[" + " & ".join(out) + "]")
    return _a("[" + '"' + s.replace('"', '""') + '"' + "]")


def assign_value_to_vb(value: str, string_target: bool = False) -> str:
    """Make an LLM-written Assign value a valid VB expression.
    * `[invoiceNumber] - [currentDate]` (several [var] groups in text) is a template -> `invoiceNumber & " - " & currentDate`
    * `C:\\Downloads`, `https://...`, `report.txt` for a String target is a literal, not code -> quoted
    Anything else is passed on unchanged (C# -> VB conversion happens in the caller)."""
    import re as _re
    s = str(value).strip()
    if not s:
        return '""'
    groups = _re.findall(r"\[([A-Za-z_]\w*)\]", s)
    if len(groups) >= 2 or (groups and not (s.startswith("[") and s.endswith("]") and s.count("[") == 1)):
        if '"' not in s and "(" not in s:                       # plain template text, not an expression
            parts = _re.split(r"(\[[A-Za-z_]\w*\])", s)
            out = [p[1:-1] if _re.fullmatch(r"\[[A-Za-z_]\w*\]", p) else '"' + p.replace('"', '""') + '"' for p in parts if p != ""]
            return " & ".join(out)
    if string_target and not s.startswith("["):
        if _re.match(r"^[A-Za-z]:[\\/]", s) or _re.match(r"^(https?|ftp)://", s) or _re.fullmatch(r"[\w .-]+\.(txt|csv|xlsx?|pdf|json|xml|log)", s):
            return '"' + s.replace('"', '""') + '"'
    return s


def _path_expr(s: str) -> str:
    """File/folder argument: [expression] stays, a bare identifier is a variable, anything else (C:\\dir\\a.txt, report.pdf,
    https://...) is a literal and gets quoted. Before this, `[C:\\dir\\a.txt]` reached the compiler (BC30035 Syntax error)."""
    import re as _re
    s = str(s).strip()
    if s.startswith("[") and s.endswith("]"):
        return s
    if _re.fullmatch(r"[A-Za-z_]\w*", s):
        return f"[{s}]"
    return '["' + s.replace('"', '""') + '"]'


def _text_expr(s: str) -> str:
    """Text argument (what gets written/sent): [expression] stays, a bare identifier is a variable, other text is a string literal."""
    return _path_expr(s)


def _vb_or_expr(s: str) -> str:
    """Keep [expressions]; turn plain literals (URL, sheet name, address) into a quoted VB string."""
    s = str(s)
    return s if s.startswith("[") and s.endswith("]") else '["' + s.replace('"', '""') + '"]'


def _sel_placeholder(kind: str = "web") -> str:
    if kind == "sap":
        return "&lt;wnd app='saplogon.exe' /&gt;"
    if kind == "webctrl":                       # partial selector inside an OpenBrowser/AttachBrowser scope
        return "&lt;webctrl tag='INPUT' /&gt;"
    return "&lt;html app='chrome.exe' title='*' /&gt;"


def render_activity_xml(activity_type: str, display_name: Optional[str], props: Dict[str, Any]) -> Optional[str]:
    """
    Returns an XML fragment for one activity, or None → caller uses LogMessage fallback.
    """
    t = (activity_type or "").strip()
    dn = display_name or t
    timeout = str(int(props.get("timeout_ms") or 30000))
    sel = props.get("selector")
    sel_s = _a(sel) if sel else _sel_placeholder(str(props.get("selector_kind") or "web"))

    if t in ("Click", "NClick"):
        return f"""      <ui:Click ClickType="CLICK_SINGLE" DisplayName="{_a(dn)}" KeyModifiers="None" MouseButton="BTN_LEFT">
        <ui:Click.Target>
          <ui:Target Selector="{sel_s}" TimeoutMS="{timeout}" />
        </ui:Click.Target>
      </ui:Click>"""

    if t in ("TypeInto", "NTypeInto"):
        text_expr = _expr(props.get("text") or "str_InputText")
        return f"""      <ui:TypeInto ClickBeforeTyping="True" DisplayName="{_a(dn)}" EmptyField="True" Text="{_a(text_expr)}">
        <ui:TypeInto.Target>
          <ui:Target Selector="{sel_s}" TimeoutMS="{timeout}" />
        </ui:TypeInto.Target>
      </ui:TypeInto>"""

    if t in ("GetText", "NGetText", "GetValue"):
        out = props.get("output") or props.get("value") or "[str_Text]"
        return f"""      <ui:GetValue DisplayName="{_a(dn)}">
        <ui:GetValue.Value>
          <OutArgument x:TypeArguments="x:String">{_a(_expr(out))}</OutArgument>
        </ui:GetValue.Value>
        <ui:GetValue.Target>
          <ui:Target Selector="{sel_s}" TimeoutMS="{timeout}" />
        </ui:GetValue.Target>
      </ui:GetValue>"""

    if t in ("WaitForElement", "WaitUiElementAppear"):
        return f"""      <ui:WaitUiElementAppear DisplayName="{_a(dn)}">
        <ui:WaitUiElementAppear.Target>
          <ui:Target Selector="{sel_s}" TimeoutMS="{timeout}" />
        </ui:WaitUiElementAppear.Target>
      </ui:WaitUiElementAppear>"""

    if t == "OpenBrowser":
        url = props.get("url") or "https://example.com"
        browser = props.get("browser_type") or "Chrome"
        return f"""      <ui:OpenBrowser BrowserType="{browser}" DisplayName="{_a(dn)}" Url="{_a(_vb_or_expr(url))}">
        <ui:OpenBrowser.Body>
          <ActivityAction x:TypeArguments="x:Object">
            <ActivityAction.Argument>
              <DelegateInArgument x:TypeArguments="x:Object" Name="ContextTarget" />
            </ActivityAction.Argument>
            <Sequence DisplayName="Do" />
          </ActivityAction>
        </ui:OpenBrowser.Body>
      </ui:OpenBrowser>"""

    if t == "NavigateTo":
        url = props.get("url") or "https://example.com"
        return f"""      <ui:NavigateTo DisplayName="{_a(dn)}" Url="{_a(_vb_or_expr(url))}" />"""

    if t in ("CloseBrowser", "CloseTab"):
        return f"""      <ui:CloseTab DisplayName="{_a(dn)}" />"""

    if t in ("LogMessage",):
        msg = str(props.get("message") or dn)
        level = {"information": "Info", "warning": "Warn"}.get(str(props.get("level") or "Info").lower(), str(props.get("level") or "Info"))
        return f"""      <ui:LogMessage DisplayName="{_a(dn)}" Level="{_a(level)}" Message="{_vb(msg)}" />"""

    if t == "Assign":
        to = _expr(props.get("to") or "[str_Result]")
        typ = props.get("type") or "x:Object"
        value = _expr(csharp_to_vb(assign_value_to_vb(props.get("value") or '""', string_target=(typ == "x:String"))))
        return f"""      <Assign DisplayName="{_a(dn)}">
        <Assign.To>
          <OutArgument x:TypeArguments="{_a(typ)}">{_a(to)}</OutArgument>
        </Assign.To>
        <Assign.Value>
          <InArgument x:TypeArguments="{_a(typ)}">{_a(value)}</InArgument>
        </Assign.Value>
      </Assign>"""

    # ── Excel (UiPath.Excel.Activities, ui:) ─────────────────────────────────
    if t == "ReadRange":
        path = props.get("workbook_path") or "[in_ExcelFilePath]"
        sheet = props.get("sheet_name") or "Sheet1"
        dt = props.get("data_table") or "[io_DataTable]"
        return f"""      <ui:ReadRange DisplayName="{_a(dn)}" WorkbookPath="{_a(_expr(path))}" SheetName="{_a(_vb_or_expr(sheet))}" Range="{_a(_vb_or_expr(props.get('range') or ''))}" AddHeaders="True" DataTable="{_a(_expr(dt))}" />"""

    if t == "WriteRange":
        path = props.get("workbook_path") or "[in_ExcelFilePath]"
        sheet = props.get("sheet_name") or "Sheet1"
        dt = props.get("data_table") or "[io_DataTable]"
        rng = props.get("range") or "A1"
        return f"""      <ui:WriteRange DisplayName="{_a(dn)}" WorkbookPath="{_a(_expr(path))}" SheetName="{_a(_vb_or_expr(sheet))}" StartingCell="{_a(_vb_or_expr(rng))}" DataTable="{_a(_expr(dt))}" AddHeaders="True" />"""

    if t == "ExcelApplicationScope":
        path = props.get("workbook_path") or "[in_ExcelFilePath]"
        return f"""      <ui:ExcelApplicationScope DisplayName="{_a(dn)}" WorkbookPath="{_a(_expr(path))}">
        <ui:ExcelApplicationScope.Body>
          <ActivityAction x:TypeArguments="ui:WorkbookApplication">
            <ActivityAction.Argument>
              <DelegateInArgument x:TypeArguments="ui:WorkbookApplication" Name="ExcelWorkbookScope" />
            </ActivityAction.Argument>
            <ui:LogMessage DisplayName="Inside Excel scope" Level="Info" Message="{_vb(dn + ': Excel geöffnet')}" />
          </ActivityAction>
        </ui:ExcelApplicationScope.Body>
      </ui:ExcelApplicationScope>"""

    if t == "ForEachRow":
        dt = props.get("data_table") or "[io_DataTable]"
        return f"""      <ui:ForEachRow DisplayName="{_a(dn)}" DataTable="{_a(_expr(dt))}">
        <ui:ForEachRow.Body>
          <ActivityAction x:TypeArguments="sd:DataRow">
            <ActivityAction.Argument>
              <DelegateInArgument x:TypeArguments="sd:DataRow" Name="row" />
            </ActivityAction.Argument>
            <ui:LogMessage DisplayName="Row iteration" Level="Info" Message="{_vb('Zeile verarbeiten')}" />
          </ActivityAction>
        </ui:ForEachRow.Body>
      </ui:ForEachRow>"""

    # ── Mail ─────────────────────────────────────────────────────────────────
    if t == "SendMail":
        to_addr = props.get("to") or "user@example.com"
        subj = props.get("subject") or "[str_Subject]"
        body = props.get("body") or "[str_Body]"
        server = props.get("server") or "smtp.example.com"
        port = str(int(props.get("port") or 587))
        return f"""      <ui:SendMail DisplayName="{_a(dn)}" To="{_a(_vb_or_expr(to_addr))}" Subject="{_a(_vb_or_expr(subj))}" Body="{_a(_vb_or_expr(body))}" Server="{_a(_vb_or_expr(server))}" Port="{port}" EnableSSL="True" SecureConnection="Auto" IsBodyHtml="False" />"""

    # ── WebAPI ───────────────────────────────────────────────────────────────
    if t in ("HttpClient", "HTTPRequest"):
        url = props.get("url") or props.get("endpoint") or "https://api.example.com/data"
        method = str(props.get("method") or "GET").upper()
        result = props.get("result") or "[str_Response]"
        return f"""      <ui:HttpClient DisplayName="{_a(dn)}" EndPoint="{_a(_vb_or_expr(url))}" Method="{method}" AcceptFormat="JSON" AuthenticationType="None" TimeoutMS="{timeout}" Result="{_a(_expr(result))}" />"""

    # ── Orchestrator ───────────────────────────────────────────────────────────
    if t == "AddQueueItem":
        # UiPath default XAML: QueueType (not QueueName), Priority; the old template used QueueName -> "Cannot set unknown member"
        qn = props.get("queue_name") or props.get("queue") or "[in_QueueName]"
        return f"""      <ui:AddQueueItem DisplayName="{_a(dn)}" QueueType="{_a(_vb_or_expr(qn))}" Priority="Normal" />"""

    if t == "GetQueueItem":
        qn = props.get("queue_name") or props.get("queue") or "[in_QueueName]"
        out_item = props.get("transaction_item") or "[out_TransactionItem]"
        return f"""      <ui:GetQueueItem DisplayName="{_a(dn)}" QueueType="{_a(_vb_or_expr(qn))}" TransactionItem="{_a(_expr(out_item))}" />"""

    if t == "SetTransactionStatus":
        item = props.get("transaction_item") or "[io_TransactionItem]"
        return f"""      <ui:SetTransactionStatus TransactionItem="{item}" Status="Successful" DisplayName="{_a(dn)}" />"""

    if t in ("GetRobotCredential", "GetCredential"):
        asset = props.get("asset_name") or "[in_AssetName]"
        return f"""      <ui:GetRobotCredential DisplayName="{_a(dn)}" AssetName="{asset}" Username="[out_Username]" Password="[out_Password]" TimeoutMS="{timeout}" />"""

    # ── Files (System / UiPath.System.Activities) ─────────────────────────────
    if t == "ReadTextFile":
        p = props.get("path") or "[in_FilePath]"
        content = props.get("content") or "[str_FileContent]"
        return f"""      <ui:ReadTextFile DisplayName="{_a(dn)}" FileName="{_a(_path_expr(p))}" Content="{_a(_expr(content))}" />"""

    if t == "WriteTextFile":
        p = props.get("path") or "[in_FilePath]"
        content = props.get("content") or "[str_FileContent]"
        return f"""      <ui:WriteTextFile DisplayName="{_a(dn)}" FileName="{_a(_path_expr(p))}" Text="{_a(_text_expr(content))}" />"""

    if t == "CopyFile":
        src = props.get("source") or "[in_SourcePath]"
        dst = props.get("destination") or "[in_DestPath]"
        return f"""      <ui:CopyFile DisplayName="{_a(dn)}" Path="{_a(_path_expr(src))}" Destination="{_a(_path_expr(dst))}" />"""

    if t == "MoveFile":
        src = props.get("source") or "[in_SourcePath]"
        dst = props.get("destination") or "[in_DestPath]"
        return f"""      <ui:MoveFile DisplayName="{_a(dn)}" Path="{_a(_path_expr(src))}" Destination="{_a(_path_expr(dst))}" />"""

    if t in ("DeleteFile", "Delete"):
        p = props.get("path") or "[in_FilePath]"
        return f"""      <ui:Delete DisplayName="{_a(dn)}" Path="{_a(_path_expr(p))}" />"""

    # ── PDF ───────────────────────────────────────────────────────────────────
    if t == "ReadPDFText":
        # Property names taken from `uip rpa activities get-default-xaml UiPath.PDF.Activities.ReadPDFText` (25 Nov 2026):
        # the old template used PDFPath, which does not exist ("Cannot set unknown member").
        pdf = props.get("pdf_path") or props.get("path") or props.get("file") or "[in_PdfPath]"
        text_out = props.get("text_out") or props.get("text") or props.get("output") or "[out_PdfText]"
        return f"""      <ui:ReadPDFText DisplayName="{_a(dn)}" FileName="{_a(_path_expr(pdf))}" Text="{_a(_expr(text_out))}" Range="All" />"""

    if t == "ExtractPDFPage":
        pdf = props.get("pdf_path") or "[in_PdfPath]"
        out_pdf = props.get("output_path") or "[out_PagePdfPath]"
        page = props.get("page_number") or "1"
        return f"""      <ui:ExtractPDFPage DisplayName="{_a(dn)}" InputPDFPath="{pdf}" OutputPDFPath="{out_pdf}" PageNumber="{page}" />"""

    # ── Database ────────────────────────────────────────────────────────────────
    if t in ("ExecuteQuery", "RunQuery"):
        # Attributes as in UiPath's default XAML for UiPath.Database.Activities.ExecuteQuery (ContinueOnError, DataTable, Sql, TimeoutMS);
        # the activity opens its own connection from ConnectionString + ProviderName.
        conn = props.get("connection_string") or "[in_ConnectionString]"
        sql = props.get("query") or props.get("sql") or "SELECT 1"
        dt = props.get("data_table") or props.get("result") or "[dt_QueryResult]"
        return f"""      <ui:ExecuteQuery ContinueOnError="False" DisplayName="{_a(dn)}" ConnectionString="{_a(_vb_or_expr(conn))}" ProviderName="System.Data.SqlClient" Sql="{_a(sql)}" DataTable="{_a(_expr(dt))}" TimeoutMS="{timeout}" />"""

    # ── SAP (modern uix — wie Studio für SAP GUI) ────────────────────────────
    if t in ("SAPTransaction", "NSAPCallTransaction"):
        tx = props.get("transaction") or props.get("tcode") or "VA01"
        return f"""      <uix:NSAPCallTransaction DisplayName="{_a(dn)}" Prefix="/n" Transaction="{_a(tx)}" Version="V4" />"""

    if t in ("OpenApplication",):
        exe = props.get("file_path") or props.get("path") or "notepad.exe"
        return f"""      <ui:OpenApplication DisplayName="{_a(dn)}" FileName="{_a(exe)}" />"""

    if t == "TakeScreenshot":
        return f"""      <ui:TakeScreenshot DisplayName="{_a(dn)}" />"""

    return None


def activity_types_for_web_flow() -> list:
    return ["OpenBrowser", "NavigateTo", "Click", "TypeInto", "GetText", "CloseBrowser"]


def activity_types_for_excel_flow() -> list:
    return ["ExcelApplicationScope", "ReadRange", "WriteRange", "ForEachRow"]


def activity_types_for_full_toolbox() -> list:
    """All activity type keys supported by render_activity_xml (for specs / docs)."""
    return sorted(
        set(
            activity_types_for_web_flow()
            + activity_types_for_excel_flow()
            + [
                "LogMessage",
                "WaitForElement",
                "SendMail",
                "HttpClient",
                "HTTPRequest",
                "AddQueueItem",
                "GetQueueItem",
                "SetTransactionStatus",
                "GetRobotCredential",
                "ReadTextFile",
                "WriteTextFile",
                "CopyFile",
                "MoveFile",
                "DeleteFile",
                "ReadPDFText",
                "ExtractPDFPage",
                "ExecuteQuery",
                "SAPTransaction",
                "OpenApplication",
                "TakeScreenshot",
            ]
        )
    )
