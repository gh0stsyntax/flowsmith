"""
src/training/pairs_to_finetune.py
Converts pseudo-pairs (input_xaml -> target_xaml) into chat-format JSONL
compatible with the existing Unsloth training script generator.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List


SYSTEM = (
    "Du bist ein UiPath RPA-Experte. "
    "Deine Aufgabe: Verbessere/repairiere den gegebenen XAML-Workflow zu Best Practice.\n"
    "Regeln: TryCatch als äußerste Ebene, LogMessage am Start/Ende/Catch, "
    "DisplayNames, Argumente in_/out_/io_, keine Klartext-Passwörter, "
    "TimeoutMS bei UI-Interaktionen."
)


def _iter_jsonl(path: Path) -> Iterable[Dict]:
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def convert_pairs_to_chat_jsonl(pairs_path: str, out_path: str) -> int:
    src = Path(pairs_path)
    dst = Path(out_path)
    n = 0
    with open(dst, "w", encoding="utf-8") as out:
        for row in _iter_jsonl(src):
            input_xaml = row.get("input_xaml") or ""
            target_xaml = row.get("target_xaml") or ""
            if not input_xaml or not target_xaml:
                continue
            cat = row.get("category") or "general_automation"
            degr = ", ".join(row.get("degradations") or [])
            user = (
                f"Repariere folgenden UiPath Workflow zu Best Practice.\n"
                f"Kategorie: {cat}\n"
                f"Bekannte Degradations: {degr}\n\n"
                f"INPUT XAML:\n{input_xaml}\n\n"
                f"Gib NUR den reparierten XAML zurück."
            )
            out.write(
                json.dumps(
                    {
                        "messages": [
                            {"role": "system", "content": SYSTEM},
                            {"role": "user", "content": user},
                            {"role": "assistant", "content": target_xaml},
                        ],
                        "metadata": {"category": cat, "source": "pseudo_pairs"},
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            n += 1
    return n

