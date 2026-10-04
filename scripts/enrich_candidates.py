#!/usr/bin/env python3
"""Enrich candidate resources with official source metadata.

Candidate resources (status NEEDS_RESEARCH) whose canonical URL is a GitHub
repository are corroborated with the authenticated `gh` CLI: repository
metadata, README-derived features and primary language are attached, the
license/developer are set, confidence is raised to HIGH, and the linked records
are promoted to COMPLETED. Nothing is inferred: every field comes from the
repository's own public metadata.

Usage:  .venv/bin/python scripts/enrich_candidates.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2 import config, research  # noqa: E402
from v2 import pipeline  # noqa: E402

GH_RE = re.compile(r"github\.com/([^/\s]+)/([^/\s?#]+)", re.I)
PLACEHOLDERS = ("", "not yet described.", "candidate resource",
                "no verified description.")


def is_placeholder(text: str | None) -> bool:
    t = (text or "").strip().lower()
    return not t or any(t.startswith(p) for p in PLACEHOLDERS if p)


def ensure_entity_url(db, entity_id: str, url: str) -> None:
    db.execute(
        "INSERT INTO entity_urls(entity_id,url,url_type,verified) "
        "SELECT ?,?,?,1 WHERE NOT EXISTS "
        "(SELECT 1 FROM entity_urls WHERE entity_id=? AND url=?)",
        (entity_id, url, "github", entity_id, url))


def enrich(db) -> int:
    rows = db.execute(
        "SELECT DISTINCT e.entity_id,e.name,e.canonical_name,e.short_description "
        "FROM entities e WHERE e.is_invalid=0 AND e.deleted_at IS NULL AND ("
        "e.canonical_name LIKE '%github.com/%/%' "
        "OR e.entity_id IN (SELECT entity_id FROM entity_urls WHERE url LIKE '%github.com/%/%') "
        "OR e.entity_id IN (SELECT entity_id FROM urls WHERE url LIKE '%github.com/%/%' "
        "AND entity_id IS NOT NULL))"
    ).fetchall()
    done = 0
    for row in rows:
        m = GH_RE.search(row["canonical_name"] or "")
        if not m:
            link = db.execute(
                "SELECT url FROM entity_urls WHERE entity_id=? AND url LIKE '%github.com/%/%' "
                "LIMIT 1", (row["entity_id"],)).fetchone()
            if not link:
                link = db.execute(
                    "SELECT url FROM urls WHERE entity_id=? AND url LIKE '%github.com/%/%' "
                    "LIMIT 1", (row["entity_id"],)).fetchone()
            m = GH_RE.search(link[0]) if link else None
        if not m:
            continue
        owner, repo = m.group(1), m.group(2)
        meta = research.github_research(owner, repo)
        if not meta:
            continue
        url = meta["url"]
        lic = (meta.get("licenseInfo") or {}).get("spdxId") if meta.get("licenseInfo") else None
        desc = (meta.get("description") or "").strip() or None
        now = config.now()
        db.execute(
            "UPDATE entities SET license=COALESCE(?,license), developer=COALESCE(developer,?), "
            "confidence='HIGH', short_description=CASE WHEN ? THEN COALESCE(?,short_description) "
            "ELSE short_description END, quality_level=MAX(COALESCE(quality_level,0),5), "
            "updated_at=? WHERE entity_id=?",
            (lic, owner, 1 if is_placeholder(row["short_description"]) else 0, desc, now,
             row["entity_id"]))
        ensure_entity_url(db, row["entity_id"], url)
        pipeline.store_resource_details(db, row["entity_id"], meta)
        db.execute(
            "UPDATE tasks SET v2_status=?, status=?, quality_level=MAX(COALESCE(quality_level,0),5), "
            "updated_at=? WHERE entity_id=? AND v2_status=?",
            (config.STATUS_COMPLETED, "COMPLETED", now, row["entity_id"],
             config.STATUS_NEEDS_RESEARCH))
        task = db.execute("SELECT task_id FROM tasks WHERE entity_id=? LIMIT 1",
                          (row["entity_id"],)).fetchone()
        db.execute(
            "INSERT INTO evidence(task_id,entity_id,kind,label,content,created_at) VALUES(?,?,?,?,?,?)",
            (task[0] if task else None, row["entity_id"], "repository_metadata",
             "OFFICIAL SOURCE", url, now))
        db.execute(
            "INSERT INTO audit_log(timestamp,entity_id,action,new_value,reason) VALUES(?,?,?,?,?)",
            (now, row["entity_id"], "ENRICH_GITHUB",
             json.dumps({"url": url, "license": lic, "stars": meta.get("stargazerCount")}),
             "Repository metadata + README corroborated via authenticated gh CLI."))
        done += 1
        print(f"{row['entity_id']} {row['name']} -> {url} "
              f"(license={lic}, stars={meta.get('stargazerCount')})", flush=True)
    db.commit()
    return done


def backfill_developers(db) -> int:
    """Set developer from a repository URL already present in evidence.

    The owner is read verbatim from a github.com/owner/repo URL linked to the
    resource; nothing is guessed. This lets same-organization relationships
    form even for resources whose repository was not re-fetched.
    """
    rows = db.execute(
        "SELECT entity_id, canonical_name FROM entities WHERE is_invalid=0 "
        "AND deleted_at IS NULL AND (developer IS NULL OR TRIM(developer)='')").fetchall()
    changed = 0
    for row in rows:
        eid = row["entity_id"]
        candidates = [row["canonical_name"]]
        candidates += [x[0] for x in db.execute(
            "SELECT url FROM entity_urls WHERE entity_id=?", (eid,))]
        candidates += [x[0] for x in db.execute(
            "SELECT DISTINCT url FROM urls WHERE entity_id=?", (eid,))]
        owner = None
        for candidate in candidates:
            m = GH_RE.search(candidate or "")
            if m:
                owner = m.group(1)
                break
        if owner:
            db.execute("UPDATE entities SET developer=?, updated_at=? WHERE entity_id=?",
                       (owner, config.now(), eid))
            changed += 1
    db.commit()
    return changed


def main() -> int:
    db = pipeline.connect()
    try:
        n = enrich(db)
        d = backfill_developers(db)
        print(f"enrich-candidates done: {n} enriched, {d} developers backfilled")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
