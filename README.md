# Deployment Link
project-mukti-two.vercel.app

# Project Mukti

**AI-assisted bail eligibility audit for undertrial prisoners in India.**

India holds over 75% of its prison population as undertrial detainees — people who have not been convicted of anything. Many of them are legally entitled to bail under the Bharatiya Nagarik Suraksha Sanhita (BNSS), 2023, but their families lack the legal literacy to know it or act on it.

Project Mukti reads an FIR image, looks up the charged sections against a curated BNSS statute database, computes a deterministic bail eligibility score, and generates a court-ready PDF bail application — all in under a minute, for free.

---

## What it does

1. **Reads an FIR** — upload a photo of the First Information Report; a vision model extracts the accused name, charged sections, arrest date, FIR number, and case narrative.
2. **Looks up the law** — each charged section is matched against `bns_bail_mapping.json`, the First Schedule of BNSS 2023, to determine bail status, punishment, and maximum sentence.
3. **Scores bail eligibility** — a pure-Python rule engine (no LLM) checks three statutory grounds:
   - **BNSS §187** — default bail if charge sheet not filed within 60/90 days
   - **BNSS §479** — mandatory release if undertrial time exceeds half the maximum sentence (one-third for first-time offenders)
   - **BNSS §436** — bail as a right for bailable offences
4. **Explains in plain language** — an LLM writes a three-paragraph summary for the prisoner's family in accessible language, always directing them to the District Legal Services Authority (DLSA) for free legal aid.
5. **Human-in-the-loop gate** — a human must review and approve the report before any PDF is generated or filed.
6. **Generates a bail application PDF** — court-ready document with the accused's details, applicable sections, grounds, and a mandatory advocate-review disclaimer.
7. **Fetches live court status** — optional eCourts lookup by CNR number; falls back to mock data if the portal is unreachable.

---

## Architecture

```
UploadFile (FIR image)           fir_data_json (manual entry / resubmit)
         │                                       │
         ▼                                       ▼
  [vision_module]  ──── fir_data ────►  confidence ≥ 40?
                                              │  No → ask for manual entry
                                              │  Yes ↓
                                    [rag_module] — bns_bail_mapping.json lookup
                                              │
                                              ▼
                                    [auditor_module]
                                    ├── calculate_bail_score()   ← pure Python, no LLM
                                    └── generate_bail_report()   ← LLM summary only
                                              │
                                    approved=False → preview (awaiting_human_review)
                                    approved=True  → [generator_module] + [ecourts_scraper]
                                                           │
                                                    generated_pdfs/
```

All four stages are orchestrated by a single `POST /process-case` endpoint in [main.py](main.py). Each stage degrades gracefully — no failure should ever surface as an unhandled exception to the caller.

---

## Key design decisions

| Decision | Why |
|---|---|
| Deterministic bail score | The score must be reproducible and auditable. An LLM cannot produce it — only pure Python rules against the JSON statute map can. |
| Human-in-the-loop gate | `generate_bail_pdf` raises `PermissionError` unless called with `approved=True`. The UI enforces a review screen before resubmitting with approval. |
| JSON statute map (no vector DB) | Bail eligibility is a lookup, not a semantic search. A flat JSON keyed by BNSS section number is faster, deterministic, and requires no embedding pipeline. |
| Compassionate advocate persona | Every LLM call shares the same system persona (injected at startup by `_inject_persona()`), ensuring consistent tone across all modules without repeating the prompt in each file. |
| Graceful degradation per stage | Vision failure → manual entry fallback; RAG failure → hardcoded BNSS 187/479/436 emergency context; audit failure → partial report; PDF failure → report without download link. |

---

## Tech stack

| Layer | Technology |
|---|---|
| Backend API | FastAPI + Uvicorn |
| LLM | Google Gemini (`gemini-1.5-pro` via `google-generativeai`) |
| Vision (FIR OCR) | Gemini multimodal / OpenCV + Pillow |
| PDF generation | ReportLab |
| eCourts scraping | Playwright (Chromium) |
| Frontend | Next.js (single-page wizard in `frontend/app/page.tsx`) |
| Statute data | `bns_bail_mapping.json` — First Schedule, BNSS 2023 |

---

## Setup

### Prerequisites

- Python 3.10+
- Node.js 18+ (for the frontend)
- A Google AI Studio API key

### Backend

```bash
# Clone and enter the repo
git clone <repo-url>
cd Project-Mukti

# Install Python dependencies
pip install -r requirements.txt

# Install Playwright browser (only needed for live eCourts lookups)
playwright install chromium

# Configure environment
cp .env.example .env          # or create .env manually
# Set GOOGLE_API_KEY=your_key_here
# Optional: MUKTI_GEMINI_MODEL=gemini-1.5-pro

# Start the unified API
uvicorn main:app --port 8000 --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev      # runs on http://localhost:3000
```

### Health check

```bash
curl http://localhost:8000/health
# {"status":"ok","model":"gemini-1.5-pro","bnss_loaded":true}
```

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GOOGLE_API_KEY` | Yes | — | Google AI Studio key for Gemini |
| `MUKTI_GEMINI_MODEL` | No | `gemini-1.5-pro` | Override the Gemini model |

---

## API reference

### `POST /process-case`

Main pipeline endpoint. Accepts `multipart/form-data`.

| Field | Type | Description |
|---|---|---|
| `file` | File (optional) | FIR image (JPEG/PNG/PDF) |
| `fir_data_json` | String (optional) | JSON of manually entered FIR fields, or a previously-built `BailEligibilityReport` for the approval shortcut |
| `cnr_number` | String (optional) | Court case number for eCourts lookup |
| `approved` | Boolean | `false` = return preview; `true` = generate PDF |

**Response — awaiting review**
```json
{
  "status": "awaiting_human_review",
  "report": { "confidence_score": 80, "bail_grounds": [...], ... },
  "preview": { "accused_name": "...", "days_in_custody": 73, ... },
  "pipeline_steps": { "vision": {...}, "rag": {...}, "audit": {...} },
  "human_review_required": true
}
```

**Response — PDF generated**
```json
{
  "status": "generated",
  "download_url": "/download/bail_application_123_abc12345.pdf",
  "court_status": { "cnr": "MHCC010012345", "next_hearing": "2024-08-15", ... },
  "human_review_required": true
}
```

### `GET /download/{filename}`

Download a previously generated bail application PDF.

### `GET /health`

Returns API status, loaded model name, and whether the BNSS statute map is present.

---

## Scoring rules

The `confidence_score` (0–100) is the sum of points from triggered grounds:

| Ground | Points | Condition |
|---|---|---|
| BNSS §187 — default bail | +40 | Charge sheet not filed within 60 days (90 days for offences carrying 10+ years / life / death) |
| BNSS §479 — half-sentence | +40 | Days in custody > half of maximum sentence (not applicable for life/death offences) |
| BNSS §479 — first-time offender | +30 | Days in custody > one-third of maximum sentence (first-time offenders only) |
| BNSS §436 — bailable offence | +20 | All charged sections are bailable (no non-bailable charge present) |

Maximum score is capped at 100. The score is never produced by an LLM.

---

## Data files

### `bns_bail_mapping.json`

Keyed by BNSS section code (e.g. `"303"`, `"303_1"`). Each entry contains:

```json
{
  "offence_name": "Murder",
  "punishment": "Death or Life Imprisonment",
  "is_bailable": false,
  "cognizable": true,
  "max_sentence_days": 36500,
  "antil_category": "A"
}
```

Adding new sections: add the key with the same shape. No re-ingestion or embedding needed.

### `mock_court_db.json`

Three seeded eCourts entries (keyed by CNR number) used as fallback when the live eCourts portal is unreachable or the 10-second scraping budget is exceeded.

---

## Disclaimer

Project Mukti is an AI tool built to help families of undertrial prisoners understand potentially applicable legal provisions. **It does not constitute legal advice.** All generated documents must be reviewed by a qualified licensed advocate before filing. Families are directed to the nearest District Legal Services Authority (DLSA / Zila Vidhi Seva Pradhikaran) for free legal representation.

---

## Built at Anthropic Hackathon 2025
