"""
src/generation/task_parser.py
Turns unstructured task documentation text (e.g., pasted from Word/Taskmanager)
into a minimal WorkflowSpec dict for the two-phase compiler.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List


def _guess_packages(text: str) -> List[str]:
    t = text.lower()
    pkgs = set()
    # Always system
    pkgs.add("UiPath.System.Activities")

    if any(k in t for k in ("excel", ".xlsx", ".xls", "worksheet", "sheet", "readrange", "writerange")):
        pkgs.add("UiPath.Excel.Activities")
    if any(k in t for k in ("sap", "me22n", "me21n", "va01", "fiori", "saplogon", "saptransaction", "nsap")):
        pkgs.add("UiPath.SAP.Activities")
    if any(k in t for k in ("mail", "email", "outlook", "getmailmessages", "sendmail", "attachment", "anhang", "imap")):
        pkgs.add("UiPath.Mail.Activities")
    if any(k in t for k in ("browser", "url", "website", "selector", "click", "typeinto", "navigate", "screenshot", "uix", "attachwindow")):
        pkgs.add("UiPath.UIAutomation.Activities")
    if any(k in t for k in ("queue", "orchestrator", "getqueueitem", "addqueueitem", "credential", "asset", "settransactionstatus")):
        pkgs.add("UiPath.Orchestrator.Activities")
    if any(k in t for k in ("http", "api", "rest", "endpoint", "json", "httpclient")):
        pkgs.add("UiPath.WebAPI.Activities")
    if any(k in t for k in ("sql", "database", "executequery", "connection string", "datenbank", "odbc", "oledb")):
        pkgs.add("UiPath.Database.Activities")
    if any(k in t for k in ("pdf", "readpdf", "extractpdf")):
        pkgs.add("UiPath.PDF.Activities")

    return sorted(pkgs)


def parse_task_text_to_spec(text: str, name: str = "GeneratedProcess") -> Dict[str, Any]:
    """
    Very lightweight parser:
    - Extracts category hints and needed packages
    - Creates a minimal ordered set of activities to seed compilation
    """
    raw = text or ""
    t = raw.strip()
    cat = "general_automation"
    tl = t.lower()
    if "sap" in tl:
        cat = "sap_automation"
    elif "excel" in tl or ".xlsx" in tl:
        cat = "excel_automation"
    elif "mail" in tl or "email" in tl or "outlook" in tl:
        cat = "email_processing"
    elif "browser" in tl or "url" in tl or "website" in tl:
        cat = "web_automation"
    elif any(k in tl for k in ("sql", "datenbank", "database", "oracle", "postgres")):
        cat = "database_operations"
    elif any(k in tl for k in ("pdf", ".pdf", "rechnung scannen")):
        cat = "pdf_processing"
    elif any(
        k in tl
        for k in (
            "datei kopieren",
            "ordner",
            "verschieben",
            "löschen",
            "copy file",
            "move file",
            "zip",
            "entpacken",
        )
    ):
        cat = "file_operations"
    elif any(k in tl for k in ("api", "rest ", " http", "endpoint", "json")):
        cat = "api_integration"
    elif any(k in tl for k in ("queue", "orchestrator", "warteschlange", "reframework")):
        cat = "orchestrator_queue"

    # Common arguments inferred from patterns like "Input:" / "Output:" lines
    args = []
    for m in re.finditer(r"(?im)^(input|eingabe)\s*:\s*(.+)$", t):
        args.append({"name": "in_Input", "type": "System.String", "direction": "in", "description": m.group(2).strip()})
        break
    for m in re.finditer(r"(?im)^(output|ausgabe)\s*:\s*(.+)$", t):
        args.append({"name": "out_Output", "type": "System.String", "direction": "out", "description": m.group(2).strip()})
        break

    # Minimal variable set
    vars_ = [
        {"name": "logPrefix", "type": "System.String", "default": name, "scope": "Main"},
        {"name": "str_InputText", "type": "System.String", "default": "", "scope": "Main"},
    ]

    # Seed activities: start/end logs + domain-typical steps (UI: Click/TypeInto/…)
    type_text = "[in_Input]" if any(a.get("name") == "in_Input" for a in args) else "[str_InputText]"

    acts = [
        {
            "id": "log_start",
            "type": "LogMessage",
            "display_name": "Log Start",
            "props": {"message": f"[{name}] Start"},
        },
    ]
    if cat == "excel_automation":
        acts += [{"id": "excel_read", "type": "ReadRange", "display_name": "Read Excel", "props": {}}]
    if cat == "sap_automation":
        acts += [{"id": "sap_tx", "type": "SAPTransaction", "display_name": "SAP Transaction", "props": {"selector_kind": "sap"}}]
    if cat == "web_automation":
        # Typischer Web-Flow: Browser → navigieren → klicken → eingeben → Text lesen → schließen
        acts += [
            {"id": "open_br", "type": "OpenBrowser", "display_name": "Open Browser", "props": {"url": "https://example.com"}},
            {"id": "nav", "type": "NavigateTo", "display_name": "Navigate", "props": {"url": "https://example.com"}},
            {"id": "clk1", "type": "Click", "display_name": "Click UI element", "props": {}},
            {"id": "typ1", "type": "TypeInto", "display_name": "Type into field", "props": {"text": type_text}},
            {"id": "gt1", "type": "GetText", "display_name": "Get text from UI", "props": {}},
            {"id": "cls", "type": "CloseBrowser", "display_name": "Close Browser", "props": {}},
        ]
    if cat == "email_processing":
        acts += [{"id": "mail_get", "type": "GetMailMessages", "display_name": "Get Mails", "props": {}}]
    if cat == "database_operations":
        acts += [{"id": "db_q", "type": "ExecuteQuery", "display_name": "Run SQL", "props": {}}]
    if cat == "pdf_processing":
        acts += [{"id": "pdf_read", "type": "ReadPDFText", "display_name": "Read PDF", "props": {}}]
    if cat == "file_operations":
        acts += [
            {"id": "f_copy", "type": "CopyFile", "display_name": "Copy file", "props": {}},
            {"id": "f_read", "type": "ReadTextFile", "display_name": "Read text file", "props": {}},
        ]
    if cat == "api_integration":
        acts += [{"id": "http1", "type": "HttpClient", "display_name": "HTTP Request", "props": {}}]
    if cat == "orchestrator_queue":
        acts += [
            {"id": "gq", "type": "GetQueueItem", "display_name": "Get queue item", "props": {}},
            {"id": "sq", "type": "SetTransactionStatus", "display_name": "Set transaction status", "props": {}},
        ]
    acts += [
        {
            "id": "log_end",
            "type": "LogMessage",
            "display_name": "Log End",
            "props": {"message": f"[{name}] Abgeschlossen"},
        },
    ]

    return {
        "name": name,
        "category": cat,
        "description": t[:1000],
        "packages": _guess_packages(t),
        "arguments": args,
        "variables": vars_,
        "activities": acts,
        "activity_order": [a["id"] for a in acts],
    }

