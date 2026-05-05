"""
Output orchestration API for Project Mukti.

Exposes POST /generate-application:
  - approved=False  -> returns review payload (no PDF written)
  - approved=True   -> generates PDF + fetches eCourts status

The review-before-generate gate is enforced by:
  1. prepare_application_for_review() returning status='pending_approval'
  2. generator_module.generate_bail_pdf() rejecting calls with approved=False
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from generator_module import generate_bail_pdf
from ecourts_scraper import get_case_status


OUTPUT_DIR = Path("./generated_pdfs")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Human-in-the-loop helper
# ---------------------------------------------------------------------------

def prepare_application_for_review(report: dict) -> dict:
    """
    Wraps a BailEligibilityReport in a review envelope.
    The PDF is NOT generated until generate_bail_pdf is called with approved=True.
    """
    return {
        "status": "pending_approval",
        "report": report,
        "preview": {
            "accused_name": report.get("accused_name"),
            "fir_number": report.get("fir_number"),
            "days_in_custody": report.get("days_in_custody"),
            "confidence_score": report.get("confidence_score"),
            "applicable_sections": report.get("applicable_sections", []),
            "ground_count": len(report.get("bail_grounds", [])),
            "summary_excerpt": (report.get("plain_language_summary") or "")[:280],
        },
        "next_step": (
            "Have a qualified advocate review the preview. Re-submit with "
            "approved=true to generate the PDF and fetch the latest court status."
        ),
    }


# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(title="Project Mukti — Output Layer")


class GenerateRequest(BaseModel):
    report: dict = Field(..., description="BailEligibilityReport JSON from auditor_module")
    cnr_number: Optional[str] = None
    approved: bool = False
    court_name: str = "[Court Name]"
    language: str = "en"


@app.post("/generate-application")
async def generate_application(payload: GenerateRequest) -> dict:
    if not payload.report:
        raise HTTPException(status_code=400, detail="report is required")

    # Gate 1 — human review.
    if not payload.approved:
        review = prepare_application_for_review(payload.report)
        review["status"] = "awaiting_human_review"
        return review

    # Gate 2 — approved path: generate PDF and fetch court status.
    fir_no = (payload.report.get("fir_number") or "unknown").replace("/", "-")
    filename = f"bail_application_{fir_no}_{uuid.uuid4().hex[:8]}.pdf"
    pdf_path = OUTPUT_DIR / filename

    try:
        generate_bail_pdf(
            payload.report,
            str(pdf_path),
            court_name=payload.court_name,
            language=payload.language,
            approved=True,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {exc}")

    court_status = await get_case_status(payload.cnr_number or "")

    return {
        "status": "generated",
        "pdf_path": str(pdf_path),
        "download_url": f"/download/{filename}",
        "court_status": court_status,
    }


@app.get("/download/{filename}")
def download_pdf(filename: str):
    safe = Path(filename).name  # strip any path traversal
    file_path = OUTPUT_DIR / safe
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found")
    return FileResponse(str(file_path), media_type="application/pdf", filename=safe)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)
