"""The phase-1 JSON schema must stay in sync with what the compiler can render."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.generation.activity_templates import render_activity_xml  # noqa: E402
from src.generation.spec_compiler import _CONTROL_TYPES, xaml_type  # noqa: E402
from src.generation.spec_schema import SPEC_ACTIVITY_TYPES, SPEC_SCHEMA, SPEC_VARIABLE_TYPES  # noqa: E402


def test_every_schema_activity_type_is_implemented():
    missing = [t for t in SPEC_ACTIVITY_TYPES if t not in _CONTROL_TYPES and render_activity_xml(t, "x", {}) is None]
    assert missing == []                     # otherwise the model may pick a type that becomes a TODO stub


def test_every_schema_variable_type_maps_to_a_xaml_type():
    for t in SPEC_VARIABLE_TYPES:
        assert xaml_type(t).startswith(("x:", "s:", "sd:", "scg:")), t


def test_schema_enums_match_constants():
    items = SPEC_SCHEMA["properties"]["activities"]["items"]["properties"]["type"]["enum"]
    assert items == list(SPEC_ACTIVITY_TYPES) and len(set(items)) == len(items)


def test_prompt_example_conforms_to_schema_enums():
    import src.generation.two_phase_generator as tp
    example = json.loads(tp._SPEC_SCHEMA_EXAMPLE)
    # the example contains SAPTransaction/Click/GetText/Assign/LogMessage: all must be allowed
    assert {a["type"] for a in example["activities"]} <= set(SPEC_ACTIVITY_TYPES)
    for v in example["variables"] + example["arguments"]:
        assert v["type"] in SPEC_VARIABLE_TYPES, v["type"]
    assert set(example["activity_order"]) == {a["id"] for a in example["activities"]}


def test_generator_sends_the_schema_to_ollama():
    import src.generation.two_phase_generator as tp
    seen = {}

    class FakeOllama:
        def model_exists(self, name):
            return False

        def generate(self, **kw):
            seen.update(kw)
            return json.dumps({"name": "N", "category": "c", "description": "d", "activities": [], "activity_order": []})

    g = tp.TwoPhaseGenerator(seed=7)
    g._ollama_client = FakeOllama()
    g._get_rag_activity_hints = lambda *a, **k: ""
    g._generate_spec("a task", "general_automation", None, attempt=2)
    assert seen["format"] == SPEC_SCHEMA          # was format="json", and even that never reached Ollama
    assert seen["seed"] == 9                      # seed + attempt
