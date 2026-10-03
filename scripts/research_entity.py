#!/usr/bin/env python3
"""Research one OCR-complete task against authoritative GitHub sources, without rerunning OCR."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from process_image import get_github_metadata, read_ocr, refresh_exports, task_row, write_entity, write_image_record
from scan_sources import ROOT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id", help="Task ID with saved OCR, e.g. IMG-0002")
    parser.add_argument("--owner", help="Explicit GitHub owner if OCR is ambiguous")
    parser.add_argument("--repo", help="Explicit GitHub repository name if OCR is ambiguous")
    parser.add_argument("--official-url", help="Primary-source URL verified independently (for non-GitHub products)")
    parser.add_argument("--name", help="Canonical entity name from a verified primary source")
    parser.add_argument("--description", help="Description supported by the cited primary source")
    parser.add_argument("--category", default="Software / AI tools", help="Conservative entity category")
    parser.add_argument("--source-title", help="Title of the primary source")
    parser.add_argument("--source-note", help="What the primary source verified; do not include unverified claims")
    parser.add_argument("--revoke-invalid", action="store_true", help="Revoke a previously misattributed entity association, retain audit history, and return the screenshot to NEEDS_RESEARCH")
    parser.add_argument("--merge-existing", action="store_true", help="Explicitly merge this verified task into an exact-name existing resource and retain the old entity as an audited archive")
    args = parser.parse_args()
    if bool(args.owner) != bool(args.repo):
        parser.error("--owner and --repo must be supplied together")
    if args.revoke_invalid and any((args.official_url, args.name, args.description, args.source_title, args.source_note, args.owner, args.repo)):
        parser.error("--revoke-invalid cannot be combined with research options")
    manual_research = bool(args.official_url or args.name or args.description or args.source_title or args.source_note)
    if manual_research and not all((args.official_url, args.name, args.description, args.source_title, args.source_note)):
        parser.error("manual primary-source research requires --official-url, --name, --description, --source-title, and --source-note")
    if manual_research and (not args.official_url.startswith("https://") or any(ch.isspace() for ch in args.official_url)):
        parser.error("--official-url must be a well-formed HTTPS URL")
    if manual_research and (args.owner or args.repo):
        parser.error("choose either explicit GitHub lookup or a manually inspected primary source")
    with sqlite3.connect(ROOT / "database" / "dataseek.sqlite3", timeout=30) as db:
        db.row_factory = sqlite3.Row
        task = task_row(db, args.task_id)
        if not task:
            print(f"Unknown task ID: {args.task_id}", file=sys.stderr)
            return 2
        output = Path(task["output_file"]) if task["output_file"] else ROOT / "data" / "images" / f"{args.task_id}.md"
        ocr = read_ocr(output)
        if ocr is None:
            print("No complete saved OCR record; use process_image.py on a pending task first.", file=sys.stderr)
            return 2
        if task["status"] in {"COMPLETED", "VERIFIED", "NOT_RELEVANT", "DUPLICATE", "UNREADABLE"} and not (args.revoke_invalid or args.merge_existing):
            print(f"Task {args.task_id} is already terminal ({task['status']}); not modifying it.")
            return 0
        if args.merge_existing and not manual_research:
            parser.error("--merge-existing requires manually cited official-source research options")
        if args.revoke_invalid:
            if not task["entity_id"]:
                print(f"Task {args.task_id} has no assigned entity; no revocation needed.")
                return 0
            old_entity_id = task["entity_id"]
            old_entity = db.execute("SELECT name,canonical_name FROM entities WHERE entity_id=?", (old_entity_id,)).fetchone()
            if not old_entity:
                print(f"Task references missing entity {old_entity_id}; manual integrity repair required.", file=sys.stderr)
                return 2
            now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(timespec="seconds")
            db.execute("DELETE FROM entity_images WHERE entity_id=? AND task_id=?", (old_entity_id, args.task_id))
            db.execute("UPDATE tasks SET entity_id=NULL,status='NEEDS_RESEARCH',research_status='NEEDS_MANUAL_WEB_RESEARCH',completed_at=NULL,error=NULL,notes=? WHERE task_id=?", (f"Revoked invalid entity association to {old_entity_id}; OCR preserved; correct resource still requires research.", args.task_id))
            remaining_sources = db.execute("SELECT COUNT(*) FROM entity_images WHERE entity_id=?", (old_entity_id,)).fetchone()[0]
            if remaining_sources == 0:
                db.execute("UPDATE entities SET is_invalid=1,deleted_at=?,deleted_by='DataSeek integrity repair',deletion_reason=?,updated_at=? WHERE entity_id=?", (now, f"Incorrectly associated with {args.task_id}; screenshot OCR does not identify this entity.", now, old_entity_id))
            else:
                db.execute("UPDATE entities SET updated_at=? WHERE entity_id=?", (now, old_entity_id))
            db.execute("UPDATE research_sources SET notes=COALESCE(notes,'') || ? WHERE entity_id=? AND source_task_id=?", (f"\\nASSOCIATION REVOKED {now}: not supported by screenshot {args.task_id}; retained for audit only.", old_entity_id, args.task_id))
            db.execute("INSERT INTO audit_log(timestamp,task_id,entity_id,action,old_value,new_value,reason) VALUES(?,?,?,?,?,?,?)", (now, args.task_id, old_entity_id, "ENTITY_ASSOCIATION_REVOKED", json.dumps({"entity_id": old_entity_id, "entity_name": old_entity[0], "canonical_name": old_entity[1]}), None, "OCR/screenshot does not support this entity match; invalid attribution was soft-deleted and provenance retained."))
            db.commit()
            summary = refresh_exports(db)
            print(f"Revoked invalid {old_entity_id} association for {args.task_id}; task returned to NEEDS_RESEARCH. Audit and source history retained.")
            return 0
        if manual_research:
            if task["status"] == "NEEDS_RESEARCH" and task["research_status"] != "PENDING" and not args.merge_existing:
                existing_attempt = db.execute("SELECT 1 FROM research_sources WHERE source_task_id=? AND source_type='Official primary source (manually inspected)' LIMIT 1", (args.task_id,)).fetchone()
                if existing_attempt and not args.merge_existing:
                    print(f"Task {args.task_id} already has a recorded manual primary-source research attempt; inspect its record before adding another.", file=sys.stderr)
                    return 0
            canonical = args.official_url.rstrip("/")
            tokens = [token.casefold() for token in re.findall(r"[A-Za-z0-9]+", args.name) if len(token) > 2]
            ocr_lower = ocr.casefold()
            if not any(token in ocr_lower for token in tokens):
                print(f"Screenshot OCR does not contain a distinguishing name token for {args.name!r}; refusing to link this entity.", file=sys.stderr)
                return 1
            try:
                request = Request(canonical, headers={"User-Agent": "DataSeek-research/1.0 (+local provenance verification)"})
                with urlopen(request, timeout=20) as response:
                    final_url = response.geturl()
                    html = response.read(1_000_000).decode("utf-8", errors="replace")
                if response.status != 200:
                    print(f"Primary source returned HTTP {response.status}; refusing entity link.", file=sys.stderr)
                    return 1
                from urllib.parse import urlparse
                source_host = (urlparse(canonical).hostname or "").casefold()
                final_host = (urlparse(final_url).hostname or "").casefold()
                if not source_host or final_host != source_host:
                    print(f"Source redirected to a different host ({final_host}); refusing unverified canonical source.", file=sys.stderr)
                    return 1
                page_text = unescape(re.sub(r"<[^>]+>", " ", html)).casefold()
                if not any(token in page_text for token in tokens):
                    print(f"Fetched page content at {canonical} does not contain a distinguishing name token for {args.name!r}; refusing entity link.", file=sys.stderr)
                    return 1
            except (OSError, HTTPError, URLError, TimeoutError) as error:
                print(f"Could not fetch the claimed primary source ({type(error).__name__}); refusing entity link: {error}", file=sys.stderr)
                return 1
            if args.merge_existing:
                existing = db.execute("SELECT e.entity_id FROM entities e JOIN entity_images i ON i.entity_id=e.entity_id WHERE lower(e.name)=lower(?) AND e.is_invalid=0 AND e.deleted_at IS NULL ORDER BY e.entity_id LIMIT 1", (args.name,)).fetchone()
            else:
                existing = db.execute("SELECT entity_id FROM entities WHERE canonical_name=? OR canonical_name=?", (canonical, args.name)).fetchone()
                if not existing:
                    existing = db.execute("SELECT e.entity_id FROM entities e JOIN entity_images i ON i.entity_id=e.entity_id WHERE lower(e.name)=lower(?) AND e.is_invalid=0 AND e.deleted_at IS NULL ORDER BY e.entity_id LIMIT 1", (args.name,)).fetchone()
            if task["entity_id"] and (not existing or existing[0] != task["entity_id"]):
                linked = db.execute("SELECT name,canonical_name FROM entities WHERE entity_id=?", (task["entity_id"],)).fetchone()
                target = db.execute("SELECT name,canonical_name FROM entities WHERE entity_id=? AND is_invalid=0 AND deleted_at IS NULL", (existing[0],)).fetchone() if existing else None
                if not linked or not target or linked["name"].strip().casefold() != args.name.strip().casefold() or target["name"].strip().casefold() != args.name.strip().casefold():
                    print(f"Potential incorrect merge: task points to {task['entity_id']}; manual primary source resolves to {existing[0] if existing else 'new entity'}. Names do not match exactly; no automatic reassignment was made.", file=sys.stderr)
                    return 2
                old_entity_id = task["entity_id"]
                now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(timespec="seconds")
                db.execute("INSERT INTO entity_images(entity_id,task_id,evidence) VALUES(?,?,?) ON CONFLICT(entity_id,task_id) DO UPDATE SET evidence=excluded.evidence", (existing[0], args.task_id, "Merged into canonical resource after exact-name match and official-source verification; original association is recorded in audit history."))
                db.execute("DELETE FROM entity_images WHERE entity_id=? AND task_id=?", (old_entity_id, args.task_id))
                db.execute("INSERT OR IGNORE INTO entity_urls(entity_id,url,url_type,verified,source_task_id) SELECT ?,url,url_type,verified,source_task_id FROM entity_urls WHERE entity_id=?", (existing[0], old_entity_id))
                db.execute("INSERT INTO research_sources(entity_id,url,title,source_type,accessed_at,notes,source_task_id) SELECT ?,s.url,s.title,s.source_type,s.accessed_at,s.notes,s.source_task_id FROM research_sources s WHERE s.entity_id=? AND NOT EXISTS(SELECT 1 FROM research_sources t WHERE t.entity_id=? AND t.url IS s.url AND t.title IS s.title AND t.source_type IS s.source_type AND t.source_task_id IS s.source_task_id)", (existing[0], old_entity_id, existing[0]))
                db.execute("UPDATE entities SET is_invalid=1,deleted_at=?,deleted_by='DataSeek merge',deletion_reason=?,updated_at=? WHERE entity_id=? AND NOT EXISTS(SELECT 1 FROM entity_images WHERE entity_id=?)", (now, f"Merged duplicate {args.name} into {existing[0]} after exact-name and official-source identity match.", now, old_entity_id, old_entity_id))
                db.execute("INSERT INTO audit_log(timestamp,task_id,entity_id,action,old_value,new_value,reason) VALUES(?,?,?,?,?,?,?)", (now, args.task_id, existing[0], "ENTITY_MERGE", json.dumps({"from_entity_id": old_entity_id, "to_entity_id": existing[0]}), json.dumps({"task_id": args.task_id, "source_url": canonical}), "Exact canonical-name match, screenshot evidence, and fetched official source verified the same resource; historical entity and research rows retained."))
                old_record = ROOT / "data" / "entities" / f"{old_entity_id}.md"
                if old_record.exists():
                    with old_record.open("a", encoding="utf-8") as stream:
                        stream.write(f"\\n\\n## Merge / archive history\\n\\n- Superseded by `{existing[0]}` after exact resource-name match and official-source verification on {now}.\\n- Original entity record, evidence, and research rows are retained for audit/provenance.\\n")
                task["entity_id"] = existing[0]
                task["merged_from_entity_id"] = old_entity_id
                entity_id = existing[0]
            else:
                now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(timespec="seconds")
            if existing:
                entity_id = existing[0]
                prior_source = db.execute("SELECT 1 FROM research_sources WHERE entity_id=? AND url=? AND source_type='Official primary source (manually inspected)' LIMIT 1", (entity_id, canonical)).fetchone()
                prior_link = db.execute("SELECT 1 FROM entity_images WHERE entity_id=? AND task_id=?", (entity_id, args.task_id)).fetchone()
                if prior_source and prior_link and not args.merge_existing:
                    print(f"This official source is already linked to {entity_id} for {args.task_id}; not duplicating the research record.")
                    return 0
            entity_type = "Repository" if (urlparse(canonical).hostname or "").casefold() in {"github.com", "www.github.com"} and len([part for part in urlparse(canonical).path.split("/") if part]) >= 2 else "Product / service"
            if not existing:
                next_entity_number = db.execute("SELECT COALESCE(MAX(CAST(substr(entity_id,5) AS INTEGER)),0)+1 FROM entities").fetchone()[0]
                entity_id = f"ENT-{next_entity_number:06d}"
                db.execute("INSERT INTO entities(entity_id,name,canonical_name,entity_type,category,short_description,detailed_description,confidence,visibility,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (entity_id, args.name, canonical, entity_type, args.category, args.description, args.description, "MEDIUM", "REVIEW_REQUIRED", now, now))
            else:
                active_sources = db.execute("SELECT task_id FROM entity_images WHERE entity_id=? ORDER BY task_id", (entity_id,)).fetchall()
                prior_record = ROOT / "data" / "entities" / f"{entity_id}.md"
                if prior_record.exists() and not active_sources:
                    with prior_record.open("a", encoding="utf-8") as stream:
                        stream.write(f"\\n\\n## Archive status\\n\\n- This record currently has no active screenshot associations as of {now}. Preserve it and its evidence history; do not treat it as an active resource until provenance is restored.\\n")
                db.execute("UPDATE entities SET updated_at=?,entity_type=?,category=?,short_description=COALESCE(short_description,?),confidence=COALESCE(confidence,'MEDIUM') WHERE entity_id=?", (now, entity_type, args.category, args.description, entity_id))
            db.execute("INSERT OR IGNORE INTO entity_images(entity_id,task_id,evidence) VALUES(?,?,?)", (entity_id, args.task_id, "Entity identified from screenshot OCR/UI and cross-checked against the cited official primary-source page."))
            db.execute("INSERT OR IGNORE INTO entity_urls(entity_id,url,url_type,verified,source_task_id) VALUES(?,?,?,?,?)", (entity_id, canonical, "Official primary-source product/service page", 1, args.task_id))
            source_exists = db.execute("SELECT 1 FROM research_sources WHERE entity_id=? AND url=? AND source_type='Official primary source (manually inspected)' LIMIT 1", (entity_id, canonical)).fetchone()
            if not source_exists:
                db.execute("INSERT INTO research_sources(entity_id,url,title,source_type,accessed_at,notes,source_task_id) VALUES(?,?,?,?,?,?,?)", (entity_id, canonical, args.source_title, "Official primary source (manually inspected)", now, args.source_note, args.task_id))
            task["entity_id"] = entity_id
            task["output_file"] = str(output)
            metadata = {"nameWithOwner": args.name, "url": canonical, "description": args.description, "homepageUrl": canonical, "language": None, "license": None, "stargazerCount": None, "forkCount": None, "updatedAt": now, "readme_text": "", "manual_source": True, "entity_id": entity_id, "entity_type": entity_type, "category": args.category, "source_title": args.source_title, "source_note": args.source_note, "verified_at": now, "catalogue_url": "https://chatlyai.app/models" if canonical == "https://chatlyai.app" else None}
            sensitive = bool(re.search(r"(?i)(?:api[_ -]?key|access[_ -]?token|password|secret)\\s*[:=]\\s*\\S+", ocr))
            write_image_record(task, ocr, output, entity_id, metadata, task["duplicate_of"], sensitive)
            with output.open("a", encoding="utf-8") as stream:
                stream.write("\n## Manually recorded primary-source evidence\n\n")
                stream.write(f"- Official source: {canonical}\n- Source title: {args.source_title}\n- Verified scope: {args.source_note}\n- Description: {args.description}\n")
                if canonical == "https://chatlyai.app":
                    stream.write("- Official model catalogue: https://chatlyai.app/models\n")
            db.execute("UPDATE tasks SET status='COMPLETED',research_status='VERIFIED_PRIMARY_SOURCE',entity_id=?,completed_at=?,error=NULL,notes=? WHERE task_id=?", (entity_id, now, "Screenshot identity and scoped claims corroborated with cited official primary-source page; raw OCR preserved and unverified.", args.task_id))
            db.execute("INSERT INTO audit_log(timestamp,task_id,entity_id,action,new_value,reason) VALUES(?,?,?,?,?,?)", (now, args.task_id, entity_id, "PRIMARY_SOURCE_VERIFICATION", json.dumps({"url": canonical, "title": args.source_title, "description": args.description}), args.source_note))
            db.commit()
            entity_sources = [row[0] for row in db.execute("SELECT task_id FROM entity_images WHERE entity_id=? ORDER BY task_id", (entity_id,))]
            write_entity(entity_id, metadata, entity_sources)
            summary = refresh_exports(db)
            print(f"Verified {args.name} → {canonical} as {entity_id} from {args.source_title}; OCR was reused. {summary['completed']} completed, {summary['pending']} pending.")
            return 0
        metadata = get_github_metadata(ocr, args.owner, args.repo)
        if not metadata:
            print("No public official GitHub match could be confirmed. The task remains NEEDS_RESEARCH/UNVERIFIED; consult screenshot context and reliable primary sources.")
            return 1
        existing = db.execute("SELECT entity_id FROM entities WHERE canonical_name=?", (metadata["nameWithOwner"],)).fetchone()
        if task["entity_id"] and (not existing or existing[0] != task["entity_id"]):
            linked = db.execute("SELECT name FROM entities WHERE entity_id=? AND is_invalid=0 AND deleted_at IS NULL", (task["entity_id"],)).fetchone()
            target = db.execute("SELECT name FROM entities WHERE entity_id=? AND is_invalid=0 AND deleted_at IS NULL", (existing[0],)).fetchone() if existing else None
            screenshot_link = db.execute("SELECT 1 FROM entity_images WHERE entity_id=? AND task_id=?", (task["entity_id"], args.task_id)).fetchone()
            if not linked or not target or linked["name"].strip().casefold() != target["name"].strip().casefold() or not screenshot_link:
                print(f"Potential incorrect merge: task points to {task['entity_id']}, verified repository maps to {existing[0] if existing else 'new entity'}. No exact-name-backed prior association was found; refusing automatic reassignment.", file=sys.stderr)
                return 2
            old_entity_id = task["entity_id"]
            now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(timespec="seconds")
            db.execute("INSERT INTO entity_images(entity_id,task_id,evidence) VALUES(?,?,?) ON CONFLICT(entity_id,task_id) DO UPDATE SET evidence=excluded.evidence", (existing[0], args.task_id, "Moved to canonical repository after official GitHub URL, README, OCR, and exact entity-name match; prior association retained in merge audit."))
            db.execute("DELETE FROM entity_images WHERE entity_id=? AND task_id=?", (old_entity_id, args.task_id))
            db.execute("UPDATE entities SET is_invalid=1,deleted_at=?,deleted_by='DataSeek merge',deletion_reason=?,updated_at=? WHERE entity_id=? AND NOT EXISTS(SELECT 1 FROM entity_images WHERE entity_id=?)", (now, f"Merged into canonical GitHub entity {existing[0]} after exact-name and source verification.", now, old_entity_id, old_entity_id))
            db.execute("INSERT INTO audit_log(timestamp,task_id,entity_id,action,old_value,new_value,reason) VALUES(?,?,?,?,?,?,?)", (now, args.task_id, existing[0], "ENTITY_MERGE", json.dumps({"from_entity_id": old_entity_id, "to_entity_id": existing[0]}), json.dumps({"source_url": metadata["url"]}), "Screenshot already had an exact-name resource association; official repository URL and README were verified and same-name identity confirmed."))
            old_record = ROOT / "data" / "entities" / f"{old_entity_id}.md"
            if old_record.exists():
                with old_record.open("a", encoding="utf-8") as stream:
                    stream.write(f"\\n\\n## Merge / archive history\\n\\n- Superseded by `{existing[0]}` after exact-name and official repository verification on {now}.\\n- Original entity and evidence history retained.\\n")
            task["entity_id"] = existing[0]
            task["merged_from_entity_id"] = old_entity_id
        entity_id = task["entity_id"] or (existing[0] if existing else f"ENT-{db.execute('SELECT COUNT(*) FROM entities').fetchone()[0] + 1:06d}")

        from process_image import upsert_entity
        entity_id, _ = upsert_entity(db, task, metadata)
        if task.get("merged_from_entity_id"):
            db.execute("UPDATE tasks SET entity_id=? WHERE task_id=?", (entity_id, args.task_id))
            db.execute("INSERT INTO entity_urls(entity_id,url,url_type,verified,source_task_id) VALUES(?,?,?,?,?) ON CONFLICT(entity_id,url) DO UPDATE SET verified=1,source_task_id=excluded.source_task_id", (entity_id, metadata["url"], "Official GitHub repository", 1, args.task_id))
            db.execute("INSERT OR IGNORE INTO research_sources(entity_id,url,title,source_type,accessed_at,notes,source_task_id) VALUES(?,?,?,?,?,?,?)", (entity_id, metadata["url"], metadata["nameWithOwner"], "Official GitHub repository metadata and README", __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(timespec="seconds"), json.dumps({key: value for key, value in metadata.items() if key != "readme_text"}, ensure_ascii=False), args.task_id))
            db.execute("INSERT INTO audit_log(timestamp,task_id,entity_id,action,new_value,reason) VALUES(?,?,?,?,?,?)", (__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(timespec="seconds"), args.task_id, entity_id, "ENTITY_MERGE_URL_VERIFIED", json.dumps({"url": metadata["url"]}), "Canonical GitHub URL verified after exact-name entity merge."))
        urls = db.execute("SELECT task_id FROM entity_images WHERE entity_id=? ORDER BY task_id", (entity_id,)).fetchall()
        write_entity(entity_id, metadata, [row[0] for row in urls])
        write_image_record(task, ocr, output, entity_id, metadata, task["duplicate_of"], bool(re.search(r"(?i)(?:api[_ -]?key|access[_ -]?token|password|secret)\s*[:=]\s*\S+", ocr)))
        db.execute("UPDATE tasks SET status='COMPLETED',research_status='VERIFIED_PRIMARY_SOURCE',entity_id=?,completed_at=datetime('now'),error=NULL,notes='Saved OCR linked to verified public GitHub metadata and README; OCR was not repeated.' WHERE task_id=?", (entity_id, args.task_id))
        db.execute("INSERT INTO audit_log(timestamp,task_id,entity_id,action,new_value,reason) VALUES(datetime('now'),?,?,?,?,?)", (args.task_id, entity_id, "PRIMARY_SOURCE_VERIFICATION", metadata["url"], "Public GitHub repository metadata URL matched OCR candidate; README endpoint returned from the same official repository."))
        db.commit()
        summary = refresh_exports(db)
        print(f"Verified {metadata['nameWithOwner']} → {metadata['url']} as {entity_id}; OCR was reused. {summary['completed']} completed, {summary['counts']['NEEDS_RESEARCH']} still need research, {summary['pending']} pending.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
