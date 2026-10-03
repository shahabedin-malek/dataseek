"""OCR engine adapters. Each returns a structured, auditable result.

Result shape:
{
  "engine": str, "engine_version": str, "preprocessing": str,
  "text": str, "confidence": float | None,
  "boxes": [{"text", "confidence", "bbox": [x0,y0,x1,y1]}],
  "error": str | None, "latency_s": float
}
"""
from __future__ import annotations

import csv
import io
import json
import shutil
import subprocess
import time
from pathlib import Path

from . import config


def _tesseract_version() -> str:
    exe = shutil.which("tesseract")
    if not exe:
        return "unavailable"
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=15)
        first = out.stdout.splitlines()[0] if out.stdout else "unknown"
        return first.replace("tesseract ", "").strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


TESSERACT_VERSION = _tesseract_version()


def run_tesseract(image_path: Path, preprocessing: str = "original", psm: int = 3,
                  timeout: int = 180) -> dict:
    """Tesseract with TSV output so we retain per-word confidence and boxes."""
    started = time.time()
    exe = shutil.which("tesseract")
    result = {"engine": config.ENGINE_TESSERACT, "engine_version": TESSERACT_VERSION,
              "preprocessing": f"psm{psm}", "text": "", "confidence": None,
              "boxes": [], "error": None, "latency_s": 0.0}
    if not exe:
        result["error"] = "tesseract not installed"
        return result
    try:
        proc = subprocess.run(
            [exe, str(image_path), "stdout", "-l", "eng", "--psm", str(psm), "tsv"],
            capture_output=True, text=True, timeout=timeout)
        if proc.returncode:
            result["error"] = (proc.stderr.strip() or f"exit {proc.returncode}")[:500]
            return result
        rows = list(csv.DictReader(io.StringIO(proc.stdout), delimiter="\t"))
        words, confs, boxes = [], [], []
        for row in rows:
            text = (row.get("text") or "").strip()
            if not text:
                continue
            try:
                conf = float(row.get("conf", "-1"))
            except ValueError:
                conf = -1.0
            if conf < 0:
                continue
            x, y = int(row.get("left", 0)), int(row.get("top", 0))
            w, h = int(row.get("width", 0)), int(row.get("height", 0))
            words.append(text)
            confs.append(conf)
            boxes.append({"text": text, "confidence": conf,
                          "bbox": [x, y, x + w, y + h]})
        result["text"] = " ".join(words)
        result["confidence"] = round(sum(confs) / len(confs), 2) if confs else 0.0
        result["boxes"] = boxes
    except (OSError, subprocess.SubprocessError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        result["latency_s"] = round(time.time() - started, 2)
    return result


_RAPID_ENGINE = None


def _rapid_engine():
    global _RAPID_ENGINE
    if _RAPID_ENGINE is None:
        from rapidocr import RapidOCR
        _RAPID_ENGINE = RapidOCR()
    return _RAPID_ENGINE


def rapidocr_available() -> bool:
    try:
        import rapidocr  # noqa: F401
        import onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


def run_rapidocr(image_path: Path, preprocessing: str = "original", timeout: int = 600) -> dict:
    """RapidOCR (PP-OCR ONNX models). CPU-friendly, no AVX2 required."""
    started = time.time()
    result = {"engine": config.ENGINE_RAPIDOCR, "engine_version": _rapid_version(),
              "preprocessing": preprocessing, "text": "", "confidence": None,
              "boxes": [], "error": None, "latency_s": 0.0}
    try:
        engine = _rapid_engine()
        out = engine(str(image_path))
        txts = list(out.txts or [])
        scores = list(out.scores or [])
        boxlist = out.boxes if out.boxes is not None else []
        boxes = []
        for i, text in enumerate(txts):
            conf = float(scores[i]) if i < len(scores) else None
            bbox = boxlist[i].tolist() if i < len(boxlist) else None
            xs = [p[0] for p in bbox] if bbox else []
            ys = [p[1] for p in bbox] if bbox else []
            rect = [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))] if xs else None
            boxes.append({"text": text, "confidence": conf, "bbox": rect})
        result["text"] = "\n".join(txts)
        if scores:
            result["confidence"] = round(sum(float(s) for s in scores) / len(scores) * 100, 2)
        result["boxes"] = boxes
    except Exception as exc:  # noqa: BLE001 - engine may fail many ways
        result["error"] = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        result["latency_s"] = round(time.time() - started, 2)
    return result


def _rapid_version() -> str:
    try:
        from importlib.metadata import version
        return version("rapidocr")
    except Exception:  # noqa: BLE001
        return "unknown"
