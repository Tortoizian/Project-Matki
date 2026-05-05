"""
Advanced RAG pipeline for Indian Bail Law.
- Custom semantic chunking of BNSS by Section
- Case-name chunking of bail commentary
- Hybrid retrieval: ChromaDB (vector) + BM25 (keyword) via EnsembleRetriever
- SQLite tabular store for offence-wise bail categorisation
- Single-shot Claude synthesis
"""

import os
import re
import sqlite3
from pathlib import Path
from typing import Optional

import anthropic
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from langchain.schema import Document
from langchain_community.vectorstores import Chroma
from langchain_community.retrievers import BM25Retriever
from langchain.retrievers import EnsembleRetriever
from langchain_huggingface import HuggingFaceEmbeddings
from pypdf import PdfReader


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VECTOR_DIR = "./legal_vectordb"
SQLITE_PATH = "./offences.db"
EMBED_MODEL = "all-MiniLM-L6-v2"
COLLECTION_NAME = "indian_bail_law"

_embeddings: Optional[HuggingFaceEmbeddings] = None
_anthropic_client = anthropic.Anthropic()


def _get_embeddings() -> HuggingFaceEmbeddings:
    global _embeddings
    if _embeddings is None:
        _embeddings = HuggingFaceEmbeddings(model_name=EMBED_MODEL)
    return _embeddings


# ---------------------------------------------------------------------------
# PDF helpers
# ---------------------------------------------------------------------------

def _read_pdf_text(pdf_path: str) -> str:
    reader = PdfReader(pdf_path)
    return "\n".join((page.extract_text() or "") for page in reader.pages)


# ---------------------------------------------------------------------------
# Chunking — BNSS by Section, Bail commentary by Case Name
# ---------------------------------------------------------------------------

# Heuristic: BNSS uses "Section 187." style markers. We split on the start of
# any "Section <num>" header, keeping section numbers up to 3 digits + optional letter.
_SECTION_PATTERN = re.compile(r"(?m)^\s*Section\s+(\d{1,3}[A-Z]?)\b[\.\:]?")


def _classify_law_type(section_num: str) -> str:
    """Crude bucketing: BNSS sections 1–172 procedural intro, 173–250 investigation,
    rest trial/sentencing. Hackathon-grade heuristic."""
    try:
        n = int(re.match(r"\d+", section_num).group())
    except (AttributeError, ValueError):
        return "Procedural"
    if n <= 172:
        return "Preliminary"
    if n <= 250:
        return "Investigation"
    if n <= 359:
        return "Trial"
    return "Sentencing"


def _chunk_bnss(pdf_path: str) -> list[Document]:
    text = _read_pdf_text(pdf_path)
    matches = list(_SECTION_PATTERN.finditer(text))
    docs: list[Document] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section_num = m.group(1)
        body = text[start:end].strip()
        if not body:
            continue
        docs.append(Document(
            page_content=body,
            metadata={
                "source": "BNSS",
                "section_number": section_num,
                "law_type": _classify_law_type(section_num),
            },
        ))
    return docs


# Known landmark bail cases — used as anchor points for chunking the commentary PDF.
_BAIL_CASES = [
    ("Satender Kumar Antil", "Categorisation of Offences for Bail"),
    ("Arnesh Kumar", "Arrest Guidelines u/s 41 CrPC"),
    ("Siddharth", "Routine Arrest Not Required"),
    ("Sanjay Chandra", "Economic Offences Bail"),
    ("Gurbaksh Singh Sibbia", "Anticipatory Bail"),
    ("P. Chidambaram", "Anticipatory Bail in Economic Offences"),
    ("Hussainara Khatoon", "Speedy Trial and Default Bail"),
    ("Bhagwan Singh", "Default Bail u/s 167(2)"),
    ("Manish Sisodia", "Right to Speedy Trial"),
    ("Mohd. Muslim", "Statutory Bail under NDPS"),
]


def _chunk_bail_commentary(pdf_path: str) -> list[Document]:
    text = _read_pdf_text(pdf_path)

    # Build a regex that matches any of the known case names.
    pattern = re.compile(
        r"(" + "|".join(re.escape(name) for name, _ in _BAIL_CASES) + r")",
        re.IGNORECASE,
    )
    matches = list(pattern.finditer(text))

    if not matches:
        # Fallback: one document, untagged
        return [Document(
            page_content=text,
            metadata={"source": "BailCommentary", "case_name": "general", "bail_topic": "overview"},
        )]

    topic_lookup = {name.lower(): topic for name, topic in _BAIL_CASES}
    docs: list[Document] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        case_name = m.group(1)
        body = text[start:end].strip()
        if not body:
            continue
        docs.append(Document(
            page_content=body,
            metadata={
                "source": "BailCommentary",
                "case_name": case_name,
                "bail_topic": topic_lookup.get(case_name.lower(), "Bail Jurisprudence"),
            },
        ))
    return docs


# ---------------------------------------------------------------------------
# SQLite — offence table
# ---------------------------------------------------------------------------

def _build_offences_db(db_path: str = SQLITE_PATH) -> None:
    """Mock the 'Illustration of Different Offences' table from the Bail PDF."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS offences")
    cur.execute("""
        CREATE TABLE offences (
            bns_section TEXT PRIMARY KEY,
            offence_name TEXT NOT NULL,
            punishment TEXT NOT NULL,
            satender_category TEXT NOT NULL
        )
    """)

    # Indicative dataset — represents the offences table extracted from the PDF.
    rows = [
        ("303", "Theft", "Imprisonment up to 3 years or fine or both", "Category A"),
        ("305", "Theft in dwelling house", "Imprisonment up to 7 years and fine", "Category A"),
        ("309", "Robbery", "Rigorous imprisonment up to 10 years and fine", "Category B"),
        ("310", "Dacoity", "Imprisonment for life or up to 10 years and fine", "Category B"),
        ("103", "Murder", "Death or imprisonment for life and fine", "Category D"),
        ("105", "Culpable homicide not amounting to murder", "Imprisonment up to 10 years or life and fine", "Category D"),
        ("115", "Voluntarily causing hurt", "Imprisonment up to 1 year or fine up to ten thousand rupees or both", "Category A"),
        ("117", "Voluntarily causing grievous hurt", "Imprisonment up to 7 years and fine", "Category B"),
        ("64", "Rape", "Rigorous imprisonment 10 years to life and fine", "Category D"),
        ("318", "Cheating", "Imprisonment up to 7 years and fine", "Category A"),
        ("319", "Cheating by personation", "Imprisonment up to 5 years and fine", "Category A"),
        ("351", "Criminal intimidation", "Imprisonment up to 2 years or fine or both", "Category A"),
        ("420", "Cheating (legacy IPC)", "Imprisonment up to 7 years and fine", "Category A"),
    ]
    cur.executemany(
        "INSERT INTO offences (bns_section, offence_name, punishment, satender_category) VALUES (?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def _query_offence(bns_section: str, db_path: str = SQLITE_PATH) -> dict:
    if not os.path.exists(db_path):
        return {}
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT bns_section, offence_name, punishment, satender_category FROM offences WHERE bns_section = ?",
        (bns_section.strip(),),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return {}
    return {
        "bns_section": row[0],
        "offence_name": row[1],
        "punishment": row[2],
        "satender_category": row[3],
    }


# ---------------------------------------------------------------------------
# Step 1 — Ingestion
# ---------------------------------------------------------------------------

def ingest_legal_data(bnss_pdf_path: str, bail_commentary_pdf_path: str) -> dict:
    """One-shot ingestion. Persists ChromaDB vectorstore and SQLite offences DB."""
    if not os.path.exists(bnss_pdf_path):
        raise FileNotFoundError(bnss_pdf_path)
    if not os.path.exists(bail_commentary_pdf_path):
        raise FileNotFoundError(bail_commentary_pdf_path)

    bnss_docs = _chunk_bnss(bnss_pdf_path)
    bail_docs = _chunk_bail_commentary(bail_commentary_pdf_path)
    all_docs = bnss_docs + bail_docs

    if not all_docs:
        raise ValueError("No documents produced from the supplied PDFs.")

    Path(VECTOR_DIR).mkdir(parents=True, exist_ok=True)
    vectordb = Chroma.from_documents(
        documents=all_docs,
        embedding=_get_embeddings(),
        collection_name=COLLECTION_NAME,
        persist_directory=VECTOR_DIR,
    )
    # Newer Chroma versions auto-persist; call defensively.
    if hasattr(vectordb, "persist"):
        try:
            vectordb.persist()
        except Exception:
            pass

    _build_offences_db(SQLITE_PATH)

    return {
        "bnss_chunks": len(bnss_docs),
        "bail_chunks": len(bail_docs),
        "vector_dir": VECTOR_DIR,
        "sqlite_path": SQLITE_PATH,
    }


# ---------------------------------------------------------------------------
# Step 2 — Hybrid Retrieval
# ---------------------------------------------------------------------------

_ensemble_cache: Optional[EnsembleRetriever] = None
_all_docs_cache: Optional[list[Document]] = None


def _load_all_docs_from_chroma(vectordb: Chroma) -> list[Document]:
    """Pull every chunk out of Chroma so BM25 can index the same corpus."""
    raw = vectordb.get()
    docs = []
    for content, meta in zip(raw.get("documents", []), raw.get("metadatas", [])):
        docs.append(Document(page_content=content, metadata=meta or {}))
    return docs


def _build_ensemble(k: int = 5) -> EnsembleRetriever:
    global _ensemble_cache, _all_docs_cache
    if _ensemble_cache is not None:
        return _ensemble_cache

    vectordb = Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=_get_embeddings(),
        persist_directory=VECTOR_DIR,
    )
    vector_retriever = vectordb.as_retriever(search_kwargs={"k": k})

    _all_docs_cache = _load_all_docs_from_chroma(vectordb)
    if not _all_docs_cache:
        raise RuntimeError("Vector store is empty. Run ingest_legal_data() first.")

    bm25 = BM25Retriever.from_documents(_all_docs_cache)
    bm25.k = k

    _ensemble_cache = EnsembleRetriever(
        retrievers=[vector_retriever, bm25],
        weights=[0.5, 0.5],
    )
    return _ensemble_cache


def get_relevant_legal_context(query: str, target_bns_section: str = None) -> dict:
    ensemble = _build_ensemble()
    docs = ensemble.invoke(query)

    retrieved_chunks = [
        {"content": d.page_content, "metadata": d.metadata}
        for d in docs
    ]

    structured_offence_data: dict = {}
    if target_bns_section:
        structured_offence_data = _query_offence(target_bns_section)

    return {
        "retrieved_chunks": retrieved_chunks,
        "structured_offence_data": structured_offence_data,
    }


# ---------------------------------------------------------------------------
# Step 3 — LLM Synthesis
# ---------------------------------------------------------------------------

SYNTHESIS_SYSTEM = (
    "You are an Indian legal assistant. Based strictly on the provided BNSS statutes, "
    "Supreme Court guidelines, and Offence Table, answer the user's query about bail "
    "eligibility in plain English. Format the answer with the General Rule, "
    "Exceptions, and Supreme Court context."
)


def _format_context_for_prompt(context: dict) -> str:
    parts: list[str] = []

    offence = context.get("structured_offence_data") or {}
    if offence:
        parts.append("=== OFFENCE TABLE (SQL) ===")
        parts.append(
            f"BNS Section: {offence.get('bns_section')}\n"
            f"Offence: {offence.get('offence_name')}\n"
            f"Punishment: {offence.get('punishment')}\n"
            f"Satender Kumar Antil Category: {offence.get('satender_category')}"
        )

    chunks = context.get("retrieved_chunks") or []
    if chunks:
        parts.append("\n=== RETRIEVED LEGAL CHUNKS ===")
        for i, ch in enumerate(chunks, 1):
            meta = ch.get("metadata", {})
            tag = meta.get("section_number") or meta.get("case_name") or meta.get("source", "")
            parts.append(f"\n[Chunk {i} | {meta.get('source','?')} | {tag}]\n{ch.get('content','')}")

    return "\n".join(parts) if parts else "No legal context retrieved."


def generate_bail_advice(query: str, context: dict) -> str:
    context_block = _format_context_for_prompt(context)

    user_prompt = (
        f"User Query:\n{query}\n\n"
        f"Legal Context:\n{context_block}\n\n"
        "Now provide structured bail advice with sections: "
        "**General Rule**, **Exceptions**, **Supreme Court Context**."
    )

    response = _anthropic_client.messages.create(
        model="claude-3-5-sonnet-latest",
        max_tokens=1500,
        system=SYNTHESIS_SYSTEM,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text


# ---------------------------------------------------------------------------
# Step 4 — FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(title="Indian Bail Law RAG")


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
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except anthropic.APIError as exc:
        raise HTTPException(status_code=502, detail=f"LLM error: {exc}")


@app.post("/ingest")
def ingest_endpoint(bnss_pdf_path: str, bail_commentary_pdf_path: str) -> dict:
    return ingest_legal_data(bnss_pdf_path, bail_commentary_pdf_path)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
