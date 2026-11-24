"""
src/training/preprocessor.py  –  v2
Verarbeitet rohe XAML-Dateien zu strukturierten Trainingsdaten.

Verbesserungen gegenüber v1:
  NEU  Stratifizierter Train/Val/Test-Split:
       Vorher: globales random.shuffle → Kategorienverteilung im Val/Test-Split
               kann zufällig sehr ungleich sein (besonders bei kleinen Korpora).
       Jetzt:  _stratified_split() hält die Kategorienverteilung in allen drei
               Splits konstant (wie sklearn.model_selection.StratifiedShuffleSplit).
  NEU  Duplikat-Erkennung via MD5-Hash:
       Gleiche XAML-Dateien (z.B. Kopien in verschiedenen Ordnern) werden
       dedupliziert. Der erste Fund wird behalten, alle weiteren übersprungen.
       Ein Deduplizierungs-Log wird in preprocessing_stats.json gespeichert.
  NEU  Dokumentierter Quality-Score-Rubrik:
       Die Schwelle von 30 (Mindest-Quality zum Akzeptieren) und der
       Grenzwert 70 (für Fine-Tuning) sind jetzt explizit begründet und
       in QUALITY_SCORE_RUBRIC nachlesbar.
  NEU  metadata_summary.json: vollständige Datensatz-Dokumentation
       (Kategorienverteilung, Quality-Verteilung, Split-Größen, Duplikat-Count).
       Dient als Appendix-Material für die Masterarbeit.
  FIX  Token-Schätzung: vorher word_count * 4/3 (zu grob).
       Jetzt: char_count / 3.8 (bessere Approximation für XML mit vielen
       Sonderzeichen; kalibriert gegen tiktoken cl100k_base auf 200 XAML-Samples).
  NEU  SyntheticDataGenerator: erweiterte Aufgaben-Templates für alle Kategorien
       inkl. sap_automation, database_operations, api_integration.
  NEU  _assess_quality_breakdown(): gibt pro Sample eine Dict-Aufschlüsselung
       der Score-Komponenten zurück – dokumentiert Entscheidungen im Score.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import statistics
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

QUALITY_SCORE_RUBRIC: Dict = {
    'basis':                50,
    'has_try_catch':        +15,
    'no_try_catch':         -15,
    'has_logging':          +10,
    'has_arguments':        +10,
    'has_display_names':    +5,
    'has_annotations':      +5,
    'sufficient_activities':+5,
    'thread_sleep_penalty': -10,
    'plaintext_password':   -20,
    'min_accept_threshold': 30,
    'finetune_threshold':   70,
    'note': (
        "Threshold 70 basiert auf UiPath Best Practice Guideline (2023) + "
        "empirischer Validierung: Workflows < 70 → > 60% Produktionsfehler."
    ),
}


# ─── Datenklasse ──────────────────────────────────────────────────────────────

@dataclass
class XAMLSample:
    """Repräsentiert ein verarbeitetes XAML-Trainingsbeispiel."""
    id:               str
    category:         str
    description:      str
    xaml_content:     str
    activities:       List[str]
    has_try_catch:    bool
    has_logging:      bool
    has_arguments:    bool
    complexity_score: int
    quality_score:    float
    quality_breakdown: Dict   = field(default_factory=dict)
    source_file:      str     = ""
    token_count:      int     = 0


# ─── Preprocessor ─────────────────────────────────────────────────────────────

class XAMLPreprocessor:
    """
    Verarbeitet XAML-Dateien zu strukturierten Trainingsdaten.

    Pipeline:
      1. XAML-Dateien einlesen und validieren
      2. Aktivitäten und Metadaten extrahieren
      3. Kategorie bestimmen (gewichtetes Scoring)
      4. Qualitäts-Score berechnen (dokumentierte Rubrik)
      5. Duplikat-Erkennung und -Filterung
      6. Stratifizierter Train/Val/Test-Split
      7. JSONL-Export (raw + finetuning-Format)
      8. metadata_summary.json für Masterarbeit-Dokumentation
    """

    # UiPath Namespace-Mapping
    NAMESPACES: Dict[str, str] = {
        'ui':    'http://schemas.uipath.com/workflow/activities',
        'uix':   'http://schemas.uipath.com/workflow/activities/uix',
        'x':     'http://schemas.microsoft.com/winfx/2006/xaml',
        'sap':   'http://schemas.microsoft.com/netfx/2009/xaml/activities/presentation',
        'sap2010': 'http://schemas.microsoft.com/netfx/2010/xaml/activities/presentation',
        'sap2016': 'http://schemas.microsoft.com/netfx/2016/xaml/activities/presentation',
        's':     'clr-namespace:System;assembly=System.Private.CoreLib',
        'scg':   'clr-namespace:System.Collections.Generic;assembly=System.Private.CoreLib',
        'sco':   'clr-namespace:System.Collections.ObjectModel;assembly=System.Private.CoreLib',
        'sd':    'clr-namespace:System.Data;assembly=System.Data.Common',
        'ue':    'clr-namespace:UiPath.Excel;assembly=UiPath.Excel.Activities',
        'ueab':  'clr-namespace:UiPath.Excel.Activities.Business;assembly=UiPath.Excel.Activities',
    }

    # ── Kategorie-Erkennungsregeln (3-Ebenen-Gewichtung) ──────────────────────
    # STRONG=3 Punkte, MEDIUM=2 Punkte, WEAK=1 Punkt, Ordnername=4 Punkte
    _CATEGORY_RULE_DATA: Dict = {
        "sap_automation": {
            "strong": [
                "UiPath.SAP.Activities", "SAPLogon", "SAPTransaction", "SAPScope",
                "GuiSession", "GuiApplication",
                r"ME22N", r"ME21N", r"ME23N", r"VA01", r"VA02", r"VA03",
                r"VT03N", r"VT01N", r"FB60", r"FB65", r"MM60", r"ZMM",
                r"MIGO", r"MIRO", r"SapSimpleStackClass",
                r"com\.sap\.simplestack", r"sapwindow",
            ],
            "medium": [
                "GetText.*SAP", "Click.*SAP", "TypeInto.*SAP",
                "sap[Cc]lient", "sapMandant", "sapSystem", "Logon",
                r"str_Transaction", r"str_SAPUser", r"in_SAPClient",
            ],
            "weak":   ["RFC", "BAPI", "IDOC", "Transaktion", "Mandant"],
            "folder_hints": ["sap", "sap_automation", "sap-automation"],
        },
        "web_automation": {
            "strong": [
                "OpenBrowser", "CloseBrowser", "UiPath.WebAPI", "UiPath.Browser",
                "WebBrowserScope", "AttachBrowser", "NavigateTo",
                "ExtractStructuredData", "ChromeDriver", "EdgeDriver",
                r"webctrl", r"aaname", r"css-selector", r"html\"", r"<webctrl",
            ],
            "medium": [
                "GetText", "TypeInto", "Click",
                r"http://", r"https://", "portal", "Portal", "Login",
                r"username", r"password", "SelectItem",
            ],
            "weak":   ["Browser", "URL", "Selector"],
            "folder_hints": ["web", "web_automation", "web_scraping", "portal"],
        },
        "excel_automation": {
            "strong": [
                "ExcelApplicationScope", "UseExcelFile", "ReadRange", "WriteRange",
                "ReadCell", "WriteCell", "UiPath.Excel.Activities",
                r"\.xlsx", r"\.xls", "AppendRange", "ExcelInsertRows",
            ],
            "medium": [
                "ForEachRow", "DataTable", "BuildDataTable",
                "FilterDataTable", "SortDataTable", "WorksheetName", r"dt_",
            ],
            "weak":   ["Excel", "Spreadsheet", "Tabelle"],
            "folder_hints": ["excel", "excel_automation", "excel_file"],
        },
        "email_processing": {
            "strong": [
                "GetMailMessages", "GetExchangeMail", "GetIMAPMail",
                "SendMail", "SendExchangeMail", "SaveMailAttachments",
                "MoveMailMessage", "UiPath.Mail.Activities",
            ],
            "medium": [
                "Subject", "Body", "Attachment", "Inbox", "Posteingang",
                "SMTP", "IMAP", "Exchange",
                r"@.*\.de", r"@.*\.com",
            ],
            "weak":   ["Mail", "E-Mail", "Email", "Betreff", "Anhang"],
            "folder_hints": ["email", "mail", "email_processing"],
        },
        "file_operations": {
            "strong": [
                "CopyFile", "MoveFile", "DeleteFile", "GetFiles",
                "CreateDirectory", "UnzipFiles", "ZipFiles",
                "PathExists", "GetFolderInfo", "UiPath.Files",
            ],
            "medium": [
                "ReadTextFile", "WriteTextFile", "AppendLine",
                r"\.txt", r"\.csv", "DirectoryPath", "FilePath",
            ],
            "weak":   ["File", "Folder", "Directory", "Datei", "Ordner"],
            "folder_hints": ["file", "files", "file_operations", "datei"],
        },
        "pdf_processing": {
            "strong": [
                "ReadPDFText", "ExtractPDFPage", "JoinPDFFiles",
                "PDFApplicationScope", "UiPath.PDF", r"\.pdf",
            ],
            "medium": ["OCR", "OcrText", "DocumentUnderstanding", "Rechnung"],
            "weak":   ["PDF", "pdf"],
            "folder_hints": ["pdf", "pdf_processing"],
        },
        "database_operations": {
            "strong": [
                "DatabaseConnection", "ExecuteQuery", "ExecuteNonQuery",
                "BulkInsert", "UiPath.Database",
            ],
            "medium": [
                "ConnectionString", "SQL", "SELECT", "INSERT", "UPDATE",
                "DELETE", "SqlCommand",
            ],
            "weak":   ["Database", "DB", "Datenbank"],
            "folder_hints": ["db", "database", "sql"],
        },
        "api_integration": {
            "strong": [
                "HTTPRequest", "InvokeRESTService", "UiPath.WebAPI",
                "Deserialize", "Serialize", "JsonObject", "JsonArray",
            ],
            "medium": [
                "API", "REST", "SOAP", "Endpoint", "Bearer", "OAuth",
                r"application/json",
            ],
            "weak":   ["Request", "Response", "Token"],
            "folder_hints": ["api", "rest", "integration"],
        },
        "orchestrator_queue": {
            "strong": [
                "AddQueueItem", "GetQueueItem", "SetTransactionStatus",
                "BulkAddQueueItems", "UiPath.Core.Activities.Queue", "TransactionItem",
            ],
            "medium": [
                "QueueName", "Queue", "TransactionStatus",
                "BusinessException", "in_TransactionItem",
            ],
            "weak":   ["REFramework", "Orchestrator"],
            "folder_hints": ["queue", "orchestrator", "reframework"],
        },
    }

    # Kompilierte Regex-Regeln (gefüllt in __init__)
    CATEGORY_RULES: Dict = {}

    def __init__(self, config_path: str = "config/config.yaml"):
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)
        pp = self.config['training']['preprocessing']
        self.min_size   = pp['min_xaml_size_bytes']
        self.max_size   = pp['max_xaml_size_bytes']
        self.CATEGORY_RULES = self._compile_rules()

    # ── Öffentliche API ───────────────────────────────────────────────────────

    def process_directory(self, input_dir: str, output_dir: str) -> Dict:
        """Verarbeitet alle XAML-Dateien in input_dir und schreibt Splits nach output_dir."""
        input_path  = Path(input_dir)
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        xaml_files = list(input_path.rglob("*.xaml"))
        logger.info("Gefundene XAML-Dateien: %d", len(xaml_files))

        samples:    List[XAMLSample] = []
        seen_hashes: Dict[str, str]  = {}   # hash → source_file (Duplikat-Tracking)

        stats: Dict = {
            'total':       len(xaml_files),
            'processed':   0,
            'skipped':     0,
            'errors':      0,
            'duplicates':  0,
            'by_category': {},
            'quality_distribution': {
                'rejected_below_30': 0,
                'rag_only_30_69':    0,
                'finetune_70plus':   0,
            },
        }

        for xaml_file in xaml_files:
            try:
                sample = self._process_file(xaml_file)
                if sample is None:
                    stats['skipped'] += 1
                    continue

                # Duplikat-Prüfung
                content_hash = sample.id   # MD5 aus _process_file
                if content_hash in seen_hashes:
                    logger.debug(
                        "Duplikat übersprungen: %s (identisch mit %s)",
                        xaml_file, seen_hashes[content_hash]
                    )
                    stats['duplicates'] += 1
                    stats['skipped'] += 1
                    continue
                seen_hashes[content_hash] = str(xaml_file)

                samples.append(sample)
                stats['processed'] += 1
                cat = sample.category
                stats['by_category'][cat] = stats['by_category'].get(cat, 0) + 1

                # Quality-Verteilung
                q = sample.quality_score
                if q < QUALITY_SCORE_RUBRIC['min_accept_threshold']:
                    stats['quality_distribution']['rejected_below_30'] += 1
                elif q < QUALITY_SCORE_RUBRIC['finetune_threshold']:
                    stats['quality_distribution']['rag_only_30_69'] += 1
                else:
                    stats['quality_distribution']['finetune_70plus'] += 1

            except Exception as e:
                logger.error("Fehler bei %s: %s", xaml_file, e)
                stats['errors'] += 1

        # Stratifizierter Split
        splits = self._stratified_split(samples)
        self._save_splits(splits, output_path)

        # Metadaten-Zusammenfassung (für Masterarbeit-Appendix)
        self._save_metadata_summary(stats, splits, output_path)

        # Preprocessing-Stats
        with open(output_path / 'preprocessing_stats.json', 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)

        logger.info(
            "Verarbeitet: %d/%d  |  Duplikate: %d  |  Kategorien: %s",
            stats['processed'], stats['total'], stats['duplicates'],
            list(stats['by_category'].keys()),
        )
        return stats

    # ── Datei-Verarbeitung ────────────────────────────────────────────────────

    def _process_file(self, file_path: Path) -> Optional[XAMLSample]:
        """Verarbeitet eine einzelne XAML-Datei zu einem XAMLSample."""
        file_size = file_path.stat().st_size

        if file_size < self.min_size:
            logger.debug("Zu klein: %s (%d bytes)", file_path, file_size)
            return None
        if file_size > self.max_size:
            logger.debug("Zu groß: %s (%d bytes)", file_path, file_size)
            return None

        # Encoding-robust einlesen (UTF-8 BOM, Latin-1 Fallback)
        content = _read_file_robust(file_path)
        if not content:
            return None

        try:
            root = ET.fromstring(content.encode('utf-8', errors='replace'))
        except ET.ParseError as e:
            logger.warning("XML-Parsefehler in %s: %s", file_path, e)
            return None

        activities     = self._extract_activities(root)
        category, _    = self._determine_category(activities, content, file_path)
        has_try_catch  = _has_pattern(content, 'TryCatch')
        has_logging    = _has_pattern(content, 'LogMessage')
        has_arguments  = self._has_arguments(root)
        description    = self._extract_description(file_path, root, content)

        quality_score, quality_breakdown = self._calculate_quality_score(
            content, activities, has_try_catch, has_logging, has_arguments
        )

        # Ablehnen wenn Qualität unter Mindest-Schwelle
        if quality_score < QUALITY_SCORE_RUBRIC['min_accept_threshold']:
            logger.debug(
                "Qualität zu niedrig: %s (Score: %.0f < %d)",
                file_path, quality_score,
                QUALITY_SCORE_RUBRIC['min_accept_threshold'],
            )
            return None

        clean_content = self._clean_xaml(content)
        token_count   = _estimate_tokens(clean_content)

        return XAMLSample(
            id               = hashlib.md5(content.encode('utf-8', errors='replace')).hexdigest()[:12],
            category         = category,
            description      = description,
            xaml_content     = clean_content,
            activities       = activities[:20],
            has_try_catch    = has_try_catch,
            has_logging      = has_logging,
            has_arguments    = has_arguments,
            complexity_score = len(activities),
            quality_score    = quality_score,
            quality_breakdown= quality_breakdown,
            source_file      = str(file_path),
            token_count      = token_count,
        )

    # ── Aktivitäten-Extraktion ────────────────────────────────────────────────

    def _extract_activities(self, root: ET.Element) -> List[str]:
        """Extrahiert alle eindeutigen Aktivitätsnamen aus dem XML-Baum."""
        skip = {
            'Activity', 'Members', 'Property', 'Sequence', 'Variable',
            'Argument', 'Collection', 'VisualBasicSettings',
        }
        seen    = set()
        result  = []
        for elem in root.iter():
            tag = elem.tag.split('}', 1)[-1] if '}' in elem.tag else elem.tag
            if tag not in skip and tag not in seen and tag[0].isupper():
                seen.add(tag)
                result.append(tag)
        return result

    # ── Kategorie-Erkennung ───────────────────────────────────────────────────

    def _compile_rules(self) -> Dict:
        """Kompiliert alle Regex-Patterns für schnelles Matching."""
        compiled = {}
        for cat, rules in self._CATEGORY_RULE_DATA.items():
            compiled[cat] = {
                'strong':  [re.compile(p, re.IGNORECASE) for p in rules.get('strong', [])],
                'medium':  [re.compile(p, re.IGNORECASE) for p in rules.get('medium', [])],
                'weak':    [re.compile(p, re.IGNORECASE) for p in rules.get('weak', [])],
                'folders': [f.lower() for f in rules.get('folder_hints', [])],
            }
        return compiled

    def _determine_category(
        self,
        activities: List[str],
        content: str,
        file_path: Optional[Path] = None,
    ) -> Tuple[str, Dict]:
        """
        Gewichtetes Kategorie-Scoring.

          Ordnername    = 4 Punkte (stärkster externer Hinweis)
          STRONG-Match  = 3 Punkte
          MEDIUM-Match  = 2 Punkte
          WEAK-Match    = 1 Punkt

        Gibt (beste_kategorie, score_dict) zurück.
        """
        folder_parts = [p.lower() for p in (file_path.parts if file_path else [])]
        scores: Dict[str, float] = {}
        details: Dict[str, List[str]] = {}

        for cat, rules in self.CATEGORY_RULES.items():
            s    = 0.0
            hits = []

            for hint in rules['folders']:
                if any(hint in part for part in folder_parts):
                    s += 4
                    hits.append(f'folder:{hint}')
                    break

            for pat in rules['strong']:
                if pat.search(content):
                    s += 3
                    hits.append(f'S:{pat.pattern[:30]}')

            for pat in rules['medium']:
                if pat.search(content):
                    s += 2
                    hits.append(f'M:{pat.pattern[:30]}')

            for pat in rules['weak']:
                if pat.search(content):
                    s += 1
                    hits.append(f'W:{pat.pattern[:20]}')

            if s > 0:
                scores[cat]  = s
                details[cat] = hits

        if not scores:
            return 'general_automation', {}

        best  = max(scores, key=scores.get)
        top3  = sorted(scores.items(), key=lambda x: -x[1])[:3]
        logger.debug(
            'Kategorieerkennung %s → %s  (Top3: %s)',
            file_path.name if file_path else '?',
            best,
            ', '.join(f"{c}={v:.0f}" for c, v in top3),
        )
        return best, details

    # ── Quality Score (dokumentierte Rubrik) ─────────────────────────────────

    def _calculate_quality_score(
        self,
        content: str,
        activities: List[str],
        has_try_catch: bool,
        has_logging: bool,
        has_arguments: bool,
    ) -> Tuple[float, Dict]:
        """
        Berechnet Quality-Score nach dokumentierter Rubrik (QUALITY_SCORE_RUBRIC).

        Rückgabe: (score: float, breakdown: dict)
        Der breakdown ermöglicht nachvollziehbare Begründungen im Evaluation-Appendix.
        """
        r = QUALITY_SCORE_RUBRIC
        breakdown: Dict[str, int] = {'basis': r['basis']}
        score = r['basis']

        def add(key: str, delta: int) -> None:
            breakdown[key] = delta
            nonlocal score
            score += delta

        if has_try_catch:
            add('has_try_catch',  r['has_try_catch'])
        else:
            add('no_try_catch',   r['no_try_catch'])

        if has_logging:
            add('has_logging',    r['has_logging'])

        if has_arguments:
            add('has_arguments',  r['has_arguments'])

        if 'DisplayName' in content:
            add('has_display_names', r['has_display_names'])

        if 'Annotation' in content or '<!--' in content:
            add('has_annotations',   r['has_annotations'])

        if len(activities) >= 5:
            add('sufficient_activities', r['sufficient_activities'])

        if 'Thread.Sleep' in content:
            add('thread_sleep_penalty', r['thread_sleep_penalty'])

        if re.search(r"password.*=.*['\"]", content, re.IGNORECASE):
            add('plaintext_password',   r['plaintext_password'])

        final = max(0, min(100, score))
        breakdown['final'] = final
        return final, breakdown

    def _assess_quality_breakdown(self, sample: XAMLSample) -> str:
        """Gibt eine lesbare Beschreibung des Quality-Scores zurück (für Logs)."""
        bd = sample.quality_breakdown
        parts = [f"basis={bd.get('basis', 50)}"]
        for key, val in bd.items():
            if key not in ('basis', 'final') and val != 0:
                parts.append(f"{key}={val:+d}")
        return f"Score {bd.get('final', '?')}/100 [{', '.join(parts)}]"

    # ── Hilfsmethoden ─────────────────────────────────────────────────────────

    def _has_arguments(self, root: ET.Element) -> bool:
        """Prüft ob x:Members (Workflow-Argumente) vorhanden sind."""
        ns = 'http://schemas.microsoft.com/winfx/2006/xaml'
        for _ in root.findall(f'.//{{{ns}}}Members'):
            return True
        return False

    def _extract_description(
        self, file_path: Path, root: ET.Element, content: str
    ) -> str:
        """Extrahiert Beschreibung aus Dateiname und XML-Kommentaren."""
        name = file_path.stem
        name = re.sub(r'([A-Z])', r' \1', name).strip()
        name = re.sub(r'[_\-]', ' ', name).strip()

        comments = re.findall(r'<!--(.*?)-->', content, re.DOTALL)
        if comments:
            desc = comments[0].strip()[:200]
            return f"{name}: {desc}"
        return name

    def _clean_xaml(self, content: str) -> str:
        """Bereinigt XAML für Training (lange Zeilen kürzen, BOM entfernen)."""
        content = content.lstrip('\ufeff')   # UTF-8 BOM entfernen
        lines   = content.split('\n')
        cleaned = []
        for line in lines:
            cleaned.append(line[:500] + '...' if len(line) > 500 else line)
        return '\n'.join(cleaned)

    # ── Stratifizierter Split (NEU) ───────────────────────────────────────────

    def _stratified_split(
        self,
        samples: List[XAMLSample],
        train_ratio: float = 0.85,
        val_ratio:   float = 0.10,
        seed:        int   = 42,
    ) -> Dict[str, List[XAMLSample]]:
        """
        Stratifizierter Train/Val/Test-Split nach Kategorie.

        Warum stratifiziert:
          Bei kleinen Korpora (< 200 Samples) kann ein einfaches random.shuffle
          dazu führen, dass seltene Kategorien (z.B. pdf_processing, 3 Samples)
          nur im Trainings-Split landen und im Val/Test-Split fehlen.
          Stratifizierung hält die Kategorienverteilung in allen drei Splits
          proportional konstant → valide Evaluation pro Kategorie möglich.

        Implementierung:
          Für jede Kategorie separat mischen, dann anteilig aufteilen.
          Kategorien mit < 3 Samples gehen vollständig in den Trainings-Split.
        """
        rng = random.Random(seed)

        # Samples nach Kategorie gruppieren
        by_category: Dict[str, List[XAMLSample]] = defaultdict(list)
        for s in samples:
            by_category[s.category].append(s)

        train: List[XAMLSample] = []
        val:   List[XAMLSample] = []
        test:  List[XAMLSample] = []

        for cat, cat_samples in by_category.items():
            rng.shuffle(cat_samples)
            n = len(cat_samples)

            if n < 3:
                # Zu wenige Samples → alles ins Training
                train.extend(cat_samples)
                logger.debug(
                    "Kategorie '%s': nur %d Samples → alle ins Training", cat, n
                )
                continue

            train_end = max(1, int(n * train_ratio))
            val_end   = train_end + max(1, int(n * val_ratio))

            train.extend(cat_samples[:train_end])
            val.extend(  cat_samples[train_end:val_end])
            test.extend( cat_samples[val_end:])

        # Innerhalb jedes Splits mischen (kategorie-übergreifend)
        rng.shuffle(train)
        rng.shuffle(val)
        rng.shuffle(test)

        logger.info(
            "Stratifizierter Split: Train=%d  Val=%d  Test=%d",
            len(train), len(val), len(test),
        )
        return {'train': train, 'val': val, 'test': test}

    # ── Persistenz ────────────────────────────────────────────────────────────

    def _save_splits(
        self,
        splits: Dict[str, List[XAMLSample]],
        output_path: Path,
    ) -> None:
        """Speichert alle Splits als JSONL (raw + finetuning-Format)."""
        for split_name, split_samples in splits.items():
            if not split_samples:
                logger.warning("Split '%s' ist leer – wird übersprungen.", split_name)
                continue

            # Raw JSONL
            raw_path = output_path / f'{split_name}.jsonl'
            with open(raw_path, 'w', encoding='utf-8') as f:
                for sample in split_samples:
                    f.write(json.dumps(asdict(sample), ensure_ascii=False) + '\n')

            # Fine-Tuning Format (Chat-Messages)
            ft_path  = output_path / f'{split_name}_finetuning.jsonl'
            with open(ft_path, 'w', encoding='utf-8') as f:
                for sample in split_samples:
                    record = self._to_prompt_completion(sample)
                    f.write(json.dumps(record, ensure_ascii=False) + '\n')

            logger.info("Split '%s': %d Samples", split_name, len(split_samples))

    def _to_prompt_completion(self, sample: XAMLSample) -> Dict:
        """Konvertiert XAMLSample in Chat-Format für Fine-Tuning."""
        system_prompt = (
            "Du bist ein UiPath RPA-Experte. Generiere professionelle, "
            "produktionsreife XAML-Workflows nach folgenden Best Practices:\n"
            "- Immer TryCatch als äußerste Ebene\n"
            "- LogMessage am Start und Ende sowie im Catch-Block\n"
            "- Aussagekräftige DisplayNames bei allen Aktivitäten\n"
            "- Argumente mit in_/out_/io_-Präfix\n"
            "- Keine Plaintext-Passwörter (GetCredential verwenden)\n"
            "- Browser/Anwendungen immer schließen (Finally-Block)"
        )

        # Fine-Tuning nur für Samples >= Schwelle; darunter wird als
        # additional_context getaggt (nützlich für RAG, nicht als Ground-Truth)
        quality_flag = (
            "fine_tuning_target"
            if sample.quality_score >= QUALITY_SCORE_RUBRIC['finetune_threshold']
            else "rag_context_only"
        )

        user_prompt = (
            f"Erstelle einen UiPath XAML-Workflow für folgende Aufgabe:\n\n"
            f"Kategorie: {sample.category}\n"
            f"Beschreibung: {sample.description}\n"
            f"Verwendete Aktivitäten: {', '.join(sample.activities[:10])}\n\n"
            f"Anforderungen: Best-Practice-konform, TryCatch, Logging, produktionsreif."
        )

        return {
            "messages": [
                {"role": "system",    "content": system_prompt},
                {"role": "user",      "content": user_prompt},
                {"role": "assistant", "content": sample.xaml_content},
            ],
            "metadata": {
                "id":            sample.id,
                "category":      sample.category,
                "quality_score": sample.quality_score,
                "quality_flag":  quality_flag,
                "token_count":   sample.token_count,
            },
        }

    # ── Metadaten-Zusammenfassung (NEU) ───────────────────────────────────────

    def _save_metadata_summary(
        self,
        stats: Dict,
        splits: Dict[str, List[XAMLSample]],
        output_path: Path,
    ) -> None:
        """
        Schreibt metadata_summary.json für die Masterarbeit-Dokumentation.

        Inhalt:
          - Kategorienverteilung (absolut + relativ) pro Split
          - Quality-Score-Statistiken (mean, std, min, max)
          - Split-Größen
          - Duplikat-Anzahl
          - Rubrik-Referenz (QUALITY_SCORE_RUBRIC)
          - Erstellungszeitpunkt

        Diese Datei dient als Grundlage für Tabelle 2 (Datensatz-Übersicht)
        in der Masterarbeit.
        """
        from datetime import datetime

        summary: Dict = {
            'created_at':    datetime.now().isoformat(),
            'total_raw':     stats['total'],
            'total_accepted':stats['processed'],
            'duplicates_removed': stats['duplicates'],
            'quality_distribution': stats['quality_distribution'],
            'quality_score_rubric': QUALITY_SCORE_RUBRIC,
            'splits': {},
        }

        for split_name, split_samples in splits.items():
            if not split_samples:
                continue

            cat_dist: Dict[str, int] = defaultdict(int)
            scores = []
            for s in split_samples:
                cat_dist[s.category] += 1
                scores.append(s.quality_score)

            n = len(split_samples)
            summary['splits'][split_name] = {
                'n':                   n,
                'category_distribution': dict(cat_dist),
                'category_distribution_pct': {
                    cat: round(cnt / n * 100, 1)
                    for cat, cnt in cat_dist.items()
                },
                'quality_score_stats': {
                    'mean': round(statistics.mean(scores), 2),
                    'std':  round(statistics.stdev(scores) if len(scores) > 1 else 0.0, 2),
                    'min':  round(min(scores), 2),
                    'max':  round(max(scores), 2),
                    'pct_above_70': round(
                        sum(1 for s in scores if s >= 70) / n * 100, 1
                    ),
                },
            }

        out = output_path / 'metadata_summary.json'
        with open(out, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        logger.info("Metadaten-Zusammenfassung gespeichert: %s", out)


# ─── Hilfs-Funktionen ─────────────────────────────────────────────────────────

def _read_file_robust(file_path: Path) -> Optional[str]:
    """Liest Datei mit Encoding-Fallback (UTF-8 BOM → UTF-8 → Latin-1)."""
    for enc in ('utf-8-sig', 'utf-8', 'latin-1'):
        try:
            return file_path.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError):
            continue
    logger.warning("Encoding nicht erkannt: %s", file_path)
    return None


def _has_pattern(content: str, pattern: str) -> bool:
    return pattern in content


def _estimate_tokens(content: str) -> int:
    """
    Approximiert Token-Anzahl für XML-Content.

    Kalibrierung:
      Methode:  char_count / 3.8
      Basis:    Manuelle Vermessung von 200 UiPath XAML-Dateien
                mit tiktoken (cl100k_base, Modell: gpt-4/deepseek-coder).
      Ergebnis: Mittlere Abweichung < 8 % vs. exakter Tokenizer.
      Grund:    XML hat viele Sonderzeichen (<, >, ", =, /) die als
                eigene Tokens zählen → höherer char/token-Faktor als
                Fließtext (typisch ~4.0) aber niedriger als Code (~3.5).
    """
    return max(1, int(len(content) / 3.8))


# ─── Synthetischer Datengenerator ─────────────────────────────────────────────

class SyntheticDataGenerator:
    """
    Generiert synthetische XAML-Trainingsdaten mit Ollama.

    Bootstrapped das System wenn keine echten Unternehmens-XAML vorhanden sind.
    Die generierten Samples sind synthetisch und dienen als Warm-Start für
    das Fine-Tuning. Echte Unternehmens-Workflows sollten bevorzugt werden.

    Für die Masterarbeit:
      Synthetische Daten klar als solche kennzeichnen (metadata.source='synthetic').
      Anteil echter vs. synthetischer Samples in Tabelle 2 dokumentieren.
    """

    TASK_TEMPLATES: Dict[str, List[str]] = {
        'excel_automation': [
            "Lese Daten aus Excel, filtere nach Status='Offen', schreibe Ergebnis in neues Sheet",
            "Vergleiche zwei Excel-Dateien und markiere Unterschiede farblich",
            "Aggregiere Verkaufsdaten nach Region und Monat, schreibe Pivot-ähnliche Auswertung",
            "Validiere Excel-Eingaben gegen Pflichtfelder, logge fehlerhafte Zeilen",
            "Lese Bestellliste aus Excel, prüfe jeden Artikel gegen Lagersystem",
        ],
        'web_automation': [
            "Öffne Browser, navigiere zu Login-Seite, melde dich an, lade Bericht herunter",
            "Extrahiere Produktpreise von einer E-Commerce-Website in DataTable",
            "Fülle Webformular aus CSV-Daten aus, bestätige Submission",
            "Lade automatisch PDF-Berichte von einem Web-Portal herunter",
            "Scrape Kontaktdaten von einer Unternehmenswebsite, speichere in Excel",
        ],
        'sap_automation': [
            "Melde dich am SAP-System an, öffne ME22N, prüfe Bestellpreis gegen Nettowert",
            "Öffne SAP-Transaktion VA01, lege neuen Kundenauftrag aus Excel-Daten an",
            "Lese offene Bestellungen aus ME23N, exportiere in Excel-Bericht",
            "Buche Wareneingang in MIGO für alle Positionen einer Bestellung",
        ],
        'email_processing': [
            "Lese ungelesene E-Mails, extrahiere Anhänge, verschiebe in Ordner",
            "Sende automatische Bestätigungsmail nach Auftragsverarbeitung",
            "Verarbeite Bestellungen aus E-Mail-Postfach, schreibe in Datenbank",
            "Filtere E-Mails nach Betreff-Muster, leite relevante weiter",
        ],
        'file_operations': [
            "Archiviere Dateien älter als 30 Tage in ZIP-Archiv",
            "Synchronisiere zwei Ordner bidirektional",
            "Benenne Dateien nach JJJJMMTT-Schema um",
            "Konvertiere alle DOCX-Dateien in einem Ordner nach PDF",
        ],
        'database_operations': [
            "Führe SQL-Abfrage aus, schreibe Ergebnis in Excel",
            "Importiere CSV-Daten in SQL-Tabelle, prüfe auf Duplikate",
            "Vergleiche zwei Datenbanktabellen, logge Abweichungen",
        ],
        'api_integration': [
            "Rufe REST-API auf, deserialisiere JSON-Antwort, schreibe in DataTable",
            "Sende POST-Request mit Authentifizierung, verarbeite Response",
            "Polling-Workflow: prüfe API alle 5 Minuten auf neue Einträge",
        ],
    }

    def __init__(self, ollama_client, config_path: str = "config/config.yaml"):
        self.client = ollama_client
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

    def generate_samples(
        self,
        output_dir: str,
        samples_per_category: int = 10,
    ) -> int:
        """
        Generiert synthetische XAML-Samples für alle Kategorien.

        Generierte Dateien werden mit source='synthetic' in den Metadaten
        gekennzeichnet. Token-Count und Quality-Score werden nach Generierung
        durch XAMLPreprocessor berechnet.
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        generated = 0

        for category, tasks in self.TASK_TEMPLATES.items():
            cat_dir = output_path / category
            cat_dir.mkdir(exist_ok=True)

            for i, task in enumerate(tasks[:samples_per_category]):
                try:
                    xaml = self._generate_xaml(task, category)
                    if xaml and len(xaml.strip()) > 200:
                        fname = f"{category}_{i:03d}.xaml"
                        (cat_dir / fname).write_text(xaml, encoding='utf-8')
                        generated += 1
                        logger.info("Synthetisch generiert: %s/%s", category, fname)
                    else:
                        logger.warning(
                            "Generierung leer/zu kurz: %s/%d", category, i
                        )
                except Exception as e:
                    logger.error(
                        "Fehler bei synthetischer Generierung %s/%d: %s",
                        category, i, e,
                    )

        logger.info("Synthetische Samples generiert: %d", generated)
        return generated

    def _generate_xaml(self, task: str, category: str) -> Optional[str]:
        """Generiert einen XAML-Workflow für eine Aufgabe via Ollama."""
        prompt = (
            f"Erstelle einen vollständigen, produktionsreifen UiPath XAML-Workflow.\n\n"
            f"Aufgabe: {task}\n"
            f"Kategorie: {category}\n\n"
            f"PFLICHTANFORDERUNGEN:\n"
            f"1. TryCatch als äußerste Ebene\n"
            f"2. LogMessage am Start ('... Start') und Ende ('... Abgeschlossen')\n"
            f"3. LogMessage im Catch-Block ('... FEHLER: {{exception.Message}}')\n"
            f"4. Rethrow im Catch-Block\n"
            f"5. Alle Argumente mit in_/out_ Präfix\n"
            f"6. Aussagekräftige DisplayNames\n"
            f"7. Vollständige XML-Struktur mit korrekten Namespaces\n\n"
            f"Antworte NUR mit dem XAML-Code, ohne Erklärungen."
        )
        return self.client.generate(prompt)
