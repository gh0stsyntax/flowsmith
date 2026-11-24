"""
src/generation/workflow_spec.py
Minimal workflow specification used for two-phase generation:
1) LLM generates WorkflowSpec (JSON)
2) Compiler turns WorkflowSpec into XAML + project.json via generator helpers
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class WorkflowArgument:
    name: str
    type: str
    direction: str  # in|out|io
    description: Optional[str] = None


@dataclass
class WorkflowVariable:
    name: str
    type: str
    default: Optional[str] = None
    scope: str = "Main"
    description: Optional[str] = None


def normalize_prop_keys(props: Dict[str, Any]) -> Dict[str, Any]:
    """The compiler reads snake_case keys (`to`, `value`, `workbook_path`). With a JSON schema the model started to
    answer in PascalCase (`To`, `Value`, `WorkbookPath`), and everything it said was silently replaced by defaults.
    Keys are normalised: `WorkbookPath` / `workbookPath` / `Workbook-Path` -> `workbook_path`."""
    import re as _re
    out: Dict[str, Any] = {}
    for k, v in dict(props).items():
        key = _re.sub(r"[\s\-]+", "_", str(k).strip())
        key = _re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key).lower()
        out.setdefault(key, v)                                  # first spelling wins if the model sent both
    return out


@dataclass
class WorkflowActivity:
    id: str
    type: str
    display_name: Optional[str] = None
    props: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowSpec:
    name: str
    category: str
    description: str
    packages: List[str] = field(default_factory=list)
    arguments: List[WorkflowArgument] = field(default_factory=list)
    variables: List[WorkflowVariable] = field(default_factory=list)
    activities: List[WorkflowActivity] = field(default_factory=list)
    # A very simple linear order for MVP; can be upgraded to graph later.
    activity_order: List[str] = field(default_factory=list)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "WorkflowSpec":
        name = str(d.get("name") or "GeneratedProcess")
        category = str(d.get("category") or "general_automation")
        description = str(d.get("description") or "")
        packages = list(d.get("packages") or [])

        arguments = []
        for a in d.get("arguments") or []:
            arguments.append(
                WorkflowArgument(
                    name=str(a.get("name")),
                    type=str(a.get("type") or "System.String"),
                    direction=str(a.get("direction") or "in"),
                    description=a.get("description"),
                )
            )

        variables = []
        for v in d.get("variables") or []:
            variables.append(
                WorkflowVariable(
                    name=str(v.get("name")),
                    type=str(v.get("type") or "System.String"),
                    default=v.get("default"),
                    scope=str(v.get("scope") or "Main"),
                    description=v.get("description"),
                )
            )

        activities = []
        for x in d.get("activities") or []:
            activities.append(
                WorkflowActivity(
                    id=str(x.get("id")),
                    type=str(x.get("type")),
                    display_name=x.get("display_name"),
                    props=normalize_prop_keys(x.get("props") or {}),
                )
            )

        order = list(d.get("activity_order") or [a.id for a in activities])
        return WorkflowSpec(
            name=name,
            category=category,
            description=description,
            packages=packages,
            arguments=arguments,
            variables=variables,
            activities=activities,
            activity_order=order,
        )

