#!/usr/bin/env python3
"""Verify DataSeek task, source, provenance, SQLite, and export consistency."""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
from pathlib import Path

from scan_sources import ROOT, image_paths, source_root


def main() -> int:
    errors: list[str] = []
    source = source_root()
    if source is None:
        errors.append("Neither configured nor fallback screenshot source exists.")
        current_paths: set[str] = set()
    else:
        current_paths = {str(path) for path in image_paths(source)}

    db_path = ROOT / "database" / "dataseek.sqlite3"
    if not db_path.is_file():
        errors.append(f"Database is missing: {db_path}")
        tasks = []
        entities = []
    else:
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            tasks = [dict(row) for row in connection.execute("SELECT * FROM tasks ORDER BY task_id")]
            entities = [dict(row) for row in connection.execute("SELECT * FROM entities ORDER BY entity_id")]
            fk_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if fk_violations:
                errors.append(f"SQLite contains {len(fk_violations)} foreign-key violation(s).")

    task_ids = [row["task_id"] for row in tasks]
    if len(task_ids) != len(set(task_ids)):
        errors.append("Duplicate task IDs found in SQLite.")
    if len([row["relative_source_path"] for row in tasks]) != len(set(row["relative_source_path"] for row in tasks)):
        errors.append("Multiple task records reference the same relative source path.")
    indexed_paths = {row["absolute_source_path"] for row in tasks}
    if current_paths != indexed_paths:
        errors.append(f"Source/task mismatch: {len(current_paths - indexed_paths)} untracked file(s), {len(indexed_paths - current_paths)} stale task(s).")

    entities_by_id = {row["entity_id"]: row for row in entities}
    if len(entities_by_id) != len(entities):
        errors.append("Duplicate entity IDs found in SQLite.")
    with sqlite3.connect(db_path) as connection:
        for entity in entities:
            evidence = connection.execute("SELECT COUNT(*) FROM entity_images WHERE entity_id=?", (entity["entity_id"],)).fetchone()[0]
            if evidence == 0 and not entity["is_invalid"] and not entity["deleted_at"]:
                errors.append(f"Active entity {entity['entity_id']} has no screenshot provenance.")

    for task in tasks:
        if task["status"] in {"COMPLETED", "VERIFIED", "NOT_RELEVANT"}:
            output = task["output_file"] or str(ROOT / "data" / "images" / f"{task['task_id']}.md")
            if not Path(output).is_file():
                errors.append(f"Completed task {task['task_id']} has no output file: {output}")
        if task["entity_id"] and task["entity_id"] not in entities_by_id:
            errors.append(f"Task {task['task_id']} references missing entity {task['entity_id']}.")
        if task["status"] == "DUPLICATE" and task["duplicate_of"] not in set(task_ids):
            errors.append(f"Duplicate task {task['task_id']} has no valid canonical task.")

    json_path = ROOT / "data" / "master" / "ALL_SCRAPED_DATA.json"
    csv_path = ROOT / "data" / "master" / "ALL_SCRAPED_DATA.csv"
    md_path = ROOT / "data" / "master" / "ALL_SCRAPED_DATA.md"
    try:
        exported = json.loads(json_path.read_text(encoding="utf-8"))
        if not isinstance(exported.get("records"), list):
            errors.append("Master JSON does not contain a records array.")
        elif len(exported["records"]) != len(tasks):
            errors.append(f"Master JSON record count {len(exported['records'])} differs from task count {len(tasks)}.")
        elif {record.get("image_id") for record in exported["records"]} != set(task_ids):
            errors.append("Master JSON image IDs do not match SQLite task IDs.")
    except (OSError, json.JSONDecodeError) as error:
        errors.append(f"Master JSON missing/invalid: {error}")

    try:
        with csv_path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        if len(rows) != len(tasks):
            errors.append(f"Master CSV record count {len(rows)} differs from task count {len(tasks)}.")
        if len({row["image_id"] for row in rows}) != len(rows):
            errors.append("Duplicate image IDs found in master CSV.")
    except (OSError, csv.Error, KeyError) as error:
        errors.append(f"Master CSV missing/invalid: {error}")

    if not md_path.is_file():
        errors.append("Master Markdown export is missing.")
    else:
        markdown = md_path.read_text(encoding="utf-8")
        missing = [task_id for task_id in task_ids if f"## {task_id} — " not in markdown]
        if missing:
            errors.append(f"Master Markdown is missing {len(missing)} task section(s); first is {missing[0]}.")

    for task in tasks:
        task_md = ROOT / "tasks" / f"{task['task_id']}.md"
        if not task_md.is_file():
            errors.append(f"Task record is missing: {task_md}")
        elif task["status"] in {"COMPLETED", "VERIFIED", "NOT_RELEVANT"} and not (ROOT / "data" / "images" / f"{task['task_id']}.md").is_file():
            errors.append(f"Completed image analysis record is missing for {task['task_id']}.")

    print(f"Source images: {len(current_paths)}; SQLite tasks: {len(tasks)}; entities: {len(entities)}")
    print(f"Integrity errors: {len(errors)}")
    for error in errors[:100]:
        print(f"ERROR: {error}")
    if len(errors) > 100:
        print(f"... and {len(errors) - 100} additional error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
