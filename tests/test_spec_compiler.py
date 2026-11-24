"""Regression tests for the spec -> XAML compiler (no UiPath installation needed).

Each assertion encodes a bug that made UiPath Studio refuse to load generated workflows
(found on 24 Nov 2026 by building the output with the UiPath CLI, see scripts/uipath_build_check.py).
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.generation.spec_compiler import SpecCompiler, xaml_type  # noqa: E402


def _spec(category="general", activities=(), arguments=(), variables=()):
    return {"name": "T", "category": category, "description": "t", "packages": [],
            "arguments": list(arguments), "variables": list(variables),
            "activities": [{"id": f"a{i}", "type": t, "display_name": t, "props": p} for i, (t, p) in enumerate(activities)]}


def test_types_are_xaml_tokens():
    assert xaml_type("System.String") == "x:String"
    assert xaml_type("System.Int32") == "x:Int32"
    assert xaml_type("System.Data.DataTable") == "sd:DataTable"
    assert xaml_type("System.DateTime") == "s:DateTime"
    assert xaml_type("x:Boolean") == "x:Boolean"
    assert xaml_type("Some.Unknown.Type") == "x:Object"


def test_arguments_and_variables_use_loadable_types():
    x = SpecCompiler().compile(_spec(arguments=[{"name": "in_A", "type": "System.String", "direction": "in"}],
                                     variables=[{"name": "n", "type": "System.Int32", "default": "0", "scope": "Main"}]))
    assert 'Type="InArgument(x:String)"' in x          # was InArgument(System.String) -> "type could not be resolved"
    assert 'x:TypeArguments="x:Int32" Name="n"' in x
    assert "System.String)" not in x


def test_no_wf3_vb_settings_block():
    x = SpecCompiler().compile(_spec())
    assert "ImportedNamespaces" not in x               # "Cannot set unknown member ...ImportedNamespaces"
    assert "<mva:VisualBasic.Settings>" in x and "<x:Null />" in x


def test_log_messages_are_valid_vb_strings():
    x = SpecCompiler().compile(_spec(activities=[("LogMessage", {"message": "[T] done"})]))
    assert 'Level="Information"' not in x and 'Level="Info"' in x
    assert 'Message="[T] done"' not in x               # would be compiled as a VB expression
    assert "&quot;[T] done&quot;" in x


def test_screenshot_only_in_ui_workflows():
    assert "TakeScreenshot" not in SpecCompiler().compile(_spec(category="file_operations"))
    assert "TakeScreenshot" in SpecCompiler().compile(_spec(category="web_automation"))
    assert "faultVariable" not in SpecCompiler().compile(_spec())


def test_ui_activities_are_scoped_inside_open_browser():
    x = SpecCompiler().compile(_spec(category="web_automation",
                                     activities=[("OpenBrowser", {}), ("TypeInto", {}), ("Click", {}), ("CloseBrowser", {})],
                                     variables=[{"name": "str_InputText", "type": "System.String", "scope": "Main"}]))
    body = x.split("<ui:OpenBrowser.Body>", 1)[1].split("</ui:OpenBrowser.Body>", 1)[0]
    assert "<ui:TypeInto" in body and "<ui:Click" in body   # UI-DBP-006: no top-level selectors outside scope
    assert "webctrl" in body and "html app" not in body
    assert "<ui:CloseBrowser" not in x and "<ui:CloseTab" in x   # CloseBrowser does not exist in UIAutomation 26.x


def test_assign_infers_type_from_target():
    x = SpecCompiler().compile(_spec(activities=[("Assign", {"to": "[n]", "value": "1 + 1"})],
                                     variables=[{"name": "n", "type": "System.Int32", "scope": "Main"}]))
    assert '<OutArgument x:TypeArguments="x:Int32">[n]</OutArgument>' in x


def test_repair_never_adds_invalid_screenshot():
    """SurgicalRepair used to insert TakeScreenshot FileName=... into every catch block (unknown member, forces UIAutomation)."""
    from src.generation.surgical_repair import SurgicalRepair
    rep = SurgicalRepair()
    plain = SpecCompiler().compile(_spec(category="file_operations", activities=[("CopyFile", {})],
                                         arguments=[{"name": "in_SourcePath", "type": "System.String", "direction": "in"},
                                                    {"name": "in_DestPath", "type": "System.String", "direction": "in"}]))
    fixed, _ = rep._fix_screenshot_in_catch(plain)
    assert "TakeScreenshot" not in fixed
    web = SpecCompiler().compile(_spec(category="web_automation", activities=[("OpenBrowser", {})])).replace(
        '<ui:TakeScreenshot DisplayName="Screenshot bei Fehler" />', "")
    fixed, changed = rep._fix_screenshot_in_catch(web)
    assert changed and "TakeScreenshot" in fixed and "FileName" not in fixed.split("TakeScreenshot", 1)[1].split("/>", 1)[0]


def test_undeclared_names_are_auto_declared():
    """BC30451 'str_Response' / 'currentDate' is not declared - all 5 end-to-end workflows failed this way."""
    x = SpecCompiler().compile(_spec(category="api_integration", activities=[
        ("HttpClient", {}),                                              # template default result [str_Response]
        ("Assign", {"to": "[currentDate]", "value": "DateTime.Now"}),
        ("LogMessage", {"message": '["Antwort: " + str_Response + row_ignored.ToString]'}),
    ]))
    assert 'x:TypeArguments="x:String" Name="str_Response"' in x
    assert 'x:TypeArguments="s:DateTime" Name="currentDate"' in x
    assert '<OutArgument x:TypeArguments="s:DateTime">[currentDate]</OutArgument>' in x
    variables = [l for l in x.splitlines() if "<Variable " in l]
    assert not any('Name="DateTime"' in l or 'Name="exception"' in l for l in variables)   # known names stay undeclared


def test_type_names_are_not_declared_as_variables():
    """'StringSplitOptions.None' was auto-declared as a String variable -> BC30456 'None' is not a member of 'String'."""
    x = SpecCompiler().compile(_spec(activities=[("Assign", {"to": "[parts]", "value": 'txt.Split(New String() {"a"}, StringSplitOptions.None)'})],
                                     variables=[{"name": "txt", "type": "System.String", "scope": "Main"}]))
    names = [l.split('Name="')[1].split('"')[0] for l in x.splitlines() if "<Variable " in l]
    assert "StringSplitOptions" not in names and "parts" in names
    assert 'x:TypeArguments="s:String[]" Name="parts"' in x                  # Split(...) returns String(), not String


def test_no_literal_defaults_for_reference_types():
    """'Literal only supports value types and System.String' (DataTable Default="")."""
    x = SpecCompiler().compile(_spec(variables=[
        {"name": "dt", "type": "System.Data.DataTable", "default": "", "scope": "Main"},
        {"name": "o", "type": "System.Object", "default": "{}", "scope": "Main"},
        {"name": "s", "type": "System.String", "default": "abc", "scope": "Main"}]))
    assert 'Name="dt" />' in x and 'Name="o" />' in x and 'Name="s" Default="abc"' in x


def test_quotes_from_spec_never_break_the_xml():
    """Real case (csv task): Default '"A1:B100"' produced Default=""A1:B100"" -> Studio: 'A1' is an unexpected token."""
    x = SpecCompiler().compile({**_spec(variables=[{"name": "r", "type": "System.String", "default": '"A1:B100"', "scope": "Main"},
                                                   {"name": "q", "type": "System.String", "default": 'say "hi"', "scope": "Main"}],
                                        activities=[("Unbekannt", {})]), "name": 'Report "Ä-1"'})
    ET.fromstring(x.split("?>", 1)[1])                    # well-formed
    assert 'Name="r" Default="A1:B100"' in x and 'Default="say &quot;hi&quot;"' in x
    assert 'x:Class="UiPath.Report1"' in x                # valid identifier


def test_csharp_expressions_are_converted_to_vb():
    """Real case (csv task): 'csvContent.Split(new[] { Environment.NewLine }, ...)' -> Studio BC30203 in a VB project."""
    from src.generation.activity_templates import csharp_to_vb
    assert csharp_to_vb("csvContent.Split(new[] { Environment.NewLine }, StringSplitOptions.None)") == \
        "csvContent.Split(New String() { Environment.NewLine }, StringSplitOptions.None)"
    assert csharp_to_vb("a != null && !b") == "a <> Nothing AndAlso Not b"
    assert csharp_to_vb("x == 1 || y") == "x = 1 OrElse y"
    assert csharp_to_vb("in_Name.Length * 2") == "in_Name.Length * 2"          # plain VB stays untouched


def test_every_toolbox_activity_compiles_to_well_formed_xml():
    from src.generation.activity_templates import activity_types_for_full_toolbox
    for t in activity_types_for_full_toolbox():
        ET.fromstring(SpecCompiler().compile(_spec(category="web_automation", activities=[(t, {})])).split("?>", 1)[1])


def test_object_members_on_string_variables_are_removed():
    """LLM wrote [read_file.Content] / [read_csv].Content on String variables -> Studio BC30456 (24 Nov 2026)."""
    from src.generation.spec_compiler import strip_members_on_strings
    xml = ('<ui:SendMail Body="[read_file.Content]" /><InArgument>[JsonConvert.DeserializeObject([read_csv].Content)]</InArgument>'
           '<x a="[other.Content]" /><x b="[my_read_file.Content]" /><x c="[read_file.ContentLength]" />')
    out = strip_members_on_strings(xml, ["read_file", "read_csv"])
    assert 'Body="[read_file]"' in out
    assert "DeserializeObject(read_csv)" in out
    assert "other.Content" in out                    # unknown / non-string names stay untouched
    assert "my_read_file.Content" in out             # prefix of a longer name must not match
    assert "read_file.ContentLength" in out          # word boundary
