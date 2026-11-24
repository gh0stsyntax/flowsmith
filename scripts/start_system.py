#!/usr/bin/env python3
"""
scripts/start_system.py
Startet das UiPath XAML ML System (REST API + Web UI).
"""

import subprocess
import sys
import os
import time
import threading
import logging
import yaml
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler

# ── Projektroot in sys.path ────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("werkzeug").setLevel(logging.WARNING)
logger = logging.getLogger("xaml_ml")

BANNER = """\
─────────────────────────────────────────────
  UiPath XAML ML System  v1.0.0
  Masterthesis – Nico Vogel – HS Albstadt
─────────────────────────────────────────────"""


# ── Ollama ─────────────────────────────────────────────────────────────────

def _ollama_models():
    """Gibt die Liste installierter Modelle zurueck, [] bei Fehler."""
    try:
        import requests
        r = requests.get("http://localhost:11434/api/tags", timeout=3)
        if r.status_code == 200:
            return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        pass
    return []


def ensure_ollama_running():
    models = _ollama_models()
    if models:
        logger.info("Ollama  ready   models=%s", models)
        return True, models

    logger.info("Ollama  starting ...")
    subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    for attempt in range(10):
        time.sleep(2)
        models = _ollama_models()
        if models:
            logger.info("Ollama  ready   models=%s", models)
            return True, models
        logger.debug("Ollama  waiting  attempt=%d/10", attempt + 1)

    logger.error("Ollama  unreachable  install: https://ollama.ai")
    return False, []


def ensure_model_available(models):
    """
    Bevorzugt ollama.primary_model aus config/config.yaml (exakter Name oder gleicher Basis-Tag).
    Nur wenn das fehlt: Fallback-Reihenfolge mit kleineren Coder-Modellen zuerst (nicht 30B vor 6.7B).
    """
    cfg_path = ROOT / "config" / "config.yaml"
    primary = None
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            primary = yaml.safe_load(f).get("ollama", {}).get("primary_model")
    except Exception as e:
        logger.debug("config yaml: %s", e)

    if primary and models:
        if primary in models:
            logger.info("Model   config  name=%s", primary)
            return True
        base = primary.split(":")[0] if ":" in primary else primary
        for m in models:
            if m == primary or m.startswith(base + ":") or m == base:
                logger.info("Model   config  name=%s (matched %s)", m, primary)
                return True
        logger.warning(
            "Model   primary_model=%s not in ollama list — install with: ollama pull %s",
            primary,
            primary,
        )

    preferred = [
        "deepseek-coder",
        "qwen2.5-coder",
        "qwen2.5",
        "llama3.1",
        "llama",
        "codellama",
        "gemma3",
        "deepseek-r1",
        "qwen3-coder",
        "qwen3",
    ]
    for keyword in preferred:
        match = next((m for m in models if keyword in m), None)
        if match:
            logger.info("Model   fallback  name=%s", match)
            return True

    fallback = "deepseek-coder:6.7b"
    logger.info("Model   pulling   name=%s  size=~4GB", fallback)
    result = subprocess.run(["ollama", "pull", fallback])
    if result.returncode != 0:
        logger.error("Model   pull failed  name=%s", fallback)
        return False
    return True


# ── Verzeichnisse ──────────────────────────────────────────────────────────

def create_directories():
    dirs = [
        "data/raw", "data/processed", "data/training",
        "models/fine_tuned", "models/checkpoints",
        "output/xaml", "logs",
    ]
    for d in dirs:
        Path(d).mkdir(parents=True, exist_ok=True)
    logger.info("Dirs    ok")


# ── API ────────────────────────────────────────────────────────────────────

def start_api():
    """Startet die Flask REST-API im Hintergrund-Thread."""
    try:
        from src.api.app import create_app
        app = create_app()
        app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
    except Exception as e:
        logger.error("API crashed: %s", e)
        import traceback
        traceback.print_exc()


# ── Web UI ─────────────────────────────────────────────────────────────────

class _UIHandler(SimpleHTTPRequestHandler):
    """Statischer Dateiserver – alle Pfade fallen auf ui/index.html zurueck."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "ui"), **kwargs)

    def log_message(self, fmt, *args):
        pass  # request-log unterdrucken

    def do_GET(self):
        target = ROOT / "ui" / self.path.lstrip("/")
        if self.path == "/" or not target.exists():
            self.path = "/index.html"
        super().do_GET()


def start_ui(port=7860, open_browser=True):
    """Blockiert im Hauptthread bis Strg+C."""
    if open_browser:
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    server = HTTPServer(("0.0.0.0", port), _UIHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


# ── main ───────────────────────────────────────────────────────────────────

def main():
    print(BANNER)

    create_directories()

    ollama_ok, models = ensure_ollama_running()
    if not ollama_ok:
        logger.error("Startup aborted  reason=ollama_unavailable")
        sys.exit(1)

    if not ensure_model_available(models):
        logger.error("Startup aborted  reason=no_model_available")
        sys.exit(1)

    api_thread = threading.Thread(target=start_api, daemon=True, name="flask-api")
    api_thread.start()
    time.sleep(1.5)

    logger.info("─" * 45)
    logger.info("Web UI   http://localhost:7860")
    logger.info("REST API http://localhost:5000")
    logger.info("Health   http://localhost:5000/health")
    logger.info("─" * 45)
    logger.info("XAML input   data/raw/")
    logger.info("XAML output  output/xaml/")
    logger.info("Stop         Ctrl+C")
    logger.info("─" * 45)

    try:
        start_ui(port=7860, open_browser=True)
    except KeyboardInterrupt:
        pass

    logger.info("Shutdown complete.")


if __name__ == "__main__":
    main()