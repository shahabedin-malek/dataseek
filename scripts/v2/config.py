"""Shared configuration and paths for the DataSeek v2 pipeline."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

PROCESSING_VERSION = "v2_multi_ocr"

ROOT = Path(__file__).resolve().parents[2]

# The source directory literally ends with a space on this host. Keep both.
CONFIGURED_SOURCE = Path("/mnt/private-ai-data/Screenshot")
TRAILING_SPACE_SOURCE = Path("/mnt/private-ai-data/Screenshot ")

DATA = ROOT / "data"
DB_PATH = ROOT / "database" / "dataseek.sqlite3"
OCR_DIR = DATA / "ocr"                 # per-image OCR cache: data/ocr/IMG-0001/<engine>.json
PREPROC_DIR = DATA / "preprocess"      # temporary variants (never the originals)
VISION_DIR = DATA / "vision"           # per-image vision-model responses
RESEARCH_DIR = DATA / "research"       # per-resource research cache
MARKDOWN_IMAGES = DATA / "images"
MARKDOWN_RESOURCES = DATA / "markdown" / "resources"
EXPORTS = DATA / "exports"
PROGRESS = ROOT / ".progress"
DOCS = ROOT / "docs"

# Retained only for the disabled vision module; Ollama is not permitted and the
# pipeline never contacts this host.
OLLAMA_HOST_RAW = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_HOST = OLLAMA_HOST_RAW if OLLAMA_HOST_RAW.startswith("http") else f"http://{OLLAMA_HOST_RAW}"
VISION_MODEL = os.environ.get("DATASEEK_VISION_MODEL", "qwen3.5:4b")

# OCR engine identifiers
ENGINE_TESSERACT = "tesseract"
ENGINE_RAPIDOCR = "rapidocr"
ENGINE_VISION = "vision"

# Processing status values used by the v2 pipeline (mirrors the DB CHECK set).
STATUS_PENDING = "PENDING"
STATUS_OCR_PROCESSING = "OCR_PROCESSING"
STATUS_OCR_REVIEW = "OCR_REVIEW"
STATUS_RESEARCHING = "RESEARCHING"
STATUS_CLASSIFYING = "CLASSIFYING"
STATUS_DUPLICATE_REVIEW = "DUPLICATE_REVIEW"
STATUS_COMPLETED = "COMPLETED"
STATUS_NEEDS_RESEARCH = "NEEDS_RESEARCH"
STATUS_NEEDS_VISION = "NEEDS_VISION"
STATUS_OCR_FAILED = "OCR_FAILED"
STATUS_FAILED = "FAILED"
STATUS_BLOCKED = "BLOCKED"

# Quality levels (section 70 of the brief).
QUALITY_LEVELS = {
    0: "unreadable / unresolved",
    1: "OCR extracted",
    2: "OCR + visual verification",
    3: "resource identified",
    4: "resource web verified",
    5: "rich researched knowledge record",
    6: "verified resource + multiple evidence sources",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def source_root() -> Path | None:
    """Return the real read-only source directory, tolerating the trailing space."""
    if CONFIGURED_SOURCE.is_dir():
        return CONFIGURED_SOURCE
    if TRAILING_SPACE_SOURCE.is_dir():
        return TRAILING_SPACE_SOURCE
    return None


def ensure_dirs() -> None:
    for path in (OCR_DIR, PREPROC_DIR, VISION_DIR, RESEARCH_DIR, MARKDOWN_IMAGES,
                 MARKDOWN_RESOURCES, EXPORTS, PROGRESS, DOCS):
        path.mkdir(parents=True, exist_ok=True)
