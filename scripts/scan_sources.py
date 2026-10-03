#!/usr/bin/env python3
"""Inventory screenshot files without modifying the read-only source tree."""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

ROOT = Path(__file__).resolve().parents[1]
CONFIGURED_SOURCE = Path("/mnt/private-ai-data/Screenshot")
TRAILING_SPACE_FALLBACK = Path("/mnt/private-ai-data/Screenshot ")
SUPPORTED = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif", ".heic"}
VALID_STATUSES = {"PENDING", "IN_PROGRESS", "COMPLETED", "DUPLICATE", "NOT_RELEVANT", "NEEDS_RESEARCH", "RETRY", "FAILED", "UNREADABLE", "VERIFIED"}
UTC_NOW = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")


def source_root() -> Path | None:
    if CONFIGURED_SOURCE.is_dir():
        return CONFIGURED_SOURCE
    if TRAILING_SPACE_FALLBACK.is_dir():
        return TRAILING_SPACE_FALLBACK
    return None


def image_paths(source: Path) -> list[Path]:
    return sorted((path for path in source.rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED), key=lambda p: p.relative_to(source).as_posix().casefold())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dhash(image: Image.Image) -> str:
    """Return a 64-bit difference hash; this is similarity evidence, not entity identity."""
    image = ImageOps.exif_transpose(image).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(image.getdata())
    bits = [int(pixels[row * 9 + col] > pixels[row * 9 + col + 1]) for row in range(8) for col in range(8)]
    value = 0
    for bit in bits:
        value = (value << 1) | bit
    return f"{value:016x}"


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript("""
    PRAGMA foreign_keys = ON;
    CREATE TABLE IF NOT EXISTS tasks (
        task_id TEXT PRIMARY KEY, source_filename TEXT NOT NULL, absolute_source_path TEXT NOT NULL,
        relative_source_path TEXT NOT NULL, extension TEXT NOT NULL, file_size INTEGER NOT NULL,
        width INTEGER, height INTEGER, sha256 TEXT NOT NULL, perceptual_hash TEXT,
        status TEXT NOT NULL CHECK(status IN ('PENDING','IN_PROGRESS','COMPLETED','DUPLICATE','NOT_RELEVANT','NEEDS_RESEARCH','RETRY','FAILED','UNREADABLE','VERIFIED')),
        created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT, retry_count INTEGER NOT NULL DEFAULT 0,
        duplicate_of TEXT REFERENCES tasks(task_id), entity_id TEXT, research_status TEXT NOT NULL DEFAULT 'PENDING',
        output_file TEXT, error TEXT, notes TEXT, visibility TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED'
    );
    CREATE TABLE IF NOT EXISTS entities (
        entity_id TEXT PRIMARY KEY, name TEXT NOT NULL, canonical_name TEXT, entity_type TEXT, category TEXT,
        subcategory TEXT, short_description TEXT, detailed_description TEXT, confidence TEXT,
        visibility TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED', is_invalid INTEGER NOT NULL DEFAULT 0,
        deleted_at TEXT, deleted_by TEXT, deletion_reason TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS entity_images (
        entity_id TEXT NOT NULL REFERENCES entities(entity_id), task_id TEXT NOT NULL REFERENCES tasks(task_id),
        evidence TEXT, PRIMARY KEY(entity_id, task_id)
    );
    CREATE TABLE IF NOT EXISTS entity_urls (
        url_id INTEGER PRIMARY KEY, entity_id TEXT NOT NULL REFERENCES entities(entity_id), url TEXT NOT NULL,
        url_type TEXT, verified INTEGER NOT NULL DEFAULT 0, source_task_id TEXT REFERENCES tasks(task_id),
        research_source_id INTEGER, UNIQUE(entity_id,url)
    );
    CREATE TABLE IF NOT EXISTS research_sources (
        research_source_id INTEGER PRIMARY KEY, entity_id TEXT NOT NULL REFERENCES entities(entity_id), url TEXT,
        title TEXT, source_type TEXT, accessed_at TEXT NOT NULL, notes TEXT, source_task_id TEXT REFERENCES tasks(task_id)
    );
    CREATE TABLE IF NOT EXISTS relationships (
        relationship_id INTEGER PRIMARY KEY, source_entity_id TEXT NOT NULL REFERENCES entities(entity_id),
        target_entity_id TEXT NOT NULL REFERENCES entities(entity_id), relation_type TEXT NOT NULL,
        evidence TEXT, source_task_id TEXT REFERENCES tasks(task_id), UNIQUE(source_entity_id,target_entity_id,relation_type)
    );
    CREATE TABLE IF NOT EXISTS audit_log (
        audit_id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, entity_id TEXT REFERENCES entities(entity_id),
        task_id TEXT REFERENCES tasks(task_id), actor TEXT, action TEXT NOT NULL, old_value TEXT, new_value TEXT, reason TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
    CREATE INDEX IF NOT EXISTS idx_tasks_sha256 ON tasks(sha256);
    CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
    """)
    try:
        connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(entity_id UNINDEXED, name, aliases, description, ocr_text, urls, features, usage, tags, category, technologies, people, organizations)")
    except sqlite3.OperationalError:
        pass


def existing_tasks(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    return {row["relative_source_path"]: dict(row) for row in connection.execute("SELECT * FROM tasks")}


def main() -> int:
    ROOT.joinpath("scripts").mkdir(parents=True, exist_ok=True)
    source = source_root()
    db_path = ROOT / "database" / "dataseek.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    (ROOT / "tasks").mkdir(parents=True, exist_ok=True)

    if source is None:
        message = f"Neither configured source {str(CONFIGURED_SOURCE)!r} nor the observed trailing-space mount {str(TRAILING_SPACE_FALLBACK)!r} exists. No tasks were fabricated."
        error_path = ROOT / "progress" / "ERRORS.md"
        error_path.parent.mkdir(parents=True, exist_ok=True)
        prior = error_path.read_text(encoding="utf-8") if error_path.exists() else "# Errors and Blockers\n"
        if message not in prior:
            error_path.write_text(prior.rstrip() + "\n\n- " + message + "\n", encoding="utf-8")
        print(message, file=sys.stderr)
        return 2

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    ensure_schema(connection)
    previous = existing_tasks(connection)
    all_paths = image_paths(source)
    relpaths = [p.relative_to(source).as_posix() for p in all_paths]
    by_hash: dict[str, str] = {}
    # Preserve established task IDs on rescans; assign deterministic IDs only to new paths.
    used_ids = {row["task_id"] for row in previous.values()}
    id_by_relative: dict[str, str] = {}
    current_relative_paths = set(relpaths)
    for relative, record in previous.items():
        if relative in current_relative_paths:
            id_by_relative[relative] = record["task_id"]
    next_number = 1
    for relative in relpaths:
        if relative in id_by_relative:
            continue
        while f"IMG-{next_number:04d}" in used_ids:
            next_number += 1
        task_id = f"IMG-{next_number:04d}"
        used_ids.add(task_id)
        id_by_relative[relative] = task_id
        next_number += 1

    records: list[dict[str, Any]] = []
    for path, relative in zip(all_paths, relpaths):
        task_id = id_by_relative[relative]
        old = previous.get(relative, {})
        stat = path.stat()
        digest = sha256(path)
        width: int | None = None
        height: int | None = None
        p_hash: str | None = None
        image_error: str | None = None
        try:
            with Image.open(path) as image:
                image.load()
                width, height = image.size
                p_hash = dhash(image)
        except (OSError, UnidentifiedImageError, ValueError) as error:
            image_error = f"Image decode failed: {type(error).__name__}: {error}"

        # Valid completed extraction survives a scan; changed bytes invalidate it explicitly.
        changed = bool(old) and (old.get("sha256") != digest or old.get("file_size") != stat.st_size)
        old_status = old.get("status")
        prior_output = old.get("output_file")
        if old_status in {"COMPLETED", "VERIFIED", "NOT_RELEVANT"} and not changed:
            expected_output = Path(prior_output) if prior_output else ROOT / "data" / "images" / f"{old.get('task_id', task_id)}.md"
            if not expected_output.is_file():
                old_status = "RETRY"
        if image_error:
            status = "UNREADABLE"
        elif old_status in VALID_STATUSES and not changed and old_status not in {"IN_PROGRESS", "RETRY"}:
            status = old_status
        else:
            status = "PENDING"
        row_output_file = old.get("output_file") or str(ROOT / "data" / "images" / f"{task_id}.md")
        row = {
            "task_id": task_id, "source_filename": path.name, "absolute_source_path": str(path),
            "relative_source_path": relative, "extension": path.suffix.lower(), "file_size": stat.st_size,
            "width": width, "height": height, "sha256": digest, "perceptual_hash": p_hash,
            "status": status, "created_at": old.get("created_at") or UTC_NOW(),
            "started_at": old.get("started_at"),            "completed_at": old.get("completed_at") if not changed else None,

            "retry_count": old.get("retry_count", 0), "duplicate_of": None, "entity_id": old.get("entity_id"),
            "research_status": old.get("research_status", "PENDING"), "output_file": row_output_file,
            "error": image_error or ("Source content changed since recorded scan; prior extraction requires review." if changed else old.get("error")),
            "notes": old.get("notes"), "visibility": old.get("visibility", "REVIEW_REQUIRED"),
        }
        records.append(row)

    # Only exact-byte duplicates are automatically linked; perceptual similarity is recorded, not merged.
    for row in records:
        canonical = by_hash.get(row["sha256"])
        if canonical is not None:
            old = previous.get(row["relative_source_path"], {})
            if row["status"] in {"PENDING", "DUPLICATE"} or old.get("status") == "DUPLICATE":
                row["status"] = "DUPLICATE"
                row["duplicate_of"] = canonical
                row["notes"] = "Exact SHA-256 file duplicate; canonical image retained as evidence."
        else:
            by_hash[row["sha256"]] = row["task_id"]

    columns = ("task_id", "source_filename", "absolute_source_path", "relative_source_path", "extension", "file_size", "width", "height", "sha256", "perceptual_hash", "status", "created_at", "started_at", "completed_at", "retry_count", "duplicate_of", "entity_id", "research_status", "output_file", "error", "notes", "visibility")
    placeholders = ",".join("?" for _ in columns)
    assignments = ",".join(f"{column}=excluded.{column}" for column in columns if column != "task_id")
    with connection:
        for row in records:
            connection.execute(f"INSERT INTO tasks ({','.join(columns)}) VALUES ({placeholders}) ON CONFLICT(task_id) DO UPDATE SET {assignments}", tuple(row[name] for name in columns))
        if relpaths:
            marks = ",".join("?" for _ in relpaths)
            # Leave removed files' existing evidence intact but make them visible as a reconciliation warning.
            stale = connection.execute(f"SELECT task_id,relative_source_path FROM tasks WHERE relative_source_path NOT IN ({marks})", relpaths).fetchall()
        else:
            stale = connection.execute("SELECT task_id,relative_source_path FROM tasks").fetchall()

    # One task description per actual image, including inventory facts and all required durable fields.
    for row in records:
        rel = row["relative_source_path"].replace("`", "\\`")
        lines = [f"# {row['task_id']} — {row['source_filename']}", "", "## Source manifest", "",
                 f"- Task ID: {row['task_id']}", f"- Source filename: `{row['source_filename']}`",
                 f"- Absolute source path: `{row['absolute_source_path']}`", f"- Relative source path: `{rel}`",
                 f"- File size: {row['file_size']} bytes", f"- Dimensions: {row['width']} × {row['height']}" if row['width'] is not None else "- Dimensions: unavailable (unreadable image)",
                 f"- SHA-256: `{row['sha256']}`", f"- Perceptual dHash: `{row['perceptual_hash'] or 'unavailable'}`",
                 f"- Status: **{row['status']}**", f"- Created at: {row['created_at']}",
                 f"- Started at: {row['started_at'] or 'not started'}", f"- Completed at: {row['completed_at'] or 'not completed'}",
                 f"- Retry count: {row['retry_count']}", f"- Duplicate of: {row['duplicate_of'] or 'none'}",
                 f"- Entity ID: {row['entity_id'] or 'unassigned'}", f"- Research status: {row['research_status']}",
                 f"- Output file: {row['output_file'] or str(ROOT / 'data/images' / (row['task_id'] + '.md'))}",
                 f"- Error: {row['error'] or 'none'}", f"- Visibility: {row['visibility']}", "",
                 "## Visual analysis", "", "Not yet analyzed. Inventory metadata is not visual-content understanding.", "",
                 "## OCR text", "", "Not yet attempted: no OCR engine was available during environment inspection.", "",
                 "## Evidence and uncertainty", "", "No entity, URL, product, or screenshot source is inferred from the filename. Review-required visibility is the safe default.", ""]
        task_record_path = ROOT / "tasks" / f"{row['task_id']}.md"
        if not task_record_path.exists() or row["status"] in {"PENDING", "RETRY", "UNREADABLE"}:
            task_record_path.write_text("\n".join(lines), encoding="utf-8")
        output = ROOT / "data" / "images" / f"{row['task_id']}.md"
        if not output.exists() and row["status"] not in {"COMPLETED", "VERIFIED", "NOT_RELEVANT"}:
            output.write_text("\n".join([
                f"# {row['task_id']} — {row['source_filename']}", "", "## Processing status", "",
                f"- Status: **{row['status']}** (inventory only)",
                f"- Source file: `{row['absolute_source_path']}`",
                f"- SHA-256: `{row['sha256']}`",
                f"- Dimensions: {row['width']} × {row['height']}",
                f"- Perceptual dHash: `{row['perceptual_hash'] or 'unavailable'}`",
                f"- Duplicate of: {row['duplicate_of'] or 'none detected in current exact-hash pass'}",
                f"- Entity: {row['entity_id'] or 'unidentified'}",
                f"- Visibility: {row['visibility']}", "", "## Visual analysis", "",
                "Not yet performed. Inventory metadata is not visual-content understanding.", "",
                "## OCR", "", "Not attempted: no OCR engine was available during environment inspection.", "",
                "## Evidence and uncertainty", "",
                "No entity, URL, product, or source platform is inferred from the filename.", "",
            ]) + "\n", encoding="utf-8")

    # Write machine-readable complete source manifest and task queue atomically.
    (ROOT / "data" / "master").mkdir(parents=True, exist_ok=True)
    manifest_json = ROOT / "progress" / "source_manifest.json"
    tmp_json = manifest_json.with_suffix(".json.tmp")
    tmp_json.write_text(json.dumps({"source_root": str(source), "configured_source_root": str(CONFIGURED_SOURCE), "source_path_fallback_used": source == TRAILING_SPACE_FALLBACK, "scanned_at": UTC_NOW(), "total_images": len(records), "records": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_json.replace(manifest_json)

    source_lines = ["# Source Manifest", "", f"- Configured source path: `{CONFIGURED_SOURCE}`", f"- Scanned source path: `{source}`", f"- Trailing-space fallback used: {'yes' if source == TRAILING_SPACE_FALLBACK else 'no'}", f"- Scan timestamp: {UTC_NOW()}", f"- Actual supported image count: {len(records)}", f"- Stale previously indexed source records: {len(stale)}", "", "| Task | Source filename | Bytes | Dimensions | SHA-256 | dHash | Status | Duplicate of |", "|---|---|---:|---:|---|---|---|---|"]
    for row in records:
        dimensions = f"{row['width']}×{row['height']}" if row["width"] is not None else "unreadable"
        source_lines.append(f"| {row['task_id']} | `{row['source_filename'].replace('|', '\\|')}` | {row['file_size']} | {dimensions} | `{row['sha256']}` | `{row['perceptual_hash'] or 'N/A'}` | {row['status']} | {row['duplicate_of'] or '—'} |")
    source_manifest_path = ROOT / "progress" / "SOURCE_MANIFEST.md"
    source_manifest_tmp = source_manifest_path.with_suffix(".md.tmp")
    source_manifest_tmp.write_text("\n".join(source_lines) + "\n", encoding="utf-8")
    source_manifest_tmp.replace(source_manifest_path)

    queue_lines = ["# Task Queue", "", f"Total source-image tasks: **{len(records)}**", "", "Exactly one stable task ID is assigned to each image currently present. Inventory does not count as visual analysis.", "", "| Task | Filename | Status | Entity | Research | Output | Error |", "|---|---|---|---|---|---|---|"]
    for row in records:
        queue_lines.append(f"| {row['task_id']} | `{row['source_filename'].replace('|', '\\|')}` | {row['status']} | {row['entity_id'] or '—'} | {row['research_status']} | `{row['output_file'] or f'data/images/{row["task_id"]}.md'}` | {(row['error'] or '—').replace('|', '\\|')} |")
    queue_path = ROOT / "progress" / "TASK_QUEUE.md"
    queue_tmp = queue_path.with_suffix(".md.tmp")
    queue_tmp.write_text("\n".join(queue_lines) + "\n", encoding="utf-8")
    queue_tmp.replace(queue_path)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        groups[row["sha256"]].append(row)
    dup_lines = ["# Deduplication", "", "Exact duplicates are defined by identical SHA-256. Perceptual dHash is retained for later review; perceptual similarity alone never discards an image or merges entities.", ""]
    for group in groups.values():
        if len(group) > 1:
            canonical = min(group, key=lambda item: item["task_id"])
            for item in group:
                if item["task_id"] != canonical["task_id"]:
                    item["status"] = "DUPLICATE"
                    item["duplicate_of"] = canonical["task_id"]
                    item["notes"] = "Exact SHA-256 file duplicate; image-specific output retained."
                    connection.execute("UPDATE tasks SET status='DUPLICATE',duplicate_of=?,notes=? WHERE task_id=?", (canonical["task_id"], item["notes"], item["task_id"]))
                    dup_lines.append(f"- {item['task_id']} → canonical image {canonical['task_id']} — exact SHA-256 match `{item['sha256']}`; image-specific evidence remains separately preserved.")
    if len(dup_lines) == 4:
        dup_lines.append("No exact SHA-256 duplicate files found in the current scan.")
    (ROOT / "progress" / "DEDUPLICATION.md").write_text("\n".join(dup_lines) + "\n", encoding="utf-8")

    status_counts = Counter(row["status"] for row in records)
    exact_duplicate_count = sum(count - 1 for count in Counter(row["sha256"] for row in records).values() if count > 1)
    unreadable_count = status_counts["UNREADABLE"]
    stats_lines = ["# Statistics", "", f"- Total discovered images/tasks: {len(records)}", f"- Expected/previously stated count: 907", f"- Difference from 907: {len(records) - 907:+d}", f"- Scanned source: `{source}`", f"- Configured path exists: {CONFIGURED_SOURCE.is_dir()}", f"- Used trailing-space fallback: {source == TRAILING_SPACE_FALLBACK}", f"- Exact duplicate files (noncanonical count): {exact_duplicate_count}", f"- Unique exact-byte images: {len(records) - exact_duplicate_count}", f"- Unreadable images: {unreadable_count}", f"- Visually analyzed tasks: {sum(status_counts[s] for s in ('COMPLETED','VERIFIED','NOT_RELEVANT'))}", f"- Pending analysis tasks: {sum(status_counts[s] for s in ('PENDING','NEEDS_RESEARCH','RETRY','IN_PROGRESS'))}", f"- Failed tasks: {status_counts['FAILED']}", "- Unique entities: 0 (no visual analysis performed)", "- Research records: 0", "- OCR: unavailable; inventory records explicitly say not attempted", f"- Database: SQLite initialized at `{db_path}` with {len(records)} image tasks", "- Website: not implemented", "- GitHub repository: not created", "- Vercel deployment: not configured or deployed", "", "## Task status counts", ""]
    stats_lines.extend(f"- {status}: {status_counts[status]}" for status in sorted(VALID_STATUSES))
    (ROOT / "progress" / "STATISTICS.md").write_text("\n".join(stats_lines) + "\n", encoding="utf-8")

    progress_path = ROOT / "progress" / "PROGRESS.md"
    progress_path.write_text("\n".join(["# DataSeek Progress", "", "## Verified current state", f"- Project root: `{ROOT}`", f"- Source root: `{source}`", f"- Configured path: `{CONFIGURED_SOURCE}` (exists: {CONFIGURED_SOURCE.is_dir()})", f"- Trailing-space fallback: {'used' if source == TRAILING_SPACE_FALLBACK else 'not used'}", f"- Actual images/tasks: {len(records)}", f"- Completed visual analyses: {sum(status_counts[s] for s in ('COMPLETED','VERIFIED','NOT_RELEVANT'))}", f"- Pending analysis: {sum(status_counts[s] for s in ('PENDING','NEEDS_RESEARCH','RETRY','IN_PROGRESS'))}", f"- Exact duplicate files: {exact_duplicate_count}", f"- Unreadable images: {unreadable_count}", f"- Unique entities: 0; web research records: 0", "", "## Implemented", "- Durable project scaffold, SQLite schema, incremental source scanner, deterministic image IDs, SHA-256, perceptual dHash, exact duplicate links, complete Markdown/JSON/CSV manifests, and per-image task records.", "- Original screenshot files have only been opened read-only.", "", "## Remaining", "- OCR and image-content analysis are not implemented; no OCR backend was available.", "- Web research, entity deduplication, search, website, authenticated admin, GitHub repository, and deployment are not complete.", "- Process images incrementally and checkpoint every completed analysis.", "" ]) + "\n", encoding="utf-8")

    # Machine-readable master is a complete inventory with explicit analysis state; it is not an OCR claim.
    master_records = []
    for row in records:
        master_records.append({"image_id": row["task_id"], "filename": row["source_filename"], "source_path": row["absolute_source_path"], "entity_id": row["entity_id"], "entity_name": None, "type": None, "urls": [], "description": None, "usage": [], "features": [], "research": [], "confidence": None, "duplicate_status": {"status": row["status"], "duplicate_of": row["duplicate_of"]}, "source_evidence": {"sha256": row["sha256"], "perceptual_hash": row["perceptual_hash"], "dimensions": [row["width"], row["height"]], "file_size": row["file_size"], "visibility": row["visibility"]}, "analysis_status": row["status"]})
    master_json_path = ROOT / "data" / "master" / "ALL_SCRAPED_DATA.json"
    master_json_tmp = master_json_path.with_suffix(".json.tmp")
    master_json_tmp.write_text(json.dumps({"schema_version": 1, "generated_at": UTC_NOW(), "source_root": str(source), "total_images": len(records), "records": master_records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    master_json_tmp.replace(master_json_path)
    master_csv_path = ROOT / "data" / "master" / "ALL_SCRAPED_DATA.csv"
    master_csv_tmp = master_csv_path.with_suffix(".csv.tmp")
    with master_csv_tmp.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["image_id", "filename", "entity_id", "entity_name", "type", "urls", "description", "usage", "features", "research", "confidence", "duplicate_status", "source_evidence", "analysis_status"])
        for item in master_records:
            writer.writerow([item["image_id"], item["filename"], item["entity_id"] or "", item["entity_name"] or "", item["type"] or "", json.dumps(item["urls"]), item["description"] or "", json.dumps(item["usage"]), json.dumps(item["features"]), json.dumps(item["research"]), item["confidence"] or "", item["duplicate_status"]["status"] + (f":{item['duplicate_status']['duplicate_of']}" if item["duplicate_status"]["duplicate_of"] else ""), json.dumps(item["source_evidence"], ensure_ascii=False), item["analysis_status"]])
    master_csv_tmp.replace(master_csv_path)

    md_path = ROOT / "data" / "master" / "ALL_SCRAPED_DATA.md"
    master_lines = ["# DataSeek — All Screenshot Records", "", f"Actual source images: **{len(records)}**. This export contains verified inventory metadata for every image; visual content, OCR, entities, URLs, and research remain explicitly unanalyzed until processed.", ""]
    for row in records:
        master_lines.extend([f"## {row['task_id']} — {row['source_filename']}", "", f"- Entity: {row['entity_id'] or 'unidentified (analysis pending)'}", f"- Status: {row['status']}", f"- Screenshot source path: `{row['absolute_source_path']}`", f"- Dimensions: {row['width']} × {row['height']}" if row['width'] is not None else "- Dimensions: unreadable", f"- SHA-256: `{row['sha256']}`", f"- Exact duplicate of: {row['duplicate_of'] or 'none'}", "- Description/usage/features/research: not yet analyzed", f"- Source visibility: {row['visibility']}", ""])
    master_md_tmp = md_path.with_suffix(".md.tmp")
    master_md_tmp.write_text("\n".join(master_lines), encoding="utf-8")
    master_md_tmp.replace(md_path)

    stale_note = f"\n- Reconciliation warning: {len(stale)} task(s) in the database reference files absent from the latest scan. Their source evidence is retained, not deleted.\n" if stale else ""
    errors = ROOT / "progress" / "ERRORS.md"
    base_errors = "# Errors and Blockers\n\n- Configured screenshot directory `/mnt/private-ai-data/Screenshot/` is absent. The read-only fallback `/mnt/private-ai-data/Screenshot ` (literal trailing space) was scanned.\n- No OCR executable or OCR Python package was detected. OCR/content analysis remain unprocessed.\n- The workspace was not initially a Git repository.\n"
    errors.write_text(base_errors.rstrip() + ("\n" + stale_note if stale_note else "\n"), encoding="utf-8")

    exact_duplicates = [row for row in records if row["status"] == "DUPLICATE"]
    next_task = next((row["task_id"] for row in records if row["status"] in {"PENDING", "RETRY", "NEEDS_RESEARCH", "FAILED"}), "none")
    last_completed = next((row["task_id"] for row in reversed(records) if row["status"] in {"COMPLETED", "VERIFIED", "NOT_RELEVANT"}), "none")
    last_state = "\n".join(["# Last State", "", f"- Timestamp (UTC): {UTC_NOW()}", f"- Project: `{ROOT}`", f"- Actual supported source images: {len(records)}", f"- Source path: `{source}`", f"- Configured source path exists: {CONFIGURED_SOURCE.is_dir()}", f"- Completed content analyses: {sum(status_counts[s] for s in ('COMPLETED','VERIFIED','NOT_RELEVANT'))}", f"- Pending content analyses: {sum(status_counts[s] for s in ('PENDING','NEEDS_RESEARCH','RETRY','IN_PROGRESS'))}", f"- Exact duplicate images: {len(exact_duplicates)}", f"- Failed/unreadable: {status_counts['FAILED'] + status_counts['UNREADABLE']}", "- Entity/research records: 0", f"- Current task: {next_task}", f"- Last completed task: {last_completed}", f"- Database: `{db_path}`; {len(records)} tasks indexed", "- Website: not implemented", "- GitHub: CLI authenticated; repository not created", "- Vercel: not configured/deployed", "- OCR: unavailable; not attempted", "- Next exact action: inspect/implement a verified OCR backend and build `process_image.py`, then process the first pending task without research claims unsupported by evidence.", "- Files generated: per-task `tasks/IMG-NNNN.md`, progress manifests/queue/stats/dedup report, `progress/source_manifest.json`, master JSON/CSV/Markdown, SQLite task rows.", "- Known issue: screenshot directory name in mount has a literal trailing space; do not rename or write inside it.", ""])
    (ROOT / "continuation" / "LAST_STATE.md").write_text(last_state, encoding="utf-8")
    continuation = "\n".join(["# Ready-to-paste continuation prompt", "", "Resume the DataSeek project at `/home/chris/dataseek`. Do not ask questions or redo valid completed work. First read `progress/PROGRESS.md`, `progress/TASK_QUEUE.md`, `progress/SOURCE_MANIFEST.md`, `progress/STATISTICS.md`, `continuation/LAST_STATE.md`, and this file, then reconcile task outputs and SQLite. The source collection currently has exactly " + str(len(records)) + " supported images at `/mnt/private-ai-data/Screenshot ` (the pathname has a literal trailing space); `/mnt/private-ai-data/Screenshot/` is absent. Do not rename or alter source files. There are " + str(len(records)) + " inventory tasks, " + str(sum(status_counts[s] for s in ('COMPLETED','VERIFIED','NOT_RELEVANT'))) + " completed visual analyses, " + str(sum(status_counts[s] for s in ('PENDING','NEEDS_RESEARCH','RETRY','IN_PROGRESS'))) + " pending/retry tasks, " + str(len(exact_duplicates)) + " exact file duplicates, and 0 identified entities. No OCR was attempted because no OCR executable or Python engine was available at inspection. Current next pending task is " + next_task + ". The SQLite inventory exists at `database/dataseek.sqlite3`; all source records default to `REVIEW_REQUIRED`. Inventory/exports are present, but visual analysis, web research, entity records, search, website, authenticated admin, GitHub repository, and Vercel deployment are not done. Next: inspect available offline OCR options without modifying the source; implement a robust per-image processor and checkpoint after every task. Do not invent OCR, entities, or web verification. Before publishing or deployment, review screenshots for private data and keep unreviewed content local. GitHub CLI was authenticated but no remote repository was created. Update all required progress/master/continuation files after every completed task.", ""])
    (ROOT / "continuation" / "CONTINUE.md").write_text(continuation, encoding="utf-8")

    connection.close()
    print(f"Scanned {len(records)} images from {source}")
    print(f"Tasks: {len(records)}; exact duplicate noncanonical images: {exact_duplicate_count}; unreadable: {unreadable_count}")
    print(f"Pending visual analysis: {sum(status_counts[s] for s in ('PENDING','NEEDS_RESEARCH','RETRY','IN_PROGRESS'))}; next task: {next_task}")
    if stale:
        print(f"WARNING: {len(stale)} previously indexed source file(s) are now absent; preserved in SQLite.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
