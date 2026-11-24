"""The Ollama client must forward `format` (JSON mode / JSON schema) to the request.

Found 24 Nov 2026: two_phase_generator passed format="json" and documented it as constrained decoding, but
OllamaClient._build_generate_payload silently dropped it, so the model was never constrained.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.generation.ollama_client import OllamaClient  # noqa: E402


def _client():
    c = OllamaClient.__new__(OllamaClient)         # no network, no config needed for payload building
    c.gen_params = {}
    return c


def test_format_json_is_forwarded():
    p = _client()._build_generate_payload("x", "m", None, False, format="json")
    assert p["format"] == "json"


def test_json_schema_is_forwarded_unchanged():
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    p = _client()._build_generate_payload("x", "m", None, False, format=schema)
    assert p["format"] == schema


def test_no_format_means_no_key():
    assert "format" not in _client()._build_generate_payload("x", "m", None, False)


def test_seed_is_forwarded_to_options():
    p = _client()._build_generate_payload("x", "m", None, False, seed=9)
    assert p["options"]["seed"] == 9
