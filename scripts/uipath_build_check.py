"""Build generated workflows with the real UiPath toolchain (UiPath CLI `uip`, needs UiPath Studio).

The internal validator scores best practices; it cannot tell whether Studio will actually load a file.
This script answers that: every XAML is placed as Main.xaml into a fresh copy of a blank UiPath project,
required activity packages are added from the activities it uses, and `uip rpa build` compiles it
(including the Workflow Analyzer).

    python scripts/uipath_build_check.py examples/generated/*.xaml
    python scripts/uipath_build_check.py output/xaml --keep
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PACKAGES = [  # activity pattern -> (package, version)
    (r"ExcelApplicationScope|<ui:ReadRange|<ui:WriteRange|ExcelReadRange|ExcelWriteRange", ("UiPath.Excel.Activities", "[3.6.1]")),
    (r"<ui:SendMail|GetMailMessages|GetOutlookMailMessages", ("UiPath.Mail.Activities", "[2.12.10]")),
    (r"<ui:HttpClient", ("UiPath.WebAPI.Activities", "[2.4.0]")),
    (r"OpenBrowser|<ui:TypeInto|<ui:Click|<ui:GetValue|<ui:CloseTab|TakeScreenshot|WaitUiElementAppear|NavigateTo", ("UiPath.UIAutomation.Activities", "[26.10.7]")),
]


def _uip(*args: str) -> str:
    exe = shutil.which("uip") or shutil.which("uip.cmd")
    if not exe:
        sys.exit("UiPath CLI 'uip' not found (npm i -g @uipath/cli) - this check needs UiPath Studio.")
    r = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout + r.stderr


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="XAML files or folders")
    ap.add_argument("--keep", action="store_true", help="keep the temporary projects")
    a = ap.parse_args()
    files = []
    for p in map(Path, a.paths):
        files += sorted(p.glob("*.xaml")) if p.is_dir() else [p]
    work = Path(tempfile.mkdtemp(prefix="flowsmith_build_"))
    out = _uip("rpa", "init", "--name", "BuildCheck", "--location", str(work), "--expression-language", "VisualBasic",
               "--target-framework", "Windows", "--skip-solution-registration", "--format", "json")
    template = work / "BuildCheck"
    if not (template / "project.json").exists():
        sys.exit("Could not create a blank UiPath project:\n" + out[-800:])
    results = []
    for i, f in enumerate(files):
        proj = work / f"p{i:03d}"
        shutil.copytree(template, proj)
        xaml = f.read_text(encoding="utf-8")
        (proj / "Main.xaml").write_text(xaml, encoding="utf-8")
        pj = json.loads((proj / "project.json").read_text(encoding="utf-8-sig"))
        for pattern, (pkg, ver) in PACKAGES:
            if re.search(pattern, xaml):
                pj["dependencies"][pkg] = ver
        (proj / "project.json").write_text(json.dumps(pj, indent=2), encoding="utf-8")
        log = _uip("rpa", "build", "--project-dir", str(proj), "--format", "json")
        ok = bool(re.search(r'"Success":\s*true', log))
        err = re.search(r"\[ERROR\][^\r\n]*", log)
        results.append({"file": f.name, "build": "OK" if ok else "FAILED", "error": "" if ok else (err.group(0)[:300] if err else log[-300:])})
        print(f"{'OK    ' if ok else 'FAILED'} {f.name}" + ("" if ok else f"\n       {results[-1]['error']}"))
    n_ok = sum(r["build"] == "OK" for r in results)
    print(f"\n{n_ok}/{len(results)} workflows build in UiPath Studio")
    if not a.keep:
        shutil.rmtree(work, ignore_errors=True)
    sys.exit(0 if n_ok == len(results) else 1)


if __name__ == "__main__":
    main()
