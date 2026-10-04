#!/usr/bin/env python3
"""DataSeek final audit.

Verifies that the entire source collection is accounted for and that the
database, per-image Markdown records, exports and search index agree. Exit code
is non-zero when a hard integrity error is found.

Usage:  .venv/bin/python scripts/audit.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2 import config  # noqa: E402

ROOT = config.ROOT


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    # --- source accounting -------------------------------------------------
    source = config.source_root()
    if source is None:
        errors.append("immutable source directory not mounted")
        source_files: set[str] = set()
    else:
        source_files = {p.name for p in source.iterdir() if p.is_file()}
    db_path = config.DB_PATH
    if not db_path.is_file():
        print("FATAL: database missing")
        return 1
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    db.row_factory = sqlite3.Row

    tasks = [dict(r) for r in db.execute("SELECT * FROM tasks ORDER BY task_id")]
    task_ids = [t["task_id"] for t in tasks]
    filenames = {t["source_filename"] for t in tasks}

    if source_files:
        if source_files - filenames:
            errors.append(f"{len(source_files - filenames)} source file(s) not tracked in DB")
        if filenames - source_files:
            errors.append(f"{len(filenames - source_files)} DB file(s) missing from source")

    # --- foreign keys ------------------------------------------------------
    fk = db.execute("PRAGMA foreign_key_check").fetchall()
    if fk:
        errors.append(f"{len(fk)} SQLite foreign-key violation(s)")

    # --- per-task durable state -------------------------------------------
    # A task only counts as processed once it has reached a terminal v2 status;
    # OCR_PROCESSING means an interrupted run still owes artifacts.
    processed = [t for t in tasks if t.get("processing_version") == config.PROCESSING_VERSION
                 and t.get("v2_status") not in (None, "OCR_PROCESSING")]
    in_progress = [t for t in tasks if t.get("v2_status") == "OCR_PROCESSING"]
    if in_progress:
        warnings.append(f"{len(in_progress)} task(s) still OCR_PROCESSING (interrupted; will retry)")
    md_dir = config.MARKDOWN_IMAGES
    missing_md = [t["task_id"] for t in processed if not (md_dir / f"{t['task_id']}.md").is_file()]
    if missing_md:
        errors.append(f"{len(missing_md)} processed task(s) lack an image Markdown record "
                       f"(first: {missing_md[0]})")
    missing_ocr = [t["task_id"] for t in processed
                   if not (config.OCR_DIR / t["task_id"] / "consensus.json").is_file()]
    if missing_ocr:
        errors.append(f"{len(missing_ocr)} processed task(s) lack OCR evidence "
                       f"(first: {missing_ocr[0]})")
    consensus_ids = {r[0] for r in db.execute("SELECT task_id FROM ocr_consensus")}
    no_consensus = [t["task_id"] for t in processed if t["task_id"] not in consensus_ids]
    if no_consensus:
        errors.append(f"{len(no_consensus)} processed task(s) lack an ocr_consensus row")

    # --- resources / provenance -------------------------------------------
    entities = {r["entity_id"]: dict(r) for r in db.execute(
        "SELECT * FROM entities WHERE is_invalid=0 AND deleted_at IS NULL")}
    for t in tasks:
        if t["entity_id"] and t["entity_id"] not in entities:
            errors.append(f"task {t['task_id']} references missing/inactive resource {t['entity_id']}")
    prov = {r[0]: r[1] for r in db.execute(
        "SELECT entity_id, COUNT(*) FROM entity_images GROUP BY entity_id")}
    for eid in entities:
        if prov.get(eid, 0) == 0:
            errors.append(f"resource {eid} has no screenshot provenance")
    no_cat = [e for e, r in entities.items() if not (r["primary_category"] or r["category"])]
    if no_cat:
        warnings.append(f"{len(no_cat)} resource(s) lack a primary category")

    # --- duplicates --------------------------------------------------------
    orphan_dups = [t["task_id"] for t in tasks
                   if t["duplicate_of"] and t["duplicate_of"] not in set(task_ids)]
    if orphan_dups:
        errors.append(f"{len(orphan_dups)} duplicate pointer(s) reference unknown tasks")

    # --- exports -----------------------------------------------------------
    all_scr = config.EXPORTS / "ALL_SCREENSHOTS.md"
    if not all_scr.is_file():
        errors.append("ALL_SCREENSHOTS.md missing")
    else:
        text = all_scr.read_text(encoding="utf-8")
        missing = [tid for tid in task_ids if f"## {tid} — " not in text]
        if missing:
            errors.append(f"ALL_SCREENSHOTS.md missing {len(missing)} section(s) "
                           f"(first: {missing[0]})")
    all_res = config.EXPORTS / "ALL_RESOURCES.md"
    if not all_res.is_file():
        errors.append("ALL_RESOURCES.md missing")
    else:
        rtext = all_res.read_text(encoding="utf-8")
        rmissing = [e for e in entities if f"`{e}`" not in rtext]
        if rmissing:
            errors.append(f"ALL_RESOURCES.md missing {len(rmissing)} resource section(s) "
                           f"(first: {rmissing[0]})")

    # --- search index ------------------------------------------------------
    idx = config.EXPORTS / "search_index.json"
    if not idx.is_file():
        errors.append("search_index.json missing")
    else:
        data = json.loads(idx.read_text(encoding="utf-8"))
        if len(data.get("records", [])) != len(tasks):
            errors.append(f"search index record count {len(data.get('records', []))} "
                           f"!= DB {len(tasks)}")
        if len(data.get("resources", [])) != len(entities):
            errors.append(f"search index resource count {len(data.get('resources', []))} "
                           f"!= DB {len(entities)}")
    web_data = ROOT / "web" / "data" / "dataseek.json"
    if not web_data.is_file():
        warnings.append("web/data/dataseek.json missing (site bundle not built)")

    open_errors = db.execute("SELECT COUNT(*) FROM processing_errors WHERE resolved=0").fetchone()[0]
    pending = len(tasks) - len(processed)
    db.close()

    print("DataSeek audit")
    print(f"  source files:      {len(source_files)}")
    print(f"  tracked tasks:     {len(tasks)}")
    print(f"  v2 processed:      {len(processed)}  (pending {pending})")
    print(f"  resources:         {len(entities)}")
    print(f"  open errors:       {open_errors}")
    print(f"  hard errors:       {len(errors)}")
    print(f"  warnings:          {len(warnings)}")
    for e in errors:
        print(f"  ERROR: {e}")
    for w in warnings:
        print(f"  WARN:  {w}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
