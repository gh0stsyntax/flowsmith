"""
scripts/benchmark.py
Vergleichs-Benchmark: Vier Generierungsstrategien im direkten Vergleich

Misst für die Ablationsstudie (Masterarbeit Kap. 6 / 8):
  A) Baseline       – Direktgenerierung, kein Repair
  B) +Repair        – Direktgenerierung + SurgicalRepair
  C) Two-Phase      – Zwei-Phasen-Generator (Spec→XAML), kein Repair
  D) Two-Phase+Rep  – Zwei-Phasen-Generator + SurgicalRepair (empfohlenes System)

Ausgabe: results/benchmark_{timestamp}.json und Konsolen-Tabelle

Verwendung:
  python scripts/benchmark.py --tasks tasks.txt --n 20
  python scripts/benchmark.py --tasks tasks.txt --n 20 --strategy two_phase
  python scripts/benchmark.py --quick    # 5 Aufgaben, alle Strategien
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Projektpfad
sys.path.insert(0, str(Path(__file__).parent.parent))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("benchmark")

# ─── Beispiel-Aufgaben (falls keine tasks.txt übergeben) ─────────────────────
DEFAULT_TASKS: List[Dict] = [
    {"task": "Melde dich am SAP an, öffne ME22N, prüfe Bestellpreis gegen Nettowert, logge Abweichungen > 1%.",
     "category": "sap_automation"},
    {"task": "Öffne Excel, lese Kundenliste, filtere Status='Aktiv', schreibe in neues Sheet 'Auswertung'.",
     "category": "excel_automation"},
    {"task": "Öffne Chrome, navigiere zu https://example.com, fülle Login-Formular aus, lade Bericht herunter.",
     "category": "web_automation"},
    {"task": "Lese ungelesene E-Mails aus Postfach, speichere Anhänge in Ordner, verschiebe verarbeitete Mails.",
     "category": "email_processing"},
    {"task": "Führe SQL-Query auf Datenbank aus, schreibe Ergebnis-DataTable in Excel-Datei.",
     "category": "database_operations"},
    {"task": "Sende HTTP-POST-Request an REST-API, deserialisiere JSON-Antwort, schreibe Ergebnis in Log.",
     "category": "api_integration"},
    {"task": "Kopiere alle PDF-Dateien aus Eingangsordner in Archivordner, erstelle Verarbeitungslog.",
     "category": "file_operations"},
    {"task": "Hole Queue-Item aus Orchestrator, verarbeite Auftragsdaten, setze TransactionStatus.",
     "category": "orchestrator_queue"},
]


# ─── Strategien ───────────────────────────────────────────────────────────────

def run_baseline(task: str, category: str) -> Dict:
    """Strategie A: Direktgenerierung ohne Repair."""
    from src.generation.generator import XAMLGenerator
    gen    = XAMLGenerator()
    start  = time.perf_counter()
    result = gen.generate(task_description=task, category=category, max_attempts=1)
    elapsed = time.perf_counter() - start
    return {**result, "strategy": "baseline", "elapsed_s": round(elapsed, 2)}


def run_baseline_repair(task: str, category: str) -> Dict:
    """Strategie B: Direktgenerierung + SurgicalRepair."""
    from src.generation.generator import XAMLGenerator
    from src.generation.surgical_repair import SurgicalRepair
    from src.validation.validator import XAMLValidator

    gen    = XAMLGenerator()
    repair = SurgicalRepair()
    start  = time.perf_counter()

    result  = gen.generate(task_description=task, category=category, max_attempts=1)
    xaml    = result['xaml_content']
    val     = result['validation']
    wf_name = _extract_workflow_name(xaml)

    fixed_xaml, rep_report, val_after = repair.repair_and_validate(xaml, wf_name)
    elapsed = time.perf_counter() - start

    return {
        **result,
        "strategy":        "baseline_repair",
        "xaml_content":    fixed_xaml,
        "score":           val_after['total_score'],
        "validation":      val_after,
        "repair_report":   rep_report,
        "elapsed_s":       round(elapsed, 2),
    }


def run_two_phase(task: str, category: str) -> Dict:
    """Strategie C: Zwei-Phasen-Generator ohne Repair."""
    from src.generation.two_phase_generator import TwoPhaseGenerator
    gen    = TwoPhaseGenerator()
    start  = time.perf_counter()
    result = gen.generate(task_description=task, category=category)
    elapsed = time.perf_counter() - start
    return {**result, "strategy": "two_phase", "elapsed_s": round(elapsed, 2)}


def run_two_phase_repair(task: str, category: str) -> Dict:
    """Strategie D: Zwei-Phasen-Generator + SurgicalRepair (vollständiges System)."""
    from src.generation.two_phase_generator import TwoPhaseGenerator
    from src.generation.surgical_repair import SurgicalRepair

    gen    = TwoPhaseGenerator()
    repair = SurgicalRepair()
    start  = time.perf_counter()

    result  = gen.generate(task_description=task, category=category)
    xaml    = result['xaml_content']
    val     = result['validation']
    wf_name = _extract_workflow_name(xaml)

    fixed_xaml, rep_report, val_after = repair.repair_and_validate(xaml, wf_name)
    elapsed = time.perf_counter() - start

    return {
        **result,
        "strategy":        "two_phase_repair",
        "xaml_content":    fixed_xaml,
        "score":           val_after['total_score'],
        "validation":      val_after,
        "repair_report":   rep_report,
        "elapsed_s":       round(elapsed, 2),
    }


STRATEGY_MAP = {
    "baseline":       run_baseline,
    "baseline_repair": run_baseline_repair,
    "two_phase":      run_two_phase,
    "two_phase_repair": run_two_phase_repair,
}

STRATEGY_LABELS = {
    "baseline":        "A) Baseline (direkt)",
    "baseline_repair": "B) Baseline + Repair",
    "two_phase":       "C) Zwei-Phasen",
    "two_phase_repair":"D) Zwei-Phasen + Repair ★",
}


# ─── Benchmark-Runner ─────────────────────────────────────────────────────────

def run_benchmark(
    tasks: List[Dict],
    strategies: List[str],
    output_dir: str = "results",
) -> Dict:
    """Führt alle Strategien auf allen Aufgaben aus und gibt Ergebnisse zurück."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    all_results: Dict[str, List[Dict]] = {s: [] for s in strategies}
    total = len(tasks) * len(strategies)
    done  = 0

    for task_item in tasks:
        task     = task_item.get("task", task_item) if isinstance(task_item, dict) else task_item
        category = task_item.get("category", None) if isinstance(task_item, dict) else None

        for strategy in strategies:
            done += 1
            logger.info("[%d/%d] %s | %s...", done, total, strategy, task[:50])
            try:
                fn     = STRATEGY_MAP[strategy]
                result = fn(task, category)
                all_results[strategy].append({
                    "task":        task,
                    "category":    result.get('category', category),
                    "score":       result.get('score', 0),
                    "elapsed_s":   result.get('elapsed_s', 0),
                    "passed":      result.get('score', 0) >= 70,
                    "component_scores": result.get('validation', {}).get(
                        'component_scores', {}),
                    "repairs":     len(result.get('repair_report', {}).get('applied', [])),
                    "phase1_tokens": result.get('phase1_tokens', 0),
                })
            except Exception as e:
                logger.error("Strategie %s Aufgabe '%s' fehlgeschlagen: %s",
                             strategy, task[:40], e)
                all_results[strategy].append({
                    "task": task, "score": 0, "error": str(e), "passed": False,
                })

    # Aggregat-Statistiken
    summary: Dict = {"timestamp": ts, "strategies": {}}
    for strategy, results in all_results.items():
        scores   = [r['score'] for r in results if 'error' not in r]
        elapsed  = [r.get('elapsed_s', 0) for r in results if 'error' not in r]
        n_passed = sum(1 for r in results if r.get('passed', False))

        comp_agg: Dict[str, List[float]] = {}
        for r in results:
            for comp, val in r.get('component_scores', {}).items():
                comp_agg.setdefault(comp, []).append(val)

        summary["strategies"][strategy] = {
            "label":        STRATEGY_LABELS.get(strategy, strategy),
            "n":            len(results),
            "n_errors":     sum(1 for r in results if 'error' in r),
            "avg_score":    round(statistics.mean(scores), 1) if scores else 0,
            "std_score":    round(statistics.stdev(scores), 1) if len(scores) > 1 else 0,
            "pass_rate":    round(n_passed / len(results) * 100, 1) if results else 0,
            "avg_elapsed_s": round(statistics.mean(elapsed), 1) if elapsed else 0,
            "component_avgs": {
                comp: round(statistics.mean(vals), 1)
                for comp, vals in comp_agg.items()
            },
            "raw_results":  results,
        }

    # Datei speichern
    out_file = Path(output_dir) / f"benchmark_{ts}.json"
    out_file.write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                        encoding='utf-8')
    logger.info("Ergebnisse gespeichert: %s", out_file)

    _print_summary_table(summary)
    return summary


# ─── Ausgabe ──────────────────────────────────────────────────────────────────

def _print_summary_table(summary: Dict) -> None:
    """Druckt eine lesbare ASCII-Tabelle für die Konsole / Thesis-Appendix."""
    strats = summary.get("strategies", {})
    if not strats:
        return

    header = (
        f"\n{'Strategie':<30} {'n':>4} {'avg':>6} {'std':>6} "
        f"{'pass%':>6} {'t/s':>6} {'struct':>7} {'log':>6} {'sec':>6}"
    )
    sep = "─" * len(header)
    print(f"\n{sep}\n BENCHMARK-ERGEBNISSE\n{sep}")
    print(header)
    print(sep)

    for strat_id, s in strats.items():
        comp = s.get("component_avgs", {})
        print(
            f"{s['label']:<30} "
            f"{s['n']:>4} "
            f"{s['avg_score']:>6.1f} "
            f"{s['std_score']:>6.1f} "
            f"{s['pass_rate']:>5.1f}% "
            f"{s['avg_elapsed_s']:>6.1f} "
            f"{comp.get('structure', 0):>6.1f} "
            f"{comp.get('logging', 0):>6.1f} "
            f"{comp.get('security', 0):>6.1f}"
        )

    print(sep)
    print(
        "Spalten: n=Stichprobengröße, avg=mittlerer Score, std=Standardabw.,\n"
        "pass%=Anteil ≥70 Punkte, t/s=mittlere Laufzeit (Sekunden),\n"
        "struct/log/sec=komponentenweise Scores (Struktur/Logging/Sicherheit)\n"
    )

    # Verbesserungen relativ zur Baseline
    if "baseline" in strats and len(strats) > 1:
        baseline_avg = strats["baseline"]["avg_score"]
        print("Verbesserung gegenüber Baseline:")
        for strat_id, s in strats.items():
            if strat_id == "baseline":
                continue
            delta = s["avg_score"] - baseline_avg
            print(f"  {s['label']:<28}: {delta:+.1f} Punkte")
    print(sep + "\n")


def _extract_workflow_name(xaml: str) -> str:
    """Extrahiert x:Class-Namen aus XAML für die Repair-Methode."""
    import re
    m = re.search(r'x:Class="[^.]*\.([^"]+)"', xaml)
    if m:
        return m.group(1)
    return "Workflow"


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="XAML-Generierungsbenchmark: Vier Strategien im Vergleich",
    )
    parser.add_argument("--tasks",    type=str, help="Textdatei mit Aufgaben (eine pro Zeile)")
    parser.add_argument("--n",        type=int, default=None,
                        help="Max. Anzahl Aufgaben (None = alle)")
    parser.add_argument("--strategy", type=str, nargs="+",
                        choices=list(STRATEGY_MAP.keys()),
                        default=list(STRATEGY_MAP.keys()),
                        help="Strategien (Standard: alle vier)")
    parser.add_argument("--output",   type=str, default="results",
                        help="Ausgabeverzeichnis")
    parser.add_argument("--quick",    action="store_true",
                        help="Schnell-Test: 5 Aufgaben, alle Strategien")
    args = parser.parse_args()

    # Aufgaben laden
    if args.quick:
        tasks     = DEFAULT_TASKS[:5]
        strategies = list(STRATEGY_MAP.keys())
    elif args.tasks:
        task_file = Path(args.tasks)
        if not task_file.exists():
            logger.error("Aufgaben-Datei nicht gefunden: %s", args.tasks)
            sys.exit(1)
        lines  = [l.strip() for l in task_file.read_text().splitlines()
                  if l.strip() and not l.startswith('#')]
        tasks  = [{"task": l, "category": None} for l in lines]
        tasks  = tasks[:args.n] if args.n else tasks
        strategies = args.strategy
    else:
        tasks      = DEFAULT_TASKS[:args.n] if args.n else DEFAULT_TASKS
        strategies = args.strategy

    logger.info(
        "Benchmark: %d Aufgaben × %d Strategien = %d Läufe",
        len(tasks), len(strategies), len(tasks) * len(strategies),
    )
    run_benchmark(tasks, strategies, output_dir=args.output)


if __name__ == "__main__":
    main()
