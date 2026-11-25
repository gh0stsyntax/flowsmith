"""Smoke-test every activity template against the real UiPath build (needs the UiPath CLI `uip`, takes a few minutes).

For each activity type the compiler offers, a one-step workflow with default properties is compiled and built.
A failure here is a defect in the template itself (unknown property, wrong activity name ...), not a model mistake;
this is how `ReadPDFText.PDFPath`, `ui:Connect` and `AddQueueItem.QueueName` were found on 25 Nov 2026.

    python scripts/template_smoke_test.py            # all types
    python scripts/template_smoke_test.py ReadPDFText SendMail
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.generation.activity_templates import render_activity_xml  # noqa: E402
from src.generation.spec_compiler import SpecCompiler, _CONTROL_TYPES  # noqa: E402
from src.generation.spec_schema import SPEC_ACTIVITY_TYPES  # noqa: E402
from src.validation.uipath_build import UipathBuilder  # noqa: E402


def one_step_spec(activity_type: str) -> dict:
    return {"name": f"Smoke{activity_type}", "category": "general_automation", "description": "template smoke test", "packages": [],
            "arguments": [], "variables": [], "activities": [{"id": "a", "type": activity_type, "props": {}}], "activity_order": ["a"]}


def main() -> int:
    wanted = sys.argv[1:] or [t for t in SPEC_ACTIVITY_TYPES if t not in _CONTROL_TYPES]
    builder = UipathBuilder()
    if not builder.available():
        print("UiPath CLI 'uip' not found (npm i -g @uipath/cli).")
        return 2
    bad = 0
    with builder:
        for t in wanted:
            if render_activity_xml(t, "x", {}) is None:
                print(f"SKIP   {t:<24} (no template)")
                continue
            r = builder.build(SpecCompiler().compile(one_step_spec(t)))
            bad += not r.ok
            first = re.sub(r"C:\\Users\\[^']+?Main\.xaml", "Main.xaml", r.errors[0])[:150] if r.errors else ""
            print(f"{'OK    ' if r.ok else 'FAILED'} {t:<24} {first}")
    print(f"\n{len(wanted) - bad}/{len(wanted)} templates build on their own")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
