"""
src/generation/generator.py  –  v3
XAML Generator mit Ablations-Unterstützung und Reproduzierbarkeit.

Verbesserungen gegenüber v2:
  NEU  ablation_condition Parameter: steuert welche Komponenten aktiv sind.
       'full_system'   – RAG + System-Prompt + Fine-Tuned Model (Standard)
       'no_rag'        – kein RAG-Kontext, sonst identisch
       'modelfile_only'– kein RAG, nur System-Prompt
       'rag_only'      – RAG aktiv, aber minimalster System-Prompt
       'lora_only'     – LoRA-Modell, kein RAG, kein erweiterter Prompt
       'zero_shot'     – Rohes Basismodell, keinerlei Anpassung
  NEU  seed Parameter: wird an OllamaClient weitergegeben (Ollama seed-Option).
       Sichert Reproduzierbarkeit aller Evaluierungsläufe.
  NEU  stream_generate(): liefert XAML Token-für-Token via Generator (SSE-kompatibel).
  FIX  generate() Rückgabe: vollständiges Dict {xaml_content, validation,
       score, attempt, output_path} – konsistent mit evaluate_model in trainer.py.
  NEU  _detect_category() nutzt jetzt gewichtetes Keyword-Scoring statt
       simple max()-Auswahl, identisch mit preprocessor.py.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Generator, List, Optional

import yaml

logger = logging.getLogger(__name__)

# ─── Namespace-Block (Studio-kompatibel) ──────────────────────────────────────
_NAMESPACES = (
    'xmlns="http://schemas.microsoft.com/netfx/2009/xaml/activities"\n'
    '         xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"\n'
    '         xmlns:mva="clr-namespace:Microsoft.VisualBasic.Activities;assembly=System.Activities"\n'
    '         xmlns:sap="http://schemas.microsoft.com/netfx/2009/xaml/activities/presentation"\n'
    '         xmlns:sap2010="http://schemas.microsoft.com/netfx/2010/xaml/activities/presentation"\n'
    '         xmlns:sap2016="http://schemas.microsoft.com/netfx/2016/xaml/activities/presentation"\n'
    '         xmlns:sads="http://schemas.microsoft.com/netfx/2010/xaml/activities/debugger"\n'
    '         xmlns:s="clr-namespace:System;assembly=System.Private.CoreLib"\n'
    '         xmlns:scg="clr-namespace:System.Collections.Generic;assembly=System.Private.CoreLib"\n'
    '         xmlns:sco="clr-namespace:System.Collections.ObjectModel;assembly=System.Private.CoreLib"\n'
    '         xmlns:ui="http://schemas.uipath.com/workflow/activities"\n'
    '         xmlns:uix="http://schemas.uipath.com/workflow/activities/uix"\n'
    '         xmlns:ue="clr-namespace:UiPath.Excel;assembly=UiPath.Excel.Activities"\n'
    '         xmlns:ueab="clr-namespace:UiPath.Excel.Activities.Business;assembly=UiPath.Excel.Activities"\n'
    '         xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"\n'
    '         mc:Ignorable="sap sap2010 sap2016 sads"'
)

# ─── Kategorie-Typische Argumente ─────────────────────────────────────────────
_CATEGORY_INPUTS: Dict[str, str] = {
    "sap_automation":      "in_SapSystemId, in_Mandant, in_Transaction, in_SapUser, in_SapPassword",
    "web_scraping":        "in_Url, in_BrowserType, in_TimeoutSeconds, out_ResultData",
    "excel_automation":    "in_FilePath, in_SheetName, in_StartRow, out_DataTable",
    "email_processing":    "in_MailFolder, in_SubjectFilter, in_AttachmentPath, out_MailCount",
    "file_operations":     "in_SourcePath, in_DestPath, in_FilePattern, in_BackupFlag",
    "database_operations": "in_ConnectionString, in_SqlQuery, in_TimeoutSeconds, out_DataTable",
    "api_integration":     "in_EndpointUrl, in_HttpMethod, in_AuthHeader, in_RequestBody, out_Response",
    "orchestrator_queue":  "in_QueueName, in_MaxRetries, io_TransactionItem, out_TransactionStatus",
    "general_automation":  "in_InputPath, in_OutputPath, in_ConfigFile, out_ResultMessage",
}

# ─── System-Prompt (vollständige Fassung) ─────────────────────────────────────
_SYSTEM_PROMPT = f"""\
Du bist ein hochspezialisierter UiPath RPA-Experte mit tiefem Wissen über
UiPath Studio, XAML-Struktur und Enterprise Best Practices.

DEINE AUFGABE: Generiere einen vollständigen, produktionsreifen UiPath XAML-Workflow.

═══════════════════════════════════════════════════
 ZWINGEND ERFORDERLICH – jede Regel ist Pflicht
═══════════════════════════════════════════════════
1. ROOT-ELEMENT: <Activity x:Class="Namespace.WorkflowName" {_NAMESPACES}>
   – NIEMALS <sap2016:WorkflowViewModel> oder andere ViewModel-Roots!
   – x:Class-Wert: CamelCase-Namespace aus der Aufgabe ableiten.

2. FEHLERBEHANDLUNG:
   – Äußerste Aktivität = <TryCatch DisplayName="Workflow TryCatch">
   – Catch: <Catch x:TypeArguments="s:Exception" faultVariable="exception">
   – Im Catch: LogMessage Level="Error" Message="[Name] FEHLER: {{exception.Message}}"
   – Im Catch: <Rethrow/> nach dem LogMessage (INNERHALB des Catch-Blocks!)
   – Finally: Ressourcen freigeben (Browser schließen, SAP abmelden etc.)

3. LOGGING (verpflichtend an diesen Stellen):
   – ERSTE Aktivität: LogMessage Level="Info" Message="[WorkflowName] Start"
   – LETZTE Aktivität: LogMessage Level="Info" Message="[WorkflowName] Abgeschlossen"
   – Kritische Schritte: LogMessage Level="Info" Message="[Schritt] ..."

4. ARGUMENTE & VARIABLEN:
   – Argumente: in_ (Input), out_ (Output), io_ (InOut) + PascalCase
   – Lokale Variablen: camelCase (dataTable, rowCount, resultText)
   – Typen explizit: x:TypeArguments="s:String", x:TypeArguments="sd:DataTable"

5. DisplayName="..." bei JEDER Aktivität, beschreibend und eindeutig.

6. SELEKTOREN: Platzhalter bei UI-Interaktionen:
   Selector="&lt;!-- SELEKTOR KONFIGURIEREN: Beschreibung des Elements --&gt;"

7. NAMESPACES: Alle oben aufgeführten xmlns vollständig übernehmen.
   – KEINE <|...|> Chat-Template-Token im Output
   – KEINE Markdown-Backticks
   – NUR reines XML

Antworte AUSSCHLIESSLICH mit dem XAML-Code.
"""

# ─── Minimaler System-Prompt (für Ablations-Bedingungen) ─────────────────────
_MINIMAL_SYSTEM_PROMPT = (
    "Generiere einen UiPath XAML-Workflow. Antworte nur mit XML."
)

# ─── Bekannte LLM-Artefakt-Muster ────────────────────────────────────────────
_ARTIFACT_PATTERNS = [
    (r'<\|[^|]*\|>',          ''),   # DeepSeek/Qwen Chat-Tokens ASCII
    (r'<｜[^｜]*｜>',          ''),   # DeepSeek/Qwen Chat-Tokens Unicode
    ('Privat€.CoreLib',        'Private.CoreLib'),
    ('Privat\u00e4.CoreLib',   'Private.CoreLib'),
    ('System.Privat\u00e4',    'System.Private'),
    ('CoreLi\u00df',           'CoreLib'),
    ('CoreLiß',                'CoreLib'),
    ('Activiti\u00e4s',        'Activities'),
    ('Activiti\u00e9s',        'Activities'),
    ('UiPa\u00efth',           'UiPath'),
    (r'\?>\s*\nxaml\s*\n',    '?>\n'),
    (r'```xml\s*',             ''),
    (r'```\s*$',               ''),
]


class XAMLGenerator:
    """
    Generiert UiPath XAML-Workflows via Ollama.

    Parameter:
      config_path         – Pfad zur config.yaml
      model_name          – Override für das Ollama-Modell (None = aus config)
      ablation_condition  – Steuert welche Komponenten aktiv sind
                            ('full_system' | 'no_rag' | 'modelfile_only' |
                             'rag_only' | 'lora_only' | 'zero_shot')
      seed                – Ollama-Seed für reproduzierbare Ergebnisse
    """

    def __init__(
        self,
        config_path: str = "config/config.yaml",
        model_name: Optional[str] = None,
        ablation_condition: str = "full_system",
        seed: int = 42,
    ):
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)
        self.gen_config         = self.config['generation']
        self.output_dir         = Path(self.gen_config['output_dir'])
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._ollama_client     = None
        self._rag_engine        = None
        self._override_model    = model_name
        self.ablation_condition = ablation_condition
        self.seed               = seed

    # ── Lazy Properties ──────────────────────────────────────────────────────

    @property
    def ollama(self):
        if self._ollama_client is None:
            from src.generation.ollama_client import OllamaClient
            self._ollama_client = OllamaClient()
        return self._ollama_client

    @property
    def rag(self):
        """RAG-Engine nur laden wenn die Ablations-Bedingung RAG erlaubt."""
        use_rag = self.ablation_condition in (
            'full_system', 'rag_only', 'rag_modelfile'
        )
        if self._rag_engine is None and use_rag and self.gen_config['rag']['enabled']:
            try:
                from src.generation.rag_engine import RAGEngine
                self._rag_engine = RAGEngine(self.gen_config['rag'])
            except Exception as e:
                logger.warning("RAG-Engine nicht verfügbar: %s", e)
        return self._rag_engine

    # ── Öffentliche API ──────────────────────────────────────────────────────

    def generate(
        self,
        task_description: str,
        category: Optional[str] = None,
        additional_requirements: Optional[str] = None,
        max_attempts: int = 3,
    ) -> Dict:
        """
        Generiert einen XAML-Workflow mit Retry bei Validierungsfehlern.

        Rückgabe-Dict:
          xaml_content  str   vollständiger XAML-Code
          validation    dict  Ergebnis von XAMLValidator.validate()
          score         int   total_score aus der Validation
          attempt       int   Anzahl benötigter Versuche
          output_path   str   gespeicherte XAML-Datei
          category      str   erkannte/übergebene Kategorie
          model         str   verwendetes Ollama-Modell
        """
        logger.info("Generiere XAML [%s]: %s...",
                    self.ablation_condition, task_description[:80])

        if category is None:
            category = self._detect_category(task_description)

        # RAG-Kontext (Ablations-gesteuert)
        context_examples: List[Dict] = []
        if self.rag:
            context_examples = self.rag.search(task_description, category)

        prompt   = self._build_prompt(
            task_description, category, additional_requirements, context_examples
        )
        template = self._get_template(category)
        system   = self._select_system_prompt()

        best_result: Optional[Dict] = None
        best_score  = 0
        error_hints = ""
        used_model  = ""

        for attempt in range(max_attempts):
            logger.info("  Versuch %d/%d", attempt + 1, max_attempts)
            try:
                enhanced = self._enhance_prompt(
                    prompt, template, error_hints, attempt
                )

                raw, used_model = self._call_llm(system, enhanced)
                xaml = self._extract_xaml(raw)
                xaml = self._sanitize_artifacts(xaml)

                from src.validation.validator import XAMLValidator
                validation = XAMLValidator().validate(xaml)
                score      = validation['total_score']
                logger.info("  Score: %d/100", score)

                if score > best_score:
                    best_score  = score
                    best_result = {
                        'xaml_content': xaml,
                        'validation':   validation,
                        'score':        score,
                        'attempt':      attempt + 1,
                        'category':     category,
                        'model':        used_model,
                    }

                threshold = self.config['validation'].get(
                    'llm_review_threshold', 7) * 10
                if score >= threshold:
                    break

                if validation.get('errors'):
                    error_hints = "\n".join(
                        f"  • {e}" for e in validation['errors'][:5]
                    )

            except Exception as e:
                logger.error("  Fehler in Versuch %d: %s", attempt + 1, e)
                if attempt == max_attempts - 1:
                    raise

        if not best_result:
            raise RuntimeError("XAML-Generierung fehlgeschlagen nach allen Versuchen")

        if self.gen_config['post_processing']['auto_format']:
            best_result['xaml_content'] = self._post_process(
                best_result['xaml_content'])

        output_path = self._save_output(best_result, task_description, category)
        best_result['output_path'] = str(output_path)

        logger.info("XAML generiert (Score: %d/100, Modell: %s): %s",
                    best_score, used_model, output_path)
        return best_result

    def stream_generate(
        self,
        task_description: str,
        category: Optional[str] = None,
        additional_requirements: Optional[str] = None,
    ) -> Generator[Dict, None, None]:
        """
        Streaming-Variante von generate().

        Liefert:
          {'event': 'start', 'category': str, 'model': str}
          {'event': 'token', 'token': str, 'done': False}
          {'event': 'done',  'xaml': str, 'score': int, 'validation': dict,
                             'ollama_stats': dict}
          {'event': 'error', 'error': str}  – bei Fehlern
        """
        if category is None:
            category = self._detect_category(task_description)

        context_examples: List[Dict] = []
        if self.rag:
            context_examples = self.rag.search(task_description, category)

        prompt   = self._build_prompt(
            task_description, category, additional_requirements, context_examples
        )
        system   = self._select_system_prompt()
        template = self._get_template(category)
        enhanced = self._enhance_prompt(prompt, template, "", 0)

        # Modell ermitteln
        model = self._override_model
        if model is None:
            ollama_cfg = self.config.get('ollama', {})
            finetuned  = ollama_cfg.get('finetuned_model')
            primary    = ollama_cfg.get('primary_model')
            model = (
                finetuned if finetuned and self.ollama.model_exists(finetuned)
                else primary
            )

        yield {'event': 'start', 'category': category, 'model': model or ''}

        full_text    = ""
        ollama_stats: Dict = {}

        try:
            for chunk in self.ollama.stream_generate(
                prompt=enhanced, model=model, system=system, seed=self.seed
            ):
                if chunk.get('type') == 'token':
                    token      = chunk['text']
                    full_text += token
                    yield {'event': 'token', 'token': token, 'done': False}
                elif chunk.get('type') == 'stats':
                    ollama_stats = chunk
        except Exception as e:
            logger.error("Stream-Fehler: %s", e)
            yield {'event': 'error', 'error': str(e)}
            return

        xaml = self._extract_xaml(full_text)
        xaml = self._sanitize_artifacts(xaml)
        if self.gen_config['post_processing']['auto_format']:
            xaml = self._post_process(xaml)

        from src.validation.validator import XAMLValidator
        validation = XAMLValidator().validate(xaml)

        yield {
            'event':        'done',
            'xaml':         xaml,
            'score':        validation['total_score'],
            'validation':   validation,
            'ollama_stats': ollama_stats,
        }

    # ── Prompt-Aufbau ─────────────────────────────────────────────────────────

    def _select_system_prompt(self) -> str:
        """Wählt System-Prompt basierend auf Ablations-Bedingung."""
        if self.ablation_condition == 'zero_shot':
            return ""   # keinerlei Anleitung
        if self.ablation_condition in ('rag_only', 'lora_only'):
            return _MINIMAL_SYSTEM_PROMPT
        return _SYSTEM_PROMPT   # 'full_system', 'modelfile_only', 'rag_modelfile'

    def _build_prompt(
        self,
        task: str,
        category: str,
        requirements: Optional[str],
        examples: List[Dict],
    ) -> str:
        """Baut strukturierten Prompt mit Kategorie-Kontext und RAG-Beispielen."""
        inputs = _CATEGORY_INPUTS.get(category, _CATEGORY_INPUTS["general_automation"])

        prompt = (
            f"Erstelle einen UiPath XAML-Workflow für folgende Aufgabe:\n\n"
            f"AUFGABE: {task}\n"
            f"KATEGORIE: {category}\n"
            f"TYPISCHE ARGUMENTE: {inputs}\n"
        )

        if requirements:
            prompt += f"\nZUSÄTZLICHE ANFORDERUNGEN:\n{requirements}\n"

        # RAG-Kontext einbetten (nur wenn Ablations-Bedingung es erlaubt)
        use_rag = self.ablation_condition in (
            'full_system', 'rag_only', 'rag_modelfile'
        )
        if use_rag and examples:
            prompt += "\n── BEST-PRACTICE REFERENZ-BEISPIELE (aus Unternehmenskorpus) ──\n"
            for i, ex in enumerate(examples[:3]):
                cat          = ex.get('category', 'general')
                acts         = ", ".join(ex.get('activities', [])[:8])
                xaml_preview = ex.get('xaml_content', '')[:900]
                score        = ex.get('quality_score', '?')
                prompt += (
                    f"\nBeispiel {i+1} [{cat}] Score={score}/100 – "
                    f"Aktivitäten: {acts}\n"
                    f"```xml\n{xaml_preview}\n... (gekürzt)\n```\n"
                )
        elif self.ablation_condition not in ('zero_shot', 'rag_only', 'lora_only'):
            prompt += (
                "\n(Kein RAG-Kontext verfügbar – generiere nach den "
                "System-Prompt-Regeln ohne Referenzbeispiele)\n"
            )

        # Pflicht-Checkliste (nur bei nicht-Zero-Shot)
        if self.ablation_condition != 'zero_shot':
            prompt += (
                "\n── PFLICHT-CHECKLISTE ──\n"
                "☑ Root-Element: <Activity x:Class=\"...\"> (KEIN ViewModel-Root)\n"
                "☑ Alle Namespaces vollständig\n"
                "☑ TryCatch als äußerste Aktivität\n"
                "☑ LogMessage Start + Abgeschlossen + FEHLER im Catch\n"
                "☑ Rethrow INNERHALB des Catch-Blocks\n"
                f"☑ Argumente mit in_/out_-Präfix ({inputs.split(',')[0].strip()} etc.)\n"
                "☑ DisplayName bei jeder Aktivität\n"
                "☑ Selektor-Platzhalter bei UI-Interaktionen\n"
                "\nGeneriere jetzt den vollständigen XAML-Code:\n"
            )
        else:
            prompt += "\nGeneriere den XAML-Workflow:\n"

        return prompt

    def _enhance_prompt(
        self,
        base_prompt: str,
        template: Optional[str],
        error_hints: str,
        attempt: int,
    ) -> str:
        """Erweitert Prompt je nach Versuch (Template-Orientierung / Fehler-Hints)."""
        if template and attempt == 0:
            return (
                f"{base_prompt}\n\n"
                f"Struktur-Orientierung (ähnliches Template):\n"
                f"```xml\n{template[:1200]}\n```"
            )
        if error_hints:
            return (
                f"{base_prompt}\n\n"
                f"FEHLER AUS LETZTEM VERSUCH – bitte korrigieren:\n"
                f"{error_hints}\n\n"
                f"Generiere den vollständigen Workflow erneut mit diesen Korrekturen."
            )
        return base_prompt

    def _call_llm(self, system: str, prompt: str) -> tuple[str, str]:
        """
        Ruft Ollama auf und gibt (raw_response, model_name) zurück.
        Seed wird für Reproduzierbarkeit übergeben.
        """
        model = self._override_model

        # Ablations-Bedingung bestimmt ob Fine-Tuned-Modell genutzt wird
        if model is None:
            ollama_cfg = self.config.get('ollama', {})
            finetuned  = ollama_cfg.get('finetuned_model')
            primary    = ollama_cfg.get('primary_model')

            use_finetuned = self.ablation_condition in (
                'full_system', 'lora_only', 'rag_modelfile'
            )
            if use_finetuned and finetuned and self.ollama.model_exists(finetuned):
                model = finetuned
            else:
                model = primary

        if system:
            messages = [
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ]
            raw = self.ollama.chat(messages=messages, model=model, seed=self.seed)
        else:
            raw = self.ollama.generate(prompt=prompt, model=model, seed=self.seed)

        return raw, (model or "")

    # ── Kategorieerkennung (gewichtet, identisch mit preprocessor.py) ────────

    def _detect_category(self, description: str) -> str:
        """
        Gewichtetes Keyword-Scoring für Kategorieerkennung.
        STRONG-Treffer zählen 3×, MEDIUM 2×, WEAK 1×.
        Identische Logik wie preprocessor._determine_category() für Konsistenz.
        """
        rules: Dict[str, Dict[str, List[str]]] = {
            'sap_automation':      {
                'strong': ['sap', 'me22n', 'va01', 'fb60', 'migo', 'miro', 'tcode', 'bapi'],
                'medium': ['transaktion', 'buchung', 'fiori', 'logon'],
                'weak':   ['mandant', 'rfc', 'idoc'],
            },
            'web_scraping':        {
                'strong': ['openbrowser', 'closebrowser', 'webctrl', 'navigateto'],
                'medium': ['browser', 'web', 'url', 'website', 'portal', 'klick', 'scrape'],
                'weak':   ['selector', 'html'],
            },
            'excel_automation':    {
                'strong': ['excel', 'xlsx', 'xls', 'readrange', 'writerange', 'excelscope'],
                'medium': ['tabelle', 'sheet', 'zeile', 'spalte', 'pivot'],
                'weak':   ['workbook', 'spreadsheet'],
            },
            'email_processing':    {
                'strong': ['getmailmessages', 'sendmail', 'saveattachments'],
                'medium': ['email', 'mail', 'outlook', 'postfach', 'smtp', 'imap'],
                'weak':   ['anhang', 'betreff'],
            },
            'file_operations':     {
                'strong': ['copyfile', 'movefile', 'deletefile', 'getfiles'],
                'medium': ['datei', 'ordner', 'kopier', 'verschieb', 'archiv', 'zip'],
                'weak':   ['directory', 'path'],
            },
            'database_operations': {
                'strong': ['executequery', 'connectionstring', 'databaseconnection'],
                'medium': ['sql', 'select', 'insert', 'update', 'datenbank'],
                'weak':   ['db', 'database', 'query'],
            },
            'api_integration':     {
                'strong': ['httprequest', 'invokerestservice', 'deserializejson'],
                'medium': ['api', 'rest', 'json', 'endpoint', 'webhook'],
                'weak':   ['bearer', 'oauth', 'token'],
            },
            'orchestrator_queue':  {
                'strong': ['addqueueitem', 'getqueueitem', 'settransactionstatus'],
                'medium': ['queue', 'orchestrator', 'warteschlange', 'reframework'],
                'weak':   ['transactionitem', 'asset'],
            },
        }

        desc_lower = description.lower()
        scores: Dict[str, float] = {}
        for cat, kw_groups in rules.items():
            s = 0.0
            s += sum(3 for kw in kw_groups.get('strong', []) if kw in desc_lower)
            s += sum(2 for kw in kw_groups.get('medium', []) if kw in desc_lower)
            s += sum(1 for kw in kw_groups.get('weak',   []) if kw in desc_lower)
            if s > 0:
                scores[cat] = s

        if not scores:
            return 'general_automation'
        return max(scores, key=scores.get)

    # ── Template-Laden ────────────────────────────────────────────────────────

    def _get_template(self, category: str) -> Optional[str]:
        """Lädt passendes XAML-Template als Struktur-Orientierung."""
        tpl_dir = Path(self.gen_config['templates']['template_dir'])
        for name in [category, 'excel_processing', 'sap_automation', 'general']:
            p = tpl_dir / f"{name}.xaml"
            if p.exists():
                return p.read_text(encoding='utf-8')
        return None

    # ── XAML-Extraktion & Bereinigung ─────────────────────────────────────────

    def _extract_xaml(self, raw: str) -> str:
        """Extrahiert XAML-Block aus LLM-Rohantwort."""
        raw = re.sub(r'```xml\s*', '', raw)
        raw = re.sub(r'```\s*', '', raw)

        for marker in ['<?xml', '<Activity', '<Sequence', '<TryCatch']:
            pos = raw.find(marker)
            if pos != -1:
                raw = raw[pos:]
                break

        # Unvollständiges Ende reparieren
        if raw and not raw.rstrip().endswith('>'):
            last = raw.rfind('</')
            if last > 0:
                end = raw.find('>', last)
                if end > 0:
                    raw = raw[:end + 1]

        return raw.strip()

    def _sanitize_artifacts(self, xaml: str) -> str:
        """Bereinigt bekannte LLM-Artefakte (DeepSeek/Qwen Token, Encoding-Fehler)."""
        for pattern, replacement in _ARTIFACT_PATTERNS:
            if isinstance(pattern, str):
                xaml = xaml.replace(pattern, replacement)
            else:
                xaml = re.sub(pattern, replacement, xaml)
        # Doppelte Leerzeilen
        xaml = re.sub(r'\n{3,}', '\n\n', xaml)
        return xaml

    def _post_process(self, xaml: str) -> str:
        """Stellt XML-Deklaration sicher und bereinigt führenden Whitespace."""
        xaml = xaml.strip()
        if not xaml.startswith('<?xml'):
            xaml = '<?xml version="1.0" encoding="utf-8"?>\n' + xaml
        return xaml

    # ── Persistenz ────────────────────────────────────────────────────────────

    def _save_output(self, result: Dict, task: str, category: str) -> Path:
        """Speichert XAML-Datei und zugehörige Metadaten-JSON."""
        ts    = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe  = re.sub(r'[^\w\s-]', '', task[:40]).strip()
        safe  = re.sub(r'\s+', '_', safe)
        fname = f"{category}_{safe}_{ts}.xaml"
        out   = self.output_dir / fname

        out.write_text(result['xaml_content'], encoding='utf-8')

        meta = {
            'task':              task,
            'category':          category,
            'score':             result['score'],
            'attempts':          result['attempt'],
            'ablation_condition': self.ablation_condition,
            'model':             result.get('model', ''),
            'component_scores':  result.get('validation', {}).get('component_scores', {}),
            'generated_at':      ts,
        }
        out.with_suffix('.json').write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding='utf-8'
        )
        return out
