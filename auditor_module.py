"""
Legal Auditor agent for Project Mukti.

Deterministic scoring + LLM-generated plain-language explanation.
The confidence_score is NEVER produced by the LLM — only Python rules set it.

Bail classification and max-sentence data come from bns_bail_mapping.json
(First Schedule of BNSS, 2023). The score engine is a pure JSON lookup —
no hardcoded section lists.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from llm_client import generate_text


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

_DEFAULT_MAX_SENTENCE_DAYS = 3 * 365  # conservative fallback


# ---------------------------------------------------------------------------
# JSON data loader
# ---------------------------------------------------------------------------

_BNS_MAPPING_CACHE: Optional[dict] = None


def _get_bns_mapping() -> dict:
    global _BNS_MAPPING_CACHE
    if _BNS_MAPPING_CACHE is None:
        json_path = Path(__file__).parent / "bns_bail_mapping.json"
        if json_path.exists():
            with open(json_path, "r", encoding="utf-8") as f:
                _BNS_MAPPING_CACHE = json.load(f)
        else:
            _BNS_MAPPING_CACHE = {}
    return _BNS_MAPPING_CACHE


def _section_entry(section: str) -> Optional[dict]:
    """Look up a section in the BNS mapping. Tolerates 'Section 303' / '303' / '303_1' forms."""
    if not section:
        return None
    mapping = _get_bns_mapping()
    s = str(section).strip()
    s = s.replace("Section ", "").replace("section ", "")
    entry = mapping.get(s)
    if isinstance(entry, dict):
        return entry
    # Try matching the parent section if user passed just "303" but mapping has "303_1"
    for key, val in mapping.items():
        if key.startswith(f"{s}_") and isinstance(val, dict):
            return val
    return None


def _is_bailable_offence(section: str) -> bool:
    entry = _section_entry(section)
    if not entry:
        return False
    val = entry.get("is_bailable")
    if val is True:
        return True
    if isinstance(val, str) and val.strip().lower() in ("true", "bailable", "yes"):
        return True
    return False


def _is_non_bailable_offence(section: str) -> bool:
    return not _is_bailable_offence(section)


def _max_sentence_days_for_section(section: str) -> int:
    entry = _section_entry(section)
    if entry and entry.get("max_sentence_days"):
        return int(entry["max_sentence_days"])
    return _DEFAULT_MAX_SENTENCE_DAYS


def _is_death_or_life_or_10yr(section: str) -> bool:
    """Return True if the section carries death, life imprisonment, or >=10 years."""
    days = _max_sentence_days_for_section(section)
    # 10 years = 3650 days; life/death encoded as very large values (e.g. 36500+)
    return days >= 3650


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
    known: list[int] = []
    for s in (sections or []):
        entry = _section_entry(s)
        if entry and entry.get("max_sentence_days"):
            known.append(int(entry["max_sentence_days"]))
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
            "is_non_bailable_case": False,
            "statutory_deadline": 60,
            "primary_ground": "",
        }

    days_in_custody = max((today_date - arrest_date).days, 0)
    estimated_max_sentence_days = _estimate_max_sentence_days(sections_charged)

    # --- Heaviest Charge Rule ---
    # If ANY section is non-bailable, the whole case is non-bailable.
    is_non_bailable_case = any(_is_non_bailable_offence(s) for s in sections_charged)

    # Determine the correct statutory deadline (60 vs 90 days).
    # 90 days applies if the most serious charge carries death, life, or >=10 years.
    has_serious_charge = any(_is_death_or_life_or_10yr(s) for s in sections_charged)
    statutory_deadline = 90 if has_serious_charge else 60

    score = 0
    reasons: list[str] = []
    applicable: list[str] = []

    # Check 1 — Section 187 BNSS (default bail on charge-sheet delay)
    if charge_sheet_not_filed and days_in_custody > statutory_deadline:
        score += 40
        reasons.append(
            f"Section 187 BNSS: Charge sheet not filed within the {statutory_deadline}-day statutory "
            f"limit — default bail eligibility triggered regardless of offence gravity"
        )
        applicable.append("BNSS Section 187")

    # Check 2 — Section 479 BNSS (half-sentence served as undertrial)
    # Does not apply to offences punishable by death or life imprisonment.
    life_or_death = any(_max_sentence_days_for_section(s) >= 36500 for s in sections_charged)
    if not life_or_death and days_in_custody > estimated_max_sentence_days / 2:
        score += 40
        reasons.append(
            "Section 479 BNSS: Undertrial detention exceeds half of maximum sentence — "
            "mandatory release on bond"
        )
        applicable.append("BNSS Section 479")

    # Check 2b — Section 479 BNSS first-time offender (1/3 threshold)
    is_first_time = bool(fir_data.get("is_first_time_offender", False))
    if (
        not life_or_death
        and is_first_time
        and not any("Section 479" in r for r in reasons)
        and days_in_custody > estimated_max_sentence_days / 3
    ):
        score += 30
        reasons.append(
            "Section 479 BNSS (First-Time Offender): Undertrial detention exceeds one-third "
            "of maximum sentence — eligible for early release as first-time offender"
        )
        applicable.append("BNSS Section 479")

    # Check 3 — Section 436 BNSS (bailable offence per First Schedule)
    # ONLY add this ground if no non-bailable charge exists.
    if not is_non_bailable_case and any(_is_bailable_offence(s) for s in sections_charged):
        score += 20
        reasons.append(
            "Section 436 BNSS: All charged offences are classified as bailable — "
            "bail is a right, not a matter of court discretion"
        )
        applicable.append("BNSS Section 436")

    score = min(score, 100)
    primary_ground = reasons[0] if reasons else ""

    return {
        "score": score,
        "reasons": reasons,
        "days_in_custody": days_in_custody,
        "applicable_sections": applicable,
        "estimated_max_sentence_days": estimated_max_sentence_days,
        "is_non_bailable_case": is_non_bailable_case,
        "statutory_deadline": statutory_deadline,
        "primary_ground": primary_ground,
    }


# ---------------------------------------------------------------------------
# Step 2 — LLM explanation
# ---------------------------------------------------------------------------

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
    is_non_bailable = score_result.get("is_non_bailable_case", False)
    statutory_deadline = score_result.get("statutory_deadline", 60)
    bail_type_note = (
        "IMPORTANT: This case includes at least one NON-BAILABLE charge. "
        "Bail is NOT a right here — it is at the sole discretion of the court. "
        "Do NOT suggest that bail is guaranteed or a right. Be honest but compassionate."
        if is_non_bailable
        else "All charges are bailable. Bail is a right under Section 436 BNSS."
    )
    return (
        f"Accused: {fir_data.get('accused_name')}\n"
        f"FIR Number: {fir_data.get('fir_number')}\n"
        f"Sections charged: {fir_data.get('sections_charged')}\n"
        f"Days in custody: {score_result['days_in_custody']}\n"
        f"Statutory deadline for charge sheet: {statutory_deadline} days\n"
        f"Bail type assessment: {bail_type_note}\n\n"
        f"Deterministic bail score: {score_result['score']}/100\n"
        f"Primary ground: {score_result.get('primary_ground', 'None')}\n"
        f"All triggered legal grounds:\n- "
        + ("\n- ".join(score_result["reasons"]) if score_result["reasons"] else "None triggered")
        + "\n\nRelevant statutory context:\n"
        + _format_legal_context(legal_context)
        + "\n\nWrite a clear three-paragraph explanation for the prisoner's family:\n"
          "Paragraph 1 — State plainly what was found. If the case is non-bailable, say so "
          "honestly and compassionately — do not give false hope.\n"
          "Paragraph 2 — Name the specific BNSS sections that apply and what each one means "
          "in plain language a non-lawyer can understand.\n"
          "Paragraph 3 — Tell the family exactly what to do next, mentioning the District "
          "Legal Services Authority (DLSA) for free legal aid. End with the mandatory closing line."
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
    is_non_bailable_case: bool = False
    statutory_deadline: int = 60
    primary_ground: str = ""
    human_review_required: bool = True
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict:
        return asdict(self)


def _detect_contradictions(fir_data: dict, score_result: dict) -> list[str]:
    """Surface internal inconsistencies that an advocate should examine."""
    contradictions = []
    sections = fir_data.get("sections_charged") or []
    is_non_bailable = score_result.get("is_non_bailable_case", False)
    statutory_deadline = score_result.get("statutory_deadline", 60)
    days = score_result["days_in_custody"]

    if not is_non_bailable and any(_is_bailable_offence(s) for s in sections) and days > 30:
        contradictions.append(
            "All offences appear bailable yet the accused has been in custody beyond 30 days — "
            "examine whether bail was applied for and rejected, or if a non-bailable charge was added."
        )
    if days > statutory_deadline and not fir_data.get("charge_sheet_filed", False):
        contradictions.append(
            f"Custody exceeds the {statutory_deadline}-day investigation deadline without a filed "
            "charge sheet — default-bail eligibility under Section 187 BNSS should be raised "
            "immediately before the next hearing."
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
        summary = generate_text(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_tokens=1200,
        )
        if CLOSING_LINE not in summary:
            summary = f"{summary}\n\n{CLOSING_LINE}"
    except Exception as exc:
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
        is_non_bailable_case=bool(score_result.get("is_non_bailable_case", False)),
        statutory_deadline=int(score_result.get("statutory_deadline", 60)),
        primary_ground=str(score_result.get("primary_ground", "")),
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
