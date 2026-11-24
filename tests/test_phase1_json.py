"""Phase-1 JSON recovery. The fixture is a real qwen3:14b answer (24 Nov 2026, synthetic CSV->Excel task) that
closes the activities array and the root object one step too early ('}}]}, {"id": ...' -> json 'Extra data')."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.generation.two_phase_generator import TwoPhaseGenerator, _balance_brackets  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "phase1_extra_closing_bracket.json"


def test_fixture_is_really_broken():
    with pytest.raises(json.JSONDecodeError):
        json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_real_answer_bracket_error_is_gone():
    """The real answer has two defects: the premature close (repairable) and, later, code outside a string
    ("A" + (index + 2) ...) which no generic repair can fix -> handled by the retry with error feedback."""
    raw = FIXTURE.read_text(encoding="utf-8")
    with pytest.raises(json.JSONDecodeError) as before:
        json.loads(raw)
    assert before.value.msg == "Extra data"
    with pytest.raises(json.JSONDecodeError) as after:
        json.loads(_balance_brackets(raw))
    assert after.value.msg != "Extra data" and after.value.pos > before.value.pos


def test_premature_root_close_is_repaired_without_losing_activities():
    raw = ('{"name": "X", "activities": [{"id": "a", "props": {"v": "1"}}]},\n'
           '  {"id": "b", "props": {}}, {"id": "c", "props": {}}],\n "activity_order": ["a", "b", "c"]\n}')
    spec = TwoPhaseGenerator._parse_spec_json(None, raw)
    assert [a["id"] for a in spec["activities"]] == ["a", "b", "c"]
    assert spec["activity_order"] == ["a", "b", "c"]


def test_retries_use_different_seeds(monkeypatch):
    seen = []
    g = TwoPhaseGenerator.__new__(TwoPhaseGenerator)
    g.seed = 42
    g._resolve_model = lambda: "m"
    g._build_spec_prompt = lambda *a: "p"
    g._get_rag_activity_hints = lambda *a: ""

    class O:
        def generate(self, **kw):
            seen.append((kw["seed"], "UNGÜLTIG" in kw["prompt"]))
            return '{"name": "n"}'
    g._ollama_client = O()
    g._generate_spec("t", "c", None, attempt=0)
    g._generate_spec("t", "c", None, attempt=1, previous_error="Extra data")
    assert seen == [(42, False), (43, True)]


def test_valid_json_is_unchanged():
    s = '{"a": [1, {"b": "x}]"}], "c": {"d": 2}}'
    assert json.loads(_balance_brackets(s)) == json.loads(s)


def test_missing_closers_are_appended():
    assert json.loads(_balance_brackets('{"a": [1, 2, {"b": 3')) == {"a": [1, 2, {"b": 3}]}
