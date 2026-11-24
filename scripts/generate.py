import sys
import os
import argparse
import logging
from pathlib import Path

os.chdir(Path(__file__).parent.parent)
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description='UiPath XAML Generator')
    parser.add_argument('--task', type=str, help='Aufgabenbeschreibung')
    parser.add_argument('--category', type=str, help='Kategorie (optional)')
    parser.add_argument('--requirements', type=str, help='Zusätzliche Anforderungen')
    parser.add_argument('--output', type=str, help='Ausgabepfad für XAML')
    parser.add_argument('--attempts', type=int, default=3, help='Max. Versuche')
    parser.add_argument('--validate', action='store_true', help='Validierung anzeigen')
    parser.add_argument('--llm-review', action='store_true', help='LLM-Review aktivieren')
    parser.add_argument('--batch', type=str, help='Datei mit Aufgaben (eine pro Zeile)')
    parser.add_argument('--quiet', action='store_true', help='Nur XAML ausgeben')
    args = parser.parse_args()

    if not args.task and not args.batch:
        parser.print_help()
        sys.exit(1)

    from src.generation.generator import XAMLGenerator
    from src.validation.validator import XAMLValidator
    
    generator = XAMLGenerator()
    validator = XAMLValidator() if args.validate else None

    def process_task(task: str):
        if not args.quiet:
            logger.info(f"🎯 Generiere: {task[:80]}...")
        
        result = generator.generate(
            task_description=task,
            category=args.category,
            additional_requirements=args.requirements,
            max_attempts=args.attempts
        )
        
        if not args.quiet:
            score = result['score']
            emoji = "✅" if result['validation']['passed'] else "⚠️"
            logger.info(f"{emoji} Score: {score}/100 | Versuche: {result['attempt']}")
        
        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(result['xaml_content'])
            if not args.quiet:
                logger.info(f"💾 Gespeichert: {args.output}")
        elif args.quiet:
            print(result['xaml_content'])
        else:
            logger.info(f"💾 Gespeichert: {result.get('output_path')}")
        
        if args.validate and not args.quiet:
            val = result['validation']
            print("\n─── Validierungs-Report ───")
            if val['errors']:
                for e in val['errors']:
                    print(f"❌ {e}")
            if val['warnings']:
                for w in val['warnings'][:5]:
                    print(f"⚠️  {w}")
            if result.get('suggestions'):
                for s in result['suggestions'][:3]:
                    print(f"💡 {s}")
        
        return result

    # Einzel-Task
    if args.task:
        process_task(args.task)
    
    # Batch-Modus
    elif args.batch:
        batch_file = Path(args.batch)
        if not batch_file.exists():
            logger.error(f"Datei nicht gefunden: {args.batch}")
            sys.exit(1)
        
        tasks = [line.strip() for line in batch_file.read_text().splitlines() 
                 if line.strip() and not line.startswith('#')]
        
        logger.info(f"📦 Batch-Modus: {len(tasks)} Aufgaben")
        
        for i, task in enumerate(tasks, 1):
            logger.info(f"\n[{i}/{len(tasks)}] {task[:60]}...")
            try:
                process_task(task)
            except Exception as e:
                logger.error(f"❌ Fehler: {e}")


if __name__ == '__main__':
    main()
