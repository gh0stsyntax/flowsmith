"""
src/generation/rag_engine.py  –  v2
RAG Engine mit verbesserter TF-IDF (IDF-Gewichtung), Activity-aware Retrieval
und optionalem Feedback-Loop.
"""

import json
import logging
import math
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class RAGEngine:
    """
    Retrieval-Augmented Generation Engine v2.

    Verbesserungen gegenüber v1:
    - Echte TF-IDF mit IDF-Gewichtung (statt reinem TF)
    - Activity-aware Retrieval: Aktivitätsnamen im Query werden höher gewichtet
    - Kategorie-Boost statt hartem Filter
    - BM25-ähnliche Längen-Normalisierung
    - Alle JSONL-Dateien werden als Korpus geladen (train + test + val + feedback)
    """

    # Aktivitätsnamen bekommen 3× Gewicht im Query-Vektor
    _ACTIVITY_BOOST = 3.0

    # Bekannte UiPath-Aktivitäten für Activity-aware Retrieval
    _KNOWN_ACTIVITIES = {
        # Core
        "trycatch", "sequence", "flowchart", "if", "while", "dowhile",
        "foreach", "assign", "invokemethod", "invokeworkflow", "delay",
        "rethrow", "throw", "setVariable", "logmessage",
        # Excel
        "readrange", "writerange", "excelapplicationscope", "foreachrow",
        "appendrange", "deletesheetrows", "getcellcolor",
        # SAP
        "saplogon", "opensaptransaction", "sapcallfunction",
        "nsaplogon", "nsapcalltransaction",
        # Web / UI
        "openbrowser", "navigateto", "click", "typeinto", "gettext",
        "attachwindow", "findelement", "elementexists", "takescreenshot",
        "extractdata", "getfulltext",
        # Email
        "getmailmessages", "sendmail", "saveattachments", "movemail",
        # Orchestrator
        "getcredential", "addqueueitem", "getqueueitem",
        "settransactionstatus", "getrobotcredential",
        # File / PDF
        "readtextfile", "writetextfile", "movefile", "copyfile",
        "readpdftext", "extractpdfpage",
        # HTTP / DB
        "httpclient", "httprequest", "executequery", "connect",
    }

    def __init__(self, rag_config: Dict):
        self.config = rag_config
        self.top_k = rag_config.get('top_k', 3)
        self.threshold = rag_config.get('similarity_threshold', 0.15)

        self.corpus: List[Dict] = []
        self._idf: Dict[str, float] = {}
        self._load_corpus()
        self._build_idf()

    # ── Korpus laden ──────────────────────────────────────────────────────────
    def _load_corpus(self):
        """Lädt alle verfügbaren JSONL-Dateien in den Korpus."""
        processed = Path('data/processed')
        files = [
            'train.jsonl', 'val.jsonl', 'test.jsonl',
            'test_finetuning.jsonl', 'feedback.jsonl',
        ]
        loaded = 0
        for fname in files:
            p = processed / fname
            if not p.exists():
                continue
            with open(p, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        sample = json.loads(line)
                        # Finetuning-Format (messages) → normalisieren
                        if 'messages' in sample and 'description' not in sample:
                            sample = self._normalize_finetuning(sample)
                        if sample:
                            self.corpus.append(sample)
                            loaded += 1
                    except Exception:
                        pass

        logger.info(f"RAG: {loaded} Beispiele geladen aus {processed}")

    def _normalize_finetuning(self, sample: Dict) -> Optional[Dict]:
        """Konvertiert Finetuning-Format (messages) in RAG-Format."""
        meta = sample.get('metadata', {})
        messages = sample.get('messages', [])
        user_msg = next((m['content'] for m in messages if m['role'] == 'user'), '')
        asst_msg = next((m['content'] for m in messages if m['role'] == 'assistant'), '')
        if not asst_msg:
            return None
        return {
            'description': user_msg[:500],
            'category': meta.get('category', 'general_automation'),
            'xaml_content': asst_msg,
            'activities': self._extract_activities(asst_msg),
            'quality_score': meta.get('quality_score', 50),
        }

    def _extract_activities(self, xaml: str) -> List[str]:
        """Extrahiert Aktivitätsnamen aus XAML."""
        return list(set(re.findall(
            r'DisplayName="([^"]{3,40})"', xaml
        )[:20]))

    # ── IDF aufbauen ──────────────────────────────────────────────────────────
    def _build_idf(self):
        """Berechnet IDF-Gewichte über alle Korpus-Dokumente."""
        if not self.corpus:
            return
        N = len(self.corpus)
        df: Dict[str, int] = Counter()
        for sample in self.corpus:
            doc = self._doc_text(sample)
            terms = set(self._tokenize_raw(doc).keys())
            for t in terms:
                df[t] += 1
        # IDF = log((N+1) / (df+1)) + 1  (smooth)
        self._idf = {
            t: math.log((N + 1) / (cnt + 1)) + 1.0
            for t, cnt in df.items()
        }

    # ── Suche ─────────────────────────────────────────────────────────────────
    def search(self, query: str, category: str = None,
               top_k: int = None) -> List[Dict]:
        """
        Findet ähnliche Beispiele per TF-IDF mit IDF-Gewichtung.

        Activity-aware: Terme die Aktivitätsnamen entsprechen werden im
        Query-Vektor mit _ACTIVITY_BOOST multipliziert.
        """
        if not self.corpus:
            return []

        k = top_k or self.top_k
        query_vec = self._vectorize_query(query)

        scored: List[Tuple[float, Dict]] = []
        for sample in self.corpus:
            doc_vec = self._vectorize_doc(sample)
            sim = self._cosine(query_vec, doc_vec)

            # Kategorie-Boost (kein harter Filter)
            if category and sample.get('category') == category:
                sim *= 1.25

            quality = sample.get('quality_score', 50) / 100.0
            combined = sim * 0.70 + quality * 0.30

            if combined >= self.threshold:
                scored.append((combined, sample))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:k]]

    # ── Feedback-Loop ─────────────────────────────────────────────────────────
    def add_to_corpus(self, sample: Dict):
        """Fügt ein neues Beispiel zum Korpus hinzu und persistiert es."""
        self.corpus.append(sample)
        # IDF inkrementell aktualisieren
        self._build_idf()

        feedback_file = Path('data/processed/feedback.jsonl')
        feedback_file.parent.mkdir(parents=True, exist_ok=True)
        with open(feedback_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(sample, ensure_ascii=False) + '\n')

    # ── Interne Helpers ───────────────────────────────────────────────────────
    def _doc_text(self, sample: Dict) -> str:
        """Kombiniert alle suchrelevanten Felder eines Dokuments."""
        parts = [
            sample.get('description', ''),
            sample.get('category', ''),
            ' '.join(sample.get('activities', [])),
        ]
        return ' '.join(parts)

    def _tokenize_raw(self, text: str) -> Dict[str, float]:
        """Tokenisiert Text → rohe Term Frequency."""
        text = text.lower()
        words = re.findall(r'\b\w{3,}\b', text)
        if not words:
            return {}
        tf = Counter(words)
        total = len(words)
        return {w: cnt / total for w, cnt in tf.items()}

    def _vectorize_query(self, query: str) -> Dict[str, float]:
        """TF-IDF-Vektor für Query mit Activity-Boost."""
        tf = self._tokenize_raw(query)
        vec: Dict[str, float] = {}
        for term, freq in tf.items():
            idf = self._idf.get(term, 1.0)
            boost = self._ACTIVITY_BOOST if term in self._KNOWN_ACTIVITIES else 1.0
            vec[term] = freq * idf * boost
        return vec

    def _vectorize_doc(self, sample: Dict) -> Dict[str, float]:
        """TF-IDF-Vektor für ein Dokument."""
        text = self._doc_text(sample)
        tf = self._tokenize_raw(text)
        return {
            term: freq * self._idf.get(term, 1.0)
            for term, freq in tf.items()
        }

    def _cosine(self, v1: Dict[str, float], v2: Dict[str, float]) -> float:
        """Cosinus-Ähnlichkeit zwischen zwei TF-IDF-Vektoren."""
        if not v1 or not v2:
            return 0.0
        common = set(v1) & set(v2)
        if not common:
            return 0.0
        dot = sum(v1[t] * v2[t] for t in common)
        mag1 = math.sqrt(sum(v ** 2 for v in v1.values()))
        mag2 = math.sqrt(sum(v ** 2 for v in v2.values()))
        if mag1 == 0 or mag2 == 0:
            return 0.0
        return dot / (mag1 * mag2)
