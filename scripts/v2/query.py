"""Read-side queries used by the search index, API and static site export."""
from __future__ import annotations

import json
import re
import sqlite3

from . import config


def connect(readonly: bool = True) -> sqlite3.Connection:
    if readonly:
        db = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True, timeout=30)
    else:
        db = sqlite3.connect(config.DB_PATH, timeout=60)
    db.row_factory = sqlite3.Row
    return db


def _j(value, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def resource_record(db: sqlite3.Connection, row: sqlite3.Row) -> dict:
    entity_id = row["entity_id"]
    urls = [r["url"] for r in db.execute(
        "SELECT url FROM entity_urls WHERE entity_id=? ORDER BY verified DESC, url", (entity_id,))]
    urls += [r["url"] for r in db.execute(
        "SELECT DISTINCT url FROM urls WHERE entity_id=? AND url NOT IN (SELECT url FROM entity_urls WHERE entity_id=?)",
        (entity_id, entity_id))]
    records = [r["task_id"] for r in db.execute(
        "SELECT task_id FROM entity_images WHERE entity_id=? ORDER BY task_id", (entity_id,))]
    tags = _j(row["tags"], [])
    for r in db.execute("SELECT t.name FROM tags t JOIN resource_tags rt ON rt.tag_id=t.tag_id "
                        "WHERE rt.entity_id=?", (entity_id,)):
        tags.append(r["name"])
    features = [r["name"] for r in db.execute(
        "SELECT name FROM features WHERE entity_id=? ORDER BY feature_id LIMIT 25", (entity_id,))]
    technologies = [r["name"] for r in db.execute(
        "SELECT name FROM technologies WHERE entity_id=? ORDER BY technology_id", (entity_id,))]
    # Only a real repository URL counts; the bare "github.com" domain is not an
    # organisation/repo and must not become a broken relative link. Rebuild it
    # canonically so fragments/queries (#readme, ?tab=...) are stripped.
    github = None
    for u in urls:
        m = re.search(r"github\.com/([^/\s?#]+)/([^/\s?#]+)", u)
        if m:
            github = f"https://github.com/{m.group(1)}/{m.group(2)}"
            break
    return {
        "entity_id": entity_id,
        "name": row["name"],
        "canonical_name": row["canonical_name"],
        "type": row["resource_type"] or row["entity_type"],
        "primary_category": row["primary_category"] or row["category"] or "Other",
        "subcategory": row["subcategory"],
        "secondary_categories": _j(row["secondary_categories"], []),
        "short_description": row["short_description"],
        "confidence": row["confidence"],
        "visibility": row["visibility"],
        "license": row["license"],
        "developer": row["developer"],
        "platforms": row["platforms"],
        "urls": urls,
        "github_url": github,
        "documentation_url": next((u for u in urls if "docs" in u.lower()), None),
        "tags": sorted(set(tags)),
        "features": features,
        "technologies": technologies,
        "source_count": len(records),
        "records": records,
        "quality_level": row["quality_level"] or 0,
        "updated_at": row["updated_at"],
    }


def all_resources(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(
        "SELECT * FROM entities WHERE is_invalid=0 AND deleted_at IS NULL "
        "ORDER BY (source_count IS NULL), source_count DESC, name").fetchall()
    return [resource_record(db, r) for r in rows]


def record_detail(db: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """Public shape for one evidence record.

    The raw source filename is deliberately not exposed: it is an internal
    provenance artefact and may itself carry the source format in its name.
    """
    task_id = row["task_id"]
    cons = db.execute("SELECT * FROM ocr_consensus WHERE task_id=?", (task_id,)).fetchone()
    entity = None
    if row["entity_id"]:
        ent = db.execute("SELECT entity_id,name,canonical_name FROM entities WHERE entity_id=?",
                         (row["entity_id"],)).fetchone()
        if ent:
            entity = {"entity_id": ent["entity_id"], "name": ent["name"],
                      "canonical_name": ent["canonical_name"]}
    urls = [r["url"] for r in db.execute("SELECT url FROM urls WHERE task_id=? ORDER BY url", (task_id,))]
    engines = [{"engine": r["engine"], "version": r["engine_version"],
                "preprocessing": r["preprocessing"], "confidence": r["confidence"],
                "chars": r["char_count"]}
               for r in db.execute(
                   "SELECT engine,engine_version,preprocessing,confidence,char_count "
                   "FROM ocr_runs WHERE task_id=? ORDER BY ocr_run_id", (task_id,))]
    return {
        "task_id": task_id,
        "width": row["width"], "height": row["height"],
        "sha256": row["sha256"], "perceptual_hash": row["perceptual_hash"],
        "status": row["v2_status"] or row["status"],
        "ocr_status": cons["status"] if cons else None,
        "record_type": cons["screenshot_type"] if cons else None,
        "confidence": cons["confidence"] if cons else None,
        "quality_level": cons["quality_level"] if cons else 0,
        "final_text": (cons["final_text"] if cons else "") or "",
        "vision_text": "",
        "entity": entity,
        "urls": urls,
        "engines": engines,
        "visibility": row["visibility"],
    }


def all_records(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute("SELECT * FROM tasks ORDER BY task_id").fetchall()
    return [record_detail(db, r) for r in rows]


def statistics(db: sqlite3.Connection) -> dict:
    def one(sql, *args):
        return db.execute(sql, args).fetchone()[0]
    cats = [{"category": r["primary_category"] or "Other", "count": r["n"]} for r in db.execute(
        "SELECT COALESCE(primary_category,category,'Other') primary_category, COUNT(*) n "
        "FROM entities WHERE is_invalid=0 AND deleted_at IS NULL GROUP BY 1 ORDER BY n DESC")]
    return {
        "records": one("SELECT COUNT(*) FROM tasks"),
        "v2_processed": one("SELECT COUNT(*) FROM tasks WHERE processing_version=?", config.PROCESSING_VERSION),
        "resources": one("SELECT COUNT(*) FROM entities WHERE is_invalid=0 AND deleted_at IS NULL"),
        "categories": len([c for c in cats if c["count"]]),
        "technologies": one("SELECT COUNT(DISTINCT name) FROM technologies"),
        "github_repositories": one("SELECT COUNT(*) FROM entities WHERE is_invalid=0 AND (resource_type='Repository' OR canonical_name LIKE '%/%')"),
        "ai_tools": one("SELECT COUNT(*) FROM entities WHERE is_invalid=0 AND primary_category='AI'"),
        "verified_resources": one("SELECT COUNT(*) FROM entities WHERE is_invalid=0 AND confidence IN ('HIGH','MEDIUM')"),
        "urls": one("SELECT COUNT(*) FROM urls"),
        "open_errors": one("SELECT COUNT(*) FROM processing_errors WHERE resolved=0"),
        "category_breakdown": cats,
    }


def search(db: sqlite3.Connection, q: str, category: str | None = None,
           resource_type: str | None = None, verified_only: bool = False,
           limit: int = 100) -> list[dict]:
    resources = all_resources(db)
    ql = (q or "").strip().lower()
    out = []
    for r in resources:
        if category and r["primary_category"] != category and category not in r["secondary_categories"]:
            continue
        if resource_type and (r["type"] or "") != resource_type:
            continue
        if verified_only and r["confidence"] not in ("HIGH", "MEDIUM"):
            continue
        haystack = " ".join([
            r["name"] or "", r["canonical_name"] or "", r["short_description"] or "",
            r["primary_category"] or "", r["subcategory"] or "",
            " ".join(r["tags"]), " ".join(r["urls"]), " ".join(r["features"]),
            " ".join(r["technologies"]), r["entity_id"],
        ]).lower()
        if ql and ql not in haystack:
            # light fuzzy: all query tokens present
            tokens = [t for t in ql.split() if len(t) > 2]
            if tokens and not all(t in haystack for t in tokens):
                continue
        score = 0
        if ql:
            if (r["name"] or "").lower().startswith(ql):
                score += 5
            if ql in (r["name"] or "").lower():
                score += 3
            if ql in (r["short_description"] or "").lower():
                score += 1
        r["_score"] = score
        out.append(r)
    out.sort(key=lambda r: (-r["_score"], -(r["source_count"] or 0), r["name"] or ""))
    for r in out:
        r.pop("_score", None)
    return out[:limit]
