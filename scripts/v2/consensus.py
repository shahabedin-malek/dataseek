"""Reconcile multiple OCR/vision results into a final, auditable record.

Majority voting alone is never trusted: the reconciled text is stored alongside
every engine's raw output, and confidence/agreement drive an escalation level.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

URL_RE = re.compile(
    r"(?i)\b(?:(?:https?://)?(?:www\.)?"
    r"(?:github\.com|gitlab\.com|bitbucket\.org|youtube\.com|youtu\.be|x\.com|"
    r"twitter\.com|reddit\.com|chromewebstore\.google\.com|play\.google\.com|"
    r"apps\.apple\.com|huggingface\.co|npmjs\.com|pypi\.org|"
    r"[a-z0-9][a-z0-9-]*\.(?:com|org|net|io|ai|dev|app|edu|gov|co|me|sh))"
    r"(?:/[^\s<>\"')\]]*)?)"
)
GITHUB_RE = re.compile(r"(?i)github\.com\s*/\s*([A-Za-z0-9_.-]+)\s*/\s*([A-Za-z0-9_.-]+)")
DOMAIN_RE = re.compile(r"(?i)\b([a-z0-9][a-z0-9-]*\.(?:com|org|net|io|ai|dev|app|co|sh|me))\b")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def similarity(a: str, b: str) -> float:
    a, b = normalize(a).lower(), normalize(b).lower()
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def reconcile(results: list[dict]) -> dict:
    """results: list of engine result dicts (see engines.run_*)."""
    ok = [r for r in results if not r.get("error") and normalize(r.get("text"))]
    engines_used = list(dict.fromkeys(r["engine"] for r in ok))
    confidences = [r["confidence"] for r in ok if r.get("confidence") is not None]

    # Agreement between the strongest engine and any *credible* second witness.
    # A weak engine's garbage must not manufacture a false "conflict".
    ocr_only = [r for r in ok if r["engine"] != "vision"]
    ranked = sorted(ocr_only, key=lambda r: (r.get("confidence") or 0), reverse=True)
    primary = ranked[0] if ranked else None
    witnesses = [r for r in ranked[1:] if (r.get("confidence") or 0) >= 55]
    agreement = round(similarity(primary["text"], witnesses[0]["text"]), 3) \
        if primary and witnesses else None
    independent_confirm = bool(primary and witnesses)

    # Final text: prefer the highest-confidence OCR engine; fall back to the richest.
    def rank(r: dict) -> tuple:
        return (r.get("confidence") or 0, len(normalize(r.get("text"))))
    ordered = sorted(ocr_only or ok, key=rank, reverse=True)
    final_text = ordered[0]["text"] if ordered else ""
    vision = next((r for r in ok if r["engine"] == "vision"), None)

    mean_conf = round(sum(confidences) / len(confidences), 2) if confidences else None
    if not ok:
        status, level = "OCR_UNREADABLE", 0
    elif len(ok) == 1 and mean_conf is not None and mean_conf < 55:
        status, level = "OCR_LOW_CONFIDENCE", 1
    elif agreement is not None and agreement < 0.35:
        # Only a *credible* witness (confidence >= 80) may declare a conflict; a
        # weak second engine disagreeing with a strong one is expected, not a
        # contradiction, and is resolved in favour of the stronger engine.
        witness_conf = witnesses[0].get("confidence") or 0
        if witness_conf >= 80:
            status, level = "OCR_CONFLICTING", 1
        elif primary and (primary.get("confidence") or 0) >= 85:
            status, level = "OCR_GOOD", 2 if vision else 1
        else:
            status, level = "OCR_PARTIAL", 1
    elif primary and (primary.get("confidence") or 0) < 70:
        status, level = "OCR_PARTIAL", 1
    elif vision:
        status, level = "OCR_GOOD", 2
    else:
        status, level = "OCR_NEEDS_VISION", 1
    if independent_confirm and status in ("OCR_PARTIAL", "OCR_NEEDS_VISION"):
        status, level = "OCR_GOOD", 2

    return {
        "final_text": final_text,
        "vision_text": vision["text"] if vision else "",
        "status": status,
        "confidence": mean_conf,
        "agreement": agreement,
        "primary_engine": primary["engine"] if primary else None,
        "engines": engines_used,
        "quality_level": level,
    }


def extract_urls(text: str, vision_text: str = "") -> list[dict]:
    """Extract candidate URLs/domains with the evidence that produced them."""
    found: dict[str, dict] = {}
    for source, blob in (("ocr", text), ("vision", vision_text)):
        for raw in URL_RE.findall(blob or ""):
            url = raw.rstrip(".,;)")
            key = url.lower()
            found.setdefault(key, {"url": url, "sources": [], "kind": "url"})
            if source not in found[key]["sources"]:
                found[key]["sources"].append(source)
        for domain in DOMAIN_RE.findall(blob or ""):
            key = domain.lower()
            found.setdefault(key, {"url": domain, "sources": [], "kind": "domain"})
            if source not in found[key]["sources"]:
                found[key]["sources"].append(source)
    return sorted(found.values(), key=lambda d: d["url"])


def github_candidates(text: str, vision_text: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for blob in (text or "", vision_text or ""):
        for owner, repo in GITHUB_RE.findall(blob):
            pair = (owner, repo.rstrip(".,)"))
            if pair not in out:
                out.append(pair)
    return out


def vision_product_name(vision_text: str) -> str | None:
    """Pull the PRODUCT_NAME field out of the vision model's structured reply."""
    if not vision_text:
        return None
    m = re.search(r"PRODUCT_NAME\s*[:\-]\s*(.+)", vision_text, re.I)
    if not m:
        return None
    name = m.group(1).strip().splitlines()[0].strip(" .*")
    if name.lower() in ("unclear", "none", "n/a", "unknown", ""):
        return None
    return name[:120]


def classify_type(text: str, vision_type: str | None) -> str:
    if vision_type:
        return vision_type
    lowered = (text or "").lower()
    if "github.com" in lowered or "pull request" in lowered or "readme" in lowered:
        return "GITHUB"
    if "instagram" in lowered or "facebook" in lowered or "tiktok" in lowered:
        return "SOCIAL_MEDIA"
    if re.search(r"http|www\.|\.com|address bar", lowered):
        return "BROWSER"
    return "MIXED_UI"
