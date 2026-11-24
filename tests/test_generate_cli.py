"""CLI behaviour of scripts/generate.py without a language model (generators are replaced by fakes).

Found on 24 Nov 2026 in a fresh clone: the CLI used the old direct generator (the weakest strategy in the benchmark)
and ended with a traceback when generation failed; the two-phase path was only reachable through the API.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def cli(monkeypatch):
    monkeypatch.chdir(ROOT)
    spec = importlib.util.spec_from_file_location("generate_cli", ROOT / "scripts" / "generate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GOOD = {"passed": True, "total_score": 91, "errors": [], "warnings": [], "suggestions": []}


def _fake_two_phase(monkeypatch, calls):
    import src.generation.two_phase_generator as tp
    import src.generation.surgical_repair as sr

    class FakeGen:
        def generate(self, **kw):
            calls.append(("two-phase", kw["task_description"]))
            return {"xaml_content": '<Activity x:Class="UiPath.T"/>', "validation": GOOD, "score": 91, "output_path": None}

    class FakeRepair:
        def repair_and_validate(self, xaml, name):
            calls.append(("repair", name))
            return xaml + "<!-- repaired -->", [], GOOD

    monkeypatch.setattr(tp, "TwoPhaseGenerator", FakeGen)
    monkeypatch.setattr(sr, "SurgicalRepair", FakeRepair)


def test_default_strategy_is_two_phase_with_repair(cli, monkeypatch, tmp_path):
    calls = []
    _fake_two_phase(monkeypatch, calls)
    out = tmp_path / "o.xaml"
    assert cli.main(["--task", "do a thing", "--output", str(out), "--quiet"]) == 0
    assert [c[0] for c in calls] == ["two-phase", "repair"]
    assert out.read_text(encoding="utf-8").endswith("<!-- repaired -->")


def test_no_repair_flag_skips_repair(cli, monkeypatch, tmp_path):
    calls = []
    _fake_two_phase(monkeypatch, calls)
    assert cli.main(["--task", "x", "--output", str(tmp_path / "o.xaml"), "--no-repair", "--quiet"]) == 0
    assert [c[0] for c in calls] == ["two-phase"]


def test_baseline_is_still_selectable(cli, monkeypatch, tmp_path):
    import src.generation.generator as g
    seen = []

    class FakeBase:
        def generate(self, **kw):
            seen.append(kw["task_description"])
            return {"xaml_content": "<Activity/>", "validation": GOOD, "score": 50, "attempt": 1}

    monkeypatch.setattr(g, "XAMLGenerator", FakeBase)
    assert cli.main(["--task", "x", "--strategy", "baseline", "--output", str(tmp_path / "b.xaml"), "--quiet"]) == 0
    assert seen == ["x"]


def test_generation_failure_is_a_clean_exit_code(cli, monkeypatch, tmp_path, caplog):
    import src.generation.two_phase_generator as tp

    class Boom:
        def generate(self, **kw):
            raise RuntimeError("XAML-Generierung fehlgeschlagen nach allen Versuchen")

    monkeypatch.setattr(tp, "TwoPhaseGenerator", Boom)
    assert cli.main(["--task", "x", "--output", str(tmp_path / "o.xaml")]) == 1
    assert not (tmp_path / "o.xaml").exists()


def test_without_task_prints_help_and_fails(cli, capsys):
    assert cli.main([]) == 1
    assert "--strategy" in capsys.readouterr().out
