# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Project Mukti** — an AI-assisted bail eligibility audit pipeline for undertrial prisoners in India. The system reads an FIR image, looks up BNSS statutes from a curated JSON mapping, computes a deterministic bail score, generates a court-ready PDF, and fetches live eCourts status.

## Commands

```bash
# install
pip install -r requirements.txt
playwright install chromium                  # only needed for live eCourts scraping

# run the unified API (single entry point — do not run modules individually in prod)
uvicorn main:app --port 8000 --reload

# health check
curl http://localhost:8000/health
```

Each module also exposes its own `app` (vision_module, rag_module, auditor_module, output_api, ecourts_scraper) on different ports for isolated testing — useful when debugging a single stage.

There is **no test suite** in this repo yet. There is no linter configured.

## LLM provider

All LLM calls go through [llm_client.py](llm_client.py), which wraps Google Gemini. Set `GOOGLE_API_KEY` in `.env`. The default model is `gemini-1.5-pro`, overridable via `MUKTI_GEMINI_MODEL`. Adding a new module that needs an LLM → import `generate_text` or `generate_with_image` from `llm_client`.

## Architecture

The system is a 4-stage pipeline orchestrated by [main.py](main.py) behind one endpoint, `POST /process-case`. Understanding the data shape that flows between stages is more important than understanding any single module.

```
UploadFile (file)             fir_data_json (JSON, fallback or resubmit)
     │                                    │
     ▼                                    ▼
[vision_module]   ── fir_data ──►  (confidence < 40 ? ask for manual entry)
                                          │
                                          ▼
                         [rag_module] ── legal_context (list[dict]) ──┐
                                                                      ▼
                              [auditor_module] ── BailEligibilityReport (dict)
                                                                      │
                              approved=False ──► preview only         │
                                                                      ▼
                              approved=True  ──► [generator_module] + [ecourts_scraper]
```

### Critical invariants

1. **Deterministic scoring**: `confidence_score` in the BailEligibilityReport is computed by pure Python rules in `auditor_module.calculate_bail_score`, using the JSON mapping for bailable-status and max-sentence lookups. The LLM is **only** used for the plain-language summary. Never let an LLM produce the score.
2. **Human-in-the-loop gate**: `generator_module.generate_bail_pdf` raises `PermissionError` unless called with `approved=True`. The `/process-case` endpoint enforces this via the `approved` form field. Do not bypass.
3. **System-wide persona**: `main.py` injects `MUKTI_PERSONA` into every module's `SYSTEM_PROMPT` / `SYNTHESIS_SYSTEM` constant at import time (see `_inject_persona()` in [main.py](main.py)). Adding new modules that call the LLM → expose a `SYSTEM_PROMPT` constant for `_inject_persona` to patch, or import `MUKTI_PERSONA` directly.
4. **Approval shortcut**: When `/process-case` is called with `approved=true` and a `fir_data_json` payload that already looks like a built BailEligibilityReport (has `confidence_score`, `bail_grounds`, `applicable_sections`), it skips vision/RAG/audit and goes straight to PDF generation. This is the path the frontend takes after the user clicks "Approve" in the review screen.
5. **Graceful degradation per stage**: Vision failure → falls back to manual entry; RAG failure → still appends `EMERGENCY_LEGAL_CONTEXT` (hardcoded BNSS 187/479/436 in [main.py](main.py)); audit failure → returns partial report with `error` flag; PDF failure → returns report without `pdf_url`. None of these stages should ever raise out of `/process-case`.

### Data contracts between stages

- **Vision → Auditor** (`fir_data`): keys `accused_name`, `sections_charged` (list[str]), `date_of_arrest` (ISO string), `police_station`, `fir_number`, `case_narrative`, `confidence_score` (int 0–100). Auditor also reads optional `charge_sheet_filed` (bool, defaults False).
- **RAG → Auditor** (`legal_context`): list of dicts with `section_number`, `text`, `prisoner_rights_summary`. Built by [main.py](main.py) `_run_rag` from `bns_bail_mapping.json` lookups plus `EMERGENCY_LEGAL_CONTEXT`.
- **Auditor → Generator** (`BailEligibilityReport.to_dict()`): canonical report shape — `accused_name`, `fir_number`, `days_in_custody`, `confidence_score`, `bail_grounds`, `legal_contradictions`, `plain_language_summary`, `applicable_sections`, `human_review_required`, `disclaimer`.

### Storage

- `./bns_bail_mapping.json` — canonical BNS section data (offence text, punishment, `is_bailable`, `cognizable`, `antil_category`, `max_sentence_days`). This is the **only** source of bail/sentence data; the previous ChromaDB + SQLite + PyPDF stack has been removed.
- `./generated_pdfs/` — output directory for approved bail applications.
- `./mock_court_db.json` — three seeded eCourts entries used as fallback when live scraping fails or exceeds the 10s budget. Keyed by CNR number.

### JSON mapping format

Keys are BNS section codes; sub-sections use underscores (e.g. `303_1`, `303_2`). The auditor's `_section_entry` helper tolerates `"Section 303"`, `"303"`, and `"303_1"` forms — if you pass `"303"` and only `"303_1"` exists, it will fall back to the first sub-section match. Adding new sections → just add the key with the same shape; no re-ingestion required.

### eCourts scraping

`ecourts_scraper._scrape_ecourts` is best-effort. The live portal mutates its markup and gates most searches with a CAPTCHA, so the selectors in that function are heuristic. Any failure or 10s timeout falls through to `_mock_lookup` against [mock_court_db.json](mock_court_db.json). Do not treat a successful Playwright run as guaranteed.

### Frontend

A Next.js wizard lives in [frontend/app/page.tsx](frontend/app/page.tsx). It calls `POST /process-case` with `file`, `fir_data_json`, `cnr_number`, `approved`. The review screen resubmits the full `report` object inside `fir_data_json` with `approved="true"` to trigger the approval shortcut.
