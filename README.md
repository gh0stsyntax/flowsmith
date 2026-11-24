# flowsmith — LLM-generated UiPath workflows ("Automating the automation")

Turn a plain-language task description into a runnable **UiPath XAML workflow** with a local LLM,
then check and repair it automatically against RPA best practices.

Built as the practical part of my master's thesis *"Automatisierung der Automation — a method for
AI-assisted generation and optimisation of RPA workflows"* (M.Sc. Data Engineering & Consulting,
Hochschule Albstadt-Sigmaringen, 2026). Everything runs locally (Ollama) — no workflow data leaves the machine.

![Architecture](figures/Abb_1_Architektur.png)

## How it works

| Stage | Module | What it does |
|---|---|---|
| Preprocessing | `src/training/preprocessor.py` | Parses existing XAML workflows, categorises them (SAP / web / Excel / mail / file …), scores quality with a best-practice rubric (try/catch, logging, arguments, display names, no plaintext passwords …) and splits them into *RAG-only* and *fine-tuning grade*. |
| Training pairs | `src/training/pair_builder.py` | Self-supervised repair pairs: good workflows are deliberately degraded (remove try/catch or logging, hard-code credentials, magic numbers …) so the model learns *bad → good*. |
| Fine-tuning | `src/training/trainer.py` | QLoRA with Unsloth (r = 16, α = 16) on **Qwen2.5-Coder-7B-Instruct**, exported to GGUF (Q4_K_M) and imported into Ollama. |
| Retrieval | `src/generation/rag_engine.py` | Retrieves similar reference workflows as context for the generator. |
| Two-phase generation | `src/generation/two_phase_generator.py`, `spec_compiler.py`, `activity_templates.py` | Phase 1: the LLM writes a structured **workflow spec** (JSON). Phase 2: a deterministic compiler turns the spec into XAML from vetted activity templates — the model never has to emit raw XML. |
| Repair | `src/generation/surgical_repair.py` | Targeted fixes of validator findings instead of regenerating the whole file. |
| Validation | `src/validation/validator.py` | Layered check (XML syntax → activity whitelist → best-practice rules → optional LLM review); weighted score 0–100 from structure 35 %, logging 25 %, naming 15 %, security 15 %, completeness 10 %; pass threshold 70. |
| Apps | `src/api/app.py`, `desktop_app.py` | Flask API (streaming) and a desktop front end. |

## Results (from `results/benchmark_*.json`)

Latest ablation run (21 June 2026, 5 tasks: SAP ME22N price check, Excel filter, web login + download, mail attachments, SQL → Excel):

| Strategy | Avg. best-practice score | Pass rate (≥ 70) |
|---|---|---|
| A) Direct generation (baseline) | 55.8 ± 21.9 | 20 % |
| B) Baseline + repair | 73.3 ± 17.6 | 40 % |
| **C) Two-phase (spec → compiler)** | **93.0 ± 8.2** | **100 %** |
| D) Two-phase + repair | 93.0 ± 8.2 | 100 % |

An earlier run (18 June, 8 tasks, different model settings) was much weaker for the two-phase variants
(avg. 43.9 / 22.9) — both runs are included unfiltered. Sample sizes are small; treat this as a
**proof of concept**, not a benchmark.

> **Important caveat.** These scores come from the project's own best-practice validator (try/catch, logging,
> naming ...). They do **not** prove that UiPath Studio can open the file. When I built the June outputs with the real
> UiPath toolchain in November 2026, they did not load (wrong type syntax, a legacy VB settings block, invalid
> activity properties, undeclared variables ...). The compiler was fixed accordingly — see below.

## Does the output really build in UiPath? (checked 24 Nov 2026)

`scripts/uipath_build_check.py` puts every XAML into a blank UiPath project and runs `uip rpa build`
(compilation + Workflow Analyzer, UiPath CLI 1.206).

| Check | Result |
|---|---|
| Compiler, 6 activity groups (core/assign, files, Excel, mail, web, HTTP) | **6/6 build** |
| End-to-end, plain-language task → local LLM (qwen3:14b) → XAML → UiPath build | **5/5 build** (PDF archive, REST status check, CSV → Excel, file → mail, web search) |
| Regression tests (`tests/`, no UiPath needed) | 20 passed; a GitHub Actions workflow runs them on Python 3.10 and 3.12 (verified locally in a clean environment with only `pytest`, `pyyaml`, `requests`) |

Each of the five workflows was generated from one German sentence; the specs are stored next to the XAML in
`examples/generated/`. Before the fixes, 0 of 5 loaded. Building is **not** the same as running: selectors are
placeholders, credentials/servers are examples, and the process logic still needs a human review in Studio.
The task descriptions were *not* tuned to the compiler; one extra task (the CSV one) failed twice with model-side
problems (C# syntax in a VB project, invalid JSON) before generic fixes made it work — those fixes are covered by tests.

## What is (and isn't) in this repository

- ✅ Full source code, configuration, figures, benchmark results, dataset statistics (`results/dataset_stats`).
- ✅ Two synthetic example workflows in `data/raw/` and five model-generated workflows with their specs in `examples/generated/` (all build in UiPath, see above).
- ❌ **Training data:** the thesis used 10 real enterprise workflows from my former employer. They are proprietary and not published; the pipeline runs on any folder of your own `.xaml` files.
- ❌ Fine-tuned model weights (4.7 GB GGUF) and the thesis document.

The training set was small (10 workflows → 9 accepted, 1 fine-tuning grade, 25 repair pairs), which is the main limitation of the fine-tuning part.
The strongest effect in the benchmark comes from the **two-phase spec → compiler design**, not from fine-tuning.

## Quick start

```bash
# 1) Ollama + a base model
ollama pull qwen3:14b            # or qwen3:8b with 16 GB RAM
# 2) Python dependencies
pip install -r requirements.txt -r requirements_desktop.txt
# 3) Run
python desktop_app.py            # or: python scripts/start_system.py
python scripts/generate.py --help
python scripts/train.py --skip-preprocess --help   # training (LoRA needs a GPU: requirements_lora.txt)
```

Verified on 24 Nov 2026 on a clean copy of this repository: preprocessing processed both example workflows
(2/2, 0 errors); the validator scored them 83 and 98, and a deliberately degraded copy (logging and display names removed)
dropped from 98 to 63 and failed.

## Project structure

```
src/        training, generation, validation, api
scripts/    benchmark, generate, train, figures, results
config/     config.yaml + activity templates
data/raw/   put your own .xaml workflows here (two synthetic examples included)
results/    benchmark runs + dataset statistics
figures/    thesis figures (architecture, LoRA, RAG, validation, case studies, ablation)
```

## Deutsch (Kurzfassung)

KI-gestützte Erzeugung von UiPath-Workflows aus Klartext: lokale LLMs (Ollama, Qwen), RAG, QLoRA-Feinabstimmung,
zweistufige Generierung (Spezifikation → deterministischer Compiler), gezielte Reparatur und mehrstufige Validierung.
Praxisteil meiner Masterarbeit. Die ursprünglichen Trainingsdaten (Workflows eines früheren Arbeitgebers) sind
nicht enthalten. Startanleitung: [STARTEN.md](STARTEN.md).

## License

MIT — see [LICENSE](LICENSE). Author: Nico Vogel ([@gh0stsyntax](https://github.com/gh0stsyntax)).
