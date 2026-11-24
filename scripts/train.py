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
    parser = argparse.ArgumentParser(description='UiPath XAML ML - Training')
    parser.add_argument('--data-dir', default='data/raw', help='XAML-Eingabeverzeichnis')
    parser.add_argument('--output-dir', default='data/processed', help='Ausgabeverzeichnis')
    parser.add_argument('--model-name', default='xaml-specialist:latest', help='Modell-Name')
    parser.add_argument('--synthetic-only', action='store_true', help='Nur synthetische Daten')
    parser.add_argument('--skip-preprocess', action='store_true', help='Vorverarbeitung überspringen')
    parser.add_argument('--lora', action='store_true', help='LoRA Fine-Tuning starten (braucht GPU)')
    parser.add_argument('--eval', action='store_true', help='Modell evaluieren')
    parser.add_argument('--samples', type=int, default=10, help='Synthetische Samples pro Kategorie')
    args = parser.parse_args()

    logger.info("🏋️ UiPath XAML ML - Training gestartet")
    logger.info("=" * 50)

    # ─── Schritt 1: Synthetische Daten ────────────────────────
    if args.synthetic_only or not Path(args.data_dir).exists() or \
       not list(Path(args.data_dir).rglob("*.xaml")):
        
        logger.info("📊 Generiere synthetische Trainingsdaten...")
        
        from src.generation.ollama_client import OllamaClient
        from src.training.preprocessor import SyntheticDataGenerator
        
        client = OllamaClient()
        if not client.is_available():
            logger.error("❌ Ollama nicht verfügbar! Starte mit: ollama serve")
            sys.exit(1)
        
        generator = SyntheticDataGenerator(client)
        count = generator.generate_samples(
            f"{args.data_dir}/synthetic", 
            args.samples
        )
        logger.info(f"✅ {count} synthetische Samples generiert")

    # ─── Schritt 2: Vorverarbeitung ───────────────────────────
    if not args.skip_preprocess:
        logger.info("\n🔧 Starte Datenvorverarbeitung...")
        
        from src.training.preprocessor import XAMLPreprocessor
        preprocessor = XAMLPreprocessor()
        
        stats = preprocessor.process_directory(args.data_dir, args.output_dir)
        
        logger.info(f"📊 Verarbeitungsstatistik:")
        logger.info(f"   Gesamt:     {stats['total']}")
        logger.info(f"   Verarbeitet:{stats['processed']}")
        logger.info(f"   Fehler:     {stats['errors']}")
        
        if stats['processed'] == 0:
            logger.warning("⚠️ Keine Samples verarbeitet!")
            logger.info("  Lege XAML-Dateien in das Verzeichnis: " + args.data_dir)
            sys.exit(1)
        
        logger.info(f"✅ {stats['processed']} Samples verarbeitet")

    # ─── Schritt 3: LoRA oder Modelfile ──────────────────────
    from src.training.trainer import OllamaTrainer
    trainer = OllamaTrainer()

    if args.lora:
        logger.info("\n🔬 Starte LoRA Fine-Tuning (GPU erforderlich)...")
        success = trainer.fine_tune_with_unsloth(args.output_dir)
        
        if success:
            logger.info("✅ LoRA Fine-Tuning erfolgreich!")
            logger.info("   Modell zu Ollama hinzufügen:")
            logger.info(f"   ollama create {args.model_name} -f models/fine_tuned/xaml-specialist/Modelfile")
        else:
            logger.error("❌ LoRA Fine-Tuning fehlgeschlagen!")
            logger.info("   Fallback auf Modelfile-Ansatz...")
            args.lora = False
    
    if not args.lora:
        logger.info("\n📝 Erstelle Ollama Modelfile...")
        modelfile = trainer.create_modelfile(args.output_dir)
        
        logger.info(f"🏗️ Baue Ollama Modell: {args.model_name}")
        success = trainer.build_model_from_modelfile(modelfile, args.model_name)
        
        if success:
            logger.info(f"✅ Modell '{args.model_name}' erfolgreich erstellt!")
        else:
            logger.error(f"❌ Fehler beim Modell-Build")
            sys.exit(1)

    # ─── Schritt 4: Evaluation ────────────────────────────────
    if args.eval:
        logger.info("\n📏 Evaluiere Modell...")
        results = trainer.evaluate_model(args.model_name, args.output_dir)
        
        logger.info("📊 Evaluierungsergebnisse:")
        logger.info(f"   Gesamt:       {results['total']}")
        logger.info(f"   Bestanden:    {results['passed']}")
        logger.info(f"   Avg Score:    {results['avg_score']:.1f}/100")
        
        if results['by_category']:
            logger.info("\n   Nach Kategorie:")
            for cat, scores in results['by_category'].items():
                avg = sum(scores) / len(scores)
                logger.info(f"   {cat}: {avg:.1f}")

    logger.info("\n🎉 Training abgeschlossen!")
    logger.info(f"   Starte System: python scripts/start_system.py")
    logger.info(f"   Oder generiere XAML: python scripts/generate.py --task 'Deine Aufgabe'")


if __name__ == '__main__':
    main()
