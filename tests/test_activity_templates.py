"""Activity templates against what UiPath itself generates (`uip rpa activities get-default-xaml`, 25 Nov 2026), plus the argument helpers.

Found by feeding the real build errors back: ReadPDFText had a property that does not exist (PDFPath), ConnectToDatabase produced
an activity that does not exist (ui:Connect), ExecuteQuery put its result in a child element UiPath does not know, and file
templates wrapped literal paths in brackets (BC30035). Real-build versions of these checks run when `uip` is installed.
"""
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.generation.activity_templates import _path_expr, render_activity_xml  # noqa: E402
from src.generation.spec_compiler import SpecCompiler  # noqa: E402
from src.generation.spec_schema import SPEC_ACTIVITY_TYPES  # noqa: E402


@pytest.mark.parametrize("value, want", [
    ("[in_FilePath]", "[in_FilePath]"),                    # expression stays
    ("in_FilePath", "[in_FilePath]"),                      # bare identifier = variable
    (r"C:\Downloads\a.txt", r'["C:\Downloads\a.txt"]'),    # literal path -> string literal
    ("report.pdf", '["report.pdf"]'),
    ("https://example.com/a", '["https://example.com/a"]'),
    ('say "hi"', '["say ""hi"""]'),                        # quotes are doubled for VB
])
def test_path_expr(value, want):
    assert _path_expr(value) == want


def test_read_pdf_text_uses_the_property_names_uipath_uses():
    x = render_activity_xml("ReadPDFText", "PDF lesen", {"pdf_path": r"C:\In\a.pdf", "text_out": "[txt]"})
    assert 'FileName="[&quot;C:\\In\\a.pdf&quot;]"' in x and 'Text="[txt]"' in x and 'Range="All"' in x
    assert "PDFPath" not in x
    assert 'FileName="[in_PdfPath]"' in render_activity_xml("ReadPDFText", "x", {})        # default is a variable, not a literal


def test_execute_query_uses_datatable_attribute_and_opens_its_own_connection():
    x = render_activity_xml("ExecuteQuery", "Abfrage", {"connection_string": "Server=s;Database=d;", "query": "SELECT 1", "data_table": "[dt]"})
    assert 'DataTable="[dt]"' in x and 'ProviderName="System.Data.SqlClient"' in x and 'Sql="SELECT 1"' in x
    assert "ExecuteQuery.Result" not in x and "CommandType" not in x
    assert "[&quot;Server=s;Database=d;&quot;]" in x                                       # literal connection string is quoted


def test_connect_to_database_is_gone_from_the_offer_and_falls_back_visibly():
    assert "ConnectToDatabase" not in SPEC_ACTIVITY_TYPES
    assert render_activity_xml("ConnectToDatabase", "x", {}) is None                      # old specs get the visible TODO step, not a broken tag


def test_file_templates_quote_literal_paths_and_texts():
    w = render_activity_xml("WriteTextFile", "w", {"path": r"C:\Out\a.txt", "content": "Hallo"})
    assert 'FileName="[&quot;C:\\Out\\a.txt&quot;]"' in w and 'Text="[Hallo]"' in w          # a bare word is a variable
    w2 = render_activity_xml("WriteTextFile", "w", {"path": "[in_Out]", "content": "Heute ist Montag"})
    assert 'FileName="[in_Out]"' in w2 and 'Text="[&quot;Heute ist Montag&quot;]"' in w2     # real text is quoted
    m = render_activity_xml("MoveFile", "m", {"source": r"C:\a\x.pdf", "destination": "[in_Dest]"})
    assert 'Path="[&quot;C:\\a\\x.pdf&quot;]"' in m and 'Destination="[in_Dest]"' in m
    assert 'Path="[in_SourcePath]"' in render_activity_xml("CopyFile", "c", {})              # defaults unchanged


def _spec(acts, variables=()):
    return {"name": "T", "category": "general_automation", "description": "x", "packages": [], "arguments": [],
            "variables": list(variables), "activities": acts, "activity_order": [a["id"] for a in acts]}


needs_uip = pytest.mark.skipif(not (shutil.which("uip") or shutil.which("uip.cmd")), reason="UiPath CLI (uip) not installed")


@needs_uip
def test_pdf_and_sql_workflows_build_in_uipath():
    """Real build (local only): both workflows failed to load before the template fixes."""
    from src.validation.uipath_build import UipathBuilder
    pdf = _spec([{"id": "r", "type": "ReadPDFText", "props": {"pdf_path": r"C:\In\a.pdf", "text_out": "[text]"}},
                 {"id": "w", "type": "WriteTextFile", "props": {"path": r"C:\Out\a.txt", "content": "[text]"}}],
                [{"name": "text", "type": "System.String"}])
    sql = _spec([{"id": "q", "type": "ExecuteQuery", "props": {"connection_string": "Server=x;Database=y;Trusted_Connection=True;", "query": "SELECT * FROM Kunden", "data_table": "[dt]"}}],
                [{"name": "dt", "type": "System.Data.DataTable"}])
    with UipathBuilder() as b:
        for name, spec in (("pdf", pdf), ("sql", sql)):
            r = b.build(SpecCompiler().compile(spec))
            assert r.ok, (name, r.errors)


# ── VB pitfalls and message text (found by feeding real build errors back, 25 Nov 2026) ─────────────
from src.generation.activity_templates import _vb, csharp_to_vb  # noqa: E402


def test_format_and_split_pitfalls_are_rewritten():
    assert csharp_to_vb('Format(Now, "yyyy-MM-dd")') == 'Now.ToString("yyyy-MM-dd")'          # Format -> UiPath.Core.Format namespace (BC30112)
    assert csharp_to_vb('"C:\a\\" & Format(DateTime.Now, "yyyy") & ".pdf"') == '"C:\a\\" & DateTime.Now.ToString("yyyy") & ".pdf"'
    assert csharp_to_vb('text.Split(",")') == 'text.Split(","c)'                                # String -> Char overload (BC30512)
    assert csharp_to_vb('text.Split(",", StringSplitOptions.None)') == 'text.Split(",", StringSplitOptions.None)'   # real String overload untouched
    assert csharp_to_vb('text.Split(", ")') == 'text.Split(", ")'                               # more than one character: not a Char


@pytest.mark.parametrize("text, want", [
    ("[T] done", '[&quot;[T] done&quot;]'),                                                      # leading [Name] is a prefix, plain text
    ("[Workflow] Start", '[&quot;[Workflow] Start&quot;]'),
    ("Alles fertig", '[&quot;Alles fertig&quot;]'),
    ('["Zeilen: " + n.ToString]', '[&quot;Zeilen: &quot; + n.ToString]'),                       # one group = a whole expression
    ("[x.Count]", "[x.Count]"),
    ("Anzahl: [filesCount]", '[&quot;Anzahl: &quot; &amp; filesCount]'),                         # embedded variable -> concatenation
    ('[WF] Datum: [DateTime.Now.ToString("yyyy-MM-dd")]',
     '[&quot;[WF]&quot; &amp; &quot; Datum: &quot; &amp; DateTime.Now.ToString(&quot;yyyy-MM-dd&quot;)]'),   # BC30034 before: whole text taken as one expression
])
def test_message_text_to_vb(text, want):
    assert _vb(text) == want


def test_queue_templates_use_queue_type_and_typed_variables():
    add = render_activity_xml("AddQueueItem", "q", {"queue_name": "Rechnung"})
    assert 'QueueType="[&quot;Rechnung&quot;]"' in add and "QueueName" not in add
    assert 'QueueType="[in_QueueName]"' in render_activity_xml("GetQueueItem", "q", {})
    x = SpecCompiler().compile(_spec([{"id": "c", "type": "GetRobotCredential", "props": {}}, {"id": "g", "type": "GetQueueItem", "props": {}}]))
    assert 'x:TypeArguments="ss:SecureString" Name="out_Password"' in x                           # was String -> BC30311
    assert 'x:TypeArguments="ui:QueueItem" Name="out_TransactionItem"' in x


def test_activities_that_cannot_build_are_not_offered():
    assert "AttachWindow" not in SPEC_ACTIVITY_TYPES and "GetMailMessages" not in SPEC_ACTIVITY_TYPES
