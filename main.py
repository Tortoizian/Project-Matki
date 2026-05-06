"""
Project Mukti — unified FastAPI service.

One endpoint (POST /process-case) drives the full pipeline:
    Vision -> RAG -> Audit -> (Preview | PDF + eCourts).

The system-wide compassionate-advocate persona is injected into every
module's Claude system prompt at startup, so all LLM calls share the
same voice without rewriting each module.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from datetime import date
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("mukti.main")


# ---------------------------------------------------------------------------
# System-wide Claude persona — injected into every module's system prompt.
# ---------------------------------------------------------------------------

MUKTI_PERSONA = (
    "You are a compassionate legal advocate for Project Mukti, helping families "
    "of undertrial prisoners in India understand their rights. You always: "
    "(1) cite exact BNSS section numbers, (2) use plain language a non-lawyer "
    "can understand, (3) remind families to consult a licensed advocate before "
    "taking any action, (4) never make guarantees about legal outcomes."
)


def _inject_persona() -> None:
    """Prepend MUKTI_PERSONA to every module-level SYSTEM_PROMPT constant."""
    import auditor_module
    import rag_module

    for mod in (auditor_module, rag_module):
        for attr in ("SYSTEM_PROMPT", "SYNTHESIS_SYSTEM"):
            if hasattr(mod, attr):
                original = getattr(mod, attr)
                setattr(mod, attr, f"{MUKTI_PERSONA}\n\n{original}")


_inject_persona()


# ---------------------------------------------------------------------------
# Module imports (after persona injection)
# ---------------------------------------------------------------------------

from vision_module import extract_fir_details
from rag_module import get_relevant_legal_context
from auditor_module import calculate_bail_score, generate_bail_report
from generator_module import generate_bail_pdf
from ecourts_scraper import get_case_status
from output_api import prepare_application_for_review


# ---------------------------------------------------------------------------
# App config
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Project Mukti API",
    description="AI-assisted bail eligibility audit for undertrial prisoners in India.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://project-mukti-two.vercel.app/",
        "http://localhost:3000"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OUTPUT_DIR = Path("./generated_pdfs")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
VECTOR_DIR = Path("./legal_vectordb")


# ---------------------------------------------------------------------------
# Emergency hardcoded BNSS context — used when RAG fails entirely.
# ---------------------------------------------------------------------------

EMERGENCY_LEGAL_CONTEXT = [
    {
        "section_number": "187",
        "text": (
            "Section 187 BNSS — Procedure when investigation cannot be completed in 24 hours. "
            "If the investigation is not completed within 60 days (or 90 days for offences "
            "punishable with death, life imprisonment, or imprisonment of 10+ years), the "
            "accused is entitled to be released on default bail upon furnishing bail."
        ),
        "prisoner_rights_summary": (
            "Right to default bail if charge sheet not filed within 60 or 90 days as applicable."
        ),
    },
    {
        "section_number": "479",
        "text": (
            "Section 479 BNSS — Maximum period for which an undertrial prisoner can be detained. "
            "An undertrial who has spent more than half of the maximum sentence prescribed for "
            "the offence in custody must be released on bond, subject to first-time-offender "
            "conditions for one-third detention release."
        ),
        "prisoner_rights_summary": (
            "Mandatory release after serving half the maximum sentence as undertrial."
        ),
    },
    {
        "section_number": "436",
        "text": (
            "Section 436 BNSS — In bailable offences, bail is a matter of right. The officer in "
            "charge or court must release the accused on bail."
        ),
        "prisoner_rights_summary": "Bail is a right, not discretion, in bailable offences.",
    },
]


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------

def _save_upload_to_tmp(image: UploadFile) -> str:
    suffix = Path(image.filename or "upload.png").suffix or ".png"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.write(image.file.read())
    tmp.close()
    return tmp.name


def _run_vision(image: Optional[UploadFile]) -> tuple[Optional[dict], dict]:
    """Returns (fir_data_or_none, vision_step_info)."""
    if image is None or not image.filename:
        logger.info("No image provided. Skipping vision step.")
        return None, {"success": False, "confidence": 0, "skipped": True}

    tmp_path = None
    try:
        logger.info("Starting vision extraction...")
        tmp_path = _save_upload_to_tmp(image)
        result = extract_fir_details(tmp_path)
        confidence = int(result.get("confidence_score", 0))
        logger.info("Vision extraction complete. Extracted data: %s", result)
        return result, {"success": True, "confidence": confidence}
    except Exception as exc:
        logger.exception("Vision step failed: %s", exc)
        return None, {"success": False, "confidence": 0, "error": str(exc)}
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _run_rag(fir_data: dict) -> tuple[list[dict], dict]:
    """JSON-backed retrieval: looks up each charged section in bns_bail_mapping.json."""
    sections = fir_data.get("sections_charged") or []
    logger.info("Starting RAG lookup for sections: %s", sections)
    aggregated: list[dict] = []

    for sec in sections:
        try:
            ctx = get_relevant_legal_context(query="", target_bns_section=str(sec))
            offence = ctx.get("structured_offence_data") or {}
            if offence:
                logger.info("RAG found info for section %s: %s", sec, offence.get('offence_name'))
                aggregated.append({
                    "section_number": offence.get("bns_section"),
                    "text": (
                        f"{offence.get('offence_name', '')}. "
                        f"Punishment: {offence.get('punishment', '')}. "
                        f"Bailable: {offence.get('is_bailable', 'Unknown')}. "
                        f"Cognizable: {offence.get('cognizable', 'Unknown')}."
                    ),
                    "prisoner_rights_summary": offence.get("satender_category", ""),
                })
        except Exception as exc:
            logger.warning("RAG lookup failed for section %s: %s", sec, exc)

    # Always append the procedural BNSS sections that govern bail timelines.
    aggregated.extend(EMERGENCY_LEGAL_CONTEXT)

    sections_found = sorted({c["section_number"] for c in aggregated if c.get("section_number")})
    logger.info("RAG complete. Total specific sections found: %d", len(sections_found) - 3)
    return aggregated, {"sections_found": len(sections_found), "sections": sections_found}


def _run_audit(fir_data: dict, legal_context: list[dict]) -> tuple[dict, dict]:
    try:
        logger.info("Starting Audit step. Calculating bail score...")
        score = calculate_bail_score(fir_data, date.today())
        logger.info("Bail score calculated: %d. Grounds found: %s", score.get("score", 0), score.get("reasons", []))
        
        logger.info("Generating plain language report with LLM...")
        report = generate_bail_report(fir_data, score, legal_context)
        report_dict = report.to_dict()
        logger.info("Audit report generated successfully.")
        return report_dict, {
            "confidence_score": report_dict["confidence_score"],
            "bail_grounds": report_dict["bail_grounds"],
        }
    except Exception as exc:
        logger.exception("Auditor step failed: %s", exc)
        partial = {
            "accused_name": fir_data.get("accused_name"),
            "fir_number": fir_data.get("fir_number"),
            "days_in_custody": 0,
            "confidence_score": 0,
            "bail_grounds": [],
            "legal_contradictions": [],
            "plain_language_summary": "",
            "applicable_sections": [],
            "human_review_required": True,
            "disclaimer": "",
            "error": f"audit_failed: {exc}",
        }
        return partial, {"confidence_score": 0, "bail_grounds": [], "error": str(exc)}


async def _run_generation(
    report: dict,
    cnr_number: Optional[str],
    approved: bool,
) -> tuple[dict, Optional[dict]]:
    """Returns (generation_step_info, court_status_or_none)."""
    if not approved:
        logger.info("Application not yet approved. Preparing review preview...")
        preview = prepare_application_for_review(report)
        return {"approved": False, "pdf_url": None, "preview": preview["preview"]}, None

    logger.info("Application approved. Generating PDF...")
    fir_no = (report.get("fir_number") or "unknown").replace("/", "-")
    filename = f"bail_application_{fir_no}_{uuid.uuid4().hex[:8]}.pdf"
    pdf_path = OUTPUT_DIR / filename

    pdf_url: Optional[str] = None
    try:
        generate_bail_pdf(report, str(pdf_path), approved=True)
        pdf_url = f"/download/{filename}"
        logger.info("PDF generated successfully at %s", pdf_path)
    except Exception as exc:
        logger.exception("PDF generation failed: %s", exc)

    court_status: Optional[dict] = None
    if cnr_number:
        logger.info("Fetching eCourts status for CNR: %s", cnr_number)
        try:
            court_status = await get_case_status(cnr_number)
            logger.info("eCourts status fetched: %s", court_status.get("status"))
        except Exception as exc:
            logger.warning("eCourts lookup failed: %s", exc)
            court_status = {"source": "error", "cnr": cnr_number, "error": str(exc)}

    return {"approved": True, "pdf_url": pdf_url}, court_status


def _looks_like_full_report(payload: dict) -> bool:
    """Detect whether the caller is resubmitting a previously-built BailEligibilityReport."""
    return all(k in payload for k in ("confidence_score", "bail_grounds", "applicable_sections"))


# ---------------------------------------------------------------------------
# Main endpoint
# ---------------------------------------------------------------------------

@app.post("/process-case")
async def process_case(
    file: Optional[UploadFile] = File(None),
    fir_data_json: Optional[str] = Form(None),
    cnr_number: Optional[str] = Form(None),
    approved: bool = Form(False),
) -> JSONResponse:
    pipeline_steps: dict = {}

    # If a previously-built report is being resubmitted for approval, accept it
    # whole instead of rerunning vision/RAG/audit.
    parsed_payload: Optional[dict] = None
    if fir_data_json:
        try:
            parsed_payload = json.loads(fir_data_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"fir_data_json is not valid JSON: {exc}")

    if approved and parsed_payload and _looks_like_full_report(parsed_payload):
        bail_report = parsed_payload
        generation_step, court_status = await _run_generation(bail_report, cnr_number, approved=True)
        pipeline_steps["generation"] = generation_step
        return JSONResponse(content={
            "status": "generated",
            "pdf_path": generation_step.get("pdf_url") or "",
            "download_url": generation_step.get("pdf_url") or "",
            "court_status": court_status,
            "pipeline_steps": pipeline_steps,
            "human_review_required": True,
        })

    # ---- STEP 1: Vision or manual entry --------------------------------
    vision_result, vision_step = _run_vision(file)
    pipeline_steps["vision"] = vision_step

    fir_data: Optional[dict] = None
    if vision_result and vision_step["confidence"] >= 40:
        fir_data = vision_result
    else:
        if parsed_payload:
            fir_data = parsed_payload
        else:
            return JSONResponse(content={
                "status": "needs_manual_entry",
                "message": "Image quality too low. Please enter case details manually.",
                "partial_data": vision_result,
                "pipeline_steps": pipeline_steps,
                "human_review_required": True,
            })

    # ---- STEP 2: RAG ---------------------------------------------------
    legal_context, rag_step = _run_rag(fir_data)
    pipeline_steps["rag"] = rag_step

    # ---- STEP 3: Audit -------------------------------------------------
    bail_report, audit_step = _run_audit(fir_data, legal_context)
    pipeline_steps["audit"] = audit_step

    # ---- STEP 4: Generate or preview ----------------------------------
    generation_step, court_status = await _run_generation(bail_report, cnr_number, approved)
    pipeline_steps["generation"] = generation_step

    # ---- STEP 5: Envelope ----------------------------------------------
    if approved and generation_step.get("pdf_url"):
        return JSONResponse(content={
            "status": "generated",
            "pdf_path": generation_step["pdf_url"],
            "download_url": generation_step["pdf_url"],
            "court_status": court_status,
            "pipeline_steps": pipeline_steps,
            "human_review_required": True,
        })

    preview = generation_step.get("preview") or {
        "accused_name": bail_report.get("accused_name"),
        "fir_number": bail_report.get("fir_number"),
        "days_in_custody": bail_report.get("days_in_custody"),
        "applicable_sections": bail_report.get("applicable_sections", []),
        "confidence_score": bail_report.get("confidence_score", 0),
        "summary_excerpt": (bail_report.get("plain_language_summary") or "")[:600],
    }

    return JSONResponse(content={
        "status": "awaiting_human_review",
        "report": bail_report,
        "preview": preview,
        "pipeline_steps": pipeline_steps,
        "human_review_required": True,
    })


# ---------------------------------------------------------------------------
# Auxiliary endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    bnss_json = Path(__file__).parent / "bns_bail_mapping.json"
    bnss_loaded = bnss_json.exists()
    from llm_client import model_name
    return {
        "status": "ok",
        "model": model_name(),
        "bnss_loaded": bnss_loaded,
    }


@app.get("/download/{filename}")
def download_pdf(filename: str):
    safe = Path(filename).name
    file_path = OUTPUT_DIR / safe
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found")
    return FileResponse(str(file_path), media_type="application/pdf", filename=safe)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
