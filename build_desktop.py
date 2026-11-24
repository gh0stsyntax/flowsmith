"""
build_desktop.py
Erstellt eine eigenständige Desktop-App (.exe / .app / Linux-Binary).

Verwendung:
    python build_desktop.py              # aktuelle Plattform
    python build_desktop.py --onefile    # alles in einer einzigen Datei (langsamer Start)
    python build_desktop.py --debug      # Konsole sichtbar (für Fehlersuche)

Voraussetzungen:
    pip install pyinstaller pywebview pystray pillow

Ausgabe:
    dist/XAMLGenerator/          ← Ordner mit App + allen Abhängigkeiten
    dist/XAMLGenerator.exe       ← nur bei --onefile (Windows)
    dist/XAMLGenerator.app/      ← macOS

Wichtig nach dem Build:
    - Ollama muss separat installiert sein (https://ollama.ai)
    - config/config.yaml muss neben der .exe liegen oder im App-Bundle sein
    - data/, models/, output/ werden beim ersten Start automatisch erstellt
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def build(onefile: bool = False, debug: bool = False) -> None:
    system = platform.system()  # Windows / Darwin / Linux

    # ── Daten-Dateien die mitgeliefert werden ────────────────────────────────
    # Format: "Quelle;Ziel-im-Bundle"
    datas = [
        f"{ROOT / 'ui'};ui",
        f"{ROOT / 'config'};config",
        f"{ROOT / 'src'};src",
    ]

    # Optionale Template-Verzeichnisse
    for extra in ["config/templates", "data/raw"]:
        p = ROOT / extra
        if p.exists():
            datas.append(f"{p};{extra}")

    # ── Hidden Imports ────────────────────────────────────────────────────────
    hidden_imports = [
        "flask", "flask_cors",
        "requests", "yaml",
        "webview",
        "pystray", "PIL", "PIL.Image", "PIL.ImageDraw",
        "src.api.app",
        "src.generation.generator",
        "src.generation.ollama_client",
        "src.generation.rag_engine",
        "src.generation.two_phase_generator",
        "src.generation.spec_compiler",
        "src.generation.surgical_repair",
        "src.generation.task_parser",
        "src.generation.activity_templates",
        "src.generation.workflow_spec",
        "src.generation.uipath_defaults",
        "src.training.trainer",
        "src.training.preprocessor",
        "src.validation.validator",
        "xml.etree.ElementTree",
        "collections",
        "statistics",
        "hashlib",
    ]

    # ── PyInstaller-Befehl zusammenbauen ─────────────────────────────────────
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", "XAMLGenerator",
        "--icon", str(ROOT / "ui" / "favicon.ico")
            if (ROOT / "ui" / "favicon.ico").exists()
            else "NONE",
        "--clean",
        "--noconfirm",
    ]

    if onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")

    if not debug:
        if system == "Windows":
            cmd.append("--noconsole")   # kein schwarzes Terminalfenster
        elif system == "Darwin":
            cmd.extend(["--windowed"])

    for data in datas:
        cmd.extend(["--add-data", data])

    for hi in hidden_imports:
        cmd.extend(["--hidden-import", hi])

    # Windows: WebView2 Runtime einbinden (falls vorhanden)
    if system == "Windows":
        webview2_dll = Path(sys.prefix) / "Lib" / "site-packages" / "webview" / "lib" / "x64"
        if webview2_dll.exists():
            cmd.extend(["--add-binary", f"{webview2_dll};webview/lib/x64"])

    # Einstiegspunkt
    cmd.append(str(ROOT / "desktop_app.py"))

    print("=" * 60)
    print(f"  XAML Generator Build  ({system})")
    print(f"  Modus: {'--onefile' if onefile else '--onedir'}")
    print(f"  Debug: {debug}")
    print("=" * 60)
    print("Starte Build (kann 2-5 Minuten dauern) ...\n")

    result = subprocess.run(cmd, cwd=ROOT)

    if result.returncode == 0:
        dist = ROOT / "dist" / "XAMLGenerator"
        if system == "Windows":
            exe = (ROOT / "dist" / "XAMLGenerator.exe")
            target = exe if (onefile and exe.exists()) else dist
        elif system == "Darwin":
            target = ROOT / "dist" / "XAMLGenerator.app"
        else:
            target = dist

        print("\n" + "=" * 60)
        print(f"  Build erfolgreich!")
        print(f"  Ausgabe: {target}")
        print("=" * 60)
        print()
        print("Nächste Schritte:")
        print("  1. Sicherstellen dass Ollama installiert ist")
        if not onefile:
            print("  2. config/config.yaml in den dist-Ordner kopieren (falls noch nicht drin)")
        print("  3. App starten:")
        if system == "Windows":
            print(f"     {target / 'XAMLGenerator.exe' if not onefile else target}")
        elif system == "Darwin":
            print(f"     open {target}")
        else:
            print(f"     {target / 'XAMLGenerator' if not onefile else target}")
    else:
        print(f"\n  Build fehlgeschlagen (Exit-Code {result.returncode})")
        print("  Häufige Ursache: pip install pyinstaller pywebview pystray pillow")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--onefile", action="store_true",
                        help="Alles in eine einzige Datei packen")
    parser.add_argument("--debug", action="store_true",
                        help="Konsolfenster sichtbar lassen")
    args = parser.parse_args()
    build(onefile=args.onefile, debug=args.debug)
