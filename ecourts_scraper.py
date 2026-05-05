"""
eCourts case status fetcher.

Primary path: Playwright async scrape of services.ecourts.gov.in.
Fallback: mock_court_db.json — three seeded entries — when the live site
is unreachable, throws, or exceeds a 10-second budget.

The live eCourts portal uses a CAPTCHA on most search forms. The primary
path is best-effort; in production you would route via NJDG APIs or
court-specific bridges. This module degrades gracefully to the mock store
so the rest of the Mukti pipeline keeps moving in demos.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


MOCK_DB_PATH = Path(__file__).parent / "mock_court_db.json"
ECOURTS_URL = "https://services.ecourts.gov.in/ecourtindia_v6/"
TIMEOUT_SECONDS = 10


# ---------------------------------------------------------------------------
# Mock fallback
# ---------------------------------------------------------------------------

def _load_mock_db() -> dict:
    if not MOCK_DB_PATH.exists():
        return {}
    try:
        with open(MOCK_DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _mock_lookup(cnr: str) -> dict:
    db = _load_mock_db()
    if cnr in db:
        return {"source": "mock", **db[cnr]}
    return {"source": "mock", "status": "not_found", "cnr": cnr}


# ---------------------------------------------------------------------------
# Live Playwright path
# ---------------------------------------------------------------------------

async def _scrape_ecourts(cnr_number: str) -> dict:
    """
    Best-effort scrape of the eCourts CNR search.
    Raises on any failure so the caller can fall back.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("playwright is not installed") from exc

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context()
            page = await context.new_page()
            page.set_default_timeout(TIMEOUT_SECONDS * 1000)

            await page.goto(ECOURTS_URL, wait_until="domcontentloaded")

            # Try the CNR search input — selector heuristics; the portal markup changes.
            cnr_input = await page.query_selector(
                "input[name='cino'], input#cino, input[placeholder*='CNR' i]"
            )
            if not cnr_input:
                raise RuntimeError("CNR input field not located on eCourts page")
            await cnr_input.fill(cnr_number)

            search_btn = await page.query_selector(
                "input[type='submit'][value*='Search' i], button:has-text('Search'), #searchbtn"
            )
            if search_btn:
                await search_btn.click()
            else:
                await cnr_input.press("Enter")

            # Wait for the results table to appear.
            await page.wait_for_selector("table, .case_details_table", timeout=TIMEOUT_SECONDS * 1000)

            data = await page.evaluate(
                """
                () => {
                  const text = (sel) => {
                    const el = document.querySelector(sel);
                    return el ? el.innerText.trim() : null;
                  };
                  const grab = (label) => {
                    const rows = Array.from(document.querySelectorAll('tr,li,div'));
                    for (const r of rows) {
                      const t = (r.innerText || '').trim();
                      if (t.toLowerCase().startsWith(label.toLowerCase())) {
                        return t.split(/[:\\-]/).slice(1).join(':').trim();
                      }
                    }
                    return null;
                  };
                  return {
                    case_number: grab('Case Number') || text('.case_number_value'),
                    next_hearing_date: grab('Next Hearing') || grab('Next Date'),
                    case_stage: grab('Case Stage') || grab('Stage'),
                    last_updated: grab('Last Updated') || grab('Updated on'),
                  };
                }
                """
            )

            return {
                "source": "ecourts",
                "cnr": cnr_number,
                "case_number": data.get("case_number"),
                "next_hearing_date": data.get("next_hearing_date"),
                "case_stage": data.get("case_stage"),
                "last_updated": data.get("last_updated"),
            }
        finally:
            await browser.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_case_status(cnr_number: str) -> dict:
    """Try live eCourts (10s budget) then fall back to mock DB."""
    if not cnr_number:
        return {"source": "mock", "status": "not_found", "cnr": cnr_number}

    try:
        return await asyncio.wait_for(_scrape_ecourts(cnr_number), timeout=TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, Exception):
        return _mock_lookup(cnr_number)


# ---------------------------------------------------------------------------
# Optional FastAPI surface (useful for testing in isolation)
# ---------------------------------------------------------------------------

app = FastAPI(title="eCourts Status Fetcher")


class CNRRequest(BaseModel):
    cnr_number: str


@app.post("/case-status")
async def case_status_endpoint(payload: CNRRequest) -> dict:
    if not payload.cnr_number.strip():
        raise HTTPException(status_code=400, detail="cnr_number is required")
    return await get_case_status(payload.cnr_number.strip())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
