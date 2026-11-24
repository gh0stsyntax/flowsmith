"""
src/generation/ollama_client.py
Wrapper für die Ollama API
"""

import requests
import json
import logging
import time
from typing import Any, Dict, Generator, Optional
import yaml

logger = logging.getLogger(__name__)


def _ollama_http_error_message(response: requests.Response) -> str:
    """Readable message from failed Ollama response (often JSON with an \"error\" field)."""
    text = (response.text or "").strip()
    if not text:
        return response.reason or f"HTTP {response.status_code}"
    try:
        data = response.json()
        if isinstance(data, dict) and data.get("error"):
            return str(data["error"])
    except Exception:
        pass
    return text[:2000] if len(text) > 2000 else text


class OllamaClient:
    """
    Client für lokale Ollama API.
    Unterstützt Streaming, Retry-Logik und Modell-Management.
    """

    def __init__(self, config_path: str = "config/config.yaml"):
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        self.base_url = config['ollama']['base_url']
        self.primary_model = config['ollama']['primary_model']
        self.finetuned_model = config['ollama']['finetuned_model']
        self.gen_params = config['ollama']['generation_params']
        self.timeout = 1200

    def _resolve_model(self, model: Optional[str]) -> str:
        if model is None:
            if self.model_exists(self.finetuned_model):
                return self.finetuned_model
            return self.primary_model
        return model

    def _build_generate_payload(
        self,
        prompt: str,
        model: str,
        system: Optional[str],
        stream: bool,
        **kwargs,
    ) -> dict:
        params = {**self.gen_params, **kwargs}
        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": stream,
            "options": {
                "temperature": params.get("temperature", 0.2),
                "top_p": params.get("top_p", 0.9),
                "top_k": params.get("top_k", 40),
                "num_predict": params.get("num_predict", 4096),
                "repeat_penalty": params.get("repeat_penalty", 1.1),
            },
        }
        if system:
            payload["system"] = system
        return payload

    def is_available(self) -> bool:
        """Prüft ob Ollama läuft"""
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return r.status_code == 200
        except:
            return False

    def list_models(self) -> list:
        """Listet verfügbare Modelle auf"""
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=10)
            if r.status_code == 200:
                return [m['name'] for m in r.json().get('models', [])]
        except Exception as e:
            logger.error(f"Fehler beim Abrufen der Modelle: {e}")
        return []

    def model_exists(self, model_name: str) -> bool:
        """Prüft ob ein Modell verfügbar ist"""
        return any(model_name in m for m in self.list_models())

    def pull_model(self, model_name: str) -> bool:
        """Pullt ein Modell von Ollama Hub"""
        logger.info(f"Lade Modell: {model_name}")
        try:
            response = requests.post(
                f"{self.base_url}/api/pull",
                json={"name": model_name, "stream": True},
                stream=True,
                timeout=3600  # 1h für große Modelle
            )
            for line in response.iter_lines():
                if line:
                    data = json.loads(line)
                    if 'status' in data:
                        print(f"\r{data['status']}", end='', flush=True)
                    if data.get('status') == 'success':
                        print()
                        return True
        except Exception as e:
            logger.error(f"Pull-Fehler: {e}")
        return False

    def generate(self, prompt: str, model: str = None, 
                 system: str = None, stream: bool = False,
                 **kwargs) -> str:
        """
        Generiert Text mit Ollama.
        
        Args:
            prompt: Der Benutzer-Prompt
            model: Modell-Name (Standard: primary_model)
            system: System-Prompt
            stream: Streaming-Modus
        """
        model = self._resolve_model(model)
        payload = self._build_generate_payload(prompt, model, system, stream, **kwargs)

        # Retry-Logik
        for attempt in range(1):
            try:
                if stream:
                    return self._stream_text_only(prompt, model, system, **kwargs)
                else:
                    return self._generate_blocking(payload)
            except requests.exceptions.ConnectionError:
                if attempt < 2:
                    logger.warning(f"Verbindungsfehler, Versuch {attempt + 2}/3...")
                    time.sleep(2)
                else:
                    raise RuntimeError("Ollama nicht erreichbar! Starte mit: ollama serve")
            except Exception as e:
                if attempt < 2:
                    logger.warning(f"Fehler: {e}, Retry...")
                    time.sleep(1)
                else:
                    raise

    def _generate_blocking(self, payload: dict) -> str:
        """Nicht-streaming Generierung"""
        payload['stream'] = False
        response = requests.post(
            f"{self.base_url}/api/generate",
            json=payload,
            timeout=self.timeout
        )
        if not response.ok:
            msg = _ollama_http_error_message(response)
            logger.error("Ollama /api/generate blocking failed: %s", msg)
            raise RuntimeError(f"Ollama ({response.status_code}): {msg}") from None
        return response.json()['response']

    def stream_generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        system: Optional[str] = None,
        **kwargs,
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Streaming: liefert pro Chunk ein Dict.
        - {"type": "token", "text": "..."}  Ausgabe-Token
        - {"type": "stats", ...}             Einmal am Ende (Ollama eval_count, Dauer in ns)
        """
        model = self._resolve_model(model)
        payload = self._build_generate_payload(prompt, model, system, True, **kwargs)

        for attempt in range(3):
            try:
                response = requests.post(
                    f"{self.base_url}/api/generate",
                    json=payload,
                    stream=True,
                    timeout=self.timeout,
                )
                if not response.ok:
                    msg = _ollama_http_error_message(response)
                    logger.error("Ollama /api/generate stream failed: %s", msg)
                    raise RuntimeError(f"Ollama ({response.status_code}): {msg}") from None

                for line in response.iter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("response"):
                        yield {"type": "token", "text": data["response"]}
                    if data.get("done"):
                        yield {
                            "type": "stats",
                            "eval_count": data.get("eval_count"),
                            "prompt_eval_count": data.get("prompt_eval_count"),
                            "total_duration_ns": data.get("total_duration"),
                            "eval_duration_ns": data.get("eval_duration"),
                            "load_duration_ns": data.get("load_duration"),
                        }
                        return
                return
            except requests.exceptions.ConnectionError:
                if attempt < 2:
                    logger.warning("Verbindungsfehler, Versuch %s/3...", attempt + 2)
                    time.sleep(2)
                else:
                    raise RuntimeError("Ollama nicht erreichbar! Starte mit: ollama serve") from None
            except Exception as e:
                if attempt < 2:
                    logger.warning("Fehler: %s, Retry...", e)
                    time.sleep(1)
                else:
                    raise

    def _stream_text_only(
        self,
        prompt: str,
        model: str,
        system: Optional[str],
        **kwargs,
    ) -> Generator[str, None, None]:
        """Nur Text-Tokens (Kompatibilität mit generate(stream=True))."""
        for evt in self.stream_generate(prompt, model=model, system=system, **kwargs):
            if evt["type"] == "token":
                yield evt["text"]

    def chat(self, messages: list, model: str = None, **kwargs) -> str:
        """Chat-API (für strukturierte Konversation)"""
        if model is None:
            model = self.finetuned_model if self.model_exists(self.finetuned_model) \
                    else self.primary_model
        
        params = {**self.gen_params, **kwargs}
        
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": params.get('temperature', 0.2),
                "num_predict": params.get('num_predict', 4096),
            }
        }
        
        response = requests.post(
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=self.timeout
        )
        if not response.ok:
            msg = _ollama_http_error_message(response)
            logger.error("Ollama /api/chat failed: %s", msg)
            raise RuntimeError(f"Ollama ({response.status_code}): {msg}") from None
        return response.json()['message']['content']

    def get_model_info(self, model_name: str) -> dict:
        """Gibt Modell-Informationen zurück"""
        try:
            r = requests.post(
                f"{self.base_url}/api/show",
                json={"name": model_name},
                timeout=10
            )
            return r.json()
        except:
            return {}
