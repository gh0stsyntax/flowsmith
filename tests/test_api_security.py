"""Security regression tests for the REST API and the start script (found by testing a fresh clone on 24 Nov 2026).

Each test pins a concrete defect that existed before:
  * /api/download used str.startswith() on an unresolved path -> files outside the output folder could be read
    (with an absolute output_dir in the config the read succeeded, HTTP 200)
  * CORS answered 'Access-Control-Allow-Origin: <any origin>' on an API without authentication
  * training endpoints accepted any directory from the request body
  * model names went unchecked into `ollama create`
  * start_system.py had no argument parsing: `--help` started the whole system; it bound 0.0.0.0 and downloaded ~4 GB unasked
"""
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

pytest.importorskip("flask")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.api.app import create_app, inside  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Isolated project: relative dirs live in tmp_path; the output dir is ABSOLUTE (the case that leaked)."""
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "processed").mkdir(parents=True)
    out = tmp_path / "output" / "xaml"
    out.mkdir(parents=True)
    (out / "ok.xaml").write_text("<Activity/>", encoding="utf-8")
    (tmp_path / "SECRET.txt").write_text("TOPSECRET", encoding="utf-8")
    cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))   # the real config, only paths overridden
    cfg["generation"]["output_dir"] = str(out)
    cfg["training"]["data_dir"], cfg["training"]["processed_dir"] = "data/raw", "data/processed"
    cfg["ollama"]["finetuned_model"] = "model-x"
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return create_app(str(cfg_path)).test_client()


def test_inside_helper():
    assert inside(ROOT, ROOT / "src")
    assert not inside(ROOT / "src", ROOT / "src" / ".." / "config")      # startswith() would have accepted this
    assert not inside(ROOT, ROOT.parent)


def test_download_serves_files_inside_output(client):
    r = client.get("/api/download/ok.xaml")
    assert r.status_code == 200 and r.data == b"<Activity/>"


@pytest.mark.parametrize("url", [
    "/api/download/..%2f..%2fSECRET.txt",
    "/api/download/../../SECRET.txt",
    "/api/download/..%5c..%5cSECRET.txt",
    "/api/download/%2e%2e%2f%2e%2e%2fSECRET.txt",
])
def test_download_blocks_path_traversal(client, url):
    r = client.get(url)
    assert r.status_code == 404 and b"TOPSECRET" not in r.data


def test_download_project_blocks_traversal(client):
    r = client.post("/api/download-project", json={"meta_filename": "../../SECRET.txt"})
    assert r.status_code == 404 and b"TOPSECRET" not in r.data


def test_cors_only_for_own_ui(client):
    fremd = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in fremd.headers
    eigen = client.get("/health", headers={"Origin": "http://localhost:7860"})
    assert eigen.headers["Access-Control-Allow-Origin"] == "http://localhost:7860"
    pre = client.options("/api/generate", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert pre.status_code in (200, 204) and "Access-Control-Allow-Origin" not in pre.headers
    ok = client.options("/api/generate", headers={"Origin": "http://localhost:7860", "Access-Control-Request-Method": "POST"})
    assert ok.headers["Access-Control-Allow-Origin"] == "http://localhost:7860"      # the project's own UI still works


@pytest.mark.parametrize("endpoint,body", [
    ("/api/training/preprocess", {"input_dir": "C:/Windows"}),
    ("/api/training/preprocess", {"output_dir": "../../elsewhere"}),
    ("/api/training/build-model", {"training_data": "../../elsewhere"}),
    ("/train", {"mode": "preprocess", "data_dir": "../../elsewhere"}),
    ("/api/build-pairs", {"processed_dir": "../../elsewhere"}),
    ("/api/build-pairs", {"out_dir": "../../elsewhere"}),
    ("/api/convert-pairs", {"processed_dir": "../../elsewhere"}),
])
def test_training_endpoints_reject_directories_outside_data(client, endpoint, body):
    r = client.post(endpoint, json=body)
    assert r.status_code == 400 and "outside" in r.get_json()["error"]


@pytest.mark.parametrize("name", ["--help", "-f/etc/passwd", "a b", "x;rm -rf /", "", "a" * 100])
def test_model_name_is_validated(client, name):
    assert client.post("/api/training/build-model", json={"model_name": name}).status_code == 400
    assert client.post("/train", json={"mode": "build", "model_name": name}).status_code == 400


def test_defaults_are_local_only():
    cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["api"]["host"] == "127.0.0.1"
    assert "*" not in cfg["api"]["cors_origins"]
    code = [z for z in (ROOT / "src" / "api" / "app.py").read_text(encoding="utf-8").splitlines() if not z.strip().startswith("#")]
    assert not any("debug=True" in z for z in code)


def test_start_system_help_starts_nothing():
    t0 = time.time()
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "start_system.py"), "--help"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0 and "usage" in r.stdout.lower() and "127.0.0.1" in r.stdout
    assert time.time() - t0 < 15                       # before the fix this hung until killed (it started the servers)
    src = (ROOT / "scripts" / "start_system.py").read_text(encoding="utf-8")
    assert '"0.0.0.0"' not in src.replace('help="interface', "")  # no hard-coded all-interfaces bind


def test_training_chain_really_works(client, tmp_path):
    """preprocess -> build-pairs -> convert-pairs on the repository's own example workflows.
    Before: /api/build-pairs imported a class that does not exist and always answered HTTP 500."""
    import shutil
    for f in (ROOT / "data" / "raw").glob("example_*.xaml"):
        shutil.copy2(f, tmp_path / "data" / "raw" / f.name)
    pre = client.post("/api/training/preprocess", json={})
    assert pre.status_code == 200 and pre.get_json()["stats"]["processed"] == 2
    pairs = client.post("/api/build-pairs", json={})
    assert pairs.status_code == 200 and pairs.get_json()["total_pairs"] > 0
    assert (tmp_path / "data" / "processed" / "pairs_train.jsonl").is_file()
    conv = client.post("/api/convert-pairs", json={})
    assert conv.status_code == 200 and conv.get_json()["train"] > 0


def test_build_pairs_without_preprocessing_is_a_clear_error(client):
    r = client.post("/api/build-pairs", json={})
    assert r.status_code == 400 and "preprocessing" in r.get_json()["error"]
