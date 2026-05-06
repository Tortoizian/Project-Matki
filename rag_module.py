"""
Simplified Legal Data fetcher using purely the BNS JSON mapping.
Replaces the heavy ChromaDB + SQLite + PyPDF pipeline.
"""

import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from llm_client import generate_text


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

JSON_PATH = Path(__file__).parent / "bns_bail_mapping.json"
_bns_mapping: Optional[dict] = None


def _get_bns_mapping() -> dict:
    global _bns_mapping
    if _bns_mapping is None:
        if JSON_PATH.exists():
            with open(JSON_PATH, "r", encoding="utf-8") as f:
                _bns_mapping = json.load(f)
        else:
            _bns_mapping = {}
    return _bns_mapping


# ---------------------------------------------------------------------------
# Query offence mapping
# ---------------------------------------------------------------------------

def _query_offence(bns_section: str) -> dict:
    mapping = _get_bns_mapping()
    section = str(bns_section).strip().upper()
    if section.startswith("SECTION "):
        section = section.replace("SECTION ", "")

    data = mapping.get(section)
    if not data:
        # Fall back to first sub-section match (e.g. "303" -> "303_1")
        for key, val in mapping.items():
            if key.startswith(f"{section}_") and isinstance(val, dict):
                data = val
                break

    if not data:
        return {}

    return {
        "bns_section": bns_section,
        "offence_name": data.get("offence", ""),
        "punishment": data.get("punishment", ""),
        "satender_category": f"Category {data.get('antil_category', '')}",
        "is_bailable": str(data.get("is_bailable", "Unknown")),
        "cognizable": str(data.get("cognizable", "Unknown")),
    }


# ---------------------------------------------------------------------------
# Core retrieval call
# ---------------------------------------------------------------------------

def get_relevant_legal_context(query: str, target_bns_section: str = None) -> dict:
    """Replaces hybrid retrieval with a direct lookup against the BNS JSON."""
    structured_offence_data = {}

    if target_bns_section:
        structured_offence_data = _query_offence(target_bns_section)

    return {
        "retrieved_chunks": [],
        "structured_offence_data": structured_offence_data,
    }


# ---------------------------------------------------------------------------
# LLM synthesis (for isolated /legal-context testing)
# ---------------------------------------------------------------------------

SYNTHESIS_SYSTEM = (
    "You are an Indian legal assistant. Based strictly on the provided Offence Table, "
    "answer the user's query about bail eligibility in plain English. Format the answer "
    "with the General Rule and Exceptions."
)


def _format_context_for_prompt(context: dict) -> str:
    parts: list[str] = []
    offence = context.get("structured_offence_data") or {}
    if offence:
        parts.append("=== OFFENCE TABLE (JSON) ===")
        parts.append(
            f"BNS Section: {offence.get('bns_section')}\n"
            f"Offence: {offence.get('offence_name')}\n"
            f"Punishment: {offence.get('punishment')}\n"
            f"Bailable: {offence.get('is_bailable')}\n"
            f"Cognizable: {offence.get('cognizable')}\n"
            f"Satender Kumar Antil Category: {offence.get('satender_category')}"
        )
    return "\n".join(parts) if parts else "No legal context provided."


def generate_bail_advice(query: str, context: dict) -> str:
    context_block = _format_context_for_prompt(context)
    user_prompt = (
        f"User Query:\n{query}\n\n"
        f"Legal Context:\n{context_block}\n\n"
        "Now provide structured bail advice."
    )
    return generate_text(
        system_prompt=SYNTHESIS_SYSTEM,
        user_prompt=user_prompt,
        max_tokens=1000,
    )


# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(title="Simplified JSON Bail RAG")


class LegalQuery(BaseModel):
    query: str
    bns_section: Optional[str] = None


@app.post("/legal-context")
def legal_context_endpoint(payload: LegalQuery) -> dict:
    if not payload.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty.")
    try:
        context = get_relevant_legal_context(payload.query, payload.bns_section)
        advice = generate_bail_advice(payload.query, context)
        return {
            "query": payload.query,
            "bns_section": payload.bns_section,
            "structured_offence_data": context["structured_offence_data"],
            "retrieved_chunk_count": len(context["retrieved_chunks"]),
            "advice": advice,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
