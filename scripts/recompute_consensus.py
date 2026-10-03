#!/usr/bin/env python3
"""Recompute consensus + image Markdown from cached OCR results (no re-OCR).

Use after consensus/taxonomy changes. Reads data/ocr/<task>/*.json, re-reconciles,
updates the database and regenerates the per-image Markdown record.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2 import config, consensus, taxonomy  # noqa: E402
from v2.pipeline import _legacy_status, connect, write_image_markdown  # noqa: E402


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


def main() -> int:
    only = sys.argv[1:] or None
    db = connect()
    rows = db.execute(
        "SELECT task_id FROM tasks WHERE processing_version=? ORDER BY task_id",
        (config.PROCESSING_VERSION,)).fetchall()
    updated = skipped = 0
    for r in rows:
        task_id = r["task_id"]
        if only and task_id not in only:
            continue
        results = load_engines(task_id)
        if not results:
            skipped += 1
            continue
        task = dict(db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())
        ocr = consensus.reconcile(results)
        ocr["screenshot_type"] = consensus.classify_type(
            ocr.get("final_text", ""),
            next((x.get("screenshot_type") for x in results if x["engine"] == "vision"), None))

        cached = {}
        cpath = config.OCR_DIR / task_id / "consensus.json"
        if cpath.is_file():
            try:
                cached = json.loads(cpath.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                cached = {}
        quality = cached.get("quality", {})
        github_meta = cached.get("github") or None
        site_meta = cached.get("official_site") or None

        # Quality level: base consensus level, raised by research evidence.
        level = ocr.get("quality_level", 0)
        if github_meta or site_meta:
            level = max(level, 6 if github_meta else 4)
        ocr["quality_level"] = level

        entity_id = task.get("entity_id")
        entity_info = None
        if entity_id:
            ent = db.execute("SELECT * FROM entities WHERE entity_id=?", (entity_id,)).fetchone()
            if ent:
                cls = taxonomy.classify(ocr.get("final_text", "") + " " + ocr.get("vision_text", ""),
                                        ocr.get("screenshot_type"), ent["resource_type"])
                db.execute("UPDATE entities SET primary_category=?, subcategory=?, "
                           "secondary_categories=?, updated_at=? WHERE entity_id=?",
                           (cls["primary_category"], cls["subcategory"],
                            json.dumps(cls["secondary_categories"]), config.now(), entity_id))
                entity_info = {"name": ent["name"], "canonical": ent["canonical_name"],
                               "type": ent["entity_type"], "description": ent["short_description"],
                               "classification": cls}
        urls = consensus.extract_urls(ocr.get("final_text", ""), ocr.get("vision_text", ""))

        final_status = {
            "OCR_UNREADABLE": config.STATUS_OCR_FAILED,
            "OCR_LOW_CONFIDENCE": config.STATUS_OCR_REVIEW,
            "OCR_CONFLICTING": config.STATUS_OCR_REVIEW,
            "OCR_PARTIAL": config.STATUS_NEEDS_RESEARCH,
            "OCR_NEEDS_VISION": config.STATUS_NEEDS_VISION,
            "OCR_GOOD": config.STATUS_COMPLETED,
        }.get(ocr["status"], config.STATUS_OCR_REVIEW)
        if entity_id and (github_meta or site_meta):
            final_status = config.STATUS_COMPLETED
        elif entity_id:
            final_status = config.STATUS_NEEDS_RESEARCH

        db.execute(
            """INSERT INTO ocr_consensus(task_id,final_text,status,confidence,agreement,
                   engines,escalation_level,quality_level,screenshot_type,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(task_id) DO UPDATE SET final_text=excluded.final_text,
                   status=excluded.status, confidence=excluded.confidence,
                   agreement=excluded.agreement, engines=excluded.engines,
                   quality_level=excluded.quality_level,
                   screenshot_type=excluded.screenshot_type, updated_at=excluded.updated_at""",
            (task_id, ocr.get("final_text"), ocr["status"], ocr.get("confidence"),
             ocr.get("agreement"), json.dumps(ocr.get("engines")), 5,
             level, ocr.get("screenshot_type"), config.now()))
        db.execute("UPDATE tasks SET v2_status=?, ocr_status=?, quality_level=?, "
                   "screenshot_type=?, ocr_confidence=?, status=?, updated_at=? WHERE task_id=?",
                   (final_status, ocr["status"], level, ocr.get("screenshot_type"),
                    ocr.get("confidence"), _legacy_status(final_status), config.now(), task_id))
        # Regenerate the image record with the corrected consensus text.
        write_image_markdown(task, results, ocr, quality, github_meta, site_meta,
                             entity_id, entity_info, urls)
        updated += 1
        if updated % 50 == 0:
            db.commit()
            print(f"  recomputed {updated}…")
    db.commit()
    db.close()
    print(f"recomputed {updated} tasks ({skipped} skipped with no cache)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())