"""Vision-model reading (DISABLED BY PROJECT POLICY).

The project owner has ruled out Ollama; no image is sent to any local or remote
vision service. The pipeline is OCR-only. This module is retained so the on-disk
shape of a vision run stays documented, but `vision_enabled()` is always False
and `run_vision` is never invoked by the pipeline.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config

VISION_PROMPT = """You are reading a screenshot for a knowledge database. \
Answer ONLY from what is visibly present in the image; do not guess.

1. VISIBLE_TEXT: transcribe every piece of text you can read, line by line.
2. URLS: list any URLs or domains visible (or "none").
3. PRODUCT_NAME: the main product/repository/website/app shown (or "unclear").
4. SCREENSHOT_TYPE: choose exactly one of SOCIAL_MEDIA, GITHUB, BROWSER, APP_UI, \
PRODUCT_PAGE, DOCUMENT, CODE, MARKETING, VIDEO_FRAME, MOBILE_UI, \
TECHNICAL_DIAGRAM, MIXED_UI.
5. INFERRED: anything you infer but cannot read directly (or "none").

Use these exact headers."""


_AVAIL_CACHE: tuple[float, bool] | None = None
_AVAIL_TTL = 60.0


def vision_enabled() -> bool:
    # Policy: Ollama is not permitted, so the vision layer is permanently off.
    # `DATASEEK_VISION` can no longer re-enable it.
    return False


def vision_available() -> bool:
    """Cached reachability probe. The model may be a remote/LAN service."""
    global _AVAIL_CACHE
    if not vision_enabled():
        return False
    if _AVAIL_CACHE and time.time() - _AVAIL_CACHE[0] < _AVAIL_TTL:
        return _AVAIL_CACHE[1]
    ok = False
    try:
        req = urllib.request.Request(f"{config.OLLAMA_HOST}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.load(resp)
        ok = bool({m["name"] for m in data.get("models", [])})
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        ok = False
    _AVAIL_CACHE = (time.time(), ok)
    return ok


def run_vision(image_path: Path, timeout: int = 900) -> dict:
    """Send the image to the local vision model and return its structured reading."""
    started = time.time()
    result = {"engine": config.ENGINE_VISION, "engine_version": config.VISION_MODEL,
              "preprocessing": "original", "text": "", "confidence": None,
              "boxes": [], "error": None, "latency_s": 0.0,
              "screenshot_type": None, "raw": ""}
    try:
        b64 = base64.b64encode(image_path.read_bytes()).decode()
        payload = {
            "model": config.VISION_MODEL,
            "messages": [{"role": "user", "content": VISION_PROMPT, "images": [b64]}],
            "stream": False,
            "think": False,
            "options": {"num_predict": 1200, "temperature": 0},
        }
        req = urllib.request.Request(
            f"{config.OLLAMA_HOST}/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        content = (data.get("message") or {}).get("content") or ""
        result["raw"] = content
        result["text"] = content
        result["screenshot_type"] = parse_type(content)
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        result["latency_s"] = round(time.time() - started, 2)
    return result


def parse_type(content: str) -> str | None:
    upper = content.upper()
    for kind in ("SOCIAL_MEDIA", "GITHUB", "BROWSER", "APP_UI", "PRODUCT_PAGE",
                 "DOCUMENT", "CODE", "MARKETING", "VIDEO_FRAME", "MOBILE_UI",
                 "TECHNICAL_DIAGRAM", "MIXED_UI"):
        if kind in upper:
            return kind
    return None
