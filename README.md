# flowsmith — LLM-generated UiPath workflows ("Automating the automation")

[![tests](https://github.com/gh0stsyntax/flowsmith/actions/workflows/tests.yml/badge.svg)](https://github.com/gh0stsyntax/flowsmith/actions/workflows/tests.yml)

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

## Does the output really build in UiPath? (checked 24–25 Nov 2026)

`scripts/uipath_build_check.py` puts every XAML into a blank UiPath project and runs `uip rpa build`
(compilation + Workflow Analyzer, UiPath CLI 1.206). The same code (`src/validation/uipath_build.py`) drives the optional
repair loop `scripts/generate.py --build-check`.

| Check | Result |
|---|---|
| Every activity template built on its own (`scripts/template_smoke_test.py`) | **25/28** (the other 3 need context: a SAP/application scope, a selector) |
| End-to-end on the 5 tasks I developed against | 5/5 — **optimistic**, the compiler was tuned on exactly these |
| Fresh task set 1 (8 new tasks × 2 seeds), 24 Nov, before the fixes below | 9/16 (56 %) |
| Fresh task set 2 (8 new tasks × 2 seeds), 25 Nov, before the template fixes of that day | 7/16 (43 %) |
| Fresh task set 3 (8 new tasks × 2 seeds), 25 Nov, after the fixes | **14/16 (87 %)** |
| Build-feedback repair loop (`--build-check`, up to 2 rounds), sets 2 and 3 | rescued 1 of 32 runs (3 %): no meaningful gain |
| Regression tests (`tests/`, no LLM; GitHub Actions on Python 3.10 and 3.12) | 117 passed; one more test builds PDF/SQL workflows with the real toolchain when `uip` is installed |

**How to read these numbers.** The sets were measured one after another and the fixes were derived from failures in sets 1 and 2,
so set 3 is *not* an independent estimate: its tasks (files, API, database, mail, credentials) are exactly the areas that were just
repaired, and Excel tasks failed in every set. A realistic expectation for new tasks lies between the earlier 43–56 % and the
latest 87 %, and each figure comes from only 16 runs. A run counts as "builds" only if `uip rpa build` reports no error;
warnings are ignored. With a fixed seed the output is reproducible (`scripts/generate.py --seed N`), so repeating a seed adds no information.

**What the real build found** (all fixed, each with a regression test): templates that did not match what UiPath itself generates
(`uip rpa activities get-default-xaml`): `ReadPDFText.PDFPath` (is `FileName`), a database activity that does not exist (`ui:Connect`),
`ExecuteQuery` with a result element UiPath does not know, `AddQueueItem.QueueName` (is `QueueType`), a password variable typed `String`
instead of `SecureString`; literal paths and texts wrapped in brackets as if they were expressions; VB pitfalls (`Format(...)` collides
with a UiPath namespace, `Split(",")` picks the Char overload); a log text such as `[Name] Date: [DateTime.Now...]` read as one expression;
and my own test setup, which did not add the PDF and database packages, so those tasks could never build.

Building is **not** the same as running: selectors are placeholders, credentials/servers are examples, and the
process logic needs a human review in Studio.

**Known limits:**

- Excel tasks still fail (type mix-ups such as `Double` where `String` is required, members used on strings).
- The remaining failures are mostly model-side: variables used before they are declared, a JSON value used as `Object` where
  `String` is required, a `String` used where a list is expected.
- Not offered to the model because they do not exist in the installed packages or cannot be typed by the compiler:
  `AttachWindow`, `GetMailMessages`, `ConnectToDatabase` (use `ExecuteQuery`, which opens its own connection). `SAPTransaction` and
  `OpenApplication` compile but must be placed in the right scope by the model; I could not test SAP.
- The repair loop is **opt-in** because it did not measurably help; the cause was in the templates, not in what the model could fix.
  Its first version also exposed that the model sometimes answers a repair request with unreadable JSON ("token repeat limit
  reached"), which now gets one retry with another seed.
- Two bugs made earlier numbers meaningless and are fixed (tests in `tests/test_ollama_payload.py`, `tests/test_spec_compiler.py`):
  the Ollama client silently dropped `format` and `seed` (so the documented "JSON mode" and reproducible seeds never existed), and the
  compiler silently replaced model output with defaults when the model wrote property names in PascalCase. Phase 1 now uses a JSON
  schema (`src/generation/spec_schema.py`) so the model can only pick activity and variable types the compiler implements.
- `If` and `ForEach` are compiled (child steps are referenced by id), but the prompt does not teach the model that format. A longer
  prompt that did scored worse (7/15) and was dropped; few-shot examples are untested.
- Fine-tuning was only tried on a very small dataset; the benchmark effect comes from the two-phase design.
- The desktop app and the web UI were not click-tested; the API is covered by `tests/test_api_security.py`.

## What is (and isn't) in this repository

- ✅ Full source code, configuration, figures, benchmark results, dataset statistics (`results/dataset_stats`).
- ✅ Two synthetic example workflows in `data/raw/` and five model-generated workflows with their specs in `examples/generated/` (all build in UiPath, see above).
- ❌ **Training data:** the thesis used 10 real enterprise workflows from my former employer. They are proprietary and not published; the pipeline runs on any folder of your own `.xaml` files.
- ❌ Fine-tuned model weights (4.7 GB GGUF) and the thesis document.

The training set was small (10 workflows → 9 accepted, 1 fine-tuning grade, 25 repair pairs), which is the main limitation of the fine-tuning part.
The strongest effect in the benchmark comes from the **two-phase spec → compiler design**, not from fine-tuning.

## Security notes (found by testing a fresh clone, fixed 24 Nov 2026)

The local API used to have real holes; `tests/test_api_security.py` (25 cases; when I ran the first 23 against the old code, 21 failed) now pins the fixes:

- `/api/download*`: the old check was `startswith()` on an unresolved path, so `..` escaped the output folder. Now `resolve()` + `relative_to()`.
- CORS: was open to every origin; now only the local UI origins (configurable in `config.yaml`).
- Training endpoints accepted arbitrary directories; now confined to the data root.
- Model names are validated before they reach Ollama (no leading `-`, no spaces/shell characters).
- The server binds to `127.0.0.1` by default and no longer runs with `debug=True`.
- `scripts/start_system.py --help` used to start servers and pull a 4 GB model; it now has real argument parsing and `--pull-model` is opt-in.

There is no authentication: this is a local tool. Do not expose it to a network.

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
nicht enthalten. Startanleitung: [docs/STARTEN.md](docs/STARTEN.md).

> **Provenance:** this repository is the maintained home of the project. Earlier copies of it under other, older GitHub accounts are obsolete and no longer updated.

## License

MIT — see [LICENSE](LICENSE). Author: Nico Vogel ([@gh0stsyntax](https://github.com/gh0stsyntax)).
