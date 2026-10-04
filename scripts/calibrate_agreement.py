#!/usr/bin/env python3
"""Calibrate an order-independent OCR agreement metric against cached results.

Prints the distribution of token-coverage agreement for every task with two or
more cached engines, so conflict/vision thresholds can be set on evidence.
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OCR_DIR = ROOT / "data" / "ocr"

TOKEN_RE = re.compile(r"[a-z0-9]{3,}")


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall((text or "").casefold())


def coverage(inner: list[str], outer: list[str]) -> float:
    """Fraction of inner tokens that appear (exactly or near) in outer.

    Near-match = same 4-char prefix and similar length. O(n) via prefix index.
    """
    if not inner or not outer:
        return 0.0
    exact = set(outer)
    buckets: dict[str, list[str]] = {}
    for t in outer:
        if len(t) >= 4:
            buckets.setdefault(t[:4], []).append(t)
    hits = 0
    for t in inner:
        if t in exact:
            hits += 1
        elif len(t) >= 4:
            if any(abs(len(t) - len(u)) <= 2 for u in buckets.get(t[:4], ())):
                hits += 1
    return hits / len(inner)


def agreement(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    x, y = coverage(ta, tb), coverage(tb, ta)
    return round(2 * x * y / (x + y), 3) if (x + y) else 0.0


def main() -> int:
    rows = []
    for d in sorted(OCR_DIR.iterdir()):
        if not d.is_dir():
            continue
        res = []
        for p in sorted(d.glob("*.json")):
            if p.name == "consensus.json":
                continue
            try:
                j = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if isinstance(j, dict) and "engine" in j and (j.get("text") or ""):
                res.append(j)
        if len(res) < 2:
            continue
        res.sort(key=lambda r: (r.get("confidence") or 0), reverse=True)
        primary, witness = res[0], res[1]
        old = SequenceMatcher(None, re.sub(r"\s+", " ", primary["text"]).lower()[:4000],
                              re.sub(r"\s+", " ", witness["text"]).lower()[:4000]).ratio()
        rows.append({
            "task": d.name,
            "new": agreement(primary["text"], witness["text"]),
            "old": round(old, 3),
            "pconf": primary.get("confidence"),
            "wconf": witness.get("confidence"),
            "pchars": len(primary["text"]),
            "wchars": len(witness["text"]),
        })
    if not rows:
        print("no multi-engine caches found")
        return 1
    new = [r["new"] for r in rows]
    old = [r["old"] for r in rows]
    print(f"tasks with >=2 engines: {len(rows)}")
    for label, vals in (("new token-coverage", new), ("old SequenceMatcher", old)):
        q = statistics.quantiles(vals, n=10)
        print(f"{label}: min={min(vals):.3f} p10={q[0]:.3f} p25={q[2]:.3f} "
              f"median={statistics.median(vals):.3f} p75={q[6]:.3f} max={max(vals):.3f}")
    for lo, hi in ((0, .2), (.2, .35), (.35, .5), (.5, .65), (.65, .8), (.8, 1.01)):
        n = sum(1 for v in new if lo <= v < hi)
        o = sum(1 for v in old if lo <= v < hi)
        print(f"  new {lo:.2f}-{hi:.2f}: {n:4d}   old: {o:4d}")
    print("\nlowest 15 by new metric:")
    for r in sorted(rows, key=lambda r: r["new"])[:15]:
        print(f"  {r['task']} new={r['new']:.3f} old={r['old']:.3f} "
              f"pconf={r['pconf']} wconf={r['wconf']} pchars={r['pchars']} wchars={r['wchars']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())