"""Build a generated workflow with the real UiPath toolchain and return ALL compiler errors.

The internal validator scores best practices; it cannot tell whether Studio will load a file. This module puts the XAML
as Main.xaml into a blank UiPath project (created once, copied per build), adds the activity packages the file uses and runs
`uip rpa build` (compilation + Workflow Analyzer). Needs the UiPath CLI (`npm i -g @uipath/cli`) and UiPath Studio.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

PACKAGES = [  # activity pattern -> (package, version)
    (r"ExcelApplicationScope|<ui:ReadRange|<ui:WriteRange|ExcelReadRange|ExcelWriteRange", ("UiPath.Excel.Activities", "[3.6.1]")),
    (r"<ui:SendMail|GetMailMessages|GetOutlookMailMessages", ("UiPath.Mail.Activities", "[2.12.10]")),
    (r"<ui:HttpClient", ("UiPath.WebAPI.Activities", "[2.4.0]")),
    (r"<ui:ReadPDFText|<ui:ExtractPDFPage", ("UiPath.PDF.Activities", "[3.25.3]")),
    (r"<ui:ExecuteQuery|<ui:Connect", ("UiPath.Database.Activities", "[1.10.2]")),
    (r"OpenBrowser|<ui:TypeInto|<ui:Click|<ui:GetValue|<ui:CloseTab|TakeScreenshot|WaitUiElementAppear|NavigateTo|<ui:OpenApplication|<ui:AttachWindow|NSAPCallTransaction", ("UiPath.UIAutomation.Activities", "[26.10.7]")),
]

MAX_ERRORS = 8
MAX_ERROR_CHARS = 300


class UipNotFound(RuntimeError):
    pass


@dataclass
class BuildResult:
    ok: bool
    errors: List[str] = field(default_factory=list)      # "BC30451: 'x' is not declared ..." (deduplicated, capped)
    raw: str = ""


def parse_build_errors(log: str) -> List[str]:
    """All distinct compiler errors of a `uip rpa build` log. The JSON `Message` lists them as '[Main.xaml] BCnnnn: text';
    the console lines '[ERROR] [Process] [BuildCheck] BCnnnn: text' repeat them, other [ERROR] lines are kept as a fallback."""
    found: List[str] = []

    def add(text: str) -> None:
        text = re.sub(r"\s+", " ", text).strip()[:MAX_ERROR_CHARS]
        if text and text not in found:
            found.append(text)

    for m in re.finditer(r"(BC\d{4,5}:[^\r\n\\]+|BC\d{4,5}: [^\r\n]+)", log):
        add(m.group(1))
    if not found:
        for m in re.finditer(r"\[ERROR\]\s*(?:\[[^\]]+\]\s*)*([^\r\n]+)", log):
            if "ProjectPackager" not in m.group(0):
                add(m.group(1))
    return found[:MAX_ERRORS]


class UipathBuilder:
    def __init__(self, uip: Optional[str] = None) -> None:
        self.uip = uip or shutil.which("uip") or shutil.which("uip.cmd")
        self._work: Optional[Path] = None
        self._template: Optional[Path] = None

    def available(self) -> bool:
        return bool(self.uip)

    def _run(self, *args: str) -> str:
        if not self.uip:
            raise UipNotFound("UiPath CLI 'uip' not found (npm i -g @uipath/cli); the build check needs UiPath Studio.")
        r = subprocess.run([self.uip, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
        return r.stdout + r.stderr

    def _ensure_template(self) -> Path:
        if self._template is None:
            self._work = Path(tempfile.mkdtemp(prefix="flowsmith_build_"))
            out = self._run("rpa", "init", "--name", "BuildCheck", "--location", str(self._work), "--expression-language", "VisualBasic",
                            "--target-framework", "Windows", "--skip-solution-registration", "--format", "json")
            template = self._work / "BuildCheck"
            if not (template / "project.json").exists():
                raise RuntimeError("Could not create a blank UiPath project:\n" + out[-800:])
            self._template = template
        return self._template

    def build(self, xaml: str) -> BuildResult:
        template = self._ensure_template()
        proj = Path(tempfile.mkdtemp(prefix="p", dir=self._work))
        shutil.rmtree(proj)
        shutil.copytree(template, proj)
        (proj / "Main.xaml").write_text(xaml, encoding="utf-8")
        pj = json.loads((proj / "project.json").read_text(encoding="utf-8-sig"))
        for pattern, (pkg, ver) in PACKAGES:
            if re.search(pattern, xaml):
                pj["dependencies"][pkg] = ver
        (proj / "project.json").write_text(json.dumps(pj, indent=2), encoding="utf-8")
        log = self._run("rpa", "build", "--project-dir", str(proj), "--format", "json")
        shutil.rmtree(proj, ignore_errors=True)
        if re.search(r'"Success":\s*true', log):
            return BuildResult(True, [], log)
        return BuildResult(False, parse_build_errors(log) or [log[-300:].strip()], log)

    def close(self) -> None:
        if self._work:
            shutil.rmtree(self._work, ignore_errors=True)
            self._work = self._template = None

    def __enter__(self) -> "UipathBuilder":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
