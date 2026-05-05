"""
Legal Auditor agent for Project Mukti.

Deterministic scoring + Claude-generated plain-language explanation.
The confidence_score is NEVER produced by the LLM — only Python rules set it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Optional

import anthropic
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DISCLAIMER = (
    "This document identifies potentially applicable legal provisions for review "
    "by a licensed advocate. It does not constitute legal advice. Project Mukti "
    "is an AI tool — all applications must be reviewed by a qualified legal "
    "professional before filing."
)

CLOSING_LINE = (
    "Please take this document to a free legal aid clinic (Zila Vidhi Seva "
    "Pradhikaran) for a qualified advocate to review and file."
)

SYSTEM_PROMPT = (
    "You are a compassionate legal advocate helping a prisoner's family in India "
    "understand their loved one's rights. You are NOT providing legal advice. "
    "You are explaining what the law says. Always end with: "
    f"'{CLOSING_LINE}'"
)

# Bailable offences under BNS / legacy IPC commonly encountered.
BAILABLE_SECTIONS = {
    "379", "420", "323", "504", "506",  # legacy IPC
    "303", "318", "319", "115", "351",  # BNS equivalents
}

# Indicative maximum-sentence lookup (in days) keyed by section code.
# Used when fir_data does not supply an explicit estimate.
_MAX_SENTENCE_DAYS = {
    "303": 3 * 365,    # Theft — up to 3 years
    "305": 7 * 365,
    "309": 10 * 365,   # Robbery
    "310": 10 * 365,   # Dacoity (max term form)
    "318": 7 * 365,    # Cheating
    "319": 5 * 365,
    "115": 1 * 365,
    "117": 7 * 365,
    "351": 2 * 365,
    "420": 7 * 365,
    "379": 3 * 365,
    "323": 1 * 365,
    "504": 2 * 365,
    "506": 2 * 365,
}
_DEFAULT_MAX_SENTENCE_DAYS = 3 * 365  # conservative fallback


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(value) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None
    return None


def _estimate_max_sentence_days(sections: list[str]) -> int:
    """Pick the harshest known section among those charged."""
    known = [_MAX_SENTENCE_DAYS[s] for s in (sections or []) if s in _MAX_SENTENCE_DAYS]
    return max(known) if known else _DEFAULT_MAX_SENTENCE_DAYS


# ---------------------------------------------------------------------------
# Step 1 — Deterministic scoring
# ---------------------------------------------------------------------------

def calculate_bail_score(fir_data: dict, today_date: date) -> dict:
    """Pure-Python rule engine. Never calls an LLM."""
    sections_charged = fir_data.get("sections_charged") or []
    arrest_date = _parse_date(fir_data.get("date_of_arrest"))
    charge_sheet_filed = bool(fir_data.get("charge_sheet_filed", False))
    charge_sheet_not_filed = not charge_sheet_filed

    if arrest_date is None:
        return {
            "score": 0,
            "reasons": ["Date of arrest is missing — cannot compute custodial duration."],
            "days_in_custody": 0,
            "applicable_sections": [],
        }

    days_in_custody = max((today_date - arrest_date).days, 0)
    estimated_max_sentence_days = _estimate_max_sentence_days(sections_charged)

    score = 0
    reasons: list[str] = []
    applicable: list[str] = []

    # Check 1 — Section 187 BNSS (default bail on charge-sheet delay)
    if days_in_custody > 60 and charge_sheet_not_filed:
        score += 40
        reasons.append(
            "Section 187 BNSS: Charge sheet not filed within 60-day limit — default bail triggered"
        )
        applicable.append("BNSS Section 187")

    # Check 2 — Section 479 BNSS (half-sentence served as undertrial)
    if days_in_custody > estimated_max_sentence_days / 2:
        score += 40
        reasons.append(
            "Section 479 BNSS: Undertrial detention exceeds half of maximum sentence"
        )
        applicable.append("BNSS Section 479")

    # Check 3 — Section 436 BNSS (bailable offence)
    if any(s in BAILABLE_SECTIONS for s in sections_charged):
        score += 20
        reasons.append(
            "Section 436 BNSS: Offence classified as bailable — bail is a right, not discretion"
        )
        applicable.append("BNSS Section 436")

    score = min(score, 100)

    return {
        "score": score,
        "reasons": reasons,
        "days_in_custody": days_in_custody,
        "applicable_sections": applicable,
        "estimated_max_sentence_days": estimated_max_sentence_days,
    }


# ---------------------------------------------------------------------------
# Step 2 — Claude explanation
# ---------------------------------------------------------------------------

_anthropic_client = anthropic.Anthropic()


def _format_legal_context(legal_context: list[dict]) -> str:
    if not legal_context:
        return "No additional statutory text was retrieved."
    parts = []
    for i, item in enumerate(legal_context, 1):
        sec = item.get("section_number", "?")
        text = item.get("text", "")
        rights = item.get("prisoner_rights_summary", "")
        parts.append(f"[{i}] Section {sec}\n{text}\nRights summary: {rights}")
    return "\n\n".join(parts)


def _build_user_prompt(fir_data: dict, score_result: dict, legal_context: list[dict]) -> str:
    return (
        f"Accused: {fir_data.get('accused_name')}\n"
        f"FIR Number: {fir_data.get('fir_number')}\n"
        f"Sections charged: {fir_data.get('sections_charged')}\n"
        f"Days in custody: {score_result['days_in_custody']}\n\n"
        f"Deterministic bail score: {score_result['score']}/100\n"
        f"Triggered legal grounds:\n- "
        + ("\n- ".join(score_result["reasons"]) if score_result["reasons"] else "None triggered")
        + "\n\nRelevant statutory context:\n"
        + _format_legal_context(legal_context)
        + "\n\nWrite a clear three-paragraph explanation for the family:\n"
          "Paragraph 1 — State plainly what was found in their loved one's case.\n"
          "Paragraph 2 — Name the specific BNSS sections that apply and what each one means.\n"
          "Paragraph 3 — Tell the family exactly what to do next, then end with the mandatory closing line."
    )


# ---------------------------------------------------------------------------
# Step 3 — Report dataclass
# ---------------------------------------------------------------------------

@dataclass
class BailEligibilityReport:
    accused_name: Optional[str]
    fir_number: Optional[str]
    days_in_custody: int
    confidence_score: int
    bail_grounds: list[str]
    legal_contradictions: list[str]
    plain_language_summary: str
    applicable_sections: list[str]
    human_review_required: bool = True
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict:
        return asdict(self)


def _detect_contradictions(fir_data: dict, score_result: dict) -> list[str]:
    """Surface internal inconsistencies that an advocate should examine."""
    contradictions = []
    sections = fir_data.get("sections_charged") or []
    bailable_hit = any(s in BAILABLE_SECTIONS for s in sections)
    if bailable_hit and score_result["days_in_custody"] > 30:
        contradictions.append(
            "Offence appears bailable yet accused has been in custody beyond 30 days — "
            "examine whether bail was applied for and rejected."
        )
    if score_result["days_in_custody"] > 60 and not fir_data.get("charge_sheet_filed", False):
        contradictions.append(
            "Custody exceeds the 60-day investigation window without a filed charge sheet — "
            "default-bail eligibility under Section 187 BNSS should be raised immediately."
        )
    if not fir_data.get("date_of_arrest"):
        contradictions.append("Date of arrest is missing from the FIR record.")
    return contradictions


def generate_bail_report(
    fir_data: dict,
    score_result: dict,
    legal_context: list[dict],
) -> BailEligibilityReport:
    user_prompt = _build_user_prompt(fir_data, score_result, legal_context)

    try:
        response = _anthropic_client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1200,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        summary = response.content[0].text.strip()
        if CLOSING_LINE not in summary:
            summary = f"{summary}\n\n{CLOSING_LINE}"
    except anthropic.APIError as exc:
        summary = (
            "An automated explanation could not be generated at this time "
            f"(LLM error: {exc}). The deterministic findings above remain valid. "
            f"{CLOSING_LINE}"
        )

    return BailEligibilityReport(
        accused_name=fir_data.get("accused_name"),
        fir_number=fir_data.get("fir_number"),
        days_in_custody=score_result["days_in_custody"],
        confidence_score=int(score_result["score"]),
        bail_grounds=list(score_result["reasons"]),
        legal_contradictions=_detect_contradictions(fir_data, score_result),
        plain_language_summary=summary,
        applicable_sections=list(score_result.get("applicable_sections", [])),
        human_review_required=True,
        disclaimer=DISCLAIMER,
    )


# ---------------------------------------------------------------------------
# Step 4 — FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(title="Project Mukti — Legal Auditor")


class LegalContextItem(BaseModel):
    section_number: Optional[str] = None
    text: Optional[str] = None
    prisoner_rights_summary: Optional[str] = None


class AuditRequest(BaseModel):
    fir_data: dict
    legal_context: list[LegalContextItem] = Field(default_factory=list)
    today_date: Optional[str] = None  # ISO date; defaults to today server-side


@app.post("/audit-case")
def audit_case(payload: AuditRequest) -> dict:
    if not payload.fir_data:
        raise HTTPException(status_code=400, detail="fir_data is required")

    today = _parse_date(payload.today_date) or date.today()
    score_result = calculate_bail_score(payload.fir_data, today)
    legal_context = [item.model_dump() for item in payload.legal_context]
    report = generate_bail_report(payload.fir_data, score_result, legal_context)
    return report.to_dict()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
