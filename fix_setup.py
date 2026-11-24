"""
fix_setup.py
Diagnose und automatische Korrektur aller häufigen Setup-Probleme.
Ausführen aus dem Projektverzeichnis (xaml_ml\):
  python fix_setup.py
"""
import sys
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
print(f"Projektverzeichnis: {ROOT}\n")

ERRORS   = []
WARNINGS = []
FIXED    = []

# ─── 1. Python-Version ────────────────────────────────────────────────────────
major, minor = sys.version_info[:2]
print(f"Python {major}.{minor}", end=" ")
if major == 3 and minor >= 10:
    print("✓")
else:
    print("⚠ empfohlen: Python 3.10+")
    WARNINGS.append(f"Python {major}.{minor} — empfohlen 3.10+")

# ─── 2. __init__.py Dateien ───────────────────────────────────────────────────
print("\n── __init__.py Dateien ──")
init_paths = [
    ROOT / "src" / "__init__.py",
    ROOT / "src" / "generation" / "__init__.py",
    ROOT / "src" / "training"   / "__init__.py",
    ROOT / "src" / "validation" / "__init__.py",
    ROOT / "src" / "api"        / "__init__.py",
]
for p in init_paths:
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text("", encoding="utf-8")
        print(f"  Erstellt: {p.relative_to(ROOT)}")
        FIXED.append(str(p.relative_to(ROOT)))
    else:
        print(f"  OK: {p.relative_to(ROOT)}")

# ─── 3. Pflicht-Quelldateien ──────────────────────────────────────────────────
print("\n── Quelldateien ──")
REQUIRED = {
    "src/generation/rag_engine.py":        "RAG-Engine (TF-IDF Retrieval)",
    "src/generation/generator.py":         "Haupt-Generator",
    "src/generation/ollama_client.py":     "Ollama API-Client",
    "src/generation/activity_templates.py":"Aktivitäts-Templates",
    "src/generation/uipath_defaults.py":   "UiPath Namespace-Defaults",
    "src/generation/workflow_spec.py":     "Workflow-Spezifikation",
    "src/generation/task_parser.py":       "Task-Parser",
    "src/generation/spec_compiler.py":     "XAML-Compiler (Zwei-Phasen)",
    "src/generation/two_phase_generator.py":"Zwei-Phasen-Generator",
    "src/generation/surgical_repair.py":   "Chirurgische Reparatur",
    "src/training/trainer.py":             "Training-Pipeline",
    "src/training/preprocessor.py":        "Daten-Preprocessor",
    "src/training/pair_builder.py":        "Pseudo-Pair-Builder",
    "src/training/pairs_to_finetune.py":   "Pairs-zu-Finetune",
    "src/validation/validator.py":         "XAML-Validator",
    "src/api/app.py":                      "Flask REST-API",
    "config/config.yaml":                  "Hauptkonfiguration",
    "scripts/benchmark.py":                "Ablationsstudie-Benchmark",
    "scripts/generate_figures.py":         "Abbildungs-Generator",
    "scripts/collect_results.py":          "Ergebnis-Sammler",
    "desktop_app.py":                      "Desktop-App",
}

missing = []
for rel, desc in REQUIRED.items():
    p = ROOT / rel
    if p.exists():
        print(f"  OK: {rel}")
    else:
        print(f"  FEHLT: {rel}  ({desc})")
        ERRORS.append(f"Datei fehlt: {rel}")
        missing.append(rel)

# ─── 4. Config.yaml prüfen ────────────────────────────────────────────────────
print("\n── config.yaml ──")
config_path = ROOT / "config" / "config.yaml"
if config_path.exists():
    import yaml
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    model = cfg.get("ollama", {}).get("primary_model", "?")
    print(f"  primary_model: {model}")
    if "qwen3" in model or "deepseek" in model:
        print("  Modell-Konfiguration: ✓")
    else:
        print(f"  ⚠ Empfohlen: qwen3:14b oder qwen3:8b")
        WARNINGS.append(f"primary_model={model} — empfohlen qwen3:14b")
else:
    print("  FEHLT — wird erstellt...")
    config_path.parent.mkdir(exist_ok=True)
    config_path.write_text("""\
ollama:
  base_url: "http://localhost:11434"
  primary_model: "qwen3:14b"
  finetuned_model: "xaml-specialist:latest"
  generation_params:
    temperature: 0.2
    top_p: 0.9
    top_k: 40
    num_predict: 4096
    repeat_penalty: 1.1
    thinking: false
    num_ctx: 4096
    seed: 42

generation:
  output_dir: "output/xaml"
  max_attempts: 3
  templates:
    template_dir: "config/templates"
  rag:
    enabled: true
    top_k: 3
    similarity_threshold: 0.15
  post_processing:
    auto_format: true

validation:
  pass_threshold: 70
  llm_review_threshold: 7

training:
  output_dir: "models/fine_tuned"
  checkpoint_dir: "models/checkpoints"
  preprocessing:
    min_xaml_size_bytes: 500
    max_xaml_size_bytes: 500000
    quality_threshold: 70
  fine_tuning:
    base_model: "qwen3:14b"
    max_seq_length: 4096
    batch_size: 2
    epochs: 3
    learning_rate: 0.0002

api:
  host: "127.0.0.1"
  port: 5000
  debug: false

ui:
  port: 7860
""", encoding="utf-8")
    FIXED.append("config/config.yaml")
    print("  config.yaml erstellt ✓")

# ─── 5. Verzeichnisse ─────────────────────────────────────────────────────────
print("\n── Verzeichnisse ──")
dirs = [
    "data/raw", "data/processed", "data/training",
    "models/fine_tuned", "models/checkpoints",
    "output/xaml", "logs", "results", "figures",
    "config/templates",
]
for d in dirs:
    p = ROOT / d
    p.mkdir(parents=True, exist_ok=True)
    print(f"  OK: {d}/")

# ─── 6. Ollama-Verbindung testen ──────────────────────────────────────────────
print("\n── Ollama ──")
try:
    import requests
    r = requests.get("http://localhost:11434/api/tags", timeout=5)
    if r.status_code == 200:
        models = [m["name"] for m in r.json().get("models", [])]
        print(f"  Verbunden ✓  Verfügbare Modelle: {models}")
        if not models:
            print("  ⚠ Kein Modell geladen. Ausführen: ollama pull qwen3:14b")
            WARNINGS.append("Kein Ollama-Modell verfügbar")
    else:
        print(f"  Fehler: HTTP {r.status_code}")
        ERRORS.append("Ollama antwortet mit Fehler")
except Exception as e:
    print(f"  Nicht erreichbar: {e}")
    print("  → Ollama starten: ollama serve")
    print("  → Download: https://ollama.ai")
    WARNINGS.append("Ollama nicht gestartet")

# ─── 7. Python-Pakete testen ──────────────────────────────────────────────────
print("\n── Python-Pakete ──")
PACKAGES = {
    "flask":      "flask",
    "flask_cors": "flask-cors",
    "yaml":       "pyyaml",
    "requests":   "requests",
    "dotenv":     "python-dotenv",
}
missing_pkgs = []
for imp, pkg in PACKAGES.items():
    try:
        __import__(imp)
        print(f"  OK: {pkg}")
    except ImportError:
        print(f"  FEHLT: {pkg}  → pip install {pkg}")
        missing_pkgs.append(pkg)
        ERRORS.append(f"Paket fehlt: {pkg}")

if missing_pkgs:
    print(f"\n  Alle fehlenden Pakete installieren:")
    print(f"  pip install {' '.join(missing_pkgs)}")

# ─── 8. Import-Test ──────────────────────────────────────────────────────────
print("\n── Import-Test ──")
sys.path.insert(0, str(ROOT))

test_imports = [
    ("src.generation.ollama_client",  "OllamaClient"),
    ("src.generation.rag_engine",     "RAGEngine"),
    ("src.generation.generator",      "XAMLGenerator"),
    ("src.generation.spec_compiler",  "SpecCompiler"),
    ("src.generation.surgical_repair","SurgicalRepair"),
    ("src.validation.validator",      "XAMLValidator"),
    ("src.training.trainer",          "OllamaTrainer"),
    ("src.training.preprocessor",     "XAMLPreprocessor"),
]
for module, cls in test_imports:
    try:
        mod = __import__(module, fromlist=[cls])
        getattr(mod, cls)
        print(f"  OK: {module}.{cls}")
    except ImportError as e:
        err = str(e)
        print(f"  FEHLT: {module}  ({err})")
        ERRORS.append(f"Import-Fehler: {module}: {err}")
    except Exception as e:
        print(f"  ⚠ Warnung: {module}  ({type(e).__name__}: {e})")

# ─── 9. Zusammenfassung ───────────────────────────────────────────────────────
print("\n" + "═" * 55)
print("  ERGEBNIS")
print("═" * 55)
if FIXED:
    print(f"\n✓ Automatisch behoben ({len(FIXED)}):")
    for f in FIXED: print(f"    {f}")

if ERRORS:
    print(f"\n✗ Kritische Fehler ({len(ERRORS)}) — bitte manuell beheben:")
    for e in ERRORS: print(f"    {e}")

if WARNINGS:
    print(f"\n⚠ Warnungen ({len(WARNINGS)}):")
    for w in WARNINGS: print(f"    {w}")

if not ERRORS and not WARNINGS:
    print("\n🎉 Alles in Ordnung — System startbereit!")
    print("   python desktop_app.py")
elif not ERRORS:
    print("\n✓ Keine kritischen Fehler.")
    print("  Warnungen sind optional — System kann gestartet werden.")
    print("  python desktop_app.py")
else:
    print(f"\n→ Fehlende Dateien bitte aus dem ZIP-Archiv kopieren.")
    print(f"  Dann fix_setup.py erneut ausführen.")

print("═" * 55)
