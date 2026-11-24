"""
src/generation/two_phase_generator.py
Zwei-Phasen-Generator: LLM → WorkflowSpec (JSON) → XAML (deterministisch)

Architektur-Begründung (für Masterarbeit Kap. 4.4 / Ausblick):

  PROBLEM mit direkter XAML-Generierung:
    LLMs müssen gleichzeitig (1) die Domänenlogik verstehen, (2) komplexe
    XML-Syntax korrekt schreiben, (3) alle Namespaces kennen, (4) die
    Aktivitätshierarchie einhalten. Jede dieser Anforderungen ist ein
    unabhängiger Fehlerkanal.

  LÖSUNG — Separation of Concerns:
    Phase 1 (LLM): Natürliche Sprache → WorkflowSpec JSON
      - LLM entscheidet NUR über Logik: welche Aktivitäten, in welcher Reihenfolge,
        mit welchen Properties.
      - Format: JSON, erzwungen durch Ollama format="json".
      - Typisch 200-400 Token statt 2000-4000 Token für vollständiges XAML.
      - Kein Namespace-Wissen nötig, keine XML-Syntax-Fehler möglich.

    Phase 2 (Compiler): WorkflowSpec JSON → XAML
      - SpecCompiler.compile(spec) ist deterministisch: kein LLM-Aufruf.
      - Namespaces, TryCatch-Wrapper, LogMessages, Catch, Finally:
        alles programmatisch garantiert.
      - Activity-Fragmente aus activity_templates.py (kuratierte Bibliothek).

  MESSBARE VERBESSERUNGEN (Ablationsstudie-Hypothese):
    - Syntaktische Fehlerrate erwartet: < 5% (vs. 10-30% bei direkter Generierung)
    - Namespace-Fehler: 0% (deterministisch eliminiert)
    - TryCatch-Fehlrate: 0% (deterministisch eingefügt)
    - Phase-1-Latenz: ~20% der vollen XAML-Generierungslatenz

Jiang et al. (2023). SELF-INFIL: Self-Calibrating Infilled Code Generation.
arXiv:2311.04397.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Any

import yaml

from src.generation.spec_compiler import SpecCompiler

logger = logging.getLogger(__name__)

# ─── JSON-Schema (Prompt-Grundlage für Phase 1) ───────────────────────────────
_SPEC_SCHEMA_EXAMPLE = """
{
  "name": "SAPBestellungPruefen",
  "category": "sap_automation",
  "description": "Prüft Bestellpreise in SAP ME22N",
  "packages": ["UiPath.System.Activities", "UiPath.SAP.Activities"],
  "arguments": [
    {"name": "in_Bestellnummer", "type": "System.String", "direction": "in",
     "description": "Die zu prüfende SAP-Bestellnummer"},
    {"name": "out_Abweichung",   "type": "System.Double",  "direction": "out",
     "description": "Berechnete Preisabweichung in Prozent"}
  ],
  "variables": [
    {"name": "nettowert",    "type": "System.Double", "default": "0.0",  "scope": "Main"},
    {"name": "berechneter",  "type": "System.Double", "default": "0.0",  "scope": "Main"},
    {"name": "abweichungPct","type": "System.Double", "default": "0.0",  "scope": "Main"}
  ],
  "activities": [
    {"id": "sap_logon",   "type": "SAPTransaction",  "display_name": "SAP Anmeldung",
     "props": {"transaction": "ME22N", "selector_kind": "sap"}},
    {"id": "nav_cond",    "type": "Click",           "display_name": "Konditionen-Tab klicken",
     "props": {}},
    {"id": "get_netto",   "type": "GetText",         "display_name": "Nettowert lesen",
     "props": {}},
    {"id": "calc_diff",   "type": "Assign",          "display_name": "Abweichung berechnen",
     "props": {"to": "[abweichungPct]",
               "value": "Math.Abs(berechneter - nettowert) / nettowert * 100"}},
    {"id": "log_diff",    "type": "LogMessage",      "display_name": "Abweichung loggen",
     "props": {"message": "[SAPBestellungPruefen] Abweichung: [abweichungPct]%"}}
  ],
  "activity_order": ["sap_logon", "nav_cond", "get_netto", "calc_diff", "log_diff"]
}
"""

_PHASE1_SYSTEM_PROMPT = f"""\
Du bist ein UiPath-Architekturexperte. Deine Aufgabe ist es, aus einer
natürlichsprachlichen Prozessbeschreibung eine strukturierte WorkflowSpec
als JSON zu erstellen.

REGELN:
- Antworte NUR mit validem JSON. Keine Erklärungen, kein Markdown.
- Wähle activity.type aus validen UiPath-Aktivitäten:
  LogMessage, TryCatch, Assign, If, ForEach, While, Sequence,
  Click, TypeInto, GetText, WaitForElement, OpenBrowser, CloseBrowser,
  NavigateTo, AttachWindow, TakeScreenshot,
  ReadRange, WriteRange, ExcelApplicationScope, ForEachRow,
  GetMailMessages, SendMail, SaveAttachments,
  ExecuteQuery, ConnectToDatabase,
  HttpClient, DeserializeJSON,
  ReadTextFile, WriteTextFile, CopyFile, MoveFile,
  AddQueueItem, GetQueueItem, SetTransactionStatus, GetRobotCredential,
  ReadPDFText, SAPTransaction, OpenApplication
- Argumente: in_PascalCase (direction: "in"), out_PascalCase (direction: "out")
- Variablen: camelCase
- activity_order muss alle activity-IDs in logischer Reihenfolge enthalten.
  LogMessage Start und Ende werden vom Compiler automatisch eingefügt – NICHT im Spec!
- TryCatch, Catch, Finally werden vom Compiler automatisch eingefügt – NICHT im Spec!

PACKAGES (wähle die passenden):
  UiPath.System.Activities       (immer)
  UiPath.UIAutomation.Activities (Browser, Desktop-UI)
  UiPath.Excel.Activities        (Excel)
  UiPath.Mail.Activities         (E-Mail)
  UiPath.SAP.Activities          (SAP GUI)
  UiPath.Database.Activities     (SQL)
  UiPath.WebAPI.Activities       (REST API)
  UiPath.PDF.Activities          (PDF)
  UiPath.Orchestrator.Activities (Queue, Assets)

BEISPIEL-OUTPUT:
{_SPEC_SCHEMA_EXAMPLE}
"""


class TwoPhaseGenerator:
    """
    Zwei-Phasen-XAML-Generator.

    Verwendung:
        gen = TwoPhaseGenerator()

        # Option A: Vollständige Pipeline (empfohlen)
        result = gen.generate(task_description="SAP ME22N Bestellpreis prüfen",
                              category="sap_automation")
        print(result['xaml_content'])  # fertiges XAML
        print(result['spec'])          # WorkflowSpec-Dict (für Debugging)

        # Option B: Nur Phase 1 (Spec ohne XAML-Kompilierung)
        spec = gen.generate_spec(task_description="...")
        # Spec manuell bearbeiten
        xaml = gen.compile_spec(spec)
    """

    def __init__(
        self,
        config_path: str = "config/config.yaml",
        model_name:  Optional[str] = None,
        seed:        int = 42,
    ):
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)
        self._override_model = model_name
        self.seed = seed
        self.compiler = SpecCompiler()
        self._ollama_client = None

    @property
    def ollama(self):
        if self._ollama_client is None:
            from src.generation.ollama_client import OllamaClient
            self._ollama_client = OllamaClient()
        return self._ollama_client

    # ── Öffentliche API ───────────────────────────────────────────────────────

    def generate(
        self,
        task_description: str,
        category:             Optional[str] = None,
        additional_requirements: Optional[str] = None,
        max_spec_attempts:    int = 3,
    ) -> Dict[str, Any]:
        """
        Vollständige Zwei-Phasen-Generierung.

        Rückgabe:
          xaml_content  str   vollständiger XAML-String
          spec          dict  WorkflowSpec-Dict aus Phase 1 (versionierbar)
          score         int   Validator-Score
          validation    dict  vollständiger Validierungsbericht
          phase1_tokens int   geschätzte Tokens für Phase 1 (Effizienz-Metrik)
          output_path   str   gespeicherte XAML-Datei
        """
        # Kategorie-Erkennung (aus Hauptgenerator importieren)
        if category is None:
            from src.generation.generator import XAMLGenerator
            category = XAMLGenerator()._detect_category(task_description)

        # Phase 1: Spec generieren (mit Retry)
        spec: Optional[Dict] = None
        phase1_tokens = 0

        for attempt in range(max_spec_attempts):
            try:
                spec, phase1_tokens = self._generate_spec(
                    task_description, category, additional_requirements
                )
                if self._validate_spec(spec):
                    break
                logger.warning("Phase 1 Versuch %d: Spec unvollständig, Retry...",
                               attempt + 1)
            except Exception as e:
                logger.error("Phase 1 Versuch %d Fehler: %s", attempt + 1, e)
                if attempt == max_spec_attempts - 1:
                    raise RuntimeError(f"Phase 1 fehlgeschlagen nach {max_spec_attempts} Versuchen: {e}")

        if spec is None:
            raise RuntimeError("WorkflowSpec konnte nicht generiert werden")

        # Phase 2: Deterministisch zu XAML kompilieren
        xaml = self.compiler.compile(spec)

        # Validierung
        from src.validation.validator import XAMLValidator
        validation = XAMLValidator().validate(xaml)
        score      = validation['total_score']

        # Speichern
        import re as _re
        from datetime import datetime
        from pathlib import Path as _Path
        out_dir  = _Path(self.config['generation']['output_dir'])
        out_dir.mkdir(parents=True, exist_ok=True)
        ts       = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe     = _re.sub(r'[^\w\s-]', '', task_description[:40]).strip()
        safe     = _re.sub(r'\s+', '_', safe)
        out_path = out_dir / f"{category}_2phase_{safe}_{ts}.xaml"
        out_path.write_text(xaml, encoding='utf-8')

        # Spec für Versionierung/Debugging speichern
        import json as _json
        spec_path = out_path.with_suffix('.spec.json')
        spec_path.write_text(_json.dumps(spec, indent=2, ensure_ascii=False),
                             encoding='utf-8')

        logger.info("Zwei-Phasen-Generierung abgeschlossen: Score=%d, Tokens(P1)≈%d, %s",
                    score, phase1_tokens, out_path)

        return {
            'xaml_content':  xaml,
            'spec':          spec,
            'score':         score,
            'validation':    validation,
            'phase1_tokens': phase1_tokens,
            'output_path':   str(out_path),
            'spec_path':     str(spec_path),
            'category':      category,
        }

    def generate_spec(
        self,
        task_description: str,
        category:         Optional[str] = None,
        additional_requirements: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Nur Phase 1: gibt WorkflowSpec-Dict zurück, ohne XAML zu kompilieren."""
        if category is None:
            from src.generation.generator import XAMLGenerator
            category = XAMLGenerator()._detect_category(task_description)
        spec, _ = self._generate_spec(task_description, category, additional_requirements)
        return spec

    def compile_spec(self, spec: Dict[str, Any]) -> str:
        """Nur Phase 2: kompiliert einen vorhandenen Spec zu XAML."""
        return self.compiler.compile(spec)

    # ── Phase 1: Spec-Generierung ─────────────────────────────────────────────

    def _generate_spec(
        self,
        task: str,
        category: str,
        requirements: Optional[str],
    ) -> tuple[Dict, int]:
        """
        Ruft Ollama mit format='json' auf → garantiert valides JSON zurück.

        Ollama's JSON-Modus:
          Wenn format="json" gesetzt ist, erzwingt Ollama grammatikbasiertes
          Constrained Decoding: das Modell kann keinen syntaktisch invaliden
          JSON produzieren. Dies eliminiert die häufigste Fehlerquelle bei
          LLM-generierten strukturierten Outputs.
        """
        model = self._resolve_model()
        prompt = self._build_spec_prompt(task, category, requirements)

        # RAG-Kontext für Spec-Generierung (Aktivitätsnamen-Hints)
        rag_hints = self._get_rag_activity_hints(task, category)
        if rag_hints:
            prompt += f"\n\nREFERENZ-AKTIVITÄTEN aus ähnlichen Workflows:\n{rag_hints}"

        raw_response = self.ollama.generate(
            prompt=prompt,
            model=model,
            system=_PHASE1_SYSTEM_PROMPT,
            seed=self.seed,
            format="json",      # ← Ollama JSON-Modus: garantiert valides JSON
        )

        # Schätzung der Phase-1-Token-Zahl
        phase1_tokens = len(raw_response) // 4

        spec = self._parse_spec_json(raw_response)
        return spec, phase1_tokens

    def _build_spec_prompt(
        self, task: str, category: str, requirements: Optional[str]
    ) -> str:
        prompt = (
            f"Erstelle einen WorkflowSpec für folgende Aufgabe:\n\n"
            f"AUFGABE: {task}\n"
            f"KATEGORIE: {category}\n"
        )
        if requirements:
            prompt += f"\nANFORDERUNGEN: {requirements}\n"
        prompt += (
            "\nDenke an alle logischen Schritte des Prozesses. "
            "Vergib beschreibende display_name-Werte. "
            "Antworte NUR mit JSON, kein anderer Text."
        )
        return prompt

    def _get_rag_activity_hints(self, task: str, category: str) -> str:
        """Extrahiert Aktivitätsnamen aus ähnlichen RAG-Beispielen als Kontext."""
        try:
            from src.generation.rag_engine import RAGEngine
            rag = RAGEngine(self.config['generation']['rag'])
            examples = rag.search(task, category, top_k=2)
            if not examples:
                return ""
            all_acts: List[str] = []
            for ex in examples:
                all_acts.extend(ex.get('activities', []))
            unique = list(dict.fromkeys(all_acts))[:15]
            return ", ".join(unique)
        except Exception:
            return ""

    # ── Spec-Validierung ──────────────────────────────────────────────────────

    def _validate_spec(self, spec: Dict) -> bool:
        """
        Minimale strukturelle Validierung des generierten Spec.
        Prüft Pflichtfelder und ob mindestens eine Aktivität definiert ist.
        """
        required = ['name', 'category', 'activities', 'activity_order']
        for field in required:
            if not spec.get(field):
                logger.warning("Spec fehlt Pflichtfeld: %s", field)
                return False
        if len(spec['activities']) == 0:
            logger.warning("Spec hat keine Aktivitäten")
            return False
        # activity_order muss alle IDs referenzieren
        act_ids = {a['id'] for a in spec['activities'] if 'id' in a}
        order_ids = set(spec['activity_order'])
        if not order_ids.issubset(act_ids):
            logger.warning("activity_order referenziert unbekannte IDs: %s",
                           order_ids - act_ids)
            # Reparieren statt ablehnen
            spec['activity_order'] = [a['id'] for a in spec['activities']]
        return True

    def _parse_spec_json(self, raw: str) -> Dict:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        cleaned = re.sub(r'```json\s*', '', raw)
        cleaned = re.sub(r'```\s*', '', cleaned).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        m = re.search(r'\{.*\}', cleaned, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        try:
            from json_repair import json_repair
            return json_repair.loads(raw)
        except ImportError:
            pass 
            
        # Wenn alles scheitert, gib zumindest ein leeres Spec zurück statt abzustürzen
        raise ValueError(
            f"Phase 1: Kein valides JSON extrahierbar. "
            f"Rohausgabe (erste 200 Zeichen): {raw[:200]}"
        )

    def _resolve_model(self) -> Optional[str]:
        if self._override_model:
            return self._override_model
        ollama_cfg = self.config.get('ollama', {})
        primary    = ollama_cfg.get('primary_model')
        finetuned  = ollama_cfg.get('finetuned_model')
        if finetuned and self.ollama.model_exists(finetuned):
            return finetuned
        return primary
