"""
src/training/pair_builder.py  –  v2
Pseudo-Pair-Generator: Erzeugt (degraded, best_practice)-Trainingspaare
durch regelbasierte Verschlechterung qualitätsgeprüfter XAML-Workflows.

Verbesserungen v2:
- 10 Degradationsregeln (v1: 5)
- Kombinatorische Paare: bis zu 2^k = 1024 Kombinationen
- Konfigurierbarer max_degradations_per_pair Parameter
- Detaillierte Statistiken pro Degradationstyp
- Quality-Score-Tracking der degradierten Varianten
- Saubere Train/Val/Test-Aufteilung (80/10/10)
"""

import json
import logging
import random
import re
from copy import deepcopy
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─── Degradationsregeln ────────────────────────────────────────────────────────
# Jede Regel ist ein Tupel:
# (name, beschreibung, apply_fn)
# apply_fn(xaml: str) -> str | None  (None = Regel nicht anwendbar)


def _remove_trycatch(xaml: str) -> Optional[str]:
    """Entfernt TryCatch-Blöcke – simuliert fehlende Fehlerbehandlung."""
    if '<TryCatch' not in xaml:
        return None
    # Ersetze TryCatch durch seinen Body-Inhalt
    result = re.sub(
        r'<TryCatch[^>]*>.*?<Catches>.*?</Catches>.*?</TryCatch>',
        '<Sequence DisplayName="Hauptsequenz (ohne Fehlerbehandlung)">',
        xaml, flags=re.DOTALL
    )
    result = result.replace('</TryCatch>', '</Sequence>')
    return result if result != xaml else None


def _remove_logging(xaml: str) -> Optional[str]:
    """Entfernt alle LogMessage-Aktivitäten – simuliert fehlendes Logging."""
    if 'LogMessage' not in xaml:
        return None
    result = re.sub(
        r'<ui:LogMessage[^/]*/>\s*', '',
        xaml, flags=re.DOTALL
    )
    result = re.sub(
        r'<ui:LogMessage[^>]*>.*?</ui:LogMessage>\s*', '',
        result, flags=re.DOTALL
    )
    return result if result != xaml else None


def _remove_start_log(xaml: str) -> Optional[str]:
    """Entfernt nur den Start-LogMessage – simuliert fehlendes Init-Logging."""
    pattern = r'<ui:LogMessage[^>]*"[^"]*Start[^"]*"[^/]*/>\s*'
    result = re.sub(pattern, '', xaml, count=1, flags=re.IGNORECASE)
    if result == xaml:
        pattern2 = r'<ui:LogMessage[^>]*"[^"]*Beginn[^"]*"[^/]*/>\s*'
        result = re.sub(pattern2, '', xaml, count=1, flags=re.IGNORECASE)
    return result if result != xaml else None


def _hardcode_credentials(xaml: str) -> Optional[str]:
    """Ersetzt GetCredential durch hartcodierte Werte – simuliert Sicherheitsverletzung."""
    if 'GetCredential' not in xaml and 'getrobotcredential' not in xaml.lower():
        return None
    result = re.sub(
        r'<ui:GetCredential[^>]*/>\s*',
        '<Assign DisplayName="Passwort (UNSICHER - hartcodiert)">'
        '<Assign.To><OutArgument x:TypeArguments="s:String">'
        '<VariableReference x:TypeArguments="s:String" VariableName="password"/>'
        '</OutArgument></Assign.To>'
        '<Assign.Value><InArgument x:TypeArguments="s:String">'
        '<Literal x:TypeArguments="s:String" Value="PASSWORT123"/>'
        '</InArgument></Assign.Value></Assign>',
        xaml
    )
    return result if result != xaml else None


def _remove_display_names(xaml: str) -> Optional[str]:
    """Entfernt DisplayName-Attribute – simuliert mangelnde Wartbarkeit."""
    if 'DisplayName=' not in xaml:
        return None
    result = re.sub(r'\s*DisplayName="[^"]*"', '', xaml)
    return result if result != xaml else None


def _remove_timeout_ms(xaml: str) -> Optional[str]:
    """Entfernt TimeoutMS-Attribute – simuliert fehlende Robustheit."""
    if 'TimeoutMS' not in xaml and 'Timeout' not in xaml:
        return None
    result = re.sub(r'\s*TimeoutMS="[^"]*"', '', xaml)
    result = re.sub(r'\s*Timeout="[^"]*"', '', result)
    return result if result != xaml else None


def _remove_rethrow(xaml: str) -> Optional[str]:
    """Entfernt Rethrow im Catch-Block – simuliert verschwundene Exception."""
    if '<Rethrow' not in xaml:
        return None
    result = re.sub(r'\s*<Rethrow\s*/>\s*', '\n', xaml)
    return result if result != xaml else None


def _add_magic_numbers(xaml: str) -> Optional[str]:
    """Ersetzt Variable-Referenzen durch hartcodierte Zahlen in Delays."""
    if '<Delay' not in xaml:
        return None
    result = re.sub(
        r'<Delay[^>]*/>\s*',
        '<Delay DisplayName="Warte 5 Sekunden (HARTCODIERT)">'
        '<Delay.Duration><InArgument x:TypeArguments="s:TimeSpan">'
        '<Literal x:TypeArguments="s:TimeSpan" Value="00:00:05"/>'
        '</InArgument></Delay.Duration></Delay>',
        xaml, count=1
    )
    return result if result != xaml else None


def _wrong_variable_naming(xaml: str) -> Optional[str]:
    """Ändert Variablennamen zu UpperCase – verletzt camelCase-Konvention."""
    # Finde camelCase-Variablen und mache sie UPPER_CASE
    vars_found = re.findall(r'VariableName="([a-z][a-zA-Z]+)"', xaml)
    if not vars_found:
        return None
    result = xaml
    for var in set(vars_found[:3]):  # Nur erste 3 ändern
        upper = re.sub(r'([A-Z])', r'_\1', var).upper()
        result = result.replace(f'VariableName="{var}"', f'VariableName="{upper}"')
    return result if result != xaml else None


def _remove_finally_block(xaml: str) -> Optional[str]:
    """Entfernt Finally-Block – simuliert fehlende Ressourcen-Freigabe."""
    if '<TryCatch.Finally>' not in xaml:
        return None
    result = re.sub(
        r'<TryCatch\.Finally>.*?</TryCatch\.Finally>',
        '',
        xaml, flags=re.DOTALL
    )
    return result if result != xaml else None


# ─── Regelregister ─────────────────────────────────────────────────────────────
DEGRADATION_RULES: List[Tuple[str, str, callable]] = [
    ("remove_trycatch",        "Fehlerbehandlung entfernt",           _remove_trycatch),
    ("remove_logging",         "Alle LogMessages entfernt",           _remove_logging),
    ("remove_start_log",       "Start-LogMessage entfernt",           _remove_start_log),
    ("hardcode_credentials",   "Credentials hartcodiert",             _hardcode_credentials),
    ("remove_display_names",   "DisplayNames entfernt",               _remove_display_names),
    ("remove_timeout_ms",      "Timeouts entfernt",                   _remove_timeout_ms),
    ("remove_rethrow",         "Rethrow aus Catch entfernt",          _remove_rethrow),
    ("add_magic_numbers",      "Magische Zahlen in Delays",           _add_magic_numbers),
    ("wrong_variable_naming",  "Variablennamen UPPER_CASE (falsch)",  _wrong_variable_naming),
    ("remove_finally",         "Finally-Block entfernt",              _remove_finally_block),
]


class PairBuilder:
    """
    Erzeugt (degraded_xaml, target_xaml)-Trainingspaare durch regelbasierte
    Verschlechterung qualitätsgeprüfter Best-Practice-Workflows.

    Aus N Quell-Workflows und K Degradationsregeln entstehen
    bis zu N × 2^K Pseudo-Paare (begrenzt durch max_per_source).
    """

    def __init__(self,
                 max_per_source: int = 32,
                 max_degradations_per_pair: int = 3,
                 min_quality_score: int = 70,
                 random_seed: int = 42):
        """
        Args:
            max_per_source:            Max. Paare pro Quell-Workflow
            max_degradations_per_pair: Max. gleichzeitig angewendete Regeln
            min_quality_score:         Nur Workflows >= diesem Score als Quelle
            random_seed:               Reproduzierbarkeit
        """
        self.max_per_source = max_per_source
        self.max_degradations = max_degradations_per_pair
        self.min_quality = min_quality_score
        random.seed(random_seed)

    def build_from_file(self,
                        input_file: str = 'data/processed/test.jsonl',
                        output_dir: str = 'data/processed',
                        val_ratio: float = 0.10,
                        test_ratio: float = 0.10) -> Dict:
        """
        Lädt Quell-Workflows, erzeugt Pseudo-Paare und schreibt
        pairs_train/val/test.jsonl sowie pairs_stats.json.

        Returns:
            Statistik-Dict
        """
        input_path = Path(input_file)
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # Quell-Daten laden
        sources = self._load_sources(input_path)
        if not sources:
            logger.warning(f"Keine Quell-Daten in {input_path}")
            return {'total_pairs': 0, 'train': 0, 'val': 0, 'test': 0}

        logger.info(f"Verarbeite {len(sources)} Quell-Workflows...")

        # Alle Pseudo-Paare erzeugen
        all_pairs = []
        rule_stats: Dict[str, int] = {r[0]: 0 for r in DEGRADATION_RULES}

        for source in sources:
            pairs = self._generate_pairs(source, rule_stats)
            all_pairs.extend(pairs)

        logger.info(f"Erzeugte {len(all_pairs)} Pseudo-Paare")

        # Shuffle & Split
        random.shuffle(all_pairs)
        n = len(all_pairs)
        n_test = max(1, int(n * test_ratio)) if n > 2 else 0
        n_val  = max(1, int(n * val_ratio))  if n > 2 else 0
        n_train = n - n_test - n_val

        splits = {
            'train': all_pairs[:n_train],
            'val':   all_pairs[n_train:n_train + n_val],
            'test':  all_pairs[n_train + n_val:],
        }

        for split_name, pairs in splits.items():
            out_file = output_path / f'pairs_{split_name}.jsonl'
            with open(out_file, 'w', encoding='utf-8') as f:
                for pair in pairs:
                    f.write(json.dumps(pair, ensure_ascii=False) + '\n')
            logger.info(f"  {split_name}: {len(pairs)} Paare → {out_file}")

        stats = {
            'total_pairs': n,
            'train': len(splits['train']),
            'val':   len(splits['val']),
            'test':  len(splits['test']),
            'source_workflows': len(sources),
            'rule_usage': rule_stats,
        }
        (output_path / 'pairs_stats.json').write_text(
            json.dumps(stats, indent=2), encoding='utf-8')

        logger.info(f"✅ Pair-Building abgeschlossen: {stats}")
        return stats

    # ── Interne Helpers ──────────────────────────────────────────────────────

    def _load_sources(self, path: Path) -> List[Dict]:
        """Lädt Quell-Workflows aus JSONL – nur Samples >= min_quality_score."""
        sources = []
        if not path.exists():
            # Alle verfügbaren JSONL-Dateien als Fallback
            for fname in ['train.jsonl', 'val.jsonl', 'test.jsonl']:
                p = path.parent / fname
                if p.exists():
                    sources.extend(self._read_jsonl(p))
        else:
            sources = self._read_jsonl(path)

        # Qualitätsfilter
        filtered = [
            s for s in sources
            if s.get('quality_score', s.get('score', 0)) >= self.min_quality
            and s.get('xaml_content') or s.get('target_xaml')
        ]
        logger.info(
            f"Qualitätsfilter ({self.min_quality}+): "
            f"{len(sources)} → {len(filtered)} Workflows"
        )
        return filtered

    def _read_jsonl(self, path: Path) -> List[Dict]:
        result = []
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        result.append(json.loads(line))
                    except Exception:
                        pass
        return result

    def _generate_pairs(self, source: Dict,
                        rule_stats: Dict[str, int]) -> List[Dict]:
        """Erzeugt alle Pseudo-Paare für einen Quell-Workflow."""
        target_xaml = source.get('xaml_content') or source.get('target_xaml', '')
        if not target_xaml:
            return []

        # Welche Regeln sind anwendbar?
        applicable = []
        for name, desc, fn in DEGRADATION_RULES:
            result = fn(target_xaml)
            if result is not None:
                applicable.append((name, desc, fn, result))

        if not applicable:
            return []

        pairs = []
        pair_id_base = source.get('id', f"src_{hash(target_xaml) % 100000}")

        # Einzelne Degradationen
        for name, desc, fn, degraded in applicable:
            pair = self._make_pair(
                pair_id_base, source, target_xaml,
                degraded, [name], [desc]
            )
            pairs.append(pair)
            rule_stats[name] = rule_stats.get(name, 0) + 1

        # Kombinationen (2 bis max_degradations)
        for combo_size in range(2, min(self.max_degradations + 1, len(applicable) + 1)):
            for combo in combinations(applicable, combo_size):
                degraded = target_xaml
                names = []
                descs = []
                valid = True
                for name, desc, fn, _ in combo:
                    result = fn(degraded)
                    if result is None:
                        valid = False
                        break
                    degraded = result
                    names.append(name)
                    descs.append(desc)
                if valid and degraded != target_xaml:
                    pair = self._make_pair(
                        pair_id_base, source, target_xaml,
                        degraded, names, descs
                    )
                    pairs.append(pair)
                    if len(pairs) >= self.max_per_source:
                        return pairs

        return pairs[:self.max_per_source]

    def _make_pair(self,
                   id_base: str,
                   source: Dict,
                   target_xaml: str,
                   degraded_xaml: str,
                   degradation_names: List[str],
                   degradation_descs: List[str]) -> Dict:
        """Erstellt ein einzelnes Trainings-Pair-Dict."""
        combo_key = '+'.join(sorted(degradation_names))
        return {
            'id':              f"{id_base}__{combo_key}",
            'category':        source.get('category', 'general_automation'),
            'description':     source.get('description', ''),
            'input_xaml':      degraded_xaml,
            'target_xaml':     target_xaml,
            'degradations':    degradation_names,
            'degradation_desc': degradation_descs,
            'source_quality':  source.get('quality_score',
                                          source.get('score', 0)),
            'meta': {
                'source_file':   source.get('id', ''),
                'activities':    source.get('activities', []),
                'num_degradations': len(degradation_names),
            },
        }


def build_pairs(input_file: str = 'data/processed/test.jsonl',
                output_dir: str = 'data/processed',
                max_per_source: int = 32,
                max_degradations: int = 3,
                min_quality: int = 70) -> Dict:
    """Convenience-Funktion für den API-Endpunkt."""
    builder = PairBuilder(
        max_per_source=max_per_source,
        max_degradations_per_pair=max_degradations,
        min_quality_score=min_quality,
    )
    return builder.build_from_file(
        input_file=input_file,
        output_dir=output_dir,
    )
