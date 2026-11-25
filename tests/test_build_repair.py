"""Build-feedback repair: error parsing, the round logic, the model call and the CLI switch (no LLM, no UiPath needed)."""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.generation.build_repair import BUILD_FIX_HINTS, feedback_loop  # noqa: E402
from src.generation.spec_schema import SPEC_SCHEMA  # noqa: E402
from src.validation import uipath_build  # noqa: E402
from src.validation.uipath_build import UipathBuilder, parse_build_errors  # noqa: E402

# A real `uip rpa build` failure (25 Nov 2026), trimmed.
REAL_LOG = '''{
  "Result": "Failure",
  "ErrorCode": "unknown_error",
  "Message": "Build failed: [Main.xaml] BC30451: 'read_csv' is not declared. It may be inaccessible due to its protection level. Variable 'read_csv' is missing. Please use Data Manager to recreate it.\\r\\n",
  "Retry": "RetryWillNotFix"
}
[WARN] [GovernancePolicyService] [ResolvePolicy] Could not tell whether this tenant is governed (notFound)
[ERROR] [Process] [BuildCheck] BC30451: 'read_csv' is not declared. It may be inaccessible due to its protection level. Variable 'read_csv' is missing. Please use Data Manager to recreate it.
[ERROR] [ProjectPackager] [PackProject] Project build failed with error code 4: [Main.xaml] BC30451: 'read_csv' is not declared. It may be inaccessible due to its protection level. Variable 'read_csv' is missing. Please use Data Manager to recreate it.
'''


def test_real_error_log_is_parsed_once_without_noise():
    errors = parse_build_errors(REAL_LOG)
    assert len(errors) == 1                                        # the message appears three times in the log
    assert errors[0].startswith("BC30451: 'read_csv' is not declared")
    assert "GovernancePolicyService" not in " ".join(errors)


def test_several_distinct_errors_are_kept_in_order_and_capped():
    log = "\n".join(f"[ERROR] [Process] [BuildCheck] BC3000{i}: problem number {i}" for i in range(12))
    errors = parse_build_errors(log)
    assert errors[0].startswith("BC30000") and len(errors) == 8     # capped at MAX_ERRORS
    assert parse_build_errors("[ERROR] [Loader] Cannot set unknown member 'Foo'") == ["Cannot set unknown member 'Foo'"]   # non-BC fallback
    assert parse_build_errors("all fine") == []


# ── the round logic ─────────────────────────────────────────────────────────────────────────
class Result:
    def __init__(self, ok, errors=()):
        self.ok, self.errors = ok, list(errors)


def test_success_on_first_build_asks_the_model_nothing():
    asked = []
    out = feedback_loop(lambda x: Result(True), lambda s: "xaml:" + s["v"], lambda *a: asked.append(a), {"v": "0"})
    assert out["ok"] and out["repair_rounds_used"] == 0 and asked == [] and out["xaml"] == "xaml:0"


def test_error_is_fed_back_and_fixed_in_round_one():
    builds, asked = [], []
    def build(xaml):
        builds.append(xaml)
        return Result(xaml == "xaml:fixed", [] if xaml == "xaml:fixed" else ["BC30451: 'x' is not declared"])
    def revise(spec, errors, n):
        asked.append((errors, n)); return {"v": "fixed"}
    out = feedback_loop(build, lambda s: "xaml:" + s["v"], revise, {"v": "broken"})
    assert out["ok"] and out["repair_rounds_used"] == 1 and out["spec"] == {"v": "fixed"}
    assert asked == [(["BC30451: 'x' is not declared"], 1)]
    assert [h["ok"] for h in out["history"]] == [False, True]


def test_never_more_rounds_than_allowed_and_failure_is_reported():
    n = {"revise": 0}
    def revise(spec, errors, round_no):
        n["revise"] += 1; return {"v": spec["v"] + "+"}              # a different spec every time, still broken
    out = feedback_loop(lambda x: Result(False, ["BC30512: still wrong"]), lambda s: s["v"], revise, {"v": "a"}, max_rounds=2)
    assert n["revise"] == 2 and out["repair_rounds_used"] == 2 and out["ok"] is False
    assert out["history"][-1]["errors"] == ["BC30512: still wrong"]
    zero = feedback_loop(lambda x: Result(False, ["e"]), lambda s: s["v"], revise, {"v": "a"}, max_rounds=0)
    assert zero["repair_rounds_used"] == 0 and n["revise"] == 2       # no repair when rounds = 0


def test_unchanged_spec_stops_the_loop_instead_of_repeating_the_failure():
    calls = []
    out = feedback_loop(lambda x: (calls.append(1), Result(False, ["e"]))[1], lambda s: "x", lambda spec, e, n: dict(spec), {"v": 1})
    assert len(calls) == 1 and out["ok"] is False


# ── the model call ──────────────────────────────────────────────────────────────────────────
@pytest.fixture
def gen(monkeypatch):
    import src.generation.two_phase_generator as tp
    g = tp.TwoPhaseGenerator(seed=7)
    seen = {}

    class FakeOllama:
        reply = json.dumps({"name": "Fixed", "category": "c", "description": "d",
                            "activities": [{"id": "a", "type": "LogMessage", "props": {"message": "x"}}], "activity_order": ["a"]})
        def model_exists(self, name): return False
        def generate(self, **kw):
            seen.update(kw)
            if isinstance(self.reply, Exception):
                raise self.reply
            return self.reply

    g._ollama_client = FakeOllama()
    return g, seen


def test_revise_spec_sends_old_spec_real_errors_and_hints_with_the_schema(gen):
    g, seen = gen
    old = {"name": "Old", "category": "c", "description": "d", "activities": [{"id": "a", "type": "LogMessage", "props": {}}], "activity_order": ["a"]}
    revised = g.revise_spec("read a csv", "file_operations", old, ["BC30451: 'read_csv' is not declared"], round_no=2)
    assert revised["name"] == "Fixed"
    assert "BC30451: 'read_csv' is not declared" in seen["prompt"] and '"name": "Old"' in seen["prompt"] and "read a csv" in seen["prompt"]
    assert BUILD_FIX_HINTS.splitlines()[0] in seen["prompt"]
    assert seen["format"] == SPEC_SCHEMA and seen["seed"] == 7 + 100 + 2      # reproducible, and different from the first attempt


def test_unusable_model_answers_keep_the_old_spec(gen):
    g, _ = gen
    old = {"name": "Old", "category": "c", "description": "d", "activities": [], "activity_order": []}
    g._ollama_client.reply = "this is not json"
    assert g.revise_spec("t", "c", old, ["BC1: x"]) == old
    g._ollama_client.reply = json.dumps({"name": "NoActivities"})            # fails the structural check
    assert g.revise_spec("t", "c", old, ["BC1: x"]) == old
    g._ollama_client.reply = RuntimeError("Ollama down")
    assert g.revise_spec("t", "c", old, ["BC1: x"]) == old                    # a failed repair never makes things worse


# ── the builder around `uip` (fake CLI) ─────────────────────────────────────────────────────
def fake_uip(monkeypatch, fail_marker="BROKEN"):
    calls = []

    def run(self, *args):
        calls.append(args)
        if args[:2] == ("rpa", "init"):
            loc = Path(args[args.index("--location") + 1]) / "BuildCheck"
            loc.mkdir(parents=True)
            (loc / "project.json").write_text(json.dumps({"dependencies": {}}), encoding="utf-8")
            return ""
        proj = Path(args[args.index("--project-dir") + 1])
        if fail_marker in (proj / "Main.xaml").read_text(encoding="utf-8"):
            return REAL_LOG
        return '{"Success": true}'
    monkeypatch.setattr(UipathBuilder, "_run", run)
    return calls


def test_builder_creates_the_template_once_and_reports_errors(monkeypatch):
    calls = fake_uip(monkeypatch)
    with UipathBuilder(uip="uip") as b:
        assert b.build("<Activity/>").ok is True
        bad = b.build("<Activity>BROKEN</Activity>")
        assert bad.ok is False and bad.errors[0].startswith("BC30451")
        work = b._work
    assert sum(1 for c in calls if c[:2] == ("rpa", "init")) == 1             # blank project is created once, copied per build
    assert not work.exists()                                                   # temp files are cleaned up


def test_builder_adds_the_packages_a_workflow_needs(monkeypatch):
    fake_uip(monkeypatch)
    seen = {}
    orig = uipath_build.json.dumps
    with UipathBuilder(uip="uip") as b:
        b._ensure_template()
        monkeypatch.setattr(uipath_build.json, "dumps", lambda obj, **kw: (seen.update(obj.get("dependencies", {})), orig(obj, **kw))[1])
        b.build('<ui:HttpClient EndPoint="x"/> <ui:SendMail/>')
    assert "UiPath.WebAPI.Activities" in seen and "UiPath.Mail.Activities" in seen and "UiPath.Excel.Activities" not in seen


def test_missing_cli_is_a_clear_error_not_a_crash(monkeypatch):
    monkeypatch.setattr(uipath_build.shutil, "which", lambda name: None)
    b = UipathBuilder()
    assert b.available() is False
    with pytest.raises(uipath_build.UipNotFound, match="uip"):
        b.build("<Activity/>")


# ── CLI ─────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def cli(monkeypatch):
    monkeypatch.chdir(ROOT)
    spec = importlib.util.spec_from_file_location("generate_cli_build", ROOT / "scripts" / "generate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GOOD = {"passed": True, "total_score": 90, "errors": [], "warnings": [], "suggestions": []}


def _fake_generator(monkeypatch, revisions):
    import src.generation.two_phase_generator as tp

    class FakeGen:
        def __init__(self, *a, **k): pass
        def generate(self, **kw):
            return {"xaml_content": "<A>BROKEN</A>", "spec": {"v": 0}, "validation": GOOD, "score": 90, "output_path": None, "category": "c"}
        def compile_spec(self, spec): return "<A>BROKEN</A>" if spec["v"] == 0 else "<A>FIXED</A>"
        def revise_spec(self, task, category, spec, errors, n):
            revisions.append(errors); return {"v": spec["v"] + 1}

    monkeypatch.setattr(tp, "TwoPhaseGenerator", FakeGen)
    import src.validation.validator as val
    monkeypatch.setattr(val.XAMLValidator, "validate", lambda self, x: dict(GOOD))


def _fake_builder(monkeypatch, available=True):
    class FakeBuilder:
        def available(self): return available
        def build(self, xaml): return SimpleNamespace(ok="FIXED" in xaml, errors=[] if "FIXED" in xaml else ["BC30451: 'x' is not declared"])
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(uipath_build, "UipathBuilder", FakeBuilder)


def test_cli_build_check_repairs_and_writes_the_fixed_file(cli, monkeypatch, tmp_path):
    revisions = []
    _fake_generator(monkeypatch, revisions); _fake_builder(monkeypatch)
    out = tmp_path / "o.xaml"
    assert cli.main(["--task", "t", "--output", str(out), "--build-check", "--no-repair", "--quiet"]) == 0
    assert "FIXED" in out.read_text(encoding="utf-8")
    assert revisions == [["BC30451: 'x' is not declared"]]


def test_cli_build_check_without_the_uip_tool_is_exit_code_2(cli, monkeypatch, tmp_path):
    _fake_generator(monkeypatch, []); _fake_builder(monkeypatch, available=False)
    out = tmp_path / "o.xaml"
    assert cli.main(["--task", "t", "--output", str(out), "--build-check", "--quiet"]) == 2
    assert not out.exists()                                                    # nothing half-written


def test_cli_failed_build_after_all_rounds_is_exit_code_1_but_keeps_the_file(cli, monkeypatch, tmp_path):
    import src.generation.two_phase_generator as tp
    _fake_generator(monkeypatch, []); _fake_builder(monkeypatch)
    monkeypatch.setattr(tp.TwoPhaseGenerator, "revise_spec", lambda self, *a: {"v": 0})   # model cannot fix it
    out = tmp_path / "o.xaml"
    assert cli.main(["--task", "t", "--output", str(out), "--build-check", "--no-repair", "--quiet"]) == 1
    assert out.exists()


def test_cli_build_check_only_for_the_two_phase_strategy(cli):
    with pytest.raises(SystemExit) as e:
        cli.main(["--task", "t", "--strategy", "baseline", "--build-check"])
    assert e.value.code == 2
