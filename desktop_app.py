"""
desktop_app.py  –  XAML ML Generator Desktop App
Startet alles (Ollama, Flask-API, Web-UI) in einem einzigen Prozess.
Öffnet ein natives Desktop-Fenster via pywebview.

Starten:
    python desktop_app.py

Paketieren als .exe (Windows) oder .app (macOS):
    python build_desktop.py

Abhängigkeiten (zusätzlich zu requirements.txt):
    pip install pywebview pystray pillow

Architektur:
    ┌─────────────────────────────────┐
    │  desktop_app.py (Hauptprozess)  │
    │  ┌──────────┐  ┌─────────────┐  │
    │  │ Flask API│  │ HTTP Server │  │
    │  │  :5000   │  │   :7860     │  │
    │  └──────────┘  └─────────────┘  │
    │  ┌──────────────────────────┐   │
    │  │   pywebview Window        │   │
    │  │   → localhost:7860        │   │
    │  └──────────────────────────┘   │
    │  ┌──────────────────────────┐   │
    │  │   pystray System-Tray    │   │
    │  └──────────────────────────┘   │
    └─────────────────────────────────┘
    + Ollama-Prozess (subprocess, extern)
"""

from __future__ import annotations

import os
import sys
import time
import threading
import subprocess
import logging
import webbrowser
import socket
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler

# ── Projektroot in sys.path ───────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("desktop")

# ── Ports ─────────────────────────────────────────────────────────────────────
API_PORT = 5000
UI_PORT  = 7860
UI_URL   = f"http://localhost:{UI_PORT}"

# ── Globaler Zustand ──────────────────────────────────────────────────────────
_api_ready    = threading.Event()
_ollama_ready = threading.Event()
_shutdown     = threading.Event()
_window       = None   # pywebview-Window (wird in main gesetzt)


# ─── Hilfsfunktionen ──────────────────────────────────────────────────────────

def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    """Prüft ob ein TCP-Port erreichbar ist."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _create_directories() -> None:
    """Stellt sicher dass alle benötigten Verzeichnisse existieren."""
    for d in [
        "data/raw", "data/processed", "data/training",
        "models/fine_tuned", "models/checkpoints",
        "output/xaml", "logs", "results",
    ]:
        Path(d).mkdir(parents=True, exist_ok=True)


# ─── Ollama ───────────────────────────────────────────────────────────────────

def _start_ollama() -> None:
    """Startet Ollama falls nicht bereits laufend. Läuft in eigenem Thread."""
    import requests

    def check() -> bool:
        try:
            r = requests.get("http://localhost:11434/api/tags", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    if check():
        logger.info("Ollama  bereits aktiv")
        _ollama_ready.set()
        return

    logger.info("Ollama  wird gestartet ...")
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        logger.error("Ollama nicht gefunden. Installiere von https://ollama.ai")
        _ollama_ready.set()   # Trotzdem fortfahren (degradierter Betrieb)
        return

    for i in range(20):
        time.sleep(1.5)
        if check():
            logger.info("Ollama  bereit")
            _ollama_ready.set()
            return

    logger.warning("Ollama antwortet nicht – System läuft ohne Modell")
    _ollama_ready.set()


# ─── Flask API ────────────────────────────────────────────────────────────────

def _start_api() -> None:
    """Startet die Flask REST-API in einem Daemon-Thread."""
    # Warten bis Ollama bereit ist (max. 30 s)
    _ollama_ready.wait(timeout=30)

    if _port_open(API_PORT):
        logger.info("API     bereits aktiv auf :%d", API_PORT)
        _api_ready.set()
        return

    try:
        from src.api.app import create_app
        app = create_app()
        logger.info("API     startet auf :%d", API_PORT)
        _api_ready.set()
        app.run(
            host="127.0.0.1",
            port=API_PORT,
            debug=False,
            use_reloader=False,
            threaded=True,
        )
    except Exception as e:
        logger.error("API     Fehler: %s", e)
        _api_ready.set()   # Fortfahren damit UI nicht ewig wartet


# ─── Web-UI HTTP-Server ───────────────────────────────────────────────────────

class _UIHandler(SimpleHTTPRequestHandler):
    """Stiller Static-File-Server für ui/."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "ui"), **kwargs)

    def log_message(self, fmt, *args):
        pass  # kein Zugriffs-Log

    def do_GET(self):
        target = ROOT / "ui" / self.path.lstrip("/")
        if self.path == "/" or not target.exists():
            self.path = "/index.html"
        super().do_GET()


def _start_ui_server() -> None:
    """Startet den UI-HTTP-Server. Blockiert bis _shutdown gesetzt."""
    if _port_open(UI_PORT):
        logger.info("UI      bereits aktiv auf :%d", UI_PORT)
        return
    server = HTTPServer(("127.0.0.1", UI_PORT), _UIHandler)
    logger.info("UI      startet auf :%d", UI_PORT)
    while not _shutdown.is_set():
        server.handle_request()
    server.server_close()


# ─── Splash / Lade-Screen ─────────────────────────────────────────────────────

_SPLASH_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    background: #1B2230;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    height: 100vh;
    font-family: 'IBM Plex Sans', 'Segoe UI', sans-serif;
    color: #E8EEF8;
    user-select: none;
  }
  .logo {
    width: 64px; height: 64px;
    background: #C95F1A;
    border-radius: 14px;
    display: flex; align-items: center; justify-content: center;
    margin-bottom: 28px;
    font-size: 28px;
  }
  h1 { font-size: 22px; font-weight: 600; margin-bottom: 8px; letter-spacing: 0.02em; }
  h1 span { color: #C95F1A; }
  .sub { font-size: 13px; color: #6B7A94; margin-bottom: 40px; }
  .bar-wrap {
    width: 260px; height: 4px;
    background: rgba(255,255,255,0.08);
    border-radius: 99px; overflow: hidden;
  }
  .bar {
    height: 100%; width: 0%;
    background: #C95F1A;
    border-radius: 99px;
    animation: load 3.5s ease-in-out forwards;
  }
  @keyframes load {
    0%   { width:  0%; }
    20%  { width: 25%; }
    50%  { width: 55%; }
    75%  { width: 78%; }
    95%  { width: 92%; }
    100% { width: 100%; }
  }
  .status {
    margin-top: 20px;
    font-size: 12px;
    font-family: 'IBM Plex Mono', monospace;
    color: #4A5568;
    min-height: 18px;
    letter-spacing: 0.04em;
  }
  .dots::after {
    content: '';
    animation: dots 1.2s steps(3, end) infinite;
  }
  @keyframes dots {
    0%   { content: ''; }
    33%  { content: '.'; }
    66%  { content: '..'; }
    100% { content: '...'; }
  }
</style>
</head>
<body>
  <div class="logo">⚡</div>
  <h1>XAML <span>Generator</span></h1>
  <div class="sub">UiPath ML System · Nico Vogel · HS Albstadt</div>
  <div class="bar-wrap"><div class="bar"></div></div>
  <div class="status"><span class="dots">Starte Dienste</span></div>
</body>
</html>"""


# ─── System-Tray ──────────────────────────────────────────────────────────────

def _build_tray_icon():
    """Erstellt pystray-Icon mit Kontext-Menü. Gibt None zurück wenn pystray fehlt."""
    try:
        import pystray
        from PIL import Image, ImageDraw

        # Einfaches Icon (orange Quadrat mit weißem Blitz)
        size = 64
        img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([4, 4, size-4, size-4], radius=12, fill="#C95F1A")
        draw.polygon([(38,10),(24,34),(34,34),(26,54),(44,28),(34,28)], fill="white")

        def on_open(_icon, _item):
            global _window
            if _window:
                try:
                    _window.show()
                    _window.restore()
                except Exception:
                    webbrowser.open(UI_URL)
            else:
                webbrowser.open(UI_URL)

        def on_browser(_icon, _item):
            webbrowser.open(UI_URL)

        def on_quit(_icon, _item):
            _shutdown.set()
            _icon.stop()
            global _window
            if _window:
                try:
                    _window.destroy()
                except Exception:
                    pass

        menu = pystray.Menu(
            pystray.MenuItem("XAML Generator öffnen", on_open, default=True),
            pystray.MenuItem("Im Browser öffnen",     on_browser),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("API: localhost:5000",    lambda *_: webbrowser.open("http://localhost:5000/health")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Beenden",                on_quit),
        )
        return pystray.Icon(
            "xaml_generator",
            img,
            "XAML Generator",
            menu,
        )
    except ImportError:
        logger.warning("pystray/pillow nicht installiert – kein System-Tray")
        return None


# ─── Hauptfenster ─────────────────────────────────────────────────────────────

def _wait_and_navigate(window) -> None:
    """
    Wartet bis API bereit ist, navigiert dann zur echten UI.
    Läuft in einem Hintergrund-Thread.
    """
    _api_ready.wait(timeout=60)
    time.sleep(0.5)   # kurze Pause damit der Browser die Seite rendert

    logger.info("Navigiere zur UI: %s", UI_URL)
    try:
        window.load_url(UI_URL)
    except Exception as e:
        logger.warning("Navigation fehlgeschlagen: %s", e)


def _run_desktop_app() -> None:
    """Startet alle Dienste und öffnet das pywebview-Fenster."""
    global _window

    try:
        import webview
    except ImportError:
        # Fallback: normaler Browser
        logger.warning("pywebview nicht installiert – öffne im Browser")
        _ollama_ready.wait(timeout=40)
        _api_ready.wait(timeout=40)
        webbrowser.open(UI_URL)
        _shutdown.wait()
        return

    # Hintergrund-Dienste starten
    threads = [
        threading.Thread(target=_start_ollama,    daemon=True, name="ollama"),
        threading.Thread(target=_start_api,        daemon=True, name="flask-api"),
        threading.Thread(target=_start_ui_server,  daemon=True, name="ui-server"),
    ]
    for t in threads:
        t.start()

    # System-Tray (nicht-blockierend)
    tray = _build_tray_icon()
    if tray:
        threading.Thread(target=tray.run, daemon=True, name="tray").start()

    # Fenster erstellen (Splash-Screen zunächst)
    _window = webview.create_window(
        title   = "XAML Generator — UiPath ML System",
        html    = _SPLASH_HTML,
        width   = 1280,
        height  = 820,
        min_size= (900, 600),
        resizable   = True,
        on_top      = False,
        background_color = "#1B2230",
    )

    # Navigation zur echten UI sobald API bereit
    threading.Thread(
        target=_wait_and_navigate,
        args=(_window,),
        daemon=True,
        name="navigator",
    ).start()

    # Blockiert bis Fenster geschlossen
    webview.start(
        debug    = False,
        http_server = False,
    )

    # Aufräumen nach Fenster-Schließen
    _shutdown.set()
    if tray:
        try:
            tray.stop()
        except Exception:
            pass
    logger.info("Desktop-App beendet.")


# ─── Einstiegspunkt ───────────────────────────────────────────────────────────

def main() -> None:
    _create_directories()

    logger.info("=" * 50)
    logger.info("  XAML Generator Desktop App")
    logger.info("  Web UI  → %s", UI_URL)
    logger.info("  API     → http://localhost:%d", API_PORT)
    logger.info("=" * 50)

    _run_desktop_app()


if __name__ == "__main__":
    main()
