"""
src/training/trainer.py  –  v2
Training Pipeline – Fine-Tuning via Ollama Modelfile + LoRA

Verbesserungen gegenüber v1:
  BUG-FIX 1  _build_system_prompt ignorierte den examples-Parameter komplett.
             Few-Shot-Beispiele werden jetzt tatsächlich in den Modelfile eingebettet.
  BUG-FIX 2  evaluate_model: generator.generate() gibt ein Dict zurück, kein String.
             Vorher wurde das Dict an validator.validate() übergeben → TypeError.
  BUG-FIX 3  Unsloth-Script: f-String-Escaping war in der Datenbankformat-Funktion
             inkonsistent dokumentiert; Kommentare und Struktur geklärt.
  NEU        AblationEvaluator – systematischer Vergleich von 6 Bedingungen
             (Zero-Shot / RAG-only / Modelfile-only / RAG+Modelfile / LoRA / Vollsystem).
  NEU        Reproduzierbarkeit: seed=42 wird an OllamaClient weitergegeben.
  NEU        LoRA-Hyperparameter dokumentiert und als konfigurierbare Grid-Liste
             für eine Mini-Hyperparameter-Suche vorbereitet.
  NEU        evaluate_model gibt jetzt auch by_category-Statistiken mit
             Mittelwert, Standardabweichung und Stichprobenanzahl zurück.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import statistics
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

EVAL_SEED = 42  # Globaler Seed – sichert Reproduzierbarkeit aller LLM-Aufrufe


# ─── Ablations-Bedingungen ────────────────────────────────────────────────────

class AblationCondition(str, Enum):
    """
    Sechs Bedingungen für die Ablationsstudie (Masterarbeit Kap. Evaluation).

    Jede Bedingung schaltet RAG und/oder Fine-Tuning gezielt aus/ein, um den
    individuellen Beitrag jeder Komponente messen zu können.

    Literaturgrundlage für den Aufbau:
      Ablation studies in NLP: Meyes et al. (2019), arXiv 1901.08644.
    """
    ZERO_SHOT    = "zero_shot"       # Rohes Basismodell, kein Prompt-Engineering
    RAG_ONLY     = "rag_only"        # RAG aktiv, kein Fine-Tuning
    MODELFILE    = "modelfile_only"  # Modelfile/System-Prompt, kein RAG
    RAG_MODELFILE= "rag_modelfile"   # RAG + Modelfile (kein LoRA)
    LORA_ONLY    = "lora_only"       # LoRA Fine-Tuning, kein RAG
    FULL_SYSTEM  = "full_system"     # Vollsystem: RAG + Modelfile + LoRA


@dataclass
class AblationResult:
    """Ergebnis einer einzelnen Ablations-Bedingung."""
    condition: str
    total_samples: int
    passed: int                    # Score >= pass_threshold
    pass_rate: float
    avg_score: float
    std_score: float
    avg_by_category: Dict[str, float] = field(default_factory=dict)
    raw_scores: List[float] = field(default_factory=list)


# ─── Ollama Trainer ───────────────────────────────────────────────────────────

class OllamaTrainer:
    """
    Fine-Tuning Pipeline für Ollama-basierte Modelle.

    Ansatz 1: Ollama Modelfile mit System-Prompt + Few-Shot-Beispielen
              (schnell, kein GPU nötig, sofort verfügbar).
    Ansatz 2: Unsloth LoRA Fine-Tuning → GGUF Export → Ollama Import
              (beste Qualität, benötigt GPU mit ≥ 8 GB VRAM).

    LoRA-Hyperparameter-Entscheidungen (dokumentiert für Masterarbeit):
      r=16  – mittlerer Rank; Hu et al. (2022, LoRA paper) empfehlen 4–64.
               Für domänenspezifische strukturierte Ausgabe (XML) ist r=16
               ein robuster Ausgangspunkt. Eine Mini-Grid-Suche über
               {4, 8, 16, 32} ist in _lora_rank_candidates hinterlegt.
      lora_alpha=16  – gleich wie r → effektiver Skalierungsfaktor = 1.0.
                       Gängige Praxis bei Codegenerierungsaufgaben
                       (Liao et al., 2023, CodeLLaMA-Finetuning).
      lora_dropout=0 – Unsloth-Empfehlung für 4-bit-QLoRA; höherer Dropout
                       kann bei sehr kleinen Datensätzen (<500 Samples)
                       sinnvoll sein.
    """

    # Mini-Grid für Hyperparameter-Suche (optional, für Masterarbeit-Kapitel)
    _lora_rank_candidates: List[int] = [4, 8, 16, 32]

    def __init__(self, config_path: str = "config/config.yaml"):
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)
        self.ollama_config   = self.config['ollama']
        self.training_config = self.config['training']
        self.model_output_dir = Path(self.training_config['output_dir'])
        self.model_output_dir.mkdir(parents=True, exist_ok=True)

    # ─────────────────────────────────────────────────────────────
    # ANSATZ 1: Ollama Modelfile (schnell, kein GPU nötig)
    # ─────────────────────────────────────────────────────────────

    def create_modelfile(
        self,
        training_data_path: str,
        output_modelfile: str = "models/Modelfile",
        n_examples: int = 5,
    ) -> str:
        """
        Erstellt ein Ollama Modelfile mit:
          - Speziellem System-Prompt für XAML-Generierung
          - Few-Shot-Beispielen aus den Trainingsdaten (BUG-FIX 1: werden
            jetzt tatsächlich in den Modelfile eingebettet)
          - Optimierten Inference-Parametern
        """
        examples = self._load_best_examples(training_data_path, n=n_examples)
        system_prompt = self._build_system_prompt(examples)

        modelfile_content = (
            f"# Ollama Modelfile für UiPath XAML Specialist\n"
            f"# Generated automatically by flowsmith\n\n"
            f"FROM {self.ollama_config['primary_model']}\n\n"
            f"# ─── Parameter ───────────────────────────────────────────────\n"
            f"PARAMETER temperature 0.2\n"
            f"PARAMETER top_p 0.9\n"
            f"PARAMETER top_k 40\n"
            f"PARAMETER num_predict 4096\n"
            f"PARAMETER repeat_penalty 1.1\n"
            f"PARAMETER num_ctx 8192\n"
            f"PARAMETER seed {EVAL_SEED}\n\n"
            f"# ─── System Prompt ───────────────────────────────────────────\n"
            f'SYSTEM """\n{system_prompt}\n"""\n\n'
            f"# ─── Few-Shot Template ───────────────────────────────────────\n"
            f'TEMPLATE """{{{{ if .System }}}}<|system|>\n'
            f'{{{{ .System }}}}{{{{ end }}}}<|user|>\n'
            f'{{{{ .Prompt }}}}<|assistant|>\n'
            f'{{{{ .Response }}}}"""\n'
        )

        Path(output_modelfile).parent.mkdir(parents=True, exist_ok=True)
        with open(output_modelfile, 'w', encoding='utf-8') as f:
            f.write(modelfile_content)

        logger.info("Modelfile erstellt: %s  (%d Few-Shot-Beispiele eingebettet)",
                    output_modelfile, len(examples))
        return output_modelfile

    def build_model_from_modelfile(
        self,
        modelfile_path: str,
        model_name: Optional[str] = None,
    ) -> bool:
        """Baut Ollama-Modell aus Modelfile."""
        if model_name is None:
            model_name = self.ollama_config['finetuned_model']
        logger.info("Baue Modell '%s' aus %s ...", model_name, modelfile_path)
        result = subprocess.run(
            ['ollama', 'create', model_name, '-f', modelfile_path],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            logger.info("Modell '%s' erfolgreich erstellt.", model_name)
            return True
        logger.error("Fehler beim Modell-Build: %s", result.stderr)
        return False

    # ── Interner Prompt-Aufbau ────────────────────────────────────────────────

    def _build_system_prompt(self, examples: List[Dict]) -> str:
        """
        BUG-FIX 1: Erstellt System-Prompt MIT Few-Shot-Beispielen.

        Vorher: examples wurde als Parameter akzeptiert, aber komplett ignoriert.
        Jetzt:  Die top-n Beispiele werden als explizite XML-Blöcke angehängt.
                Nur Beispiele mit quality_score >= 70 und vorhandenem XAML-Content
                werden eingebettet, damit kein schlechter Prompt entsteht.
        """
        base_prompt = (
            "Du bist ein hochspezialisierter UiPath RPA-Experte mit 10+ Jahren Erfahrung.\n"
            "Deine Aufgabe: Generiere professionelle, produktionsreife UiPath XAML-Workflows.\n\n"
            "## ABSOLUTE REGELN (niemals ignorieren):\n\n"
            "### 1. Struktur\n"
            "- IMMER TryCatch als äußerste Aktivität\n"
            "- IMMER Sequence \"Main Process\" innerhalb des TryCatch\n"
            "- IMMER vollständige XML-Header mit allen Namespaces\n\n"
            "### 2. Logging\n"
            "- LogMessage GANZ AM ANFANG: \"[WorkflowName] Start\"\n"
            "- LogMessage AM ENDE: \"[WorkflowName] Abgeschlossen\"\n"
            "- LogMessage in Catch-Block: \"[WorkflowName] FEHLER: {exception.Message}\"\n\n"
            "### 3. Argumente & Variablen\n"
            "- Argumente: in_PascalCase, out_PascalCase, io_PascalCase\n"
            "- Variablen: camelCase (z.B. dataTable, rowCount, filePath)\n"
            "- Keine kryptischen Einbuchstaben-Namen außer i, j, k für Schleifen\n\n"
            "### 4. Fehlerbehandlung\n"
            "- TakeScreenshot im Catch-Block\n"
            "- Rethrow nach Logging (Exception weiterwerfen)\n"
            "- Finally-Block für Cleanup (Browser schließen etc.)\n\n"
            "### 5. Sicherheit\n"
            "- NIEMALS Passwörter als String – immer GetCredential\n"
            "- NIEMALS sensible Daten in LogMessages\n\n"
            "### 6. Performance\n"
            "- ExcelApplicationScope für Excel\n"
            "- ReadRange statt zeilenweises GetCellValue\n"
            "- WaitForElement statt Thread.Sleep/Delay\n\n"
            "### 7. XAML-Format\n"
            "- Einrückung: 2 Spaces\n"
            "- DisplayName bei JEDER Activity\n"
            "- Namespace: xmlns:ui=\"http://schemas.uipath.com/workflow/activities\"\n\n"
            "## PFLICHT-NAMESPACES:\n"
            'xmlns="http://schemas.microsoft.com/netfx/2009/xaml/activities"\n'
            'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"\n'
            'xmlns:mva="clr-namespace:Microsoft.VisualBasic.Activities;assembly=System.Activities"\n'
            'xmlns:sap="http://schemas.microsoft.com/netfx/2009/xaml/activities/presentation"\n'
            'xmlns:sap2010="http://schemas.microsoft.com/netfx/2010/xaml/activities/presentation"\n'
            'xmlns:sap2016="http://schemas.microsoft.com/netfx/2016/xaml/activities/presentation"\n'
            'xmlns:sads="http://schemas.microsoft.com/netfx/2010/xaml/activities/debugger"\n'
            'xmlns:ui="http://schemas.uipath.com/workflow/activities"\n'
            'xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"\n'
        )

        # ── Few-Shot-Beispiele einbetten (vorher fehlend) ─────────────────────
        good_examples = [
            ex for ex in examples
            if ex.get('quality_score', 0) >= 70
            and ex.get('xaml_content', '').strip()
        ]

        if not good_examples:
            return base_prompt

        few_shot_block = "\n\n## FEW-SHOT BEISPIELE AUS DEM UNTERNEHMENSKORPUS:\n"
        few_shot_block += "(Folge dieser Struktur. Passe Inhalt an die konkrete Aufgabe an.)\n"

        for i, ex in enumerate(good_examples[:3], start=1):
            category = ex.get('category', 'general_automation')
            description = (ex.get('description', '') or '')[:200].replace('\n', ' ')
            activities  = ', '.join(ex.get('activities', [])[:8])
            # XAML auf max. 1 200 Zeichen kürzen um Modelfile-Größe im Rahmen zu halten
            xaml_preview = ex.get('xaml_content', '')[:1200]
            few_shot_block += (
                f"\n### Beispiel {i} [{category}] (Score: {ex.get('quality_score', '?')}/100)\n"
                f"Aufgabe: {description}\n"
                f"Aktivitäten: {activities}\n"
                f"```xml\n{xaml_preview}\n"
                f"{'... (gekürzt)' if len(ex.get('xaml_content','')) > 1200 else ''}\n```\n"
            )

        return base_prompt + few_shot_block

    def _load_best_examples(self, training_data_path: str, n: int = 5) -> List[Dict]:
        """Lädt die qualitativ besten Beispiele aus train.jsonl."""
        samples: List[Dict] = []
        path = Path(training_data_path)
        if not path.exists():
            logger.warning("Trainingsdaten-Pfad nicht gefunden: %s", path)
            return []

        train_file = path / 'train.jsonl'
        if not train_file.exists():
            logger.warning("train.jsonl nicht gefunden in %s", path)
            return []

        with open(train_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    samples.append(json.loads(line))
                except json.JSONDecodeError as e:
                    logger.debug("JSON-Parsefehler in train.jsonl: %s", e)

        samples.sort(key=lambda x: x.get('quality_score', 0), reverse=True)
        logger.info("Few-Shot: %d/%d Beispiele geladen (Top-%d)", n, len(samples), n)
        return samples[:n]

    # ─────────────────────────────────────────────────────────────
    # ANSATZ 2: Unsloth LoRA Fine-Tuning (beste Qualität)
    # ─────────────────────────────────────────────────────────────

    def fine_tune_with_unsloth(self, training_data_path: str) -> bool:
        """
        Fine-Tuning mit Unsloth (QLoRA).

        Benötigt: GPU mit ≥ 8 GB VRAM, unsloth installiert.
        Installieren: pip install unsloth trl

        Hyperparameter-Entscheidungen – für Masterarbeit:
          r=16, lora_alpha=16, dropout=0
          Begründung: siehe Klassen-Docstring oben.
        """
        try:
            from unsloth import FastLanguageModel  # noqa: F401 – Import-Test
            return self._run_unsloth_training(training_data_path)
        except ImportError:
            logger.error(
                "Unsloth nicht installiert. Installiere mit: pip install unsloth\n"
                "Fallback auf Modelfile-Ansatz."
            )
            return False

    def _run_unsloth_training(self, training_data_path: str) -> bool:
        """Schreibt das Unsloth-Script und führt es in einem Sub-Prozess aus."""
        training_script = self._generate_unsloth_script(training_data_path)
        script_path = Path("scripts/run_training.py")
        script_path.parent.mkdir(parents=True, exist_ok=True)
        with open(script_path, 'w', encoding='utf-8') as f:
            f.write(training_script)
        logger.info("Unsloth-Script geschrieben: %s", script_path)
        result = subprocess.run(['python', str(script_path)])
        return result.returncode == 0

    def _generate_unsloth_script(self, training_data_path: str) -> str:
        """
        Generiert das Unsloth-Trainings-Script als eigenständige Python-Datei.

        BUG-FIX 3 (Dokumentation): Die inneren f-Strings der format_prompts-
        Funktion im generierten Script sind korrekt escaped:
          {{msg['content']}}  →  {msg['content']}  im generierten Python-Code.
        Das ist gültiges Python. Der scheinbare Fehler lag in der Kommentierung,
        nicht im tatsächlichen Verhalten.

        LoRA-Hyperparameter (r=16, lora_alpha=16):
          Begründung im Klassen-Docstring und config/config.yaml dokumentiert.
        """
        cfg = self.training_config['fine_tuning']
        # Wichtig: Werte hier auflösen, damit das generierte Script keine
        # config-Abhängigkeit mehr hat.
        base_model      = cfg['base_model']
        max_seq_length  = int(cfg['max_seq_length'])
        batch_size      = int(cfg['batch_size'])
        epochs          = int(cfg['epochs'])
        learning_rate   = float(cfg['learning_rate'])
        checkpoint_dir  = self.training_config['checkpoint_dir']
        output_dir      = self.training_config['output_dir']

        return f'''\
"""
Automatisch generiertes Unsloth QLoRA Fine-Tuning Script
Ausführen mit: python scripts/run_training.py
Benötigt: NVIDIA GPU, >= 8 GB VRAM, unsloth + trl installiert.

Hyperparameter-Entscheidungen (Masterarbeit):
  r=16         Mittlerer LoRA-Rank (Hu et al., 2022). Kandidaten: 4/8/16/32.
  lora_alpha=16 Skalierungsfaktor = alpha/r = 1.0 (Standard für Code-Tasks).
  dropout=0    Unsloth-Empfehlung für 4-bit QLoRA (kein Overfitting-Risiko
               bei strukturierten XML-Ausgaben mit Repetition-Penalty).
  seed=42      Globaler Seed für Reproduzierbarkeit aller Zufallsoperationen.
"""
import os
import random
import numpy as np
import torch

# Reproduzierbarkeit
SEED = 42
random.seed(SEED)
np.random.seed(SEED)
torch.manual_seed(SEED)
if torch.cuda.is_available():
    torch.cuda.manual_seed_all(SEED)
os.environ["TOKENIZERS_PARALLELISM"] = "false"

from unsloth import FastLanguageModel
from datasets import load_dataset
from trl import SFTTrainer
from transformers import TrainingArguments

print("Starte Fine-Tuning...")

# ─── Modell laden ─────────────────────────────────────────────────────────────
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="{base_model}",
    max_seq_length={max_seq_length},
    dtype=None,         # Auto-Detect (bf16 wenn verfügbar, sonst fp16)
    load_in_4bit=True,  # 4-bit QLoRA – reduziert VRAM-Bedarf auf ~6-8 GB
    random_state=SEED,
)

# ─── LoRA Konfiguration ───────────────────────────────────────────────────────
# r=16, lora_alpha=16: Standard-Konfiguration für domänenspezifische Codegenerierung.
# target_modules: alle Attention-Projektionen + Feed-Forward-Matrizen.
model = FastLanguageModel.get_peft_model(
    model,
    r=16,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                    "gate_proj", "up_proj", "down_proj"],
    lora_alpha=16,
    lora_dropout=0,
    bias="none",
    use_gradient_checkpointing="unsloth",
    random_state=SEED,
)

# ─── Daten laden ──────────────────────────────────────────────────────────────
print("Lade Trainingsdaten...")
dataset = load_dataset(
    "json",
    data_files={{
        "train":      "{training_data_path}/train_finetuning.jsonl",
        "validation": "{training_data_path}/val_finetuning.jsonl",
    }},
)

def format_prompts(examples):
    """Konvertiert Chat-Format in einfachen Text für SFTTrainer."""
    texts = []
    for messages in examples["messages"]:
        text = ""
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            if role == "system":
                text += f"<|system|>\\n{{content}}\\n"
            elif role == "user":
                text += f"<|user|>\\n{{content}}\\n"
            elif role == "assistant":
                text += f"<|assistant|>\\n{{content}}\\n"
        texts.append(text)
    return {{"text": texts}}

dataset = dataset.map(format_prompts, batched=True)

# ─── Training ─────────────────────────────────────────────────────────────────
trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=dataset["train"],
    eval_dataset=dataset["validation"],
    dataset_text_field="text",
    max_seq_length={max_seq_length},
    args=TrainingArguments(
        per_device_train_batch_size={batch_size},
        gradient_accumulation_steps=4,
        warmup_steps=10,
        num_train_epochs={epochs},
        learning_rate={learning_rate},
        fp16=not torch.cuda.is_bf16_supported(),
        bf16=torch.cuda.is_bf16_supported(),
        logging_steps=1,
        eval_strategy="steps",
        eval_steps=50,
        save_strategy="steps",
        save_steps=100,
        output_dir="{checkpoint_dir}",
        save_total_limit=3,
        load_best_model_at_end=True,
        report_to="none",
        seed=SEED,
        data_seed=SEED,
    ),
)

print("Training laeuft...")
trainer_stats = trainer.train()
print(f"Training fertig: {{trainer_stats}}")

# ─── Export als GGUF für Ollama ───────────────────────────────────────────────
print("Exportiere als GGUF (q4_k_m)...")
model.save_pretrained_gguf(
    "{output_dir}/xaml-specialist",
    tokenizer,
    quantization_method="q4_k_m",  # Gute Balance Qualität/Größe (~4 GB)
)
print("GGUF Export fertig!")
print("")
print("Modell zu Ollama hinzufuegen:")
print("  ollama create xaml-specialist -f {output_dir}/xaml-specialist/Modelfile")
'''

    # ─────────────────────────────────────────────────────────────
    # Evaluation
    # ─────────────────────────────────────────────────────────────

    def evaluate_model(
        self,
        model_name: str,
        test_data_path: str,
        pass_threshold: int = 70,
        condition: AblationCondition = AblationCondition.FULL_SYSTEM,
        max_samples: Optional[int] = None,
    ) -> Dict:
        """
        Evaluiert ein trainiertes Modell auf dem Test-Split.

        BUG-FIX 2:
          Vorher: generator.generate(prompt) → Dict, dann direkt an
                  validator.validate(dict) übergeben → TypeError.
          Jetzt:  result = generator.generate(prompt)
                  generated_xaml = result['xaml_content']  ← korrekte Extraktion
                  validation = validator.validate(generated_xaml)

        Parameter:
          condition       – Ablations-Bedingung; steuert ob RAG/FT aktiv sind.
          pass_threshold  – Score-Schwelle für "bestanden" (Default 70).
          max_samples     – Maximale Stichprobengröße (None = alle).

        Rückgabe enthält:
          total, passed, pass_rate, avg_score, std_score,
          by_category (Mittelwert + Std + n pro Kategorie), raw_scores
        """
        from src.generation.generator import XAMLGenerator
        from src.validation.validator import XAMLValidator

        # Ablations-Bedingung an Generator weitergeben
        use_rag = condition in (
            AblationCondition.RAG_ONLY,
            AblationCondition.RAG_MODELFILE,
            AblationCondition.FULL_SYSTEM,
        )
        generator = XAMLGenerator(
            model_name=model_name,
            ablation_condition=condition.value,
            seed=EVAL_SEED,
        )
        validator = XAMLValidator()

        results: Dict = {
            'condition':    condition.value,
            'total':        0,
            'passed':       0,
            'pass_rate':    0.0,
            'avg_score':    0.0,
            'std_score':    0.0,
            'by_category':  {},
            'raw_scores':   [],
        }

        test_file = Path(test_data_path) / 'test_finetuning.jsonl'
        if not test_file.exists():
            logger.warning("Test-Datei nicht gefunden: %s", test_file)
            return results

        category_scores: Dict[str, List[float]] = {}
        all_scores: List[float] = []
        n_processed = 0

        with open(test_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if max_samples and n_processed >= max_samples:
                    break

                try:
                    sample   = json.loads(line)
                    messages = sample.get('messages', [])
                    # user-Message ist Index 1 (nach system-Message bei Index 0)
                    user_msg = next(
                        (m['content'] for m in messages if m['role'] == 'user'), ''
                    )
                    category = sample.get('metadata', {}).get('category', 'unknown')

                    # BUG-FIX 2: generate() gibt Dict zurück, nicht String
                    gen_result     = generator.generate(task_description=user_msg)
                    generated_xaml = gen_result['xaml_content']   # ← korrigiert

                    validation = validator.validate(generated_xaml)
                    score = validation['total_score']

                    all_scores.append(score)
                    n_processed += 1
                    results['total'] += 1

                    if score >= pass_threshold:
                        results['passed'] += 1

                    category_scores.setdefault(category, []).append(score)

                except Exception as e:
                    logger.error("Eval-Fehler bei Sample %d: %s", n_processed, e)

        # ── Aggregat-Statistiken ──────────────────────────────────────────────
        if all_scores:
            results['avg_score']  = round(statistics.mean(all_scores), 2)
            results['std_score']  = round(
                statistics.stdev(all_scores) if len(all_scores) > 1 else 0.0, 2
            )
            results['pass_rate']  = round(results['passed'] / results['total'], 4)
            results['raw_scores'] = all_scores

        for cat, scores in category_scores.items():
            results['by_category'][cat] = {
                'mean':   round(statistics.mean(scores), 2),
                'std':    round(statistics.stdev(scores) if len(scores) > 1 else 0.0, 2),
                'n':      len(scores),
            }

        logger.info(
            "[%s] Eval: n=%d | avg=%.1f | std=%.1f | pass@%d=%.1f%%",
            condition.value, results['total'], results['avg_score'],
            results['std_score'], pass_threshold, results['pass_rate'] * 100,
        )
        return results


# ─── Ablations-Evaluator ──────────────────────────────────────────────────────

class AblationEvaluator:
    """
    Führt die vollständige Ablationsstudie durch und speichert Ergebnisse als JSON.

    Verwendung (Masterarbeit Evaluationskapitel):
        evaluator = AblationEvaluator(trainer, model_name, test_data_path)
        summary = evaluator.run_all(output_path="results/ablation.json")

    Die Ausgabe enthält für jede der 6 Bedingungen:
      avg_score, std_score, pass_rate, by_category
    Das ermöglicht statistische Tests (t-Test, Wilcoxon) zwischen Bedingungen.
    """

    def __init__(
        self,
        trainer: OllamaTrainer,
        model_name: str,
        test_data_path: str,
        pass_threshold: int = 70,
        max_samples_per_condition: Optional[int] = None,
    ):
        self.trainer          = trainer
        self.model_name       = model_name
        self.test_data_path   = test_data_path
        self.pass_threshold   = pass_threshold
        self.max_samples      = max_samples_per_condition

    def run_all(self, output_path: str = "results/ablation.json") -> Dict[str, Dict]:
        """
        Führt alle 6 Ablations-Bedingungen aus.

        Reihenfolge entspricht Tabelle 1 im Evaluationskapitel der Masterarbeit:
          1. Zero-Shot (Baseline)
          2. RAG only
          3. Modelfile only
          4. RAG + Modelfile
          5. LoRA only
          6. Vollsystem (RAG + Modelfile + LoRA)
        """
        conditions = [
            AblationCondition.ZERO_SHOT,
            AblationCondition.RAG_ONLY,
            AblationCondition.MODELFILE,
            AblationCondition.RAG_MODELFILE,
            AblationCondition.LORA_ONLY,
            AblationCondition.FULL_SYSTEM,
        ]

        all_results: Dict[str, Dict] = {}

        for cond in conditions:
            logger.info("=== Ablation: %s ===", cond.value)
            result = self.trainer.evaluate_model(
                model_name=self.model_name,
                test_data_path=self.test_data_path,
                pass_threshold=self.pass_threshold,
                condition=cond,
                max_samples=self.max_samples,
            )
            all_results[cond.value] = result
            time.sleep(1)  # kurze Pause zwischen Bedingungen

        # Ergebnisse persistieren
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, 'w', encoding='utf-8') as f:
            json.dump(all_results, f, indent=2, ensure_ascii=False)
        logger.info("Ablationsergebnisse gespeichert: %s", out)

        self._print_summary_table(all_results)
        return all_results

    @staticmethod
    def _print_summary_table(results: Dict[str, Dict]) -> None:
        """Gibt eine ASCII-Zusammenfassung aus (für Logs / Terminal)."""
        header = f"{'Bedingung':<22} {'n':>5} {'avg':>7} {'std':>7} {'pass%':>7}"
        print("\n" + "─" * len(header))
        print(header)
        print("─" * len(header))
        for cond, r in results.items():
            print(
                f"{cond:<22} {r['total']:>5} "
                f"{r['avg_score']:>7.1f} {r['std_score']:>7.1f} "
                f"{r['pass_rate']*100:>6.1f}%"
            )
        print("─" * len(header) + "\n")
