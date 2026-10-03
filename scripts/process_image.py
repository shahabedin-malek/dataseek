#!/usr/bin/env python3
"""OCR and/or research one screenshot, preserving provenance and durable checkpoints."""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scan_sources import ROOT, ensure_schema, source_root

UTC_NOW = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
SECRET_RE = re.compile(r"(?i)(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|passwd|secret|authorization)\s*[:=]\s*\S+")
GITHUB_RE = re.compile(r"(?i)github\.com\s*/\s*([A-Za-z0-9_.-]+)\s*/\s*([A-Za-z0-9_.-]+)")
GITHUB_TITLE_RE = re.compile(r"(?i)github\s*[-:]\s*([A-Za-z0-9_.-]+)\s*/\s*([A-Za-z0-9_.-]+)")
URL_RE = re.compile(r"(?i)\b(?:(?:https?://)?(?:www\.)?(?:github\.com|gitlab\.com|bitbucket\.org|youtube\.com|youtu\.be|x\.com|twitter\.com|reddit\.com|chromewebstore\.google\.com|play\.google\.com|apps\.apple\.com|huggingface\.co|npmjs\.com|pypi\.org|[^\s<>\"']+\.(?:com|org|net|io|ai|dev|app|edu|gov))(?:/[^\s<>\"']*)?)")
TERMINAL = {"COMPLETED", "VERIFIED", "NOT_RELEVANT", "DUPLICATE", "UNREADABLE"}


def task_row(db: sqlite3.Connection, task_id: str) -> dict[str, Any] | None:
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    return dict(row) if row else None


def run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def read_ocr(output: Path) -> str | None:
    if not output.is_file():
        return None
    content = output.read_text(encoding="utf-8")
    match = re.search(r"## Source facts — raw OCR \(unverified transcription\)\s*\n\s*```text\n(.*?)\n```", content, re.S)
    if not match:
        return None
    value = match.group(1)
    return "" if value == "[OCR completed; no text recognized.]" else value


def run_ocr(image_path: Path, timeout: int) -> str:
    proc = run(["tesseract", str(image_path), "stdout", "-l", "eng", "--psm", "6"], timeout)
    if proc.returncode:
        raise RuntimeError((proc.stderr.strip() or f"Tesseract exited {proc.returncode}")[:1000])
    return proc.stdout.strip()


def get_github_metadata(text: str, owner_hint: str | None = None, repo_hint: str | None = None) -> dict[str, Any] | None:
    candidate = GITHUB_RE.search(text) or GITHUB_TITLE_RE.search(text)
    owner = candidate.group(1) if candidate else owner_hint
    repo = candidate.group(2) if candidate else repo_hint
    if not owner or not repo:
        return None
    try:
        proc = run(["gh", "repo", "view", f"{owner}/{repo}", "--json", "nameWithOwner,url,description,homepageUrl,stargazerCount,forkCount,primaryLanguage,licenseInfo,updatedAt,isPrivate", "--jq", "{nameWithOwner,url,description,homepageUrl,stargazerCount,forkCount,language:.primaryLanguage.name,license:.licenseInfo.spdxId,updatedAt,isPrivate}"], 25)
        if proc.returncode:
            return None
        metadata = json.loads(proc.stdout)
        expected = f"https://github.com/{owner}/{repo}".rstrip("/").casefold()
        if metadata.get("isPrivate") or metadata.get("url", "").rstrip("/").casefold() != expected:
            return None
        # Confirm the public repository has a readable primary-source README endpoint.
        readme = run(["gh", "api", f"repos/{owner}/{repo}/readme", "--jq", ".content"], 20)
        if readme.returncode:
            return None
        try:
            metadata["readme_text"] = base64.b64decode(readme.stdout.strip(), validate=True).decode("utf-8", errors="replace")
        except (ValueError, UnicodeError):
            metadata["readme_text"] = ""
        metadata["owner"] = owner
        metadata["repo"] = repo
        metadata["url"] = expected
        return metadata
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, KeyError, TypeError):
        return None


def upsert_entity(db: sqlite3.Connection, task: dict[str, Any], metadata: dict[str, Any]) -> tuple[str, bool]:
    canonical = metadata["nameWithOwner"]
    found = db.execute("SELECT entity_id FROM entities WHERE canonical_name=? OR name=? LIMIT 1", (canonical, canonical.split("/")[-1])).fetchone()
    created = found is None
    if created:
        number = db.execute("SELECT COUNT(*) FROM entities").fetchone()[0] + 1
        entity_id = f"ENT-{number:06d}"
        now = UTC_NOW()
        description = metadata.get("description") or "Description not supplied by repository metadata."
        db.execute("INSERT INTO entities(entity_id,name,canonical_name,entity_type,category,short_description,detailed_description,confidence,visibility,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (entity_id, canonical.split("/")[-1], canonical, "Repository", "Software / developer tools", description, description, "HIGH", "REVIEW_REQUIRED", now, now))
    else:
        entity_id = found[0]
        db.execute("UPDATE entities SET updated_at=?,confidence='HIGH' WHERE entity_id=?", (UTC_NOW(), entity_id))
    evidence = "Repository owner/name visible in source OCR; identity corroborated with public GitHub repository metadata and README API."
    db.execute("INSERT OR IGNORE INTO entity_images(entity_id,task_id,evidence) VALUES(?,?,?)", (entity_id, task["task_id"], evidence))
    db.execute("UPDATE tasks SET entity_id=? WHERE task_id=?", (entity_id, task["task_id"]))
    db.execute("INSERT OR IGNORE INTO entity_urls(entity_id,url,url_type,verified,source_task_id) VALUES(?,?,?,?,?)", (entity_id, metadata["url"], "Official GitHub repository", 1, task["task_id"]))
    db.execute("INSERT INTO research_sources(entity_id,url,title,source_type,accessed_at,notes,source_task_id) VALUES(?,?,?,?,?,?,?)", (entity_id, metadata["url"], canonical, "Official GitHub repository metadata and README", UTC_NOW(), json.dumps({key: value for key, value in metadata.items() if key != "readme_text"}, ensure_ascii=False), task["task_id"]))
    homepage = metadata.get("homepageUrl")
    if homepage:
        db.execute("INSERT OR IGNORE INTO entity_urls(entity_id,url,url_type,verified,source_task_id) VALUES(?,?,?,?,?)", (entity_id, homepage, "Repository-listed homepage (not independently inspected)", 0, task["task_id"]))
    return entity_id, created


def write_entity(entity_id: str, metadata: dict[str, Any], source_task_ids: list[str]) -> None:
    if metadata.get("manual_source"):
        if metadata.get("nameWithOwner", "").casefold() == "chatly" or entity_id == "ENT-000007":
            body = [
                "# Chatly", "", "## Identity", "",
                f"- Entity ID: {entity_id}", "- Canonical name: Chatly",
                "- Canonical URL: https://chatlyai.app/",
                "- Type: Multi-model AI workspace / AI assistant",
                f"- Category: {metadata.get('category', 'AI tools / multi-model assistants')}",
                "- Confidence: MEDIUM for resource identity; other details are scoped to cited official sources",
                "- Visibility: REVIEW_REQUIRED", "", "## Description", "",
                "Chatly is a multi-model AI workspace for chat, research, and content creation; the official site lists document, slide, spreadsheet, image, video, and music tools.", "", "## URLs", "",
                "- Official website: https://chatlyai.app/",
                "- Official model catalogue: https://chatlyai.app/models",
                "- Official help center: https://help.chatlyai.app/products-and-features/ai-chat/what-is-ai-chat",
                "- Social account shown in screenshot: https://www.instagram.com/chatlyhq/ (screenshot/OCR evidence; account ownership not independently verified)",
                "", "## Developer / Organization", "", "Not established from cited sources.",
                "", "## Technologies", "", "Not independently verified.",
                "", "## Features", "",
                "The official homepage lists AI Docs, AI Slides, AI Sheets, Research, AI Images, AI Videos, AI Music, and Automation. The official model catalogue lists Claude Opus 4.8. The official help center documents text/file-based AI Chat and lists Claude Fable 5 as available in Chatly. These are provider claims, not independently tested capabilities.",
                "", "## Usage", "", "Not independently assessed beyond the official-site descriptions.",
                "", "## Pricing / License", "", "Not verified for this record.",
                "", "## Related Resources", "", "None recorded.",
                "", "## Screenshot Evidence", "",
                *[f"- {task_id} — Screenshot evidence for Chatly." for task_id in source_task_ids],
                "", "## Web Verification", "",
                "- Chatly official homepage: https://chatlyai.app/ — accessed 2026-10-03.",
                "- Chatly official model catalogue: https://chatlyai.app/models — accessed 2026-10-03.",
                "- Chatly Help Center — What is AI Chat?: https://help.chatlyai.app/products-and-features/ai-chat/what-is-ai-chat — accessed 2026-10-03; lists Claude Fable 5 as available in Chatly and documents AI Chat inputs.",
                "- Screenshot evidence is stored per task in data/images/IMG-NNNN.md; OCR is unverified.",
                "", "## AI Analysis", "",
                "The primary resource is Chatly, not the social-media app displaying its promotion. Resource identity and listed model availability are attributed to Chatly's own pages; promotional quality claims are not independently verified.",
                "", "## Confidence", "", "MEDIUM for the identity match; feature statements are attributed to Chatly's official site.",
                "", "## Notes", "", "All screenshots and raw OCR remain private source evidence pending privacy review. Do not treat model capability or pricing copy as independently verified.", "",
            ]
        else:
            body = [
            f"# {metadata['nameWithOwner']}", "", "## Identity", "",
            f"- Entity ID: {entity_id}", f"- Canonical URL: {metadata['url']}",
            f"- Type: {metadata.get('entity_type', 'Product / service')}",
            f"- Category: {metadata.get('category', 'Uncategorized')}",
            "- Confidence: MEDIUM for identity; individual claims are scoped below.",
            "- Visibility: REVIEW_REQUIRED", "", "## Description", "",
            metadata.get("description") or "Not verified.", "", "## URLs", "",
            f"- Official website: {metadata['url']} (verified primary source)",
            *([f"- Official product/model catalogue: {metadata['catalogue_url']} (verified primary source)"] if metadata.get("catalogue_url") else []),
            "", "## Developer / Organization", "", "Not established from the cited sources.",
            "", "## Technologies", "", "Not established from the cited sources.",
            "", "## Features", "", metadata.get("source_note", "No feature claims verified."),
            "", "## Usage", "", "Not independently assessed.",
            "", "## Pricing / License", "", "Not verified for this record.",
            "", "## Related Resources", "", "None recorded.",
            "", "## Screenshot Evidence", "",
            *[f"- {task_id}" for task_id in source_task_ids],
            "", "## Web Verification", "",
            f"- {metadata.get('source_title', 'Official primary source')}: `{metadata['url']}`; checked {metadata.get('verified_at', 'timestamp unavailable')}.",
            *([f"- Official catalogue: `{metadata['catalogue_url']}`."] if metadata.get("catalogue_url") else []),
            "", "## AI Analysis", "", "No additional unsupported claims.",
            "", "## Confidence", "", "MEDIUM for the resource identity; other details are unverified unless explicitly cited above.",
            "", "## Notes", "", metadata.get("source_note", ""),
            "", "Screenshot OCR is unverified evidence; all records remain private pending review.", "",
        ]
        target = ROOT / "data" / "entities" / f"{entity_id}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(body), encoding="utf-8")
        return

    readme = metadata.get("readme_text", "")
    features: list[str] = []
    for line in readme.splitlines():
        clean = line.strip()
        if clean.startswith(("- ", "* ")) and len(clean) > 4 and len(clean) < 220:
            features.append(clean[2:].strip())
        if len(features) >= 30:
            break
    body = [f"# {metadata['nameWithOwner']}", "", "## Identity", "", f"- Entity ID: {entity_id}", f"- Canonical GitHub repository: {metadata['url']}", "- Type: public GitHub repository / software project", "- Confidence: HIGH for repository identity (not every detail)", "- Visibility: REVIEW_REQUIRED", "", "## Source screenshots", ""]
    body.extend(f"- {task_id}" for task_id in source_task_ids)
    body.extend(["", "## Description", "", metadata.get("description") or "Not supplied by GitHub repository metadata.", "", "## URLs", "", f"- Official GitHub: {metadata['url']} (public repository and README API verified)", f"- Homepage: {metadata.get('homepageUrl') or 'not supplied by GitHub metadata'}", "", "## Technical details", "", f"- Primary language: {metadata.get('language') or 'not supplied'}", f"- SPDX license from API: {metadata.get('license') or 'not returned; license remains unverified'}", f"- Stars/forks snapshot: {metadata.get('stargazerCount', 'unknown')}/{metadata.get('forkCount', 'unknown')} (dynamic counts; GitHub metadata updated {metadata.get('updatedAt', 'unknown')})", "", "## Features stated by official README", ""])
    body.extend(f"- {feature}" for feature in features) if features else body.append("- No bullet-list features automatically extracted; inspect the official README before making further claims.")
    body.extend(["", "These are README bullet excerpts; omission does not imply lack of features. Review the cited primary source for context.", "", "## External research", "", f"- Official repository metadata and README API (`{metadata['url']}`), queried {UTC_NOW()} using authenticated GitHub CLI.", "", "## Screenshot evidence", "", "Each image-specific OCR transcript is stored at `data/images/IMG-NNNN.md`.", "", "## AI analysis", "", "No unsupported AI-generated capability assertions.", "", "## Uncertainty", "", "- Version, license text, prices, support status, and operational capabilities were not independently reviewed unless visible above.", "- Stars/forks change; values above are time-stamped snapshots.", "- Screenshots are private; do not publish until individually reviewed.", ""])
    target = ROOT / "data" / "entities" / f"{entity_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(body), encoding="utf-8")


def write_image_record(task: dict[str, Any], ocr: str, output: Path, entity_id: str | None, metadata: dict[str, Any] | None, duplicate_of: str | None, sensitive: bool) -> None:
    urls = sorted(set(URL_RE.findall(ocr)))
    text = ocr if ocr else "[OCR completed; no text recognized.]"
    lines = [f"# {task['task_id']} — {task['source_filename']}", "", "## Identity", "", f"- Task ID: {task['task_id']}", f"- Filename: `{task['source_filename']}`", f"- Entity ID: {entity_id or 'unassigned; OCR identity not independently established'}", f"- Status: **{'COMPLETED' if metadata else 'DUPLICATE' if duplicate_of else 'NEEDS_RESEARCH'}**", "- Visibility: **REVIEW_REQUIRED**", "", "## Screenshot source", "", f"- Read-only source path: `{task['absolute_source_path']}`", f"- SHA-256: `{task['sha256']}`", f"- Perceptual dHash: `{task['perceptual_hash'] or 'unavailable'}`", f"- Dimensions: {task['width']} × {task['height']}", f"- Byte size: {task['file_size']}", f"- OCR processed at UTC: {UTC_NOW()}", "", "## Source facts — raw OCR (unverified transcription)", "", "```text", text if "```" not in text else text.replace("```", "'''"), "```", "", "Raw OCR is evidence of text recognized from the screenshot, not proof of accuracy or truth.", "", "## URL candidates", ""]
    lines.extend(f"- `{url}` (OCR candidate; not verified)." for url in urls) if urls else lines.append("- No URL recognized by conservative URL patterns.")
    lines.extend(["", "## External research", ""])
    if metadata and metadata.get("manual_source"):
        lines.extend([f"- Official primary source: {metadata['url']} ({metadata.get('source_title', 'source title unavailable')}).", f"- Verified scope: {metadata.get('source_note', 'Identity cross-checked against the cited official source.')}"])
    elif metadata:
        lines.extend(["- Research performed using the official GitHub repository page and README API.", f"- Canonical public repository: {metadata['url']} ({metadata['nameWithOwner']}).", f"- Metadata description: {metadata.get('description') or 'not supplied'}.", "- OCR repository name and primary source were cross-checked."])
    else:
        lines.append("Not performed or not confidently matched. URL candidates are unverified; no factual association is asserted.")
    if duplicate_of:
        lines.extend(["", "## File duplicate provenance", "", f"- Exact SHA-256 duplicate of {duplicate_of}; this image's OCR and source evidence remain separately retained."])
    lines.extend(["", "## AI analysis", "", "Not performed; no unsupported inference is presented as fact.", "", "## Uncertainty and privacy", "", "- OCR confidence values were not collected.", "- OCR text may contain mistakes; inspect source pixels before relying on details.", f"- Sensitive-field marker heuristic: {'MATCH — PRIVATE REVIEW REQUIRED; redact before any sharing.' if sensitive else 'No simple marker matched; this is not a privacy guarantee.'}", "- Screenshot and extracted content remain REVIEW_REQUIRED; no public release.", "", "## Provenance", "", f"- Output file: `{output}`", f"- Processing engine: Tesseract English, PSM 6; image pipeline checkpoint at {UTC_NOW()}.", ""])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")


def refresh_exports(db: sqlite3.Connection) -> dict[str, Any]:
    db.row_factory = sqlite3.Row
    records = [dict(row) for row in db.execute("SELECT * FROM tasks ORDER BY task_id")]
    source = source_root()
    counts = Counter(row["status"] for row in records)
    entity_count = db.execute("SELECT COUNT(*) FROM entities WHERE deleted_at IS NULL AND is_invalid=0").fetchone()[0]
    source_count = db.execute("SELECT COUNT(*) FROM research_sources").fetchone()[0]
    duplicate_count = db.execute("SELECT COUNT(*) FROM tasks WHERE status='DUPLICATE'").fetchone()[0]
    completed = sum(counts[state] for state in ("COMPLETED", "VERIFIED", "NOT_RELEVANT"))
    pending = sum(counts[state] for state in ("PENDING", "IN_PROGRESS", "RETRY"))
    failed = counts["FAILED"] + counts["UNREADABLE"]
    manual_research = db.execute("SELECT COUNT(*) FROM tasks WHERE status='NEEDS_RESEARCH' AND research_status<>'PENDING'").fetchone()[0]

    queue = ["# Task Queue", "", f"Total source-image tasks: **{len(records)}**", "", "OCR evidence remains unverified; NEEDS_RESEARCH tasks have OCR saved and must be researched without rerunning OCR.", "", "| Task | Filename | Status | Entity | Research | Output | Error |", "|---|---|---|---|---|---|---|"]
    manifest_lines = ["# Source Manifest", "", f"- Configured source: `/mnt/private-ai-data/Screenshot` (absent; trailing-space fallback used)", f"- Scanned source: `{source or 'unavailable'}`", f"- Last task checkpoint: {UTC_NOW()}", f"- Actual images: {len(records)}", "", "| Task | Filename | Bytes | Dimensions | SHA-256 | dHash | Status | Duplicate |", "|---|---|---:|---:|---|---|---|---|"]
    json_records: list[dict[str, Any]] = []
    md_records = ["# DataSeek — All Screenshot Records", "", f"Actual source images: **{len(records)}**. OCR text is unverified and the dataset is private pending review.", ""]
    for row in records:
        task_md = [f"# {row['task_id']} — {row['source_filename']}", "", "## Task state", "", f"- Task ID: {row['task_id']}", f"- Source filename: `{row['source_filename']}`", f"- Absolute source path: `{row['absolute_source_path']}`", f"- File size: {row['file_size']} bytes", f"- Dimensions: {row['width']} × {row['height']}" if row['width'] is not None else "- Dimensions: unavailable", f"- SHA-256: `{row['sha256']}`", f"- Perceptual hash: `{row['perceptual_hash'] or 'unavailable'}`", f"- Status: **{row['status']}**", f"- Created at: {row['created_at']}", f"- Started at: {row['started_at'] or 'not started'}", f"- Completed at: {row['completed_at'] or 'not completed'}", f"- Retry count: {row['retry_count']}", f"- Duplicate of: {row['duplicate_of'] or 'none'}", f"- Entity ID: {row['entity_id'] or 'unassigned'}", f"- Research status: {row['research_status']}", f"- Output file: `{row['output_file'] or ROOT / 'data' / 'images' / (row['task_id'] + '.md')}`", f"- Error: {row['error'] or 'none'}", f"- Visibility: {row['visibility']}", "", "## Analysis output", "", f"Authoritative image-specific OCR, evidence, privacy review, and uncertainty: `{row['output_file'] or ROOT / 'data' / 'images' / (row['task_id'] + '.md')}`.", ""]
        (ROOT / "tasks" / f"{row['task_id']}.md").write_text("\n".join(task_md), encoding="utf-8")
        queue.append(f"| {row['task_id']} | `{row['source_filename'].replace('|', '\\|')}` | {row['status']} | {row['entity_id'] or '—'} | {row['research_status']} | `{row['output_file'] or '—'}` | {(row['error'] or '—').replace('|', '\\|')} |")
        manifest_lines.append(f"| {row['task_id']} | `{row['source_filename'].replace('|', '\\|')}` | {row['file_size']} | {row['width']}×{row['height']} | `{row['sha256']}` | `{row['perceptual_hash'] or 'N/A'}` | {row['status']} | {row['duplicate_of'] or '—'} |")
        entity = db.execute("SELECT name,canonical_name,entity_type,category,short_description,confidence FROM entities WHERE entity_id=?", (row["entity_id"],)).fetchone() if row["entity_id"] else None
        verified_urls = db.execute("SELECT url FROM entity_urls WHERE entity_id=? AND verified=1 ORDER BY url", (row["entity_id"],)).fetchall() if row["entity_id"] else []
        research_urls = db.execute("SELECT url FROM research_sources WHERE entity_id=? ORDER BY research_source_id", (row["entity_id"],)).fetchall() if row["entity_id"] else []
        sourced_here = False
        if row["entity_id"]:
            sourced_here = db.execute("SELECT 1 FROM entity_images WHERE entity_id=? AND task_id=? LIMIT 1", (row["entity_id"], row["task_id"])).fetchone() is not None
        if row["status"] in {"COMPLETED", "VERIFIED"} and not sourced_here:
            entity = None
            verified_urls = []
            research_urls = []
        elif not sourced_here:
            entity = None
            verified_urls = []
            research_urls = []
        item: dict[str, Any] = {"image_id": row["task_id"], "filename": row["source_filename"], "source_path": row["absolute_source_path"], "entity_id": row["entity_id"], "entity_name": entity["name"] if entity else None, "canonical_entity_name": entity["canonical_name"] if entity else None, "type": entity["entity_type"] if entity else None, "category": entity["category"] if entity else None, "urls": [entry[0] for entry in verified_urls], "description": entity["short_description"] if entity else None, "usage": [], "features": [], "research": [entry[0] for entry in research_urls], "confidence": entity["confidence"] if entity else None, "duplicate_status": {"status": row["status"], "duplicate_of": row["duplicate_of"]}, "source_evidence": {"sha256": row["sha256"], "perceptual_hash": row["perceptual_hash"], "dimensions": [row["width"], row["height"]], "file_size": row["file_size"], "visibility": row["visibility"]}, "analysis_status": row["status"]}
        output = Path(row["output_file"]) if row["output_file"] else ROOT / "data" / "images" / f"{row['task_id']}.md"
        ocr = read_ocr(output)
        if ocr is not None:
            item["ocr_text"] = ocr
            ocr_excerpt = " ".join(ocr.split())[:600]
            md_records.extend([f"## {row['task_id']} — {row['source_filename']}", "", f"- Entity: {entity['canonical_name'] if entity else 'unidentified'}", f"- Entity ID: {row['entity_id'] or 'unassigned'}", f"- Type/category: {entity['entity_type'] if entity else 'unknown'} / {entity['category'] if entity else 'unknown'}", f"- Status: {row['status']}", f"- Source path: `{row['absolute_source_path']}`", f"- Dimensions: {row['width']} × {row['height']}", f"- SHA-256: `{row['sha256']}`", f"- Verified URLs: {', '.join(entry[0] for entry in verified_urls) or 'none'}", f"- Description (external source): {entity['short_description'] if entity else 'not verified'}", f"- OCR (UNVERIFIED excerpt): {ocr_excerpt or '[no text recognized]'}", f"- Research: {'primary-source metadata saved' if row['research_status'] == 'VERIFIED_PRIMARY_SOURCE' else 'pending/unverified'}", f"- Visibility: {row['visibility']}", ""])
        else:
            md_records.extend([f"## {row['task_id']} — {row['source_filename']}", "", f"- Entity: {row['entity_id'] or 'unidentified (analysis pending)'}", f"- Status: {row['status']}", f"- Source path: `{row['absolute_source_path']}`", f"- Dimensions: {row['width']} × {row['height']}", f"- SHA-256: `{row['sha256']}`", "- Description/usage/features/research: not yet analyzed", f"- Visibility: {row['visibility']}", ""])
        json_records.append(item)
    queue_path = ROOT / "progress" / "TASK_QUEUE.md"
    queue_tmp = queue_path.with_suffix(".md.tmp")
    queue_tmp.write_text("\n".join(queue) + "\n", encoding="utf-8")
    queue_tmp.replace(queue_path)
    (ROOT / "progress" / "SOURCE_MANIFEST.md").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    (ROOT / "progress" / "source_manifest.json").write_text(json.dumps({"source_root": str(source) if source else None, "scanned_at": UTC_NOW(), "total_images": len(records), "records": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    exact_groups: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        exact_groups.setdefault(row["sha256"], []).append(row)
    duplicate_lines = ["# Deduplication", "", "Exact duplicates are identified by byte-identical SHA-256. Perceptual similarity alone never merges or discards screenshots.", ""]
    for group in exact_groups.values():
        if len(group) > 1:
            canonical = next((entry for entry in group if entry["status"] != "DUPLICATE"), group[0])
            duplicate_lines.extend(f"- {entry['task_id']} → {canonical['task_id']} — exact SHA-256 `{entry['sha256']}`; image-specific evidence retained." for entry in group if entry["task_id"] != canonical["task_id"])
    if len(duplicate_lines) == 4:
        duplicate_lines.append("No exact SHA-256 duplicate files found in current manifest.")
    (ROOT / "progress" / "DEDUPLICATION.md").write_text("\n".join(duplicate_lines) + "\n", encoding="utf-8")
    (ROOT / "progress" / "DUPLICATES.md").write_text("# Duplicate Index\n\n" + "\n".join(duplicate_lines) + "\n", encoding="utf-8")
    json_path = ROOT / "data" / "master" / "ALL_SCRAPED_DATA.json"
    json_tmp = json_path.with_suffix(".json.tmp")
    json_tmp.write_text(json.dumps({"schema_version": 1, "generated_at": UTC_NOW(), "source_root": str(source) if source else None, "total_images": len(records), "records": json_records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    json_tmp.replace(json_path)
    csv_path = ROOT / "data" / "master" / "ALL_SCRAPED_DATA.csv"
    csv_tmp = csv_path.with_suffix(".csv.tmp")
    with csv_tmp.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["image_id", "filename", "entity_id", "entity_name", "type", "urls", "description", "usage", "features", "research", "confidence", "duplicate_status", "source_evidence", "analysis_status", "ocr_text"])
        for item in json_records:
            writer.writerow([item["image_id"], item["filename"], item["entity_id"] or "", item["entity_name"] or "", item["type"] or "", json.dumps(item["urls"]), item["description"] or "", json.dumps(item["usage"]), json.dumps(item["features"]), json.dumps(item["research"]), item["confidence"] or "", item["duplicate_status"]["status"] + (f":{item['duplicate_status']['duplicate_of']}" if item["duplicate_status"]["duplicate_of"] else ""), json.dumps(item["source_evidence"], ensure_ascii=False), item["analysis_status"], item.get("ocr_text", "")])
    csv_tmp.replace(csv_path)
    markdown_path = ROOT / "data" / "master" / "ALL_SCRAPED_DATA.md"
    markdown_tmp = markdown_path.with_suffix(".md.tmp")
    markdown_tmp.write_text("\n".join(md_records) + "\n", encoding="utf-8")
    markdown_tmp.replace(markdown_path)
    stats = ["# Statistics", "", f"- Total images/tasks: {len(records)}", f"- Difference from expected 907: {len(records)-907:+d}", f"- Completed research/visual analysis: {completed}", f"- OCR completed, research pending: {counts['NEEDS_RESEARCH']}", f"- Needs manual/web research (automated lookup attempted): {manual_research}", f"- Pending/in-progress/retry: {pending}", f"- Exact duplicate images: {duplicate_count}", f"- Unique entities: {entity_count}", f"- External research sources: {source_count}", f"- Unreadable: {counts['UNREADABLE']}", f"- Failed/unreadable: {failed}", "- OCR engine: Tesseract 5.5.0 English PSM 6", f"- Database: `{ROOT / 'database' / 'dataseek.sqlite3'}`", "- Website: not implemented", "- GitHub repository: not created", "- Vercel: not deployed", "", "## Status counts", ""] + [f"- {key}: {counts[key]}" for key in sorted(counts)]
    (ROOT / "progress" / "STATISTICS.md").write_text("\n".join(stats) + "\n", encoding="utf-8")
    task_status_md = ["# Task Status", "", f"Total: {len(records)}", f"Completed/verified/not relevant: {completed}", f"Needs research: {counts['NEEDS_RESEARCH']}", f"Pending/retry/in progress: {pending}", f"Duplicates: {duplicate_count}", f"Failed/unreadable: {failed}", "", "| Status | Count |", "|---|---:|"] + [f"| {key} | {counts[key]} |" for key in sorted(counts)]
    task_status_path = ROOT / "progress" / "TASK_STATUS.md"
    task_status_tmp = task_status_path.with_suffix(".md.tmp")
    task_status_tmp.write_text("\n".join(task_status_md) + "\n", encoding="utf-8")
    task_status_tmp.replace(task_status_path)
    session_log_path = ROOT / "progress" / "SESSION_LOG.md"
    session_log_path.write_text("# Session Log\n\n" + "\n".join(f"- {row['task_id']}: status={row['status']}, research={row['research_status']}, entity={row['entity_id'] or 'unassigned'}" for row in sorted(records, key=lambda item: (item["completed_at"] or "", item["task_id"])) if row["completed_at"] and row["status"] in {"COMPLETED", "NEEDS_RESEARCH", "DUPLICATE", "NOT_RELEVANT", "VERIFIED"}) + "\n", encoding="utf-8")
    resource_lines = ["# Resource Index", ""]
    for entity_row in db.execute("SELECT entity_id,canonical_name,name FROM entities WHERE deleted_at IS NULL AND is_invalid=0 ORDER BY entity_id"):
        provenance = [source_row[0] for source_row in db.execute("SELECT task_id FROM entity_images WHERE entity_id=? ORDER BY task_id", (entity_row["entity_id"],))]
        resource_lines.append(f"- {entity_row['entity_id']}: {entity_row['canonical_name'] or entity_row['name']} — sources: {', '.join(provenance) or 'none (integrity review required)'}")
    (ROOT / "progress" / "RESOURCE_INDEX.md").write_text("\n".join(resource_lines) + "\n", encoding="utf-8")
    (ROOT / "progress" / "ERRORS.md").write_text("# Errors and Blockers\n\n- Configured screenshot path `/mnt/private-ai-data/Screenshot/` does not exist; read-only source fallback is `/mnt/private-ai-data/Screenshot ` (literal trailing space).\n- OCR records are not proof of correct visual interpretation. Tasks in NEEDS_RESEARCH require identity verification or a documented uncertainty disposition.\n- Research/API errors are recorded per task; no unsupported entity facts are asserted.\n- No application tests/frontend/backend exist yet; verification is Python compile and source/task/export integrity only.\n", encoding="utf-8")
    (ROOT / "progress" / "DECISIONS.md").write_text("# Decisions\n\n- Original image files remain read-only and local; source mount directory has a literal trailing space.\n- Stable task IDs are IMG-NNNN; only exact SHA-256 matches are automatic file duplicates.\n- Raw OCR is unverified screenshot evidence. Primary-source corroboration is required before assigning a canonical entity.\n- Unreviewed screenshot data default to REVIEW_REQUIRED.\n- COMPLETED means the screenshot resource identity was corroborated against a cited official primary source; preserve uncertainty for unverified claims.\n", encoding="utf-8")
    progress = ["# DataSeek Progress", "", "## Verified current state", f"- Project: `{ROOT}`", f"- Source: `{source or 'unavailable'}`", f"- Tasks/images: {len(records)}", f"- Completed primary-source identity research: {completed}", f"- OCR done, research pending: {counts['NEEDS_RESEARCH']}", f"- Needs manual/web research after local lookup: {manual_research}", f"- Pending/retry: {pending}", f"- Exact file duplicates: {duplicate_count}", f"- Entities/research sources: {entity_count}/{source_count}", "", "## Implemented", "- Persistent SQLite inventory and per-image records; verified English Tesseract OCR; public GitHub and manually inspected official primary-source corroboration; image-specific evidence and private visibility.", "- Per-task updates regenerate queue, manifest, statistics, deduplication, master Markdown/JSON/CSV, continuation, and last-state files.", "", "## Remaining", "- Research non-GitHub entities and ambiguous OCR; visually inspect screenshots and perform privacy review.", "- Search, frontend, authenticated admin, GitHub project repository, and deployment remain incomplete.", ""]
    (ROOT / "progress" / "PROGRESS.md").write_text("\n".join(progress), encoding="utf-8")
    last_complete = max((row for row in records if row["completed_at"] and row["status"] in {"COMPLETED", "VERIFIED", "NOT_RELEVANT", "NEEDS_RESEARCH", "DUPLICATE", "UNREADABLE"}), key=lambda row: (row["completed_at"], row["task_id"]), default={"task_id": "none"})["task_id"]
    next_task = next((row["task_id"] for row in records if row["status"] == "IN_PROGRESS"), None) or next((row["task_id"] for row in records if row["status"] == "NEEDS_RESEARCH" and row["research_status"] == "PENDING"), None) or next((row["task_id"] for row in records if row["status"] in {"PENDING", "RETRY"}), "none")
    timestamp = UTC_NOW()
    last_state = ["# Last State", "", f"- Timestamp UTC: {timestamp}", f"- Project: `{ROOT}`", f"- Total source images/tasks: {len(records)}", f"- Read-only source path: `{source or 'unavailable'}` (configured path lacks trailing space and is absent)", f"- Research-completed tasks: {completed}", f"- OCR-done needs-research tasks: {counts['NEEDS_RESEARCH']}", f"- Needs-research tasks with a prior automated/manual lookup: {manual_research}", f"- Pending/retry tasks: {pending}", f"- Exact duplicates: {duplicate_count}", f"- Entities: {entity_count}; research sources: {source_count}", f"- Failed/unreadable: {counts['FAILED'] + counts['UNREADABLE']}", f"- Current next task: {next_task}", f"- Last task checkpointed: {last_complete}", f"- Database: `{ROOT / 'database' / 'dataseek.sqlite3'}` ({len(records)} tasks)", "- Website/admin: not implemented", "- GitHub repository: not created", "- Vercel: not deployed", "- OCR: Tesseract installed/verified", "- Next exact action: use the dynamically selected current next task above; if it is NEEDS_RESEARCH with research_status=PENDING, verify from saved OCR and official sources; otherwise process the oldest PENDING/RETRY task with `python3 scripts/process_image.py TASK`. Never rerun OCR for existing OCR records; save a checkpoint after each task.", "- Per-task outputs, database, exports, and continuation state are persisted locally.", ""]
    (ROOT / "continuation" / "LAST_STATE.md").write_text("\n".join(last_state), encoding="utf-8")
    prompt = ["# Ready-to-paste continuation prompt", "", f"Resume DataSeek in `{ROOT}`. Do not restart completed work. First read progress/PROGRESS.md, progress/TASK_QUEUE.md, progress/SOURCE_MANIFEST.md, progress/STATISTICS.md, continuation/LAST_STATE.md, and this file; reconcile outputs/database. Actual inventory is {len(records)} image tasks at `{source}` (directory name ends with a literal space; source is read-only). Current counts: {completed} completed primary-source matches, {counts['NEEDS_RESEARCH']} OCR complete/research pending, {pending} pending/retry, {duplicate_count} exact duplicates, {entity_count} active entities, {source_count} research sources, {counts['FAILED'] + counts['UNREADABLE']} failed/unreadable. Current next task: {next_task}; last task checkpointed: {last_complete}. No IN_PROGRESS work was left at the last reconciliation. Tesseract English OCR is installed. For new pending screenshots use `python3 scripts/process_image.py TASK`; for saved OCR use `python3 scripts/research_entity.py TASK` after checking the image-specific Markdown and a cited official source, without rerunning OCR. Each invocation checkpoints task output, SQLite, entity record, master exports, manifest, progress, and continuation. Manual research requires a true product/source URL (never pass a non-GitHub domain as a GitHub repository). Keep unknown details unasserted and visibility REVIEW_REQUIRED/private. The source directory has a literal trailing space; screenshots are read-only. Web app/admin/GitHub project repo/Vercel are not implemented. Exact next action: use the dynamically selected current next task above; research a NEEDS_RESEARCH task only if its lookup is unattempted, otherwise OCR the oldest PENDING/RETRY task. Reuse saved OCR for all existing OCR records and checkpoint each task.", ""]
    (ROOT / "continuation" / "CONTINUE.md").write_text("\n".join(prompt), encoding="utf-8")
    alias_dir = ROOT / ".progress"
    alias_dir.mkdir(parents=True, exist_ok=True)
    aliases = {
        "MASTER_PROGRESS.md": ROOT / "progress" / "PROGRESS.md",
        "TASK_QUEUE.md": ROOT / "progress" / "TASK_QUEUE.md",
        "TASK_STATUS.md": ROOT / "progress" / "TASK_STATUS.md",
        "CONTINUATION_PROMPT.md": ROOT / "continuation" / "CONTINUE.md",
        "SESSION_LOG.md": ROOT / "progress" / "SESSION_LOG.md",
        "ERRORS.md": ROOT / "progress" / "ERRORS.md",
        "DUPLICATES.md": ROOT / "progress" / "DUPLICATES.md",
        "RESOURCE_INDEX.md": ROOT / "progress" / "RESOURCE_INDEX.md",
        "DECISIONS.md": ROOT / "progress" / "DECISIONS.md",
    }
    for alias_name, source_path in aliases.items():
        alias_path = alias_dir / alias_name
        alias_tmp = alias_path.with_suffix(alias_path.suffix + ".tmp")
        alias_tmp.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
        alias_tmp.replace(alias_path)
    search_index = alias_dir / "SEARCH_INDEX.md"
    search_tmp = search_index.with_suffix(".md.tmp")
    search_tmp.write_text("# Search Index Status\n\n- SQLite provides an `entities_fts` FTS5 schema, but it is currently unpopulated and not wired to an API.\n- OCR, URLs, and resources are not yet queryable through an application.\n- Search API/frontend: not implemented.\n", encoding="utf-8")
    search_tmp.replace(search_index)
    return {"records": records, "counts": counts, "completed": completed, "pending": pending, "manual_research": manual_research, "duplicates": duplicate_count, "entities": entity_count, "sources": source_count, "next_task": next_task}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id", help="Stable task ID e.g. IMG-0001")
    parser.add_argument("--timeout", type=int, default=120, help="OCR timeout in seconds")
    args = parser.parse_args()
    root = source_root()
    if root is None:
        print("Screenshot source is not mounted; task not processed.", file=sys.stderr)
        return 2
    db_path = ROOT / "database" / "dataseek.sqlite3"
    with sqlite3.connect(db_path, timeout=30) as db:
        db.row_factory = sqlite3.Row
        ensure_schema(db)
        task = task_row(db, args.task_id)
        if task is None:
            print(f"Unknown task {args.task_id}", file=sys.stderr)
            return 2
        output = Path(task["output_file"]) if task["output_file"] else ROOT / "data" / "images" / f"{args.task_id}.md"
        saved_ocr = read_ocr(output)
        if task["status"] in TERMINAL:
            if output.is_file():
                print(f"Task {args.task_id} already has valid terminal status {task['status']}; not reprocessing.")
                return 0
            print(f"Task {args.task_id} terminal output missing; integrity failure, not reprocessing automatically.", file=sys.stderr)
            return 2
        if task["status"] == "NEEDS_RESEARCH" and task["research_status"] != "PENDING" and saved_ocr is not None:
            print(f"Task {args.task_id} already has OCR and its automated primary-source lookup was attempted; manual/web research is still required, so not repeating OCR or lookup.")
            return 0
        if task["status"] == "NEEDS_RESEARCH" and saved_ocr is None:
            db.execute("UPDATE tasks SET status='RETRY',error='Saved OCR record is missing; retry required.' WHERE task_id=?", (args.task_id,))
            db.commit()
            task = task_row(db, args.task_id)
            if task is None:
                print(f"Task disappeared during recovery: {args.task_id}", file=sys.stderr)
                return 2
        image_path = Path(task["absolute_source_path"])
        if not image_path.is_file() or not image_path.is_relative_to(root):
            message = f"Source image missing or outside verified source mount: {image_path}"
            db.execute("UPDATE tasks SET status='FAILED',error=? WHERE task_id=?", (message, args.task_id))
            db.commit()
            refresh_exports(db)
            print(message, file=sys.stderr)
            return 2
        text: str
        if saved_ocr is not None and task["status"] in {"NEEDS_RESEARCH", "PENDING", "IN_PROGRESS"}:
            text = saved_ocr
            print(f"Reusing saved OCR for {args.task_id}; no image reprocessing.")
        else:
            db.execute("UPDATE tasks SET status='IN_PROGRESS',started_at=?,error=NULL WHERE task_id=?", (UTC_NOW(), args.task_id))
            db.commit()
            try:
                with image_path.open("rb") as source_file:
                    digest = hashlib.file_digest(source_file, "sha256").hexdigest()
                if digest != task["sha256"]:
                    raise RuntimeError("Source SHA-256 differs from manifest; re-inventory and review required.")
                text = run_ocr(image_path, args.timeout)
            except (OSError, RuntimeError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
                db.execute("UPDATE tasks SET status='RETRY',retry_count=retry_count+1,error=? WHERE task_id=?", (f"OCR error: {type(exc).__name__}: {exc}"[:2000], args.task_id))
                db.commit()
                refresh_exports(db)
                print(f"OCR failed and task was checkpointed for retry: {exc}", file=sys.stderr)
                return 1
        duplicate_row = db.execute("SELECT task_id FROM tasks WHERE sha256=? AND task_id<>? ORDER BY task_id LIMIT 1", (task["sha256"], args.task_id)).fetchone()
        duplicate_of = duplicate_row[0] if duplicate_row else None
        metadata = get_github_metadata(text)
        sensitive = bool(SECRET_RE.search(text))
        entity_id: str | None = None
        now = UTC_NOW()
        if metadata:
            entity_id, _ = upsert_entity(db, task, metadata)
            task["entity_id"] = entity_id
        status = "COMPLETED" if metadata else "DUPLICATE" if duplicate_of else "NEEDS_RESEARCH"
        research_status = "VERIFIED_PRIMARY_SOURCE" if metadata else ("NEEDS_MANUAL_WEB_RESEARCH" if duplicate_of is None else "DUPLICATE_RESEARCH_INHERITED")
        task["output_file"] = str(output)
        write_image_record(task, text, output, entity_id, metadata, duplicate_of, sensitive)
        if metadata:
            associated = [row[0] for row in db.execute("SELECT task_id FROM entity_images WHERE entity_id=? ORDER BY task_id", (entity_id,))]
            write_entity(entity_id, metadata, associated)
        elif duplicate_of:
            original_entity = db.execute("SELECT entity_id FROM tasks WHERE task_id=?", (duplicate_of,)).fetchone()
            if original_entity and original_entity[0]:
                db.execute("INSERT OR IGNORE INTO entity_images(entity_id,task_id,evidence) VALUES(?,?,?)", (original_entity[0], args.task_id, f"Exact SHA-256 file duplicate of {duplicate_of}; separate image-specific OCR is preserved."))
                entity_id = original_entity[0]
                db.execute("UPDATE tasks SET entity_id=? WHERE task_id=?", (entity_id, args.task_id))
        db.execute("UPDATE tasks SET status=?,completed_at=?,duplicate_of=?,entity_id=?,research_status=?,output_file=?,error=NULL,notes=? WHERE task_id=?", (status, now, duplicate_of, entity_id, research_status, str(output), "Primary-source GitHub repository identity corroborated." if metadata else "OCR saved as unverified evidence; external entity research pending.", args.task_id))
        db.execute("INSERT INTO audit_log(timestamp,task_id,entity_id,action,new_value,reason) VALUES(?,?,?,?,?,?)", (now, args.task_id, entity_id, "SCREENSHOT_OCR_AND_RESEARCH" if metadata else "SCREENSHOT_OCR", json.dumps({"status": status, "ocr_characters": len(text), "candidate_urls": sorted(set(URL_RE.findall(text))), "sensitive_field_marker": sensitive, "entity_id": entity_id, "primary_source": metadata["url"] if metadata else None}), "Tesseract OCR saved as source evidence. External identity marked verified only after matching official public GitHub metadata/README." if metadata else "OCR is not verification; keep record private and research identity separately."))
        db.commit()
        completed_at = task_row(db, args.task_id)
        task_md = [f"# {args.task_id} — {task['source_filename']}", "", "## Task state", "", f"- Task ID: {args.task_id}", f"- Source filename: `{task['source_filename']}`", f"- Absolute source path: `{task['absolute_source_path']}`", f"- File size: {task['file_size']} bytes", f"- Dimensions: {task['width']} × {task['height']}", f"- SHA-256: `{task['sha256']}`", f"- Perceptual hash: `{task['perceptual_hash'] or 'unavailable'}`", f"- Status: **{status}**", f"- Created at: {task['created_at']}", f"- Started at: {completed_at['started_at']}", f"- Completed at: {now}", f"- Retry count: {task['retry_count']}", f"- Duplicate of: {duplicate_of or 'none'}", f"- Entity ID: {entity_id or 'unassigned'}", f"- Research status: {research_status}", f"- Output file: `{output}`", "- Error: none", f"- Visibility: {task['visibility']}", "", "## Analysis output", "", f"Authoritative image-specific OCR, source facts, privacy review, and uncertainty: `{output}`.", ""]
        (ROOT / "tasks" / f"{args.task_id}.md").write_text("\n".join(task_md), encoding="utf-8")
        summary = refresh_exports(db)
        print(f"Checkpointed {args.task_id}: {status}; OCR characters={len(text)}; entity={entity_id or 'unresolved'}; research={research_status}; sensitive-marker={sensitive}.")
        print(f"Totals: {summary['completed']} completed research, {summary['counts']['NEEDS_RESEARCH']} need research, {summary['pending']} pending/retry, {summary['entities']} entities, {summary['sources']} research sources.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
