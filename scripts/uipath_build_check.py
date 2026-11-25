"""Build generated workflows with the real UiPath toolchain (UiPath CLI `uip`, needs UiPath Studio).

The internal validator scores best practices; it cannot tell whether Studio will actually load a file.
This script answers that: every XAML is placed as Main.xaml into a fresh copy of a blank UiPath project,
required activity packages are added from the activities it uses, and `uip rpa build` compiles it
(including the Workflow Analyzer). The work is done by src/validation/uipath_build.py, the same code the
repair loop of `scripts/generate.py --build-check` uses.

    python scripts/uipath_build_check.py examples/generated/*.xaml
    python scripts/uipath_build_check.py output/xaml
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.validation.uipath_build import UipathBuilder  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="XAML files or folders")
    a = ap.parse_args()
    files = []
    for p in map(Path, a.paths):
        files += sorted(p.glob("*.xaml")) if p.is_dir() else [p]
    builder = UipathBuilder()
    if not builder.available():
        sys.exit("UiPath CLI 'uip' not found (npm i -g @uipath/cli) - this check needs UiPath Studio.")
    n_ok = 0
    with builder:
        for f in files:
            r = builder.build(f.read_text(encoding="utf-8"))
            n_ok += r.ok
            print(f"{'OK    ' if r.ok else 'FAILED'} {f.name}" + ("" if r.ok else "".join(f"\n       [ERROR] {e}" for e in r.errors[:3])))
    print(f"\n{n_ok}/{len(files)} workflows build in UiPath Studio")
    sys.exit(0 if n_ok == len(files) else 1)


if __name__ == "__main__":
    main()
