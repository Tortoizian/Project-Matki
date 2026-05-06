import os
import base64
import json
import tempfile
import re
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse

from llm_client import generate_with_image

app = FastAPI(title="FIR Vision Extractor")

SYSTEM_PROMPT = (
    "You are a forensic document reader specialising in Indian legal documents. "
    "Extract information with maximum accuracy. If a field is illegible or absent, "
    "return null. Never hallucinate or guess."
)

EXTRACTION_PROMPT = """Analyse this FIR (First Information Report) image and extract the following fields.
Return ONLY a valid JSON object with exactly these keys — no markdown, no explanation:

{
  "accused_name": "<string or null>",
  "sections_charged": ["<IPC/CrPC section>", ...] or null,
  "date_of_arrest": "<ISO 8601 date YYYY-MM-DD or null>",
  "police_station": "<string or null>",
  "fir_number": "<string or null>",
  "case_narrative": "<max 200 words summary or null>"
}"""

_KEY_FIELDS = ("accused_name", "sections_charged", "date_of_arrest", "police_station", "fir_number")


def _preprocess_image(image_path: str) -> str:
    """Enhance the FIR image for better OCR/vision results. Returns path to temp enhanced file."""
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"Could not load image from {image_path}")

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    denoised = cv2.fastNlMeansDenoising(enhanced, h=10, templateWindowSize=7, searchWindowSize=21)

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    cv2.imwrite(tmp.name, denoised)
    tmp.close()
    return tmp.name


def _image_to_base64(image_path: str) -> tuple[str, str]:
    """Return (base64_data, media_type) for the given image file."""
    suffix = Path(image_path).suffix.lower()
    media_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
    media_type = media_map.get(suffix, "image/png")
    with open(image_path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("utf-8")
    return data, media_type


def _calculate_confidence(fields: dict) -> int:
    """20 points per non-null key field (max 100)."""
    score = sum(20 for key in _KEY_FIELDS if fields.get(key) is not None)
    return min(score, 100)


def _parse_response(text: str) -> dict:
    """Extract JSON from Claude's response, tolerating minor formatting noise."""
    text = text.strip()
    # Strip markdown code fences if present
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        text = match.group(1)
    else:
        # Find the first { … } block (if it's not already just a dict)
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            text = text[start:end]
            
    # Sometimes json mode forces it to be a raw dict with no markup
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"Failed to parse text as JSON. Raw text was:\n{text}")
        raise exc


def extract_fir_details(image_path: str) -> dict:
    """
    Load, preprocess, and send an FIR image to Claude Vision.
    Returns a dict with extracted fields and a confidence_score.
    """
    enhanced_path: str | None = None
    try:
        enhanced_path = _preprocess_image(image_path)
        with open(enhanced_path, "rb") as f:
            image_bytes = f.read()
        media_type = "image/png"

        raw_text = generate_with_image(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=EXTRACTION_PROMPT,
            image_bytes=image_bytes,
            mime_type=media_type,
            max_tokens=1024,
            json_mode=True,
        )
        fields = _parse_response(raw_text)

        # Normalise sections_charged to list or null
        if "sections_charged" in fields and isinstance(fields["sections_charged"], str):
            fields["sections_charged"] = [fields["sections_charged"]] if fields["sections_charged"] else None

        confidence_score = _calculate_confidence(fields)

        return {
            "accused_name": fields.get("accused_name"),
            "sections_charged": fields.get("sections_charged"),
            "date_of_arrest": fields.get("date_of_arrest"),
            "police_station": fields.get("police_station"),
            "fir_number": fields.get("fir_number"),
            "case_narrative": fields.get("case_narrative"),
            "confidence_score": confidence_score,
        }

    except Exception as exc:
        raise RuntimeError(f"Vision LLM error: {exc}") from exc
    finally:
        if enhanced_path and os.path.exists(enhanced_path):
            os.unlink(enhanced_path)


# ---------------------------------------------------------------------------
# FastAPI endpoint
# ---------------------------------------------------------------------------

@app.post("/extract-fir")
async def extract_fir(image: UploadFile = File(...)) -> JSONResponse:
    """Accept a multipart image upload, run FIR extraction, return JSON."""
    allowed_types = {"image/jpeg", "image/png", "image/webp", "image/jpg"}
    if image.content_type not in allowed_types:
        raise HTTPException(status_code=415, detail=f"Unsupported media type: {image.content_type}")

    suffix = Path(image.filename or "upload.png").suffix or ".png"
    tmp_input: tempfile.NamedTemporaryFile | None = None
    try:
        tmp_input = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        contents = await image.read()
        tmp_input.write(contents)
        tmp_input.close()

        result = extract_fir_details(tmp_input.name)
        return JSONResponse(content=result)

    except (ValueError, RuntimeError) as exc:
        return JSONResponse(
            status_code=422,
            content={"error": "vision_failed", "fields": None, "detail": str(exc)},
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=500,
            content={"error": "vision_failed", "fields": None, "detail": str(exc)},
        )
    finally:
        if tmp_input and os.path.exists(tmp_input.name):
            os.unlink(tmp_input.name)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
