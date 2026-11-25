"""JSON schema for the phase-1 workflow spec, passed to Ollama as `format` (constrained decoding).

With a schema the model can only emit activity types and variable types the compiler really implements, so it can no
longer invent a type that would become a '[TODO: Implementieren]' step. `tests/test_spec_schema.py` keeps the type list
and the compiler in sync.
"""
from __future__ import annotations

from typing import Any, Dict

# Activity types the spec compiler renders (activity_templates.render_activity_xml + If/ForEach in spec_compiler).
SPEC_ACTIVITY_TYPES = (
    "LogMessage", "Assign", "If", "ForEach",
    "Click", "TypeInto", "GetText", "WaitForElement", "OpenBrowser", "CloseBrowser", "NavigateTo", "TakeScreenshot",
    "ReadRange", "WriteRange", "ExcelApplicationScope", "ForEachRow",
    "SendMail",
    "ExecuteQuery",
    "HttpClient",
    "ReadTextFile", "WriteTextFile", "CopyFile", "MoveFile",
    "AddQueueItem", "GetQueueItem", "SetTransactionStatus", "GetRobotCredential",
    "ReadPDFText", "SAPTransaction", "OpenApplication",
)

SPEC_VARIABLE_TYPES = (
    "System.String", "System.Int32", "System.Int64", "System.Boolean", "System.Double", "System.Decimal",
    "System.Object", "System.DateTime", "System.Data.DataTable", "System.String[]",
)

_PROP_VALUE = {"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "boolean"},
                         {"type": "array", "items": {"type": "string"}}]}

SPEC_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "category": {"type": "string"},
        "description": {"type": "string"},
        "packages": {"type": "array", "items": {"type": "string"}},
        "arguments": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "type": {"type": "string", "enum": list(SPEC_VARIABLE_TYPES)},
                           "direction": {"type": "string", "enum": ["in", "out", "io"]}, "description": {"type": "string"}},
            "required": ["name", "type", "direction"]}},
        "variables": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "type": {"type": "string", "enum": list(SPEC_VARIABLE_TYPES)},
                           "default": {"type": "string"}, "scope": {"type": "string"}, "description": {"type": "string"}},
            "required": ["name", "type"]}},
        "activities": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "type": {"type": "string", "enum": list(SPEC_ACTIVITY_TYPES)},
                           "display_name": {"type": "string"},
                           "props": {"type": "object", "additionalProperties": _PROP_VALUE}},
            "required": ["id", "type", "props"]}},
        "activity_order": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["name", "category", "description", "activities", "activity_order"],
}
