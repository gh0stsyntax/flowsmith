"""
Minimal XAML snippets for common UiPath activities (classic ui: + modern uix: where typical).
Selectors are placeholders — replace in Studio with real selectors from Recorder.
"""

from __future__ import annotations

import html
from typing import Any, Dict, Optional


def _a(s: str) -> str:
    return html.escape(s, quote=True)


def _sel_placeholder(kind: str = "web") -> str:
    if kind == "sap":
        return "&lt;wnd app='saplogon.exe' /&gt;"
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
        return f"""      <ui:Click ClickType="CLICK_SINGLE" DisplayName="{_a(dn)}" MouseButton="BTN_LEFT" TimeoutMS="{timeout}">
        <ui:Click.Target>
          <ui:Target Selector="{sel_s}" />
        </ui:Click.Target>
      </ui:Click>"""

    if t in ("TypeInto", "NTypeInto"):
        text_expr = props.get("text") or "[str_InputText]"
        return f"""      <ui:TypeInto ClickBeforeMode="Single" DisplayName="{_a(dn)}" EmptyFieldMode="SingleLine" Text="{_a(text_expr)}" TimeoutMS="{timeout}">
        <ui:TypeInto.Target>
          <ui:Target Selector="{sel_s}" />
        </ui:TypeInto.Target>
      </ui:TypeInto>"""

    if t in ("GetText", "NGetText"):
        return f"""      <ui:GetText DisplayName="{_a(dn)}" TimeoutMS="{timeout}">
        <ui:GetText.Target>
          <ui:Target Selector="{sel_s}" />
        </ui:GetText.Target>
      </ui:GetText>"""

    if t == "WaitForElement":
        return f"""      <ui:WaitForElement DisplayName="{_a(dn)}" TimeoutMS="{timeout}">
        <ui:WaitForElement.Target>
          <ui:Target Selector="{sel_s}" />
        </ui:WaitForElement.Target>
      </ui:WaitForElement>"""

    if t == "OpenBrowser":
        url = props.get("url") or "https://example.com"
        browser = props.get("browser_type") or "Chrome"
        return f"""      <ui:OpenBrowser BrowserType="{browser}" DisplayName="{_a(dn)}" Url="{_a(url)}" />"""

    if t == "NavigateTo":
        url = props.get("url") or "https://example.com"
        return f"""      <ui:NavigateTo DisplayName="{_a(dn)}" Url="{_a(url)}" />"""

    if t == "CloseBrowser":
        return f"""      <ui:CloseBrowser DisplayName="{_a(dn)}" />"""

    if t in ("LogMessage",):
        msg = props.get("message") or f"[{dn}]"
        return f"""      <ui:LogMessage DisplayName="{_a(dn)}" Level="Information" Message="{_a(msg)}" />"""

    # ── Excel (UiPath.Excel.Activities, ui:) ─────────────────────────────────
    if t == "ReadRange":
        path = props.get("workbook_path") or "[in_ExcelFilePath]"
        sheet = props.get("sheet_name") or "Sheet1"
        dt = props.get("data_table") or "[io_DataTable]"
        return f"""      <ui:ReadRange DisplayName="{_a(dn)}" WorkbookPath="{_a(path)}" SheetName="{_a(sheet)}" AddHeaders="True">
        <ui:ReadRange.Result>
          <OutArgument x:TypeArguments="sd:DataTable">{dt}</OutArgument>
        </ui:ReadRange.Result>
      </ui:ReadRange>"""

    if t == "WriteRange":
        path = props.get("workbook_path") or "[in_ExcelFilePath]"
        sheet = props.get("sheet_name") or "Sheet1"
        dt = props.get("data_table") or "[io_DataTable]"
        rng = props.get("range") or "A1"
        return f"""      <ui:WriteRange DisplayName="{_a(dn)}" WorkbookPath="{_a(path)}" SheetName="{_a(sheet)}" StartingCell="{_a(rng)}" DataTable="{dt}" AddHeaders="False" />"""

    if t == "ExcelApplicationScope":
        path = props.get("workbook_path") or "[in_ExcelFilePath]"
        return f"""      <ui:ExcelApplicationScope DisplayName="{_a(dn)}" WorkbookPath="{_a(path)}">
        <ui:ExcelApplicationScope.Body>
          <ActivityAction>
            <ActivityAction.Argument>
              <DelegateInArgument Name="ExcelWorkbookScope" Type="ui:WorkbookApplication" />
            </ActivityAction.Argument>
            <ui:LogMessage DisplayName="Inside Excel scope" Level="Information" Message="[{_a(dn)}] Excel geöffnet" />
          </ActivityAction>
        </ui:ExcelApplicationScope.Body>
      </ui:ExcelApplicationScope>"""

    if t == "ForEachRow":
        dt = props.get("data_table") or "[io_DataTable]"
        return f"""      <ui:ForEachRow DisplayName="{_a(dn)}" DataTable="{dt}">
        <ui:ForEachRow.Body>
          <ActivityAction>
            <ActivityAction.Argument>
              <DelegateInArgument Name="row" Type="sd:DataRow" />
            </ActivityAction.Argument>
            <ui:LogMessage DisplayName="Row iteration" Level="Information" Message="Zeile verarbeiten" />
          </ActivityAction>
        </ui:ForEachRow.Body>
      </ui:ForEachRow>"""

    # ── Mail ─────────────────────────────────────────────────────────────────
    if t == "GetMailMessages":
        folder = props.get("mail_folder") or "Inbox"
        out_var = props.get("output_messages") or "[list_Mails]"
        return f"""      <ui:GetMailMessages DisplayName="{_a(dn)}" MailFolder="{_a(folder)}" OutputMessages="{out_var}" Top="30" />"""

    if t == "SendMail":
        to_addr = props.get("to") or "user@example.com"
        subj = props.get("subject") or "[str_Subject]"
        body = props.get("body") or "[str_Body]"
        return f"""      <ui:SendMail DisplayName="{_a(dn)}" To="{_a(to_addr)}" Subject="{_a(subj)}" Body="{_a(body)}" />"""

    # ── WebAPI ───────────────────────────────────────────────────────────────
    if t in ("HttpClient", "HTTPRequest"):
        url = props.get("url") or props.get("endpoint") or "https://api.example.com/data"
        return f"""      <ui:HttpClient DisplayName="{_a(dn)}" Endpoint="{_a(url)}" TimeoutMS="{timeout}" ContinueOnError="False" />"""

    # ── Orchestrator ───────────────────────────────────────────────────────────
    if t == "AddQueueItem":
        qn = props.get("queue_name") or "[in_QueueName]"
        return f"""      <ui:AddQueueItem DisplayName="{_a(dn)}" QueueName="{qn}" Reference="{{x:Null}}" />"""

    if t == "GetQueueItem":
        qn = props.get("queue_name") or "[in_QueueName]"
        out_item = props.get("transaction_item") or "[out_TransactionItem]"
        return f"""      <ui:GetQueueItem DisplayName="{_a(dn)}" QueueName="{qn}" TransactionItem="{out_item}" />"""

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
        return f"""      <ui:ReadTextFile DisplayName="{_a(dn)}" FileName="{p}">
        <ui:ReadTextFile.Result>
          <OutArgument x:TypeArguments="x:String">{content}</OutArgument>
        </ui:ReadTextFile.Result>
      </ui:ReadTextFile>"""

    if t == "WriteTextFile":
        p = props.get("path") or "[in_FilePath]"
        content = props.get("content") or "[str_FileContent]"
        return f"""      <ui:WriteTextFile DisplayName="{_a(dn)}" FileName="{p}" Text="{content}" />"""

    if t == "CopyFile":
        src = props.get("source") or "[in_SourcePath]"
        dst = props.get("destination") or "[in_DestPath]"
        return f"""      <ui:CopyFile DisplayName="{_a(dn)}" Path="{src}" Destination="{dst}" />"""

    if t == "MoveFile":
        src = props.get("source") or "[in_SourcePath]"
        dst = props.get("destination") or "[in_DestPath]"
        return f"""      <ui:MoveFile DisplayName="{_a(dn)}" Path="{src}" Destination="{dst}" />"""

    if t == "DeleteFile":
        p = props.get("path") or "[in_FilePath]"
        return f"""      <ui:DeleteFile DisplayName="{_a(dn)}" Path="{p}" />"""

    # ── PDF ───────────────────────────────────────────────────────────────────
    if t == "ReadPDFText":
        pdf = props.get("pdf_path") or "[in_PdfPath]"
        text_out = props.get("text_out") or "[out_PdfText]"
        return f"""      <ui:ReadPDFText DisplayName="{_a(dn)}" PDFPath="{pdf}" Text="{text_out}" />"""

    if t == "ExtractPDFPage":
        pdf = props.get("pdf_path") or "[in_PdfPath]"
        out_pdf = props.get("output_path") or "[out_PagePdfPath]"
        page = props.get("page_number") or "1"
        return f"""      <ui:ExtractPDFPage DisplayName="{_a(dn)}" InputPDFPath="{pdf}" OutputPDFPath="{out_pdf}" PageNumber="{page}" />"""

    # ── Database ────────────────────────────────────────────────────────────────
    if t in ("ExecuteQuery", "RunQuery"):
        conn = props.get("connection_string") or "[in_ConnectionString]"
        sql = props.get("query") or props.get("sql") or "SELECT 1"
        dt = props.get("data_table") or "[dt_QueryResult]"
        return f"""      <ui:ExecuteQuery ContinueOnError="False" DisplayName="{_a(dn)}" ConnectionString="{conn}" Sql="{_a(sql)}" CommandType="Text" TimeoutMS="{timeout}">
        <ui:ExecuteQuery.Result>
          <OutArgument x:TypeArguments="sd:DataTable">{dt}</OutArgument>
        </ui:ExecuteQuery.Result>
      </ui:ExecuteQuery>"""

    if t == "ConnectToDatabase":
        conn = props.get("connection_string") or "[in_ConnectionString]"
        return f"""      <ui:Connect DisplayName="{_a(dn)}" ConnectionString="{conn}" DatabaseConnection="[db_Connection]" ProviderName="System.Data.SqlClient" />"""

    # ── SAP (modern uix — wie Studio für SAP GUI) ────────────────────────────
    if t in ("SAPTransaction", "NSAPCallTransaction"):
        tx = props.get("transaction") or props.get("tcode") or "VA01"
        return f"""      <uix:NSAPCallTransaction DisplayName="{_a(dn)}" Prefix="/n" Transaction="{_a(tx)}" Version="V4" />"""

    if t in ("OpenApplication",):
        exe = props.get("file_path") or props.get("path") or "notepad.exe"
        return f"""      <ui:OpenApplication DisplayName="{_a(dn)}" FileName="{_a(exe)}" />"""

    if t in ("AttachWindow",):
        return f"""      <ui:AttachWindow DisplayName="{_a(dn)}" TimeoutMS="{timeout}">
        <ui:AttachWindow.Target>
          <ui:Target Selector="{sel_s}" />
        </ui:AttachWindow.Target>
      </ui:AttachWindow>"""

    if t == "TakeScreenshot":
        file_name = props.get("file_name") or '[\"Screenshot_\" + DateTime.Now.ToString(\"yyyyMMdd_HHmmss\") + \".png\"]'
        return f"""      <ui:TakeScreenshot DisplayName="{_a(dn)}" FileName="{file_name}" />"""

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
                "GetMailMessages",
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
                "ConnectToDatabase",
                "SAPTransaction",
                "OpenApplication",
                "AttachWindow",
                "TakeScreenshot",
            ]
        )
    )
