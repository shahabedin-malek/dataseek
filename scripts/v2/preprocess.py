"""Image quality analysis and non-destructive preprocessing variants.

Originals are never modified. Variants live under data/preprocess/<task_id>/
and are deleted after each image is processed.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np

from . import config


def analyze_quality(image_path: Path) -> dict:
    """Return objective quality metrics used to choose preprocessing."""
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return {"readable": False}
    h, w = img.shape[:2]
    mean = float(np.mean(img))
    std = float(np.std(img))
    lap_var = float(cv2.Laplacian(img, cv2.CV_64F).var())
    # Text density proxy: fraction of "edge-like" pixels.
    edges = cv2.Canny(img, 80, 200)
    edge_density = float(np.count_nonzero(edges)) / float(h * w)
    # Estimate background polarity: border pixels are usually background.
    border = np.concatenate([img[0, :], img[-1, :], img[:, 0], img[:, -1]])
    dark_ui = bool(np.mean(border) < 110)
    return {
        "readable": True,
        "width": w,
        "height": h,
        "mean_brightness": round(mean, 1),
        "contrast_std": round(std, 1),
        "sharpness_lapvar": round(lap_var, 1),
        "edge_density": round(edge_density, 4),
        "dark_ui": dark_ui,
        "low_contrast": std < 40,
        "blurry": lap_var < 90,
        "small_text": h * w > 1_500_000 and edge_density > 0.06,
    }


@contextmanager
def variants(image_path: Path, task_id: str, quality: dict) -> Iterator[dict[str, Path]]:
    """Yield named preprocessing variants. Files are removed on exit."""
    out_dir = config.PREPROC_DIR / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    made: dict[str, Path] = {}
    bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if bgr is None:
        yield made
        return
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    def save(name: str, array: np.ndarray) -> None:
        path = out_dir / f"{name}.png"
        cv2.imwrite(str(path), array)
        made[name] = path

    save("original", bgr)
    save("grayscale", gray)

    # CLAHE contrast enhancement (helps dark UI screenshots).
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    save("clahe", enhanced)

    if quality.get("dark_ui"):
        save("inverted", cv2.bitwise_not(gray))

    # 2x upscale (helps small text) — only when the image is not huge.
    if quality.get("small_text") or quality.get("blurry"):
        up = cv2.resize(enhanced, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        save("upscaled2x", up)

    # Adaptive threshold for high-contrast UI text.
    th = cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY, 31, 10)
    save("adaptive_thresh", th)

    # Denoise + sharpen for noisy photographs.
    den = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
    save("denoised", den)

    try:
        yield made
    finally:
        for path in made.values():
            try:
                path.unlink()
            except OSError:
                pass
        try:
            out_dir.rmdir()
        except OSError:
            pass
