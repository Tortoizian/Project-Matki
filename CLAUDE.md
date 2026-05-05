# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Project Mukti** — an AI-assisted bail eligibility audit pipeline for undertrial prisoners in India. The system reads an FIR image, retrieves relevant BNSS statutes and Supreme Court precedents, computes a deterministic bail score, generates a court-ready PDF, and fetches live eCourts status.

## Commands

```bash
# install
pip install -r requirements.txt
playwright install chromium                  # only needed for live eCourts scraping

# one-time corpus ingestion (creates ./legal_vectordb and ./offences.db)
python -c "from rag_module import ingest_legal_data; ingest_legal_data('BNSS.pdf','Bail.pdf')"

# run the unified API (single entry point — do not run modules individually in prod)
uvicorn main:app --port 8000 --reload

# health check
curl http://localhost:8000/health
```

Each module also exposes its own `app` (vision_module, rag_module, auditor_module, output_api, ecourts_scraper) on different ports for isolated testing — useful when debugging a single stage.

There is **no test suite** in this repo yet. There is no linter configured.

## Architecture

The system is a 4-stage pipeline orchestrated by [main.py](main.py) behind one endpoint, `POST /process-case`. Understanding the data shape that flows between stages is more important than understanding any single module.

```
UploadFile (image)            manual_fir_data (JSON, fallback)
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

1. **Deterministic scoring**: `confidence_score` in the BailEligibilityReport is computed by pure Python rules in `auditor_module.calculate_bail_score`. Claude is **only** used for the plain-language summary. Never let an LLM produce the score.
2. **Human-in-the-loop gate**: `generator_module.generate_bail_pdf` raises `PermissionError` unless called with `approved=True`. The `/process-case` endpoint enforces this via the `approved` form field. Do not bypass.
3. **System-wide Claude persona**: `main.py` injects `MUKTI_PERSONA` into every module's `SYSTEM_PROMPT` / `SYNTHESIS_SYSTEM` constant at import time (see `_inject_persona()` in [main.py](main.py)). Adding new modules that call Claude → either expose a `SYSTEM_PROMPT` constant for `_inject_persona` to patch, or import `MUKTI_PERSONA` directly. The persona must apply to every LLM call.
4. **Graceful degradation per stage**: Vision failure → falls back to manual entry; RAG failure → falls back to `EMERGENCY_LEGAL_CONTEXT` (hardcoded BNSS 187/479/436 in [main.py](main.py)); audit failure → returns partial report with `error` flag; PDF failure → returns report without `pdf_url`. None of these stages should ever raise out of `/process-case`.

### Data contracts between stages

- **Vision → Auditor** (`fir_data`): keys `accused_name`, `sections_charged` (list[str]), `date_of_arrest` (ISO string), `police_station`, `fir_number`, `case_narrative`, `confidence_score` (int 0–100). Auditor also reads optional `charge_sheet_filed` (bool, defaults False).
- **RAG → Auditor** (`legal_context`): list of dicts with `section_number`, `text`, `prisoner_rights_summary`. Other keys (`case_name`, etc.) are tolerated.
- **Auditor → Generator** (`BailEligibilityReport.to_dict()`): canonical report shape — `accused_name`, `fir_number`, `days_in_custody`, `confidence_score`, `bail_grounds`, `legal_contradictions`, `plain_language_summary`, `applicable_sections`, `human_review_required`, `disclaimer`.

### Storage

- `./legal_vectordb/` — ChromaDB persistent collection `indian_bail_law` (HuggingFace `all-MiniLM-L6-v2` embeddings).
- `./offences.db` — SQLite with one table `offences(bns_section, offence_name, punishment, satender_category)`. Categories A–D follow the *Satender Kumar Antil* judgment.
- `./generated_pdfs/` — output directory for approved bail applications.
- `./mock_court_db.json` — three seeded eCourts entries used as fallback when live scraping fails or exceeds the 10s budget. Keyed by CNR number.

### Chunking strategy

- `rag_module._chunk_bnss` splits the BNSS PDF strictly at line-anchored `^Section <num>` headers, not by character count. If the PDF format changes, adjust `_SECTION_PATTERN`.
- `rag_module._chunk_bail_commentary` splits on landmark case names listed in `_BAIL_CASES`. Adding new precedents → append to that list.

### Hybrid retrieval

`rag_module._build_ensemble` combines a Chroma vector retriever and a BM25 retriever with equal weights. BM25 indexes the same docs pulled out of Chroma to keep the corpora in sync. The ensemble is cached at module level — restart the process after re-ingestion.

### eCourts scraping

`ecourts_scraper._scrape_ecourts` is best-effort. The live portal mutates its markup and gates most searches with a CAPTCHA, so the selectors in that function are heuristic. Any failure or 10s timeout falls through to `_mock_lookup` against [mock_court_db.json](mock_court_db.json). Do not treat a successful Playwright run as guaranteed.

### Model

All Claude calls use `claude-sonnet-4-5` (configured per-module). The `/health` endpoint reports this string explicitly — keep it in sync if you change models.
