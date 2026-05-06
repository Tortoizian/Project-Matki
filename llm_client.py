"""
Tiny Gemini wrapper so every module shares one LLM call surface.

Reads GOOGLE_API_KEY from the environment. Default model is gemini-1.5-pro,
overridable via MUKTI_GEMINI_MODEL.
"""

from __future__ import annotations

import os
from typing import Optional

import google.generativeai as genai


_DEFAULT_MODEL = os.environ.get("MUKTI_GEMINI_MODEL", "gemini-1.5-pro")
_API_KEY = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
_configured = False


def _ensure_configured() -> None:
    global _configured
    if _configured:
        return
    if not _API_KEY:
        raise RuntimeError(
            "GOOGLE_API_KEY (or GEMINI_API_KEY) is not set. "
            "Add it to your .env file."
        )
    genai.configure(api_key=_API_KEY)
    _configured = True


def model_name() -> str:
    return _DEFAULT_MODEL


def generate_text(
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int = 1500,
    model: Optional[str] = None,
) -> str:
    _ensure_configured()
    m = genai.GenerativeModel(
        model or _DEFAULT_MODEL,
        system_instruction=system_prompt,
    )
    resp = m.generate_content(
        user_prompt,
        generation_config={"max_output_tokens": max_tokens, "temperature": 0.4},
    )
    return (resp.text or "").strip()


def generate_with_image(
    system_prompt: str,
    user_prompt: str,
    image_bytes: bytes,
    mime_type: str = "image/png",
    *,
    max_tokens: int = 1500,
    model: Optional[str] = None,
) -> str:
    _ensure_configured()
    m = genai.GenerativeModel(
        model or _DEFAULT_MODEL,
        system_instruction=system_prompt,
    )
    resp = m.generate_content(
        [{"mime_type": mime_type, "data": image_bytes}, user_prompt],
        generation_config={"max_output_tokens": max_tokens, "temperature": 0.2},
    )
    return (resp.text or "").strip()
