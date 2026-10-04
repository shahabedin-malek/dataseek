#!/usr/bin/env python3
"""Broader official-site resolution for records that still have no resource.

For every unresolved record that has at least one URL in evidence, try *every*
stored URL (not just the top 3) against the page's own metadata. A resource is
created only when the fetched page confirms the identity (its own og:site_name /
title, or a supplied candidate name); otherwise the record is left unresolved.

Network-bound and resumable: it never touches a record that already has a
resource, and it commits (and can be killed) per record.

Usage:
  .venv/bin/python scripts/resolve_urls_broader.py [--limit N] [--max-urls N]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2 import config, consensus  # noqa: E402
from v2 import pipeline  # noqa: E402
import resolve_cached  # noqa: E402


def load_bundle(task_id: str) -> tuple[dict | None, dict, list[dict]]:
    """Return (ocr, quality, engine_results) from the cached OCR evidence."""
    cpath = config.OCR_DIR / task_id / "consensus.json"
    ocr: dict | None = None
    quality: dict = {}
    if cpath.is_file():
        try:
            data = json.loads(cpath.read_text(encoding="utf-8"))
            ocr = data.get("consensus")
            quality = data.get("quality") or {}
        except json.JSONDecodeError:
            ocr = None
    results = resolve_cached.load_engines(task_id)
    if not ocr and results:
        ocr = consensus.reconcile(results)
    return (ocr or None), quality, results or []


def url_entries(ocr: dict, stored: list[str]) -> list[dict]:
    extracted = [u["url"] for u in consensus.extract_urls(
        ocr.get("final_text", ""), ocr.get("vision_text", "") or "")]
    seen: dict[str, None] = {}
    for u in stored + extracted:
        u = (u or "").strip()
        if u:
            seen.setdefault(u, None)
    return [{"url": u, "sources": ["stored_url"]} for u in seen]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-urls", type=int, default=16,
                    help="max URLs to try per record (every stored URL by default)")
    args = ap.parse_args()
    config.ensure_dirs()
    db = pipeline.connect()
    rows = db.execute(
        "SELECT * FROM tasks WHERE entity_id IS NULL AND task_id IN "
        "(SELECT DISTINCT task_id FROM urls) ORDER BY task_id").fetchall()
    tasks = [dict(r) for r in rows]
    if args.limit:
        tasks = tasks[: args.limit]
    print(f"resolve-broader: {len(tasks)} unresolved records with a URL", flush=True)
    resolved = 0
    for i, task in enumerate(tasks, 1):
        task_id = task["task_id"]
        ocr, quality, results = load_bundle(task_id)
        if not ocr:
            continue
        stored = [r[0] for r in db.execute(
            "SELECT DISTINCT url FROM urls WHERE task_id=?", (task_id,))]
        entries = url_entries(ocr, stored)
        if not entries:
            continue
        name_hint = consensus.vision_product_name(ocr.get("vision_text", "") or "")
        try:
            site_meta, matched = pipeline._resolve_site_from_urls(
                entries, resolve_cached.GENERIC, name_hint, max_tries=args.max_urls)
        except Exception as exc:  # noqa: BLE001 - keep going
            pipeline.log_error(db, task_id, "resolve_urls_broader",
                               f"{type(exc).__name__}: {exc}")
            continue
        if not site_meta:
            continue
        entity_id, entity_info = pipeline.resolve_entity(db, task, ocr, None, site_meta)
        pipeline.store_urls(db, task_id, entity_id, entries,
                            {matched.lower(), site_meta["url"].lower()})
        now = config.now()
        db.execute(
            "UPDATE tasks SET v2_status=?, status=?, quality_level=MAX(COALESCE(quality_level,0),4), "
            "completed_at=?, updated_at=? WHERE task_id=?",
            (config.STATUS_COMPLETED, "COMPLETED", now, now, task_id))
        db.execute(
            "INSERT INTO audit_log(timestamp,task_id,entity_id,action,new_value,reason) "
            "VALUES(?,?,?,?,?,?)",
            (now, task_id, entity_id, "RESOLVE_URL_BROADER",
             json.dumps({"matched_url": matched, "level": 4}),
             "Identity confirmed from a page metadata match on a stored URL."))
        db.commit()
        try:
            pipeline.write_image_markdown(task, results, ocr, quality, None, site_meta,
                                          entity_id, entity_info, entries)
        except Exception as exc:  # noqa: BLE001 - DB state already checkpointed
            pipeline.log_error(db, task_id, "resolve_urls_broader:markdown",
                               f"{type(exc).__name__}: {exc}")
        resolved += 1
        print(f"[{i}/{len(tasks)}] {task_id}: {entity_id} "
              f"{(entity_info or {}).get('name')} <- {site_meta['url']}", flush=True)
    print(f"resolve-broader done: {resolved} resolved", flush=True)
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
