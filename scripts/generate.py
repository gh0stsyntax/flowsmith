"""Generate a UiPath workflow (XAML) from a plain-language task with a local LLM (Ollama).

Default strategy is the one verified to build in UiPath Studio: two-phase generation (LLM writes a spec,
a deterministic compiler writes the XAML) followed by targeted repair. `--strategy baseline` keeps the old
direct generation, which the benchmark shows is much weaker.

    python scripts/generate.py --task "Move every PDF from an inbox folder to an archive folder" --output out.xaml --validate
"""
import argparse
import logging
import os
import re
import sys
from pathlib import Path

os.chdir(Path(__file__).parent.parent)
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


def build_parser():
    parser = argparse.ArgumentParser(description='UiPath XAML Generator (local LLM)')
    parser.add_argument('--task', type=str, help='Task description (plain language)')
    parser.add_argument('--category', type=str, help='Category (optional, auto-detected)')
    parser.add_argument('--requirements', type=str, help='Additional requirements')
    parser.add_argument('--output', type=str, help='Write the XAML to this path')
    parser.add_argument('--attempts', type=int, default=3, help='Max. attempts')
    parser.add_argument('--strategy', choices=['two-phase', 'baseline'], default='two-phase',
                        help='two-phase = spec -> compiler (+ repair), default; baseline = direct generation')
    parser.add_argument('--seed', type=int, default=None, help='sampling seed (default: the generator default)')
    parser.add_argument('--no-repair', action='store_true', help='skip the targeted repair step (two-phase only)')
    parser.add_argument('--validate', action='store_true', help='print the validation report')
    parser.add_argument('--llm-review', action='store_true', help='enable LLM review (baseline only)')
    parser.add_argument('--batch', type=str, help='File with tasks (one per line, # = comment)')
    parser.add_argument('--quiet', action='store_true', help='print only the XAML')
    return parser


def generate_two_phase(task, args):
    from src.generation.surgical_repair import SurgicalRepair
    from src.generation.two_phase_generator import TwoPhaseGenerator
    result = (TwoPhaseGenerator(seed=args.seed) if args.seed is not None else TwoPhaseGenerator()).generate(task_description=task, category=args.category,
                                          additional_requirements=args.requirements, max_spec_attempts=args.attempts)
    xaml, validation = result['xaml_content'], result['validation']
    if not args.no_repair:
        name = (re.search(r'x:Class="([^"]+)"', xaml) or [None, 'Workflow'])[1]
        xaml, _report, validation = SurgicalRepair().repair_and_validate(xaml, name)
    return {'xaml_content': xaml, 'validation': validation, 'score': validation.get('total_score', result.get('score', 0)),
            'attempt': 1, 'output_path': result.get('output_path'), 'suggestions': validation.get('suggestions', [])}


def generate_baseline(task, args):
    from src.generation.generator import XAMLGenerator
    return XAMLGenerator().generate(task_description=task, category=args.category,
                                    additional_requirements=args.requirements, max_attempts=args.attempts)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.task and not args.batch:
        parser.print_help()
        return 1

    def process_task(task):
        if not args.quiet:
            logger.info(f"Generating ({args.strategy}): {task[:80]}...")
        result = (generate_two_phase if args.strategy == 'two-phase' else generate_baseline)(task, args)

        if not args.quiet:
            mark = "OK" if result['validation'].get('passed') else "WARN"
            logger.info(f"{mark} score {result['score']}/100 | attempts: {result.get('attempt', 1)}")

        if args.output:
            Path(args.output).write_text(result['xaml_content'], encoding='utf-8')
            if not args.quiet:
                logger.info(f"Saved: {args.output}")
        elif args.quiet:
            print(result['xaml_content'])
        elif result.get('output_path'):
            logger.info(f"Saved: {result['output_path']}")

        if args.validate and not args.quiet:
            val = result['validation']
            print("\n--- Validation report ---")
            for e in val.get('errors', []):
                print(f"ERROR   {e}")
            for w in val.get('warnings', [])[:5]:
                print(f"WARNING {w}")
            for s in (result.get('suggestions') or [])[:3]:
                print(f"HINT    {s}")
        return result

    try:
        if args.task:
            process_task(args.task)
        else:
            batch_file = Path(args.batch)
            if not batch_file.exists():
                logger.error(f"File not found: {args.batch}")
                return 1
            tasks = [l.strip() for l in batch_file.read_text(encoding='utf-8').splitlines()
                     if l.strip() and not l.startswith('#')]
            logger.info(f"Batch mode: {len(tasks)} tasks")
            failed = 0
            for i, task in enumerate(tasks, 1):
                logger.info(f"[{i}/{len(tasks)}] {task[:60]}...")
                try:
                    process_task(task)
                except Exception as e:  # one failing task must not stop the batch
                    failed += 1
                    logger.error(f"Failed: {e}")
            return 1 if failed else 0
    except RuntimeError as e:
        logger.error(f"Generation failed: {e}")
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
