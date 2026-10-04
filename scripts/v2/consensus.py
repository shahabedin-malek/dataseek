"""Reconcile multiple OCR/vision results into a final, auditable record.

Majority voting alone is never trusted: the reconciled text is stored alongside
every engine's raw output, and confidence/agreement drive an escalation level.
"""
from __future__ import annotations

import re

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
TOKEN_RE = re.compile(r"[a-z0-9]{3,}")

# A second engine only counts as a witness when it is confident and has
# produced a transcription of comparable substance to the primary. A truncated
# second engine (typical for Tesseract on stylised UI text) read *less*; that is
# not evidence of a disagreement and must not manufacture a false "conflict".
WITNESS_MIN_CONFIDENCE = 70.0
WITNESS_MIN_TOKEN_RATIO = 0.6

# Below this coverage of the primary engine's words, the two engines are
# reading different words: a genuine conflict needing review.
CONFLICT_AGREEMENT = 0.35
# A strong primary needs no witness; this is the confidence at which a single
# engine's reading is accepted as good rather than merely partial.
GOOD_CONFIDENCE = 70.0
LOW_CONFIDENCE = 55.0


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall((text or "").casefold())


def _coverage(inner: list[str], outer: list[str]) -> float:
    """Fraction of inner tokens present (exactly or near) in outer. O(n)."""
    if not inner or not outer:
        return 0.0
    exact = set(outer)
    buckets: dict[str, list[str]] = {}
    for tok in outer:
        if len(tok) >= 4:
            buckets.setdefault(tok[:4], []).append(tok)
    hits = 0
    for tok in inner:
        if tok in exact:
            hits += 1
        elif len(tok) >= 4 and any(abs(len(tok) - len(u)) <= 2
                                   for u in buckets.get(tok[:4], ())):
            hits += 1
    return hits / len(inner)


def similarity(a: str, b: str) -> float:
    """Order-independent agreement between two transcriptions.

    Reading order differs between engines, so a sequence-based ratio
    understates real agreement badly. Token coverage (fuzzy, order-free) is
    symmetric here and correlates with whether the engines read the same words.
    """
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    x, y = _coverage(ta, tb), _coverage(tb, ta)
    return round(2 * x * y / (x + y), 3) if (x + y) else 0.0


def reconcile(results: list[dict]) -> dict:
    """results: list of engine result dicts (see engines.run_*)."""
    ok = [r for r in results if not r.get("error") and normalize(r.get("text"))]
    engines_used = list(dict.fromkeys(r["engine"] for r in ok))
    confidences = [r["confidence"] for r in ok if r.get("confidence") is not None]

    ocr_only = [r for r in ok if r["engine"] != "vision"]
    ranked = sorted(ocr_only, key=lambda r: (r.get("confidence") or 0), reverse=True)
    primary = ranked[0] if ranked else None
    vision = next((r for r in ok if r["engine"] == "vision"), None)

    primary_tokens = tokens(primary["text"]) if primary else []

    def substantive(r: dict) -> bool:
        """Confident *and* not merely a truncated fragment of the page."""
        if (r.get("confidence") or 0) < WITNESS_MIN_CONFIDENCE:
            return False
        if not primary_tokens:
            return True
        return len(tokens(r["text"])) >= WITNESS_MIN_TOKEN_RATIO * len(primary_tokens)

    witnesses = [r for r in ranked[1:] if substantive(r)]
    # Agreement = how much of the primary engine's reading the witness confirms.
    # Directional on purpose: a witness that simply read less is not a conflict,
    # and that case is already excluded by the substantiveness gate.
    agreement = round(_coverage(primary_tokens, tokens(witnesses[0]["text"])), 3) \
        if primary and witnesses else None
    independent_confirm = bool(witnesses and agreement is not None
                               and agreement >= CONFLICT_AGREEMENT)

    # Final text: prefer the highest-confidence OCR engine; fall back to the richest.
    def rank(r: dict) -> tuple:
        return (r.get("confidence") or 0, len(normalize(r.get("text"))))
    ordered = sorted(ocr_only or ok, key=rank, reverse=True)
    final_text = ordered[0]["text"] if ordered else ""

    mean_conf = round(sum(confidences) / len(confidences), 2) if confidences else None
    primary_conf = (primary.get("confidence") or 0) if primary else 0.0

    if not ok:
        status, level = "OCR_UNREADABLE", 0
    elif primary is None:
        # Only a vision reading exists.
        status, level = "OCR_PARTIAL", 1
    elif witnesses and agreement is not None and agreement < CONFLICT_AGREEMENT:
        # Two substantive engines read different words: a genuine conflict that
        # needs human/vision review, not a silent pick.
        status, level = "OCR_CONFLICTING", 1
    elif primary_conf >= GOOD_CONFIDENCE:
        status, level = "OCR_GOOD", 2 if (vision or independent_confirm) else 1
    elif primary_conf >= LOW_CONFIDENCE:
        status, level = "OCR_PARTIAL", 1
    else:
        status, level = "OCR_LOW_CONFIDENCE", 1

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
