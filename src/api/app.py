"""
src/api/app.py
REST API of flowsmith
"""

from flask import Flask, request, jsonify, send_file
import logging
import re
import os
import json
import time
from pathlib import Path
import zipfile
import yaml

logger = logging.getLogger(__name__)

_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}$")   # no leading '-' (option injection), no spaces/shell characters
DEFAULT_CORS_ORIGINS = ("http://localhost:7860", "http://127.0.0.1:7860")


def inside(base: Path, candidate) -> bool:
    """True only if `candidate` resolves to a location inside `base`. Resolving first is the point: a plain
    str.startswith() on the unresolved path accepts 'base/../../secret' (path traversal)."""
    try:
        Path(candidate).resolve().relative_to(Path(base).resolve())
        return True
    except (ValueError, OSError):
        return False


def create_app(config_path: str = "config/config.yaml") -> Flask:
    app = Flask(__name__)

    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    # Zuletzt erfolgreich generierter Workflow – für die Neural-View-Visualisierung.
    # Serverseitig gehalten statt im Browser-localStorage, da Haupt-UI und die
    # per window.open() geöffnete Visualisierung (insbesondere im pywebview-
    # Desktop-Kontext) nicht zuverlässig denselben Storage teilen.
    _last_generation = {}
    
    # CORS: only the project's own UI origin(s); configurable via api.cors_origins. Never '*': the API has no
    # authentication, so any web page the user visits could otherwise drive it from their browser.
    allowed_origins = set((config.get('api') or {}).get('cors_origins') or DEFAULT_CORS_ORIGINS)

    @app.after_request
    def add_cors(response):
        origin = request.headers.get("Origin")
        if origin in allowed_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            response.headers["Vary"] = "Origin"
        return response

    @app.route("/", methods=["OPTIONS"])
    @app.route("/<path:p>", methods=["OPTIONS"])
    def options_handler(p=""):
        from flask import Response
        return Response(status=204)

    # Directories a client may name in a request must stay inside the data tree (default: ./data).
    data_root = Path(config['training']['data_dir']).resolve().parent

    def safe_dir(value, default):
        """Directory from a request: default if absent, otherwise it must resolve inside data_root."""
        chosen = Path(value) if value else Path(default)
        if not inside(data_root, chosen):
            raise ValueError("directory outside the project data folder")
        return str(chosen)

    @app.errorhandler(ValueError)
    def bad_value(e):
        return jsonify({'success': False, 'error': str(e)}), 400
    
    # Lazy-load heavy components
    _generator = None
    _validator = None
    _trainer = None

    def get_generator():
        nonlocal _generator
        if _generator is None:
            from src.generation.generator import XAMLGenerator
            _generator = XAMLGenerator(config_path)
        return _generator

    def get_validator():
        nonlocal _validator
        if _validator is None:
            from src.validation.validator import XAMLValidator
            _validator = XAMLValidator(config_path)
        return _validator

    # ─────────────────────────────────────────────────────────────
    # HEALTH CHECK
    # ─────────────────────────────────────────────────────────────
    
    @app.route('/health', methods=['GET'])
    def health():
        """Fast health check – Ollama ping with short timeout, never blocks UI."""
        import requests as _req
        ollama_url = config.get('ollama', {}).get('base_url', 'http://localhost:11434')
        models = []
        available = False
        try:
            r = _req.get(f"{ollama_url}/api/tags", timeout=2)
            if r.status_code == 200:
                available = True
                models = [m['name'] for m in r.json().get('models', [])]
        except Exception:
            pass
        primary = next(
            (m for m in models if any(k in m for k in ('qwen3','deepseek','codellama','llama','gemma'))),
            models[0] if models else None
        )
        
        # Count RAG documents
        rag_docs = 0
        processed_dir = Path('data/processed')
        if processed_dir.exists():
            for f in ['train.jsonl', 'val.jsonl', 'test.jsonl', 'feedback.jsonl']:
                p = processed_dir / f
                if p.exists():
                    with open(p, 'r', encoding='utf-8') as file:
                        rag_docs += sum(1 for _ in file)
                        
        return jsonify({
            'status': 'ok',
            'ollama_available': available,
            'models': models,
            'available_models': models,
            'primary_model': primary,
            'corpus_size': len(list(Path('data/raw').rglob("*.xaml"))) if Path('data/raw').exists() else 0,
            'chroma_docs': rag_docs,
            'version': config.get('system', {}).get('version', '1.0.0')
        })

    # ─────────────────────────────────────────────────────────────
    # GENERATE
    # ─────────────────────────────────────────────────────────────
    
    @app.route('/api/generate', methods=['POST'])
    def generate():
        data = request.get_json()
        if not data or 'task' not in data:
            return jsonify({'error': 'task ist erforderlich'}), 400

        start_time = time.time()
        try:
            result = get_generator().generate(
                task_description=data['task'],
                category=data.get('category'),
                additional_requirements=data.get('requirements'),
                max_attempts=data.get('max_attempts', 3)
            )
            return jsonify({
                'success': True,
                'xaml': result['xaml_content'],
                'score': result['score'],
                'passed': result['validation']['passed'],
                'errors': result['validation']['errors'],
                'warnings': result['validation']['warnings'],
                'suggestions': result.get('suggestions', []),
                'output_path': result.get('output_path'),
                'generation_time_s': round(time.time() - start_time, 2),
                'attempts': result['attempt']
            })
        except Exception as e:
            logger.error(f"Generierungsfehler: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    # ─────────────────────────────────────────────────────────────
    # VALIDATE
    # ─────────────────────────────────────────────────────────────
    
    @app.route('/api/validate', methods=['POST'])
    def validate():
        if 'file' in request.files:
            file = request.files['file']
            raw_bytes = file.read()
            for encoding in ("utf-8", "utf-8-sig", "windows-1252", "latin-1"):
                try:
                    xaml_content = raw_bytes.decode(encoding)
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            else:
                xaml_content = raw_bytes.decode("utf-8", errors="replace")
        elif request.is_json:
            data = request.get_json()
            xaml_content = data.get('xaml', '')
        else:
            return jsonify({'error': 'XAML oder Datei erforderlich'}), 400
        
        if not xaml_content:
            return jsonify({'error': 'Leerer XAML-Inhalt'}), 400

        llm_review = request.args.get('llm_review', 'false').lower() == 'true'
        try:
            result = get_validator().validate(xaml_content, run_llm_review=llm_review)
            return jsonify({
                'success': True,
                'score': result['total_score'],
                'passed': result['passed'],
                'errors': result['errors'],
                'warnings': result['warnings'],
                'info': result['info'],
                'suggestions': result.get('suggestions', []),
                'layer_results': result['layers']
            })
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    # ─────────────────────────────────────────────────────────────
    # TRAINING
    # ─────────────────────────────────────────────────────────────

    @app.route('/api/training/preprocess', methods=['POST'])
    def preprocess():
        data = request.get_json() or {}
        input_dir = safe_dir(data.get('input_dir'), config['training']['data_dir'])
        output_dir = safe_dir(data.get('output_dir'), config['training']['processed_dir'])
        try:
            from src.training.preprocessor import XAMLPreprocessor
            preprocessor = XAMLPreprocessor(config_path)
            stats = preprocessor.process_directory(input_dir, output_dir)
            return jsonify({'success': True, 'stats': stats})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/training/build-model', methods=['POST'])
    def build_model():
        data = request.get_json() or {}
        training_data = safe_dir(data.get('training_data'), config['training']['processed_dir'])
        model_name = data.get('model_name', config['ollama']['finetuned_model'])
        if not _MODEL_NAME.match(str(model_name)):
            return jsonify({'success': False, 'error': 'invalid model name'}), 400
        try:
            from src.training.trainer import OllamaTrainer
            trainer = OllamaTrainer(config_path)
            modelfile_path = trainer.create_modelfile(training_data)
            success = trainer.build_model_from_modelfile(modelfile_path, model_name)
            return jsonify({
                'success': success,
                'model_name': model_name,
                'modelfile_path': modelfile_path
            })
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/training/status', methods=['GET'])
    def training_status():
        from src.generation.ollama_client import OllamaClient
        client = OllamaClient(config_path)
        finetuned_exists = client.model_exists(config['ollama']['finetuned_model'])
        stats_file = Path(config['training']['processed_dir']) / 'preprocessing_stats.json'
        stats = {}
        if stats_file.exists():
            with open(stats_file) as f:
                stats = json.load(f)
        return jsonify({
            'finetuned_model_exists': finetuned_exists,
            'available_models': client.list_models(),
            'preprocessing_stats': stats
        })

    # ─────────────────────────────────────────────────────────────
    # DOWNLOADS
    # ─────────────────────────────────────────────────────────────
    
    @app.route('/api/download/<path:filename>', methods=['GET'])
    def download(filename):
        output_dir = Path(config['generation']['output_dir']).resolve()
        file_path = (output_dir / filename).resolve()
        if not inside(output_dir, file_path) or not file_path.is_file():
            return jsonify({'error': 'Datei nicht gefunden'}), 404
        return send_file(file_path, as_attachment=True)

    @app.route('/api/download-project', methods=['POST'])
    def download_project():
        output_dir = Path(config['generation']['output_dir']).resolve()
        data = request.get_json() or {}
        xaml_filename = data.get("xaml_filename")
        meta_filename = data.get("meta_filename")
        if not xaml_filename and not meta_filename:
            return jsonify({"error": "xaml_filename oder meta_filename erforderlich"}), 400
        if xaml_filename:
            xaml_path = output_dir / xaml_filename
            meta_path = xaml_path.with_suffix(".json")
        else:
            meta_path = output_dir / meta_filename
        if not inside(output_dir, meta_path) or not meta_path.is_file():
            return jsonify({'error': 'Datei nicht gefunden'}), 404
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        project_dir = meta.get("project_dir")
        if not project_dir:
            return jsonify({"error": "Kein project_dir in Metadaten"}), 404
        project_path = Path(project_dir)
        if not project_path.exists() or not project_path.is_dir():
            return jsonify({"error": "Projektordner nicht gefunden"}), 404
        project_resolved = project_path.resolve()
        output_resolved = output_dir.resolve()
        if output_resolved not in project_resolved.parents:
            return jsonify({"error": "Ungültiger Projektpfad"}), 400
        zip_name = project_path.name + ".zip"
        zip_path = output_dir / zip_name
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in project_path.rglob("*"):
                if p.is_file():
                    arc = p.relative_to(project_path)
                    zf.write(p, arcname=str(arc))
        return send_file(zip_path, as_attachment=True, download_name=zip_name)

    @app.route('/api/list-outputs', methods=['GET'])
    def list_outputs():
        output_dir = Path(config['generation']['output_dir'])
        files = []
        for f in sorted(output_dir.glob('*.xaml'), reverse=True):
            meta_file = f.with_suffix('.json')
            meta = {}
            if meta_file.exists():
                with open(meta_file) as mf:
                    meta = json.load(mf)
            files.append({
                'filename': f.name,
                'size': f.stat().st_size,
                'created': f.stat().st_mtime,
                'score': meta.get('score', 0),
                'category': meta.get('category', 'unknown'),
                'task': meta.get('task', '')[:100]
            })
        return jsonify({'files': files[:50]})

    @app.route('/api/compile-spec', methods=['POST'])
    def compile_spec():
        data = request.get_json() or {}
        try:
            from src.generation.workflow_spec import WorkflowSpec
            spec = WorkflowSpec.from_dict(data)
            compiled = get_generator().compile_spec_to_project(spec)
            return jsonify({"success": True, **compiled})
        except Exception as e:
            logger.error("compile_spec error: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route('/api/parse-task', methods=['POST'])
    def parse_task():
        data = request.get_json() or {}
        text = data.get("text") or ""
        name = data.get("name") or "GeneratedProcess"
        compile_ = bool(data.get("compile", False))
        try:
            from src.generation.task_parser import parse_task_text_to_spec
            spec_dict = parse_task_text_to_spec(text=text, name=name)
            if not compile_:
                return jsonify({"success": True, "spec": spec_dict})
            from src.generation.workflow_spec import WorkflowSpec
            spec = WorkflowSpec.from_dict(spec_dict)
            compiled = get_generator().compile_spec_to_project(spec)
            return jsonify({"success": True, "spec": spec_dict, **compiled})
        except Exception as e:
            logger.error("parse_task error: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route('/api/parse-docx', methods=['POST'])
    def parse_docx():
        try:
            if "file" not in request.files:
                return jsonify({"success": False, "error": "file fehlt"}), 400
            f = request.files["file"]
            filename = (f.filename or "").lower()
            if not filename.endswith(".docx"):
                return jsonify({"success": False, "error": "Nur .docx unterstützt"}), 400
            from docx import Document
            doc = Document(f)
            text = "\n".join(p.text for p in doc.paragraphs if p.text and p.text.strip())
            name = request.form.get("name") or "GeneratedProcess"
            compile_ = (request.form.get("compile") or "").strip().lower() in ("1", "true", "yes", "y")
            from src.generation.task_parser import parse_task_text_to_spec
            spec_dict = parse_task_text_to_spec(text=text, name=name)
            if not compile_:
                return jsonify({"success": True, "text": text, "spec": spec_dict})
            from src.generation.workflow_spec import WorkflowSpec
            spec = WorkflowSpec.from_dict(spec_dict)
            compiled = get_generator().compile_spec_to_project(spec)
            return jsonify({"success": True, "text": text, "spec": spec_dict, **compiled})
        except Exception as e:
            logger.error("parse_docx error: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500

    # ─────────────────────────────────────────────────────────────
    # ALIAS ROUTES (UI COMPATIBILITY) - INCLUDING TWO-PHASE MODE!
    # ─────────────────────────────────────────────────────────────
    
    @app.route('/generate', methods=['POST'])
    def generate_alias():
        """
        Haupt-Generierungs-Endpunkt für das Frontend.
        Liest den 'mode' Parameter aus der URL (?mode=two_phase).
        """
        data = request.get_json() or {}
        # Normalize field names from UI
        if 'description' in data and 'task' not in data:
            data['task'] = data.pop('description')
        if 'system_info' in data and 'requirements' not in data:
            data['requirements'] = data.pop('system_info')

        start_time = time.time()
        try:
            mode = request.args.get('mode', 'direct')
            
            if mode == 'two_phase':
                # ZWEI-PHASEN-GENERIERUNG (Deterministischer Compiler)
                from src.generation.two_phase_generator import TwoPhaseGenerator
                gen = TwoPhaseGenerator(config_path=config_path)
                result = gen.generate(
                    task_description=data.get('task', ''),
                    category=data.get('category'),
                    additional_requirements=data.get('requirements')
                )
            else:
                # DIREKTE GENERIERUNG (LLM only)
                result = get_generator().generate(
                    task_description=data.get('task', ''),
                    category=data.get('category'),
                    additional_requirements=data.get('requirements'),
                    max_attempts=data.get('max_attempts', 3)
                )

            response_data = {
                'success': True,
                'xaml': result.get('xaml_content', ''),
                'xaml_content': result.get('xaml_content', ''),
                'score': result.get('score', 0),
                'passed': result.get('validation', {}).get('passed', False),
                'errors': result.get('validation', {}).get('errors', []),
                'warnings': result.get('validation', {}).get('warnings', []),
                'suggestions': result.get('suggestions', []),
                'output_path': result.get('output_path'),
                'generation_time_s': round(time.time() - start_time, 2),
                'attempts': result.get('attempt', 1)
            }

            if response_data['xaml_content']:
                _last_generation.update({
                    'task': data.get('task', ''),
                    'category': data.get('category'),
                    'mode': mode,
                    'score': response_data['score'],
                    'passed': response_data['passed'],
                    'generationTimeS': response_data['generation_time_s'],
                    'attempts': response_data['attempts'],
                    'xaml': response_data['xaml_content'],
                })

            return jsonify(response_data)
        except Exception as e:
            logger.error('generate_alias error: %s', e)
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/last-generation', methods=['GET'])
    def last_generation():
        if not _last_generation:
            return jsonify({'available': False}), 404
        return jsonify({'available': True, **_last_generation})

    @app.route('/generate/stream', methods=['POST'])
    def generate_stream():
        from flask import Response, stream_with_context
        import json as _json

        data = request.get_json() or {}
        task = data.get('task') or data.get('description', '')
        category = data.get('category') or None
        requirements = data.get('requirements') or data.get('system_info') or None
        model_override = data.get('model') or None

        if not task:
            return jsonify({'error': 'task erforderlich'}), 400

        def event_stream():
            try:
                from src.generation.ollama_client import OllamaClient
                from src.generation.generator import XAMLGenerator

                client = OllamaClient(config_path)
                gen = XAMLGenerator(config_path, model_name=model_override)

                if not category:
                    cat = gen._detect_category(task)
                else:
                    cat = category

                context_examples = []
                if gen.rag:
                    context_examples = gen.rag.search(task, cat)

                prompt = gen._build_prompt(task, cat, requirements, context_examples)

                if model_override:
                    model = model_override
                elif client.model_exists(client.finetuned_model):
                    model = client.finetuned_model
                else:
                    model = client.primary_model

                _ev = _json.dumps({"event": "start", "model": model, "category": cat})
                yield f"data: {_ev}\n\n"

                full_text = []
                token_count = 0
                ollama_stats = {}

                for evt in client.stream_generate(
                    prompt=prompt,
                    model=model,
                    system=gen.SYSTEM_PROMPT,
                ):
                    if evt.get("type") == "token":
                        token = evt["text"]
                        full_text.append(token)
                        token_count += 1
                        _ev = _json.dumps({"token": token, "count": token_count, "done": False})
                        yield f"data: {_ev}\n\n"
                    elif evt.get("type") == "stats":
                        ollama_stats = {k: v for k, v in evt.items() if k != "type"}

                raw = ''.join(full_text)
                xaml = gen._extract_xaml(raw)
                if gen.gen_config['post_processing']['auto_format']:
                    xaml = gen._post_process(xaml)

                from src.validation.validator import XAMLValidator
                validator = XAMLValidator(config_path)
                validation = validator.validate(xaml)
                score = validation['total_score']

                output_path = gen._save_output(
                    {'xaml_content': xaml, 'validation': validation,
                     'attempt': 1, 'score': score},
                    task, cat
                )

                _final = {"done": True, "xaml": xaml, "score": score,
                              "passed": validation["passed"],
                              "errors": validation.get("errors", []),
                              "warnings": validation.get("warnings", []),
                              "output_path": str(output_path),
                              "token_count": token_count,
                              "ollama_stats": ollama_stats}

                if xaml:
                    _last_generation.update({
                        'task': task,
                        'category': cat,
                        'mode': 'stream',
                        'score': score,
                        'passed': validation['passed'],
                        'generationTimeS': None,
                        'attempts': 1,
                        'xaml': xaml,
                    })

                _ev = _json.dumps(_final)
                yield f"data: {_ev}\n\n"

            except Exception as e:
                logger.error('stream error: %s', e)
                import traceback; traceback.print_exc()
                _ev = _json.dumps({"error": str(e), "done": True})
                yield f"data: {_ev}\n\n"

        return Response(
            stream_with_context(event_stream()),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
                'Access-Control-Allow-Origin': '*',
            }
        )

    @app.route('/validate', methods=['POST'])
    def validate_alias():
        data = request.get_json() or {}
        xaml_content = data.get('xaml', '')
        llm_review = data.get('run_llm_review', False)
        if not xaml_content:
            return jsonify({'error': 'xaml erforderlich'}), 400
        try:
            result = get_validator().validate(xaml_content, run_llm_review=llm_review)
            return jsonify({
                'success': True,
                'total_score': result.get('total_score', 0),
                'score': result.get('total_score', 0),
                'passed': result.get('passed', False),
                'errors': result.get('errors', []),
                'warnings': result.get('warnings', []),
                'info': result.get('info', []),
                'suggestions': result.get('suggestions', []),
                'layers': result.get('layers', {})
            })
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/models', methods=['GET'])
    def models_list():
        from src.generation.ollama_client import OllamaClient
        client = OllamaClient(config_path)
        models = client.list_models()
        return jsonify({'models': models})

    @app.route('/train', methods=['POST'])
    def train_alias():
        data = request.get_json() or {}
        mode = data.get('mode', 'preprocess')
        if mode == 'synthetic':
            data_dir = safe_dir(data.get('data_dir'), config['training']['data_dir'])
            samples = int(data.get('samples', 10))
            try:
                from src.generation.ollama_client import OllamaClient
                from src.training.preprocessor import SyntheticDataGenerator
                client = OllamaClient()
                if not client.is_available():
                    return jsonify({'success': False, 'error': 'Ollama nicht verfügbar'}), 503
                generator = SyntheticDataGenerator(client)
                count = generator.generate_samples(f"{data_dir}/synthetic", samples)
                return jsonify({'success': True, 'count': count})
            except Exception as e:
                return jsonify({'success': False, 'error': str(e)}), 500
        elif mode == 'preprocess':
            input_dir = safe_dir(data.get('data_dir'), config['training']['data_dir'])
            output_dir = config['training']['processed_dir']
            try:
                from src.training.preprocessor import XAMLPreprocessor
                preprocessor = XAMLPreprocessor()
                stats = preprocessor.process_directory(input_dir, output_dir)
                return jsonify({'success': True, **stats})
            except Exception as e:
                return jsonify({'success': False, 'error': str(e)}), 500
        elif mode == 'build':
            model_name = data.get('model_name', config['ollama']['finetuned_model'])
            if not _MODEL_NAME.match(str(model_name)):
                return jsonify({'success': False, 'error': 'invalid model name'}), 400
            try:
                from src.training.trainer import OllamaTrainer
                trainer = OllamaTrainer()
                modelfile_path = trainer.create_modelfile(config['training']['processed_dir'])
                success = trainer.build_model_from_modelfile(modelfile_path, model_name)
                return jsonify({'success': success, 'model_name': model_name, 'message': f'Modell {model_name} erstellt' if success else 'Fehler beim Erstellen'})
            except Exception as e:
                return jsonify({'success': False, 'error': str(e)}), 500
        return jsonify({'error': 'unknown mode'}), 400

    @app.route('/api/build-pairs', methods=['POST'])
    def build_pairs():
        data = request.get_json() or {}
        processed_dir = safe_dir(data.get("processed_dir"), config["training"]["processed_dir"])
        out_dir = safe_dir(data.get("out_dir"), config["training"]["processed_dir"])
        k = max(1, min(int(data.get("k_per_sample") or 2), 20))
        try:
            # The endpoint used to import a class that does not exist (PseudoPairBuilder) and always failed.
            from src.training.pair_builder import build_pairs as make_pairs
            source = Path(processed_dir) / "train.jsonl"     # the quality-checked sources written by /api/training/preprocess
            if not source.is_file():
                return jsonify({"success": False, "error": "no train.jsonl in processed_dir; run preprocessing first"}), 400
            stats = make_pairs(input_file=str(source), output_dir=out_dir, max_per_source=k)
            return jsonify({"success": True, **stats})
        except Exception as e:
            logger.error("build_pairs error: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route('/api/convert-pairs', methods=['POST'])
    def convert_pairs():
        data = request.get_json() or {}
        processed_dir = Path(safe_dir(data.get("processed_dir"), config["training"]["processed_dir"]))
        try:
            from src.training.pairs_to_finetune import convert_pairs_to_chat_jsonl

            train_in = processed_dir / "pairs_train.jsonl"
            val_in = processed_dir / "pairs_val.jsonl"
            train_out = processed_dir / "train_finetuning.jsonl"
            val_out = processed_dir / "val_finetuning.jsonl"

            n_train = convert_pairs_to_chat_jsonl(str(train_in), str(train_out)) if train_in.exists() else 0
            n_val = convert_pairs_to_chat_jsonl(str(val_in), str(val_out)) if val_in.exists() else 0
            return jsonify({"success": True, "train": n_train, "val": n_val})
        except Exception as e:
            logger.error("convert_pairs error: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500

    return app


if __name__ == '__main__':
    import logging
    logging.basicConfig(level=logging.INFO)
    
    app = create_app()
    # Never debug=True here: the Werkzeug debugger allows code execution for anyone who can reach the port.
    app.run(host=os.environ.get("XAMLML_HOST", "127.0.0.1"), port=5000, debug=False)