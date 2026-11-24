"""
scripts/generate_figures.py
Erstellt alle Thesis-Abbildungen als druckfertige PNG-Dateien (300 DPI).
Optimierte Version: Auto-Cleanup, perfekte Abstände, kein Abschneiden der Ränder.
"""

from __future__ import annotations
import argparse
from pathlib import Path
import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch
import numpy as np

OUT = Path("figures")
OUT.mkdir(exist_ok=True)

DPI    = 300
ACCENT = "#C95F1A"
BLUE   = "#2B6CB0"
GREEN  = "#276749"
GRAY   = "#4A5568"
LIGHT  = "#EDF2F7"
WHITE  = "#FFFFFF"
FONT   = "DejaVu Sans"

plt.rcParams.update({
    "font.family": FONT,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.spines.left": False,
    "axes.spines.bottom": False,
})

def save(fig, name: str):
    p = OUT / name
    fig.savefig(p, dpi=DPI, bbox_inches="tight", facecolor=WHITE, pad_inches=0.2)
    plt.close(fig)
    print(f"  Gespeichert: {p}")

def box(ax, x, y, w, h, label, color=LIGHT, textcolor="#1A202C",
        fontsize=9, bold=False, border=GRAY, radius=0.05):
    """Zeichnet eine abgerundete Box mit zentriertem Text."""
    fancy = FancyBboxPatch((x - w/2, y - h/2), w, h,
                           boxstyle=f"round,pad={radius}",
                           linewidth=1.5, edgecolor=border,
                           facecolor=color, zorder=3)
    ax.add_patch(fancy)
    ax.text(x, y, label, ha="center", va="center",
            fontsize=fontsize, color=textcolor,
            fontweight="bold" if bold else "normal",
            wrap=True, zorder=4, multialignment="center")

def get_edge_point(x, y, w, h, tx, ty):
    """Berechnet den Schnittpunkt einer Linie vom Zentrum zum Ziel mit dem Box-Rand."""
    dx, dy = tx - x, ty - y
    if dx == 0 and dy == 0: return x, y
    scale_x = (w/2) / abs(dx) if dx != 0 else float('inf')
    scale_y = (h/2) / abs(dy) if dy != 0 else float('inf')
    scale = min(scale_x, scale_y)
    return x + dx * scale, y + dy * scale

def connect_boxes(ax, x1, y1, w1, h1, x2, y2, w2, h2, color=GRAY, lw=1.8, style="->"):
    """Zieht einen Pfeil exakt von der Außenkante der Startbox zur Außenkante der Zielbox."""
    sx, sy = get_edge_point(x1, y1, w1, h1, x2, y2)
    ex, ey = get_edge_point(x2, y2, w2, h2, x1, y1)
    
    pad = 0.08
    length = math.hypot(ex-sx, ey-sy)
    if length > 0:
        sx += (ex-sx)/length * pad
        sy += (ey-sy)/length * pad
        ex -= (ex-sx)/length * pad
        ey -= (ey-sy)/length * pad
        
    ax.annotate("", xy=(ex, ey), xytext=(sx, sy),
                arrowprops=dict(arrowstyle=style, color=color,
                                lw=lw, shrinkA=0, shrinkB=0), zorder=2)

# ─── Abb. 1: Vier-Schichten-Architektur ───────────────────────────────────────
def fig1_architecture():
    fig, ax = plt.subplots(figsize=(10, 8))
    ax.set_xlim(0, 10); ax.set_ylim(0, 10)
    ax.axis("off")
    ax.set_title("Vier-Schichten-Architektur des XAML-ML-Systems", fontsize=12, fontweight="bold", pad=15)

    boxes = [
        (5, 9.2, 8, 1.0, "Web UI (Port 7860)  |  REST API (Port 5000)", LIGHT, GRAY),
        (5, 7.4, 8, 1.4, "Validation Layer\nMulti-Layer-Validierung (XML-Syntax, Struktur, Best Practices)", "#F0FFF4", GREEN),
        (5, 5.4, 8, 1.4, "Generation Layer\nXAML-Generator (RAG, Two-Phase, Prompt-Engineering)", "#EBF8FF", BLUE),
        (5, 3.4, 8, 1.4, "Model Layer\nLokales LLM via Ollama (z.B. qwen3-coder, LoRA Fine-Tuning)", "#FEFCBF", ACCENT),
        (5, 1.4, 8, 1.4, "Data Layer\nXAML-Korpus + TF-IDF Retrieval Engine", "#E2E8F0", "#718096")
    ]

    for i, (x, y, w, h, label, color, border) in enumerate(boxes):
        box(ax, x, y, w, h, label, color, border=border, fontsize=10, bold=(i==0))
        if i < len(boxes) - 1:
            connect_boxes(ax, x, y, w, h, boxes[i+1][0], boxes[i+1][1], boxes[i+1][2], boxes[i+1][3])

    ax.text(0.5, 7.4, "Layer 4", ha="center", va="center", fontsize=10, color=GREEN, fontweight="bold", rotation=90)
    ax.text(0.5, 5.4, "Layer 3", ha="center", va="center", fontsize=10, color=BLUE, fontweight="bold", rotation=90)
    ax.text(0.5, 3.4, "Layer 2", ha="center", va="center", fontsize=10, color=ACCENT, fontweight="bold", rotation=90)
    ax.text(0.5, 1.4, "Layer 1", ha="center", va="center", fontsize=10, color="#718096", fontweight="bold", rotation=90)

    save(fig, "Abb_1_Architektur.png")

# ─── Abb. 2: LoRA-Mechanismus ─────────────────────────────────────────────────
def fig2_lora():
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.set_xlim(0, 12); ax.set_ylim(0, 6)
    ax.axis("off")
    ax.set_title("LoRA-Mechanismus — Low-Rank Adaptation", fontsize=12, fontweight="bold", pad=15)

    box(ax, 6, 0.8, 1.5, 0.8, "Input x", WHITE, border=GRAY, fontsize=10)
    box(ax, 6, 5.5, 2.5, 0.8, "Output: W₀x + BAx", "#C6F6D5", border=GREEN, fontsize=10, bold=True, textcolor="#276749")

    box(ax, 3, 3.5, 2.5, 3.0, "W₀\n(Vortrainierte Gewichte)\nd × d\n\nEingefroren", "#E2E8F0", border="#718096", fontsize=10)
    
    box(ax, 9, 2.5, 2.0, 1.2, "A\nd × r\n(trainierbar)", "#FEFCBF", border=ACCENT, fontsize=10, textcolor="#744210")
    box(ax, 9, 4.5, 2.0, 1.2, "B\nr × d\n(trainierbar)", "#FEFCBF", border=ACCENT, fontsize=10, textcolor="#744210")

    connect_boxes(ax, 6, 0.8, 1.5, 0.8, 3, 3.5, 2.5, 3.0) 
    connect_boxes(ax, 6, 0.8, 1.5, 0.8, 9, 2.5, 2.0, 1.2) 
    connect_boxes(ax, 9, 2.5, 2.0, 1.2, 9, 4.5, 2.0, 1.2) 
    connect_boxes(ax, 3, 3.5, 2.5, 3.0, 6, 5.5, 2.5, 0.8) 
    connect_boxes(ax, 9, 4.5, 2.0, 1.2, 6, 5.5, 2.5, 0.8) 

    ax.text(9, 1.0, "LoRA-Adapter\nr = 16  ≪  d = 4096", ha="center", fontsize=10, color=ACCENT, fontweight="bold")
    ax.text(6, 4.7, "+", ha="center", va="center", fontsize=20, color=GREEN, fontweight="bold")

    save(fig, "Abb_2_LoRA_Mechanismus.png")

# ─── Abb. 3: Unsloth-Pipeline ─────────────────────────────────────────────────
def fig3_unsloth():
    fig, ax = plt.subplots(figsize=(14, 4))
    ax.set_xlim(0, 14); ax.set_ylim(0, 4)
    ax.axis("off")
    ax.set_title("Unsloth QLoRA Fine-Tuning Pipeline", fontsize=12, fontweight="bold", pad=12)

    steps = [
        (1.5, "Basismodell\nladen\n(qwen3:14b)"),
        (3.7, "4-bit\nQuantisierung\n(NF4)"),
        (5.9, "LoRA-Adapter\nanfügen\n(r=16, α=16)"),
        (8.1, "Training\n(SFTTrainer\n3 Epochen)"),
        (10.3, "GGUF Export\n(q4_k_m)\n~4 GB"),
        (12.5, "Ollama\nImport\nxaml-specialist"),
    ]
    colors = [LIGHT, "#FFF5F5", "#FEFCBF", "#EBF8FF", "#F0FFF4", "#E9D8FD"]
    w, h = 1.8, 2.2

    for i, (x, label) in enumerate(steps):
        border = ACCENT if i in (2, 3) else GRAY
        box(ax, x, 2.2, w, h, label, color=colors[i], fontsize=9, border=border)
        if i < len(steps) - 1:
            connect_boxes(ax, x, 2.2, w, h, steps[i+1][0], 2.2, w, h)

    ax.text(7.0, 0.5, "Unsloth: 2× schneller, 60% weniger VRAM als Standard-HuggingFace", ha="center", fontsize=9, color=GRAY, style="italic")

    save(fig, "Abb_3_Unsloth_Pipeline.png")

# ─── Abb. 4: RAG-Workflow ─────────────────────────────────────────────────────
def fig4_rag():
    fig, ax = plt.subplots(figsize=(15, 6))
    ax.set_xlim(0, 15); ax.set_ylim(0, 6)
    ax.axis("off")
    ax.set_title("TF-IDF Retrieval-Augmented Generation (RAG) Workflow", fontsize=12, fontweight="bold", pad=12)

    ax.text(5.5, 5.5, "Offline-Indexierungsphase", ha="center", fontsize=10, color=BLUE, fontweight="bold")
    box(ax, 2.0, 4.6, 2.2, 1.0, "XAML-Korpus\n(Workflows)", LIGHT, fontsize=9)
    box(ax, 5.5, 4.6, 2.6, 1.0, "TF-IDF Vektorisierung\n+ IDF-Gewichtung", "#EBF8FF", border=BLUE, fontsize=9)
    box(ax, 9.0, 4.6, 2.2, 1.0, "In-Memory\nVektor-Index", "#EBF8FF", border=BLUE, fontsize=9)
    
    connect_boxes(ax, 2.0, 4.6, 2.2, 1.0, 5.5, 4.6, 2.6, 1.0, color=BLUE)
    connect_boxes(ax, 5.5, 4.6, 2.6, 1.0, 9.0, 4.6, 2.2, 1.0, color=BLUE)

    ax.text(3.0, 3.2, "Online-Retrieval & Generierung", ha="center", fontsize=10, color=ACCENT, fontweight="bold")
    
    nodes = [
        (1.5, "Nutzer-\neingabe", LIGHT, GRAY),
        (3.9, "TF-IDF\nVektorisierung\n+ Activity-Boost", "#FEFCBF", ACCENT),
        (6.3, "Kosinus-\nÄhnlichkeit\nTop-k=3", "#FEFCBF", ACCENT),
        (8.7, "Kontext-\nEinbettung\nin Prompt", "#FFF5F5", "#C53030"),
        (11.1, "LLM\n(qwen3:14b)\nGenerierung", "#E9D8FD", "#553C9A"),
        (13.5, "XAML\nOutput\n+ Validierung", "#F0FFF4", GREEN),
    ]
    w, h = 2.0, 1.6
    for i, (x, label, color, border) in enumerate(nodes):
        box(ax, x, 1.8, w, h, label, color, border=border, fontsize=9)
        if i < len(nodes)-1:
            connect_boxes(ax, x, 1.8, w, h, nodes[i+1][0], 1.8, w, h)

    connect_boxes(ax, 9.0, 4.6, 2.2, 1.0, 6.3, 1.8, w, h, color=BLUE, style="->")

    ax.text(7.5, 0.4, "Kombinierter Score = 0.7 × Vektorähnlichkeit + 0.3 × Qualitätsscore", ha="center", fontsize=9, color=GRAY, style="italic")

    save(fig, "Abb_4_RAG_Workflow.png")

# ─── Abb. 5: Multi-Layer Validierung ─────────────────────────────────────────
def fig5_validation():
    fig, ax = plt.subplots(figsize=(13, 8))
    # Y-Achse nach unten erweitert (-0.5), damit die Box bei y=0.3 nicht abgeschnitten wird!
    ax.set_xlim(0, 13); ax.set_ylim(-0.5, 8.5)
    ax.axis("off")
    ax.set_title("Multi-Layer-Validierungspipeline", fontsize=12, fontweight="bold", pad=12)

    layers = [
        (6.5, "Layer 1: XML-Syntax (Gate)", "Wohlgeformtes XML?\nAlle Namespaces vorhanden?", "#FFF5F5", "#C53030", "ABBRUCH:\nScore = 0"),
        (4.8, "Layer 2: Aktivitäts-Whitelist", "Sind alle Aktivitäten real?\n(Hallucination-Filter)", "#FEFCBF", "#D69E2E", "⚠ Fehler:\n-15 Punkte"),
        (3.1, "Layer 3: Best-Practice-Regeln", "TryCatch, Rethrow, Logging\nArgument-Präfixe", "#EBF8FF", BLUE, "Scoring:\ncrit -30 / err -15"),
        (1.4, "Layer 4: Strukturanalyse", "Komplexität (> 60 Akt.)\nVerschachtelungstiefe", "#F0FFF4", GREEN, "Hinweise &\nOptimierung"),
    ]

    w_main, h_main = 5.0, 1.2
    w_pen, h_pen = 2.5, 1.0
    x_main = 4.0
    x_pen = 9.0

    for i, (y, title, detail, color, border, penalty) in enumerate(layers):
        box(ax, x_main, y, w_main, h_main, "", color, border=border)
        ax.text(x_main - w_main/2 + 0.2, y + 0.2, title, fontsize=10, fontweight="bold", color=border, va="center", ha="left")
        ax.text(x_main - w_main/2 + 0.2, y - 0.2, detail, fontsize=9, color=GRAY, va="center", ha="left")
        
        box(ax, x_pen, y, w_pen, h_pen, penalty, WHITE, border=border, fontsize=9, textcolor=border)
        connect_boxes(ax, x_main, y, w_main, h_main, x_pen, y, w_pen, h_pen, color=border)
        
        if i < len(layers) - 1:
            connect_boxes(ax, x_main, y, w_main, h_main, x_main, layers[i+1][0], w_main, h_main)

    center_x = (x_main - w_main/2 + x_pen + w_pen/2) / 2
    total_w = (x_pen + w_pen/2) - (x_main - w_main/2)
    
    box(ax, center_x, 0.3, total_w, 0.6, "Gesamt-Score: 0–100 | Threshold: ≥ 70 = Bestanden", color="#C6F6D5", border=GREEN, fontsize=10, bold=True, textcolor=GREEN)
    connect_boxes(ax, x_main, 1.4, w_main, h_main, center_x, 0.3, total_w, 0.6)

    save(fig, "Abb_5_Validierungspipeline.png")

# ─── Abb. 6: Logistikportal-Prozessablauf ───────────────────────────────────────────
def fig6_logistics():
    fig, ax = plt.subplots(figsize=(15, 5))
    ax.set_xlim(0, 15); ax.set_ylim(0, 5)
    ax.axis("off")
    ax.set_title("Logistikportal (Fallstudie A) — Automatisierter Prozessablauf", fontsize=12, fontweight="bold", pad=12)

    steps = [
        (1.5, "Credentials\nholen\n(Orchestrator)"),
        (3.5, "Browser\nöffnen\nChrome"),
        (5.5, "Login\nPortal\n(TypeInto)"),
        (7.5, "Auftrags-\nliste\nabrufen"),
        (9.5, "Für jeden\nAuftrag\n(ForEach)"),
        (11.5, "Daten\nextrahieren\n(GetText)"),
        (13.5, "Status\nsetzen\n+ Logout"),
    ]
    cols = [LIGHT, LIGHT, "#FEFCBF", "#EBF8FF", "#EBF8FF", "#EBF8FF", "#F0FFF4"]
    bords = [GRAY, GRAY, ACCENT, BLUE, BLUE, BLUE, GREEN]
    w, h = 1.7, 2.0

    for i, ((x, label), color, border) in enumerate(zip(steps, cols, bords)):
        box(ax, x, 2.8, w, h, label, color, border=border, fontsize=9)
        if i < len(steps) - 1:
            connect_boxes(ax, x, 2.8, w, h, steps[i+1][0], 2.8, w, h)

    rect = mpatches.FancyBboxPatch((0.3, 1.5), 14.4, 2.6, boxstyle="round,pad=0.1", linewidth=2, edgecolor=ACCENT, facecolor="none", linestyle="--", zorder=0)
    ax.add_patch(rect)
    
    ax.text(7.5, 1.0, "TryCatch — Catch: TakeScreenshot + LogMessage(Error) + Rethrow | Finally: CloseBrowser", ha="center", fontsize=9, color=ACCENT, style="italic")
    ax.text(7.5, 0.4, "Validator-Score: 87/100  |  Manuelle Anpassungen: Selektoren, Portal-URL", ha="center", fontsize=10, color=GREEN, fontweight="bold")

    save(fig, "Abb_6_Logistik_Prozessablauf.png")

# ─── Abb. 7: SAP ME22N Struktur ──────────────────────────────────────────────
def fig7_sap():
    fig, ax = plt.subplots(figsize=(13, 7))
    ax.set_xlim(0, 13); ax.set_ylim(0, 7)
    ax.axis("off")
    ax.set_title("SAP ME22N — Automatisierter Bestellpreis-Prüfprozess", fontsize=12, fontweight="bold", pad=12)

    w, h = 2.6, 1.2
    
    box(ax, 2.0, 5.5, w, h, "SAP-Anmeldung\n(GetCredential + Logon)", LIGHT)
    box(ax, 2.0, 3.8, w, h, "ME22N\nöffnen", LIGHT)
    box(ax, 2.0, 2.1, w, h, "Konditionen-\nTab klicken", LIGHT)
    
    connect_boxes(ax, 2.0, 5.5, w, h, 2.0, 3.8, w, h)
    connect_boxes(ax, 2.0, 3.8, w, h, 2.0, 2.1, w, h)

    box(ax, 5.5, 2.1, w, h, "Nettowert\nlesen (GetText)", "#EBF8FF", border=BLUE)
    box(ax, 5.5, 3.8, w, h, "Preis berechnen\n(Menge × EP)", "#EBF8FF", border=BLUE)
    box(ax, 5.5, 5.5, w, h, "Abweichung\nberechnen (%)", "#FEFCBF", border=ACCENT)

    connect_boxes(ax, 2.0, 2.1, w, h, 5.5, 2.1, w, h)
    connect_boxes(ax, 5.5, 2.1, w, h, 5.5, 3.8, w, h)
    connect_boxes(ax, 5.5, 3.8, w, h, 5.5, 5.5, w, h)

    diamond = plt.Polygon([[8.5, 6.2], [9.8, 5.5], [8.5, 4.8], [7.2, 5.5]], closed=True, facecolor="#FEFCBF", edgecolor=ACCENT, linewidth=1.5, zorder=3)
    ax.add_patch(diamond)
    ax.text(8.5, 5.5, "Δ > 1%?", ha="center", va="center", fontsize=10, fontweight="bold", color="#744210", zorder=4)
    connect_boxes(ax, 5.5, 5.5, w, h, 7.2, 5.5, 0.1, 0.1, color=ACCENT)

    box(ax, 11.5, 5.5, w, h, "LogMessage Warning\n+ Bestellnr. markieren", "#FFF5F5", border="#C53030")
    box(ax, 8.5, 3.0, w, h, "LogMessage\nInfo (OK)", "#F0FFF4", border=GREEN)

    connect_boxes(ax, 9.8, 5.5, 0.1, 0.1, 11.5, 5.5, w, h, color="#C53030")
    ax.text(10.3, 5.7, "Ja", fontsize=10, color="#C53030", fontweight="bold")
    
    connect_boxes(ax, 8.5, 4.8, 0.1, 0.1, 8.5, 3.0, w, h, color=GREEN)
    ax.text(8.7, 4.2, "Nein", fontsize=10, color=GREEN, fontweight="bold")

    ax.text(6.5, 0.5, "Validator-Score: 91/100  |  Toleranzwert konfigurierbar  |  Getestet mit SAP-Mandant 100", ha="center", fontsize=10, color=GREEN, style="italic")

    save(fig, "Abb_7_SAP_ME22N.png")

# ─── Abb. 8: Feedback-Loop ────────────────────────────────────────────────────
def fig8_feedback():
    fig, ax = plt.subplots(figsize=(11, 8))
    # Y-Achse nach unten erweitert (-0.5), zur Sicherheit
    ax.set_xlim(0, 11); ax.set_ylim(-0.5, 8.5)
    ax.axis("off")
    ax.set_title("Feedback-Loop für kontinuierliches Lernen", fontsize=12, fontweight="bold", pad=12)

    nodes = {
        "user":  (5.5, 7.0, "Nutzeranfrage\n(Natürliche Sprache)", LIGHT, GRAY),
        "gen":   (2.5, 5.0, "XAML-\nGenerierung\n(LLM + RAG)", "#EBF8FF", BLUE),
        "val":   (5.5, 5.0, "Multi-Layer\nValidierung\n(Score 0–100)", "#FEFCBF", ACCENT),
        "man":   (8.5, 5.0, "Manuelle\nAnpassung\n(Entwickler)", "#FFF5F5", "#C53030"),
        "add":   (8.5, 2.5, "add_to_corpus()\nRAG-Pool\nerweitern", "#F0FFF4", GREEN),
        "check": (5.5, 2.5, "Qualitäts-\nprüfung\n≥ 70?", "#FEFCBF", ACCENT),
        "json":  (2.5, 2.5, "Feedback\nJSONL\nakkumulieren", "#E9D8FD", "#553C9A"),
        "lora":  (2.5, 0.8, "LoRA\nFine-Tuning\n(nächster Run)", "#E9D8FD", "#553C9A"),
    }

    w, h = 2.4, 1.4
    for key, (x, y, label, color, border) in nodes.items():
        box(ax, x, y, w, h, label, color, border=border, fontsize=9)

    def link(k1, k2, color=GRAY):
        n1, n2 = nodes[k1], nodes[k2]
        connect_boxes(ax, n1[0], n1[1], w, h, n2[0], n2[1], w, h, color=color)

    link("user", "gen")
    link("gen", "val")
    link("val", "man")
    link("man", "add", GREEN)
    link("add", "check", ACCENT)
    link("check", "json", "#553C9A")
    link("json", "lora", "#553C9A")
    link("lora", "gen", "#553C9A")

    ax.text(5.5, 1.5, "Score ≥ 70", ha="center", fontsize=9, color=GREEN, fontweight="bold")
    ax.text(4.0, 3.8, "Score < 70", ha="center", fontsize=9, color="#C53030", style="italic")
    
    ax.text(10.5, 0.0, "Jeder akzeptierte Workflow verbessert alle folgenden Generierungen", ha="right", fontsize=9, color=GRAY, style="italic")

    save(fig, "Abb_8_Feedback_Loop.png")

# ─── Abb. 9: Ablationsstudie ──────────────────────────────────────────────────
def fig_ablation(results: dict | None = None):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    fig.suptitle("Ablationsstudie: Vergleich der vier Generierungsstrategien", fontsize=12, fontweight="bold")

    if results is None:
        data = {
            "A: Zero-Shot": {"avg": 52, "pass": 28, "struct": 45},
            "B: +Repair":   {"avg": 63, "pass": 48, "struct": 72},
            "C: Zwei-Phas.":{"avg": 71, "pass": 65, "struct": 95},
            "D: Zwei-Ph+Rep":{"avg": 79, "pass": 78, "struct": 98},
        }
    else:
        data = {}
        for k, v in results.items():
            label = {"baseline":"A: Zero-Shot","baseline_repair":"B: +Repair",
                     "two_phase":"C: Zwei-Phas.","two_phase_repair":"D: Zwei-Ph+Rep"}.get(k,k)
            data[label] = {
                "avg":    v.get("avg_score", 0),
                "pass":   v.get("pass_rate", 0),
                "struct": v.get("component_avgs", {}).get("structure", 0),
            }

    labels = list(data.keys())
    colors = [GRAY, "#6B7280", BLUE, ACCENT]

    vals = [data[l]["avg"] for l in labels]
    bars = ax1.bar(labels, vals, color=colors, alpha=0.85, edgecolor="white", linewidth=1)
    ax1.axhline(70, color="red", linestyle="--", linewidth=1.5, label="Schwelle 70")
    ax1.set_title("Mittlerer Validierungsscore (0–100)", fontsize=10)
    ax1.set_ylabel("Score"); ax1.set_ylim(0, 100)
    ax1.legend(fontsize=9)
    for bar, val in zip(bars, vals):
        ax1.text(bar.get_x()+bar.get_width()/2, val+2, f"{val:.1f}", ha="center", va="bottom", fontsize=10, fontweight="bold")

    vals2 = [data[l]["pass"] for l in labels]
    bars2 = ax2.bar(labels, vals2, color=colors, alpha=0.85, edgecolor="white", linewidth=1)
    ax2.set_title("Pass-Rate (% Workflows ≥ 70 Punkte)", fontsize=10)
    ax2.set_ylabel("Pass-Rate (%)"); ax2.set_ylim(0, 110)
    for bar, val in zip(bars2, vals2):
        ax2.text(bar.get_x()+bar.get_width()/2, val+2, f"{val:.0f}%", ha="center", va="bottom", fontsize=10, fontweight="bold")

    if results is None:
        for ax in (ax1, ax2):
            ax.text(0.5, 0.5, "PLATZHALTER\nbenchmark.py ausführen", transform=ax.transAxes, ha="center", va="center", fontsize=14, color="red", alpha=0.2, fontweight="bold", rotation=15)

    fig.tight_layout(pad=2.0)
    save(fig, "Abb_Ablationsstudie.png")

# ─── Hauptprogramm ────────────────────────────────────────────────────────────
FIGURES = {
    1: ("Vier-Schichten-Architektur", fig1_architecture),
    2: ("LoRA-Mechanismus",           fig2_lora),
    3: ("Unsloth-Pipeline",           fig3_unsloth),
    4: ("RAG-Workflow",               fig4_rag),
    5: ("Validierungspipeline",       fig5_validation),
    6: ("Logistikportal Prozessablauf",      fig6_logistics),
    7: ("SAP ME22N",                  fig7_sap),
    8: ("Feedback-Loop",              fig8_feedback),
    9: ("Ablationsstudie",            fig_ablation),
}

def main():
    parser = argparse.ArgumentParser(description="Erstellt Thesis-Abbildungen als PNG")
    parser.add_argument("--fig", type=int, default=None, help="Nur eine Abbildung (1–9); ohne Angabe: alle")
    parser.add_argument("--benchmark", type=str, default=None, help="Pfad zu benchmark_DATUM.json für echte Ablationsdaten")
    args = parser.parse_args()

    # --- AUTO CLEANUP: Löscht alle alten Bilder im Ordner ---
    print(f"Räume alten Ordner auf: {OUT.resolve()} ...")
    for p in OUT.glob("*.png"):
        try:
            p.unlink()
        except Exception:
            pass
    # --------------------------------------------------------

    benchmark_data = None
    if args.benchmark:
        import json
        with open(args.benchmark) as f:
            benchmark_data = json.load(f).get("strategies", None)

    print(f"Erstelle neue Abbildungen → {OUT.resolve()}/")

    if args.fig:
        if args.fig not in FIGURES:
            print(f"Unbekannte Abbildung: {args.fig}. Wähle 1–9.")
            return
        name, fn = FIGURES[args.fig]
        print(f"  Abb. {args.fig}: {name}")
        if args.fig == 9 and benchmark_data:
            fn(benchmark_data)
        else:
            fn()
    else:
        for num, (name, fn) in FIGURES.items():
            print(f"  Abb. {num}: {name}")
            if num == 9 and benchmark_data:
                fn(benchmark_data)
            else:
                fn()

    print(f"\nFertig. {len(FIGURES) if not args.fig else 1} Abbildung(en) gespeichert.")
    print(f"Ordner: {OUT.resolve()}")

if __name__ == "__main__":
    main()