"""Repair loop driven by the REAL compiler: build -> read the errors -> ask the model for a corrected spec -> compile again.

Why spec level: the model never writes XAML here, it writes a workflow spec that a deterministic compiler turns into XAML.
So build errors are fed back to the phase-1 model, which fixes the spec (a missing variable, a wrong type, a bad expression),
and everything downstream (compiler fixes, surgical repair) runs again on the new spec.

Research on self-repair (Olausson et al. 2023; later studies) finds that most of the gain comes in the first one or two rounds and
that the quality of the error feedback is the bottleneck: compiler messages are precise, so two rounds are the default here.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

DEFAULT_ROUNDS = 2

# Generic fixes for the error classes observed in the runs of 24/25 Nov 2026 (not tied to a task).
BUILD_FIX_HINTS = """\
HINWEISE ZUR KORREKTUR (VB.NET, UiPath Studio):
- "'x' is not declared": Variable x in "variables" mit passendem Typ anlegen oder eine vorhandene Variable verwenden.
- "'M' is not a member of 'String'": Text-Variablen haben keine Eigenschaften wie .Content, .Response, .Text; die Variable selbst verwenden.
  Einen JSON-Wert liest man mit  JObject.Parse(antwort)("feld").ToString().
- "Option Strict On disallows implicit conversions from 'Object' to 'String'": Ausdruck mit .ToString() abschließen oder Variable als System.Object deklarieren.
- "cannot be converted to 'IEnumerable(Of String)'" / "'String()'": Listen (Dateien, Zeilen) brauchen eine Variable vom Typ System.String[].
- "Syntax error" / "Expression expected": Werte sind VB-Ausdrücke; Texte in Anführungszeichen setzen, Pfade und URLs als "…" schreiben.
"""


def feedback_loop(build: Callable[[str], Any], compile_spec: Callable[[Dict], str], revise: Callable[[Dict, List[str], int], Dict],
                  spec: Dict, max_rounds: int = DEFAULT_ROUNDS) -> Dict[str, Any]:
    """`build(xaml) -> object with .ok and .errors`; `compile_spec(spec) -> xaml` (including every post-processing step);
    `revise(spec, errors, round_no) -> corrected spec`. Stops at the first successful build, after `max_rounds` repair rounds,
    or when the model returns an unchanged spec (asking again would only repeat the failure)."""
    history: List[Dict[str, Any]] = []
    xaml = compile_spec(spec)
    for round_no in range(max_rounds + 1):
        result = build(xaml)
        history.append({"round": round_no, "ok": bool(result.ok), "errors": list(result.errors)})
        if result.ok or round_no == max_rounds:
            break
        revised = revise(spec, list(result.errors), round_no + 1)
        if json.dumps(revised, sort_keys=True) == json.dumps(spec, sort_keys=True):
            logger.info("Build-Reparatur: Modell liefert unveränderten Spec, Abbruch nach Runde %d", round_no)
            break
        spec = revised
        xaml = compile_spec(spec)
    return {"xaml": xaml, "spec": spec, "ok": history[-1]["ok"], "history": history,
            "repair_rounds_used": len(history) - 1}
