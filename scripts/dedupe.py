#!/usr/bin/env python3
"""Detect exact and near-duplicate screenshots.

- Exact duplicates: identical SHA-256 (already recorded on tasks.duplicate_of).
- Near duplicates: Hamming distance between perceptual dHash values, plus
  same-resource screenshots (multiple images showing one resource are NOT
  duplicates — they are distinct evidence and are never merged).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2 import config  # noqa: E402
from v2.pipeline import connect  # noqa: E402

HAMMING_THRESHOLD = 6  # out of 64 bits; conservative to avoid false merges


def hamming(a: str, b: str) -> int:
    if not a or not b or len(a) != len(b):
        return 999
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def main() -> int:
    db = connect()
    rows = db.execute(
        "SELECT task_id, sha256, perceptual_hash, entity_id FROM tasks ORDER BY task_id").fetchall()
    exact = 0
    for r in rows:
        dup = db.execute("SELECT task_id FROM tasks WHERE sha256=? AND task_id<? ORDER BY task_id LIMIT 1",
                         (r["sha256"], r["task_id"])).fetchone()
        if dup:
            db.execute("UPDATE tasks SET duplicate_of=? WHERE task_id=?", (dup["task_id"], r["task_id"]))
            db.execute("INSERT OR IGNORE INTO duplicate_links(task_id,other_task_id,method,score,created_at) "
                       "VALUES(?,?,?,?,?)",
                       (r["task_id"], dup["task_id"], "sha256", 1.0, config.now()))
            exact += 1

    # Near duplicates: bucket by dHash prefix to avoid the full O(n^2) scan.
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        ph = r["perceptual_hash"]
        if ph and len(ph) >= 4:
            buckets.setdefault(ph[:4], []).append(dict(r))
    near = 0
    for group in buckets.values():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if a["task_id"] == b["task_id"]:
                    continue
                dist = hamming(a["perceptual_hash"], b["perceptual_hash"])
                if dist <= HAMMING_THRESHOLD:
                    first, second = sorted([a["task_id"], b["task_id"]])
                    db.execute("INSERT OR IGNORE INTO duplicate_links"
                               "(task_id,other_task_id,method,score,created_at) VALUES(?,?,?,?,?)",
                               (first, second, "dhash", 1 - dist / 64, config.now()))
                    near += 1
    db.commit()

    # Same-resource clusters (distinct evidence, not duplicates).
    clusters = db.execute(
        "SELECT entity_id, COUNT(*) n FROM entity_images GROUP BY entity_id HAVING n > 1 "
        "ORDER BY n DESC").fetchall()
    db.close()
    print(f"exact duplicates: {exact}")
    print(f"near-duplicate pairs (dHash<= {HAMMING_THRESHOLD}): {near}")
    print(f"resources with multiple screenshots: {len(clusters)}")
    for c in clusters[:10]:
        print(f"  {c['entity_id']}: {c['n']} screenshots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())