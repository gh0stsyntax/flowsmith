# flowsmith — Startanleitung (Kurzfassung, Deutsch)

Die englische Kurzfassung steht im README unter „Quick start“. Diese Datei beschreibt dasselbe etwas ausführlicher.

## Einmalige Installation (ca. 20 Minuten)

### Schritt 1: Ollama installieren
Windows: https://ollama.ai/download → OllamaSetup.exe
macOS:   https://ollama.ai/download → Ollama.dmg
Linux:   curl -fsSL https://ollama.ai/install.sh | sh

Testen: ollama --version

### Schritt 2: Modell laden (je nach RAM)
  16 GB RAM:  ollama pull qwen3:8b
  32 GB RAM:  ollama pull qwen3:14b      (empfohlen)
  32 GB RAM+: ollama pull qwen3:30b-a3b  (beste Qualität)

### Schritt 3: Python-Abhängigkeiten
  pip install -r requirements.txt
  pip install -r requirements_desktop.txt

### Schritt 4: Modell in config.yaml eintragen
  ollama:
    primary_model: "qwen3:14b"

## Täglich starten

  python desktop_app.py     (empfohlen – alles startet automatisch)

  ODER manuell:
  python scripts/start_system.py
  Web UI:  http://localhost:7860
  API:     http://localhost:5000

## Erste Generierung
  1. Prozessbeschreibung eingeben
  2. Kategorie wählen
  3. "Workflow generieren" klicken
  4. Warten (30–120 Sekunden)
  5. XAML + Validierungsbericht erscheinen

## Benchmark (Ablationsstudie)
  python scripts/benchmark.py --quick    # 5 Tasks, erste Zahlen
  python scripts/benchmark.py --n 20    # 20 Tasks, Thesis-Zahlen

## Häufige Probleme
  RAM zu wenig:    ollama pull qwen3:8b  +  config.yaml anpassen
  Port 5000 belegt: config.yaml: api.port: 5001
  Ollama offline:  ollama serve (im Terminal)
