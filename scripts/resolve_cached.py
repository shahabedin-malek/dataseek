#!/usr/bin/env python3
"""Upgrade unresolved screenshots using cached OCR evidence (no re-OCR).

For every v2-processed task that still has no resource, this re-runs only the
cheap resolution stages against the OCR already stored under data/ocr/:
URL extraction -> GitHub research -> official-site research (from a URL visible
in the screenshot). Network-bound and safe to run alongside the OCR batch.

Usage:
  python3 scripts/resolve_cached.py [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2 import config, consensus, research  # noqa: E402
from v2 import pipeline  # noqa: E402


def load_engines(task_id: str) -> list[dict]:
    d = config.OCR_DIR / task_id
    if not d.is_dir():
        return []
    out = []
    for path in sorted(d.glob("*.json")):
        if path.name == "consensus.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "engine" in data:
            out.append(data)
    return out


GENERIC = {"github.com", "gitlab.com", "youtube.com", "youtu.be", "instagram.com",
           "facebook.com", "twitter.com", "x.com", "reddit.com", "tiktok.com",
           "google.com", "linkedin.com", "medium.com", "wikipedia.org"}

STATUS_MAP = {
    "OCR_UNREADABLE": config.STATUS_OCR_FAILED,
    "OCR_LOW_CONFIDENCE": config.STATUS_OCR_REVIEW,
    "OCR_CONFLICTING": config.STATUS_OCR_REVIEW,
    "OCR_PARTIAL": config.STATUS_NEEDS_RESEARCH,
    "OCR_NEEDS_VISION": config.STATUS_NEEDS_VISION,
    "OCR_GOOD": config.STATUS_COMPLETED,
}


def resolve_one(db, task: dict, results: list[dict]) -> dict | None:
    ocr = consensus.reconcile(results)
    ocr["screenshot_type"] = consensus.classify_type(
        ocr.get("final_text", ""),
        next((r.get("screenshot_type") for r in results if r["engine"] == "vision"), None))
    urls = consensus.extract_urls(ocr.get("final_text", ""), ocr.get("vision_text", ""))
    github_meta = site_meta = None
    verified: set[str] = set()
    level = ocr.get("quality_level", 0)

    # Candidates come from the reconciled OCR text AND from URLs the pipeline
    # already extracted into evidence (a visible github.com/owner/repo link is
    # cited provenance, not an inference).
    url_blob = " ".join(u.get("url", "") for u in urls)
    gh = consensus.github_candidates(
        ocr.get("final_text", ""),
        (ocr.get("vision_text", "") or "") + " " + url_blob)
    if gh:
        github_meta = research.github_research(*gh[0])
        if github_meta:
            verified.add(github_meta["url"].lower())
            level = max(level, 6)
    if github_meta is None:
        name_hint = consensus.vision_product_name(ocr.get("vision_text", ""))
        site_meta, matched = pipeline._resolve_site_from_urls(urls, GENERIC, name_hint)
        if site_meta:
            verified.add(matched.lower())
            verified.add(site_meta["url"].lower())
            level = max(level, 4)

    if github_meta is None and site_meta is None:
        return None

    ocr["quality_level"] = level
    entity_id, entity_info = pipeline.resolve_entity(db, task, ocr, github_meta, site_meta)
    pipeline.store_urls(db, task["task_id"], entity_id, urls, verified)
    if entity_id:
        pipeline.store_resource_details(db, entity_id, github_meta)

    final_status = config.STATUS_COMPLETED if (github_meta or site_meta) else config.STATUS_NEEDS_RESEARCH
    db.execute(
        "UPDATE tasks SET v2_status=?, quality_level=?, status=?, updated_at=? WHERE task_id=?",
        (final_status, level, pipeline._legacy_status(final_status), config.now(), task["task_id"]))
    db.execute(
        "INSERT INTO audit_log(timestamp,task_id,entity_id,action,new_value,reason) "
        "VALUES(?,?,?,?,?,?)",
        (config.now(), task["task_id"], entity_id, "RESOLVE_CACHED",
         json.dumps({"status": final_status, "level": level}),
         "Resolved from cached OCR via screenshot-visible URL (no re-OCR)."))
    db.commit()

    # Refresh the per-image Markdown with the corrected resource.
    cpath = config.OCR_DIR / task["task_id"] / "consensus.json"
    quality = {}
    if cpath.is_file():
        try:
            quality = json.loads(cpath.read_text(encoding="utf-8")).get("quality", {})
        except json.JSONDecodeError:
            quality = {}
    try:
        pipeline.write_image_markdown(task, results, ocr, quality, github_meta, site_meta,
                                      entity_id, entity_info, urls)
    except Exception:  # noqa: BLE001 - DB state already checkpointed
        pass
    return {"task_id": task["task_id"], "entity_id": entity_id, "level": level,
            "name": (entity_info or {}).get("name")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    config.ensure_dirs()
    db = pipeline.connect()
    # Never touch a task the OCR batch is actively working on: it has a version
    # stamp and no entity yet, but its cache is still being written.
    rows = db.execute(
        "SELECT * FROM tasks WHERE processing_version=? AND entity_id IS NULL "
        "AND (v2_status IS NULL OR v2_status<>'OCR_PROCESSING') "
        "ORDER BY task_id", (config.PROCESSING_VERSION,)).fetchall()
    ids = [r["task_id"] for r in rows]
    if args.limit:
        ids = ids[: args.limit]
    print(f"resolve-cached: {len(ids)} unresolved tasks", flush=True)
    resolved = skipped = 0
    for i, task_id in enumerate(ids, 1):
        task = dict(db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())
        results = load_engines(task_id)
        if not results:
            skipped += 1
            continue
        try:
            out = resolve_one(db, task, results)
        except Exception as exc:  # noqa: BLE001 - keep going
            print(f"[{i}/{len(ids)}] {task_id}: ERROR {type(exc).__name__}: {exc}", flush=True)
            continue
        if out:
            resolved += 1
            print(f"[{i}/{len(ids)}] {task_id}: {out['entity_id']} {out['name']} "
                  f"(level {out['level']})", flush=True)
    print(f"resolve-cached done: {resolved} resolved, {skipped} skipped (no cache)", flush=True)
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
