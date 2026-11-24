#!/usr/bin/env python3
"""
scripts/start_system.py
Startet das UiPath XAML ML System (REST API + Web UI).
"""

import argparse
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


def ensure_model_available(models, allow_pull=False):
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
    if not allow_pull:
        # A start script must not silently download ~4 GB. Ask for it explicitly with --pull-model.
        logger.error("Model   none usable  install one yourself (ollama pull %s) or start with --pull-model", fallback)
        return False
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

def start_api(host="127.0.0.1", port=5000):
    """Startet die Flask REST-API im Hintergrund-Thread."""
    try:
        from src.api.app import create_app
        app = create_app()
        app.run(host=host, port=port, debug=False, use_reloader=False)
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


def start_ui(port=7860, open_browser=True, host="127.0.0.1"):
    """Blockiert im Hauptthread bis Strg+C."""
    if open_browser:
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    server = HTTPServer((host, port), _UIHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


# ── main ───────────────────────────────────────────────────────────────────

def build_parser():
    ap = argparse.ArgumentParser(
        description="Start the flowsmith system (REST API on :5000 + web UI on :7860). "
                    "By default everything listens on 127.0.0.1 only.")
    ap.add_argument("--check", action="store_true",
                    help="only check Ollama and the configured model, start nothing, exit 0/1")
    ap.add_argument("--host", default="127.0.0.1",
                    help="interface to listen on (default 127.0.0.1; 0.0.0.0 exposes the unauthenticated API to your network)")
    ap.add_argument("--api-port", type=int, default=5000)
    ap.add_argument("--ui-port", type=int, default=7860)
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    ap.add_argument("--pull-model", action="store_true",
                    help="allow downloading a fallback model (~4 GB) if none is installed")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)          # --help exits here, before anything is started
    print(BANNER)

    if args.check:
        models = _ollama_models()
        if not models:
            logger.error("Check    Ollama not reachable on localhost:11434")
            return 1
        ok = ensure_model_available(models, allow_pull=False)
        logger.info("Check    %s  models=%d", "ok" if ok else "FAILED", len(models))
        return 0 if ok else 1

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning("Host    %s: the API has no authentication - anyone on this network can use it", args.host)

    create_directories()

    ollama_ok, models = ensure_ollama_running()
    if not ollama_ok:
        logger.error("Startup aborted  reason=ollama_unavailable")
        return 1

    if not ensure_model_available(models, allow_pull=args.pull_model):
        logger.error("Startup aborted  reason=no_model_available")
        return 1

    api_thread = threading.Thread(target=start_api, args=(args.host, args.api_port), daemon=True, name="flask-api")
    api_thread.start()
    time.sleep(1.5)

    logger.info("─" * 45)
    logger.info("Web UI   http://localhost:%d", args.ui_port)
    logger.info("REST API http://localhost:%d", args.api_port)
    logger.info("Health   http://localhost:%d/health", args.api_port)
    logger.info("─" * 45)
    logger.info("XAML input   data/raw/")
    logger.info("XAML output  output/xaml/")
    logger.info("Stop         Ctrl+C")
    logger.info("─" * 45)

    try:
        start_ui(port=args.ui_port, open_browser=not args.no_browser, host=args.host)
    except KeyboardInterrupt:
        pass

    logger.info("Shutdown complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())