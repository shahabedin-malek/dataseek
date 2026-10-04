"""Main v2 orchestrator for one screenshot.

Stages: quality analysis -> preprocessing -> multi-OCR -> vision -> consensus ->
entity/URL resolution -> web research -> classification -> entity upsert ->
Markdown + OCR cache + database checkpoint.

Every stage leaves durable state; a failure at any stage is recorded and the
task keeps whatever evidence was already produced.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import config, consensus, engines, preprocess, research, taxonomy, vision
from .schema import ensure_v2_schema

ESCALATION_NOTES: list[str] = []


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(config.DB_PATH, timeout=60)
    db.row_factory = sqlite3.Row
    ensure_v2_schema(db)
    return db


def task_row(db: sqlite3.Connection, task_id: str) -> dict | None:
    row = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    return dict(row) if row else None


def log_error(db: sqlite3.Connection, task_id: str | None, stage: str, message: str) -> None:
    db.execute(
        "INSERT INTO processing_errors(task_id,stage,message,created_at) VALUES(?,?,?,?)",
        (task_id, stage, message[:2000], config.now()),
    )
    db.commit()


def _ocr_dir(task_id: str) -> Path:
    d = config.OCR_DIR / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_engine_result(task_id: str, result: dict) -> Path:
    path = _ocr_dir(task_id) / f"{result['engine']}_{result['preprocessing'].replace('/','_')}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def record_ocr_run(db: sqlite3.Connection, task_id: str, result: dict) -> None:
    db.execute(
        """INSERT INTO ocr_runs(task_id,engine,engine_version,preprocessing,confidence,
               char_count,box_count,text,payload,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(task_id,engine,preprocessing) DO UPDATE SET
               engine_version=excluded.engine_version, confidence=excluded.confidence,
               char_count=excluded.char_count, box_count=excluded.box_count,
               text=excluded.text, payload=excluded.payload, created_at=excluded.created_at""",
        (task_id, result["engine"], result.get("engine_version"), result["preprocessing"],
         result.get("confidence"), len(result.get("text") or ""),
         len(result.get("boxes") or []), result.get("text"),
         json.dumps({k: v for k, v in result.items() if k not in ("text", "boxes")}),
         config.now()),
    )
    run_id = db.execute(
        "SELECT ocr_run_id FROM ocr_runs WHERE task_id=? AND engine=? AND preprocessing=?",
        (task_id, result["engine"], result["preprocessing"])).fetchone()[0]
    db.execute("DELETE FROM ocr_regions WHERE ocr_run_id=?", (run_id,))
    for idx, box in enumerate(result.get("boxes") or []):
        bbox = box.get("bbox") or [None, None, None, None]
        db.execute(
            """INSERT INTO ocr_regions(ocr_run_id,task_id,engine,text,confidence,
                   x0,y0,x1,y1,region_index) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (run_id, task_id, result["engine"], box.get("text"), box.get("confidence"),
             bbox[0], bbox[1], bbox[2], bbox[3], idx),
        )
    db.commit()


def run_ocr_ladder(image_path: Path, quality: dict, task_id: str,
                   use_vision: bool, db: sqlite3.Connection) -> tuple[list[dict], int]:
    """Execute the OCR escalation ladder and return (results, escalation_level)."""
    results: list[dict] = []
    level = 1

    if engines.rapidocr_available():
        r = engines.run_rapidocr(image_path, "original")
        results.append(r)
        save_engine_result(task_id, r)
        record_ocr_run(db, task_id, r)
        if r.get("error"):
            log_error(db, task_id, "ocr:rapidocr", r["error"])
    else:
        log_error(db, task_id, "ocr:rapidocr", "rapidocr not installed")

    # Level 2: a structurally different engine.
    r = engines.run_tesseract(image_path, "original", psm=3)
    results.append(r)
    save_engine_result(task_id, r)
    record_ocr_run(db, task_id, r)
    level = 2

    # Level 3: preprocessing variant — only escalate when the primary engine was
    # weak. Running a second full OCR pass on every dark-UI screenshot doubles cost
    # for little gain, so this is driven by confidence, not image type alone.
    primary = next((r for r in results if r["engine"] == config.ENGINE_RAPIDOCR), None)
    weak = primary is None or (primary.get("confidence") or 0) < 72 \
        or len((primary.get("text") or "")) < 80
    if weak and (quality.get("dark_ui") or quality.get("low_contrast") or quality.get("blurry")):
        with preprocess.variants(image_path, task_id, quality) as variants:
            variant = variants.get("clahe") or variants.get("grayscale")
            if variant is not None and engines.rapidocr_available():
                r = engines.run_rapidocr(variant, "clahe")
                results.append(r)
                save_engine_result(task_id, r)
                record_ocr_run(db, task_id, r)
                level = 3

    # Level 5: vision model.
    if use_vision and vision.vision_available():
        v = vision.run_vision(image_path)
        results.append(v)
        save_engine_result(task_id, v)
        record_ocr_run(db, task_id, v)
        _record_vision(db, task_id, v)
        if v.get("error"):
            log_error(db, task_id, "vision", v["error"])
        else:
            level = max(level, 5)
    return results, level


def _record_vision(db: sqlite3.Connection, task_id: str, v: dict) -> None:
    db.execute(
        """INSERT INTO vision_runs(task_id,model,screenshot_type,visible_text,urls,
               entities,raw_response,latency_s,created_at) VALUES(?,?,?,?,?,?,?,?,?)
           ON CONFLICT(task_id,model) DO UPDATE SET
               screenshot_type=excluded.screenshot_type, visible_text=excluded.visible_text,
               urls=excluded.urls, entities=excluded.entities,
               raw_response=excluded.raw_response, latency_s=excluded.latency_s,
               created_at=excluded.created_at""",
        (task_id, v.get("engine_version"), v.get("screenshot_type"), v.get("text"),
         json.dumps(consensus.extract_urls(v.get("text") or "")),
         json.dumps([]), v.get("raw"), v.get("latency_s"), config.now()),
    )
    db.commit()


def _host_of(raw: str) -> str:
    candidate = raw if "//" in raw else "//" + raw
    host = (urlsplit(candidate).hostname or "").casefold()
    return host[4:] if host.startswith("www.") else host


def _resolve_site_from_urls(urls: list[dict], generic: set[str], name_hint: str | None,
                            max_tries: int = 3) -> tuple[dict | None, str]:
    """Try the screenshot's own visible URLs, preferring paths over bare domains.

    Returns (research_metadata, matched_entry_url). Only a page that responds and
    confirms identity (via the supplied name, or its own metadata) is accepted.
    """
    def rank(entry: dict) -> tuple:
        raw = entry["url"].strip()
        host = _host_of(raw)
        bare = "//" not in raw
        return (host in generic, bare)

    tried = 0
    for entry in sorted(urls, key=rank):
        raw = entry["url"].strip()
        host = _host_of(raw)
        if not host or host in generic:
            continue
        candidate = raw if raw.startswith(("http://", "https://")) else f"https://{raw}"
        meta = research.official_site_research(candidate, name_hint or None)
        if meta:
            return meta, raw
        tried += 1
        if tried >= max_tries:
            break
    return None, ""


def resolve_entity(db: sqlite3.Connection, task: dict, ocr: dict, github_meta: dict | None,
                   site_meta: dict | None) -> tuple[str | None, dict | None]:
    """Find or create the resource (entity) for this screenshot."""
    name = canonical = None
    entity_type = resource_type = None
    description = None
    confidence = "MEDIUM"
    if github_meta:
        canonical = github_meta["nameWithOwner"]
        name = canonical.split("/")[-1]
        entity_type = "Repository"
        resource_type = "Repository"
        description = github_meta.get("description")
    elif site_meta:
        name = site_meta.get("name") or site_meta.get("title")
        canonical = site_meta.get("url")
        entity_type = "Product"
        resource_type = "Website"
        description = site_meta.get("title")
    if not name:
        # Conservative fallback: only when OCR and the vision model independently
        # agree on a product name do we record a *candidate* resource (LOW confidence).
        candidate = consensus.vision_product_name(ocr.get("vision_text", ""))
        if candidate and candidate.casefold() in (ocr.get("final_text") or "").casefold():
            name = candidate
            entity_type = "Product"
            resource_type = "Product"
            confidence = "LOW"
            description = "Candidate resource: name corroborated by OCR and vision model; not yet web-verified."
        else:
            return None, None

    found = db.execute(
        "SELECT entity_id FROM entities WHERE is_invalid=0 AND deleted_at IS NULL AND "
        "(lower(canonical_name)=lower(?) OR lower(name)=lower(?)) LIMIT 1",
        (canonical or name, name)).fetchone()
    cls = taxonomy.classify(ocr.get("final_text", "") + " " + ocr.get("vision_text", ""),
                            ocr.get("screenshot_type"), resource_type)
    now = config.now()
    if found:
        entity_id = found[0]
        db.execute(
            "UPDATE entities SET updated_at=?, source_count=source_count+1, "
            "primary_category=COALESCE(primary_category,?), subcategory=COALESCE(subcategory,?), "
            "processing_version=? WHERE entity_id=?",
            (now, cls["primary_category"], cls["subcategory"], config.PROCESSING_VERSION, entity_id))
    else:
        number = db.execute("SELECT COUNT(*) FROM entities").fetchone()[0] + 1
        entity_id = f"ENT-{number:06d}"
        db.execute(
            """INSERT INTO entities(entity_id,name,canonical_name,entity_type,category,
                   subcategory,short_description,detailed_description,confidence,visibility,
                   created_at,updated_at,primary_category,secondary_categories,tags,
                   resource_type,source_count,quality_level,processing_version)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",             (entity_id, name, canonical, entity_type, cls["primary_category"],
             cls["subcategory"], description or "Not yet described.", description,
             confidence, "REVIEW_REQUIRED", now, now, cls["primary_category"],
             json.dumps(cls["secondary_categories"]), json.dumps(_tags(ocr, resource_type)),
             resource_type, 1, ocr.get("quality_level", 1), config.PROCESSING_VERSION))
    db.execute(
        "INSERT OR IGNORE INTO entity_images(entity_id,task_id,evidence) VALUES(?,?,?)",
        (entity_id, task["task_id"],
         "Screenshot evidence; identity reconciled from multi-OCR + vision and web research."))
    db.execute("UPDATE tasks SET entity_id=? WHERE task_id=?", (entity_id, task["task_id"]))
    db.execute(
        "INSERT INTO evidence(task_id,entity_id,kind,label,content,created_at) VALUES(?,?,?,?,?,?)",
        (task["task_id"], entity_id, "resource_identity", "OCR EVIDENCE",
         f"resolved to {canonical or name}", now))
    db.commit()
    return entity_id, {"name": name, "canonical": canonical, "type": entity_type,
                       "description": description, "classification": cls}


def _tags(ocr: dict, resource_type: str | None) -> list[str]:
    text = (ocr.get("final_text", "") + " " + ocr.get("vision_text", "")).lower()
    tags = []
    for kw, tag in [("open source", "Open Source"), ("github", "GitHub"),
                    ("python", "Python"), ("javascript", "JavaScript"),
                    ("docker", "Docker"), ("self-hosted", "Self Hosted"),
                    ("llm", "LLM"), ("ocr", "OCR"), ("ai ", "AI"),
                    ("browser extension", "Browser Extension"), ("cli", "CLI"),
                    ("api", "API"), ("saas", "SaaS"), ("security", "Security"),
                    ("osint", "OSINT"), ("voice", "Voice"), ("automation", "Automation")]:
        if kw in text:
            tags.append(tag)
    if resource_type:
        tags.append(resource_type)
    return sorted(set(tags))


def store_urls(db: sqlite3.Connection, task_id: str, entity_id: str | None,
               urls: list[dict], verified: set[str]) -> None:
    now = config.now()
    for entry in urls:
        url = entry["url"]
        # Upsert: a URL first seen before the resource was resolved must still be
        # linked once an entity exists, without clobbering earlier verification.
        db.execute(
            """INSERT INTO urls(task_id,entity_id,url,url_type,evidence,verified,created_at)
                   VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(task_id,url) DO UPDATE SET
                   entity_id=COALESCE(urls.entity_id, excluded.entity_id),
                   verified=MAX(urls.verified, excluded.verified)""",
            (task_id, entity_id, url, "candidate", json.dumps(entry.get("sources", [])),
             1 if url.lower() in verified else 0, now))


def store_resource_details(db: sqlite3.Connection, entity_id: str, github_meta: dict | None) -> None:
    if not github_meta:
        return
    for feature in _readme_features(github_meta.get("readme_text", "")):
        db.execute("INSERT OR IGNORE INTO features(entity_id,name,evidence,source_kind) VALUES(?,?,?,?)",
                   (entity_id, feature[:200], github_meta["url"], "OFFICIAL SOURCE"))
    lang = (github_meta.get("primaryLanguage") or {}).get("name") if github_meta.get("primaryLanguage") else None
    if lang:
        db.execute("INSERT OR IGNORE INTO technologies(entity_id,name,evidence) VALUES(?,?,?)",
                   (entity_id, lang, github_meta["url"]))
    lic = (github_meta.get("licenseInfo") or {}).get("spdxId") if github_meta.get("licenseInfo") else None
    db.execute("UPDATE entities SET license=COALESCE(?,license), platforms=platforms, updated_at=? WHERE entity_id=?",
               (lic, config.now(), entity_id))
    db.commit()


def _readme_features(readme: str) -> list[str]:
    out = []
    for line in (readme or "").splitlines():
        clean = line.strip()
        if clean.startswith(("- ", "* ")) and 4 < len(clean) < 200:
            out.append(clean[2:].strip())
        if len(out) >= 25:
            break
    return out


def write_ocr_cache(task_id: str, results: list[dict], ocr: dict, quality: dict,
                    github_meta: dict | None, site_meta: dict | None) -> None:
    d = _ocr_dir(task_id)
    (d / "consensus.json").write_text(json.dumps({
        "processing_version": config.PROCESSING_VERSION,
        "task_id": task_id, "generated_at": config.now(),
        "quality": quality, "consensus": ocr,
        "github": {k: v for k, v in (github_meta or {}).items() if k != "readme_text"},
        "official_site": site_meta,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def write_image_markdown(task: dict, results: list[dict], ocr: dict, quality: dict,
                         github_meta: dict | None, site_meta: dict | None,
                         entity_id: str | None, entity_info: dict | None,
                         urls: list[dict]) -> Path:
    path = config.MARKDOWN_IMAGES / f"{task['task_id']}.md"
    cls = (entity_info or {}).get("classification") or {}
    lines = [
        f"# {task['task_id']} — {task['source_filename']}", "",
        "## Processing Status", "",
        f"- Processing version: `{config.PROCESSING_VERSION}`",
        f"- OCR status: **{ocr['status']}**", f"- Quality level: **{ocr.get('quality_level', 0)}**",
        f"- Engines used: {', '.join(ocr.get('engines') or []) or 'none'}",
        f"- Processed at UTC: {config.now()}", "",
        "## Screenshot Identity", "",
        f"- Task ID: `{task['task_id']}`",
        f"- Source filename: `{task['source_filename']}`",
        f"- SHA-256: `{task['sha256']}`",
        f"- Perceptual hash: `{task.get('perceptual_hash') or 'unavailable'}`",
        f"- Dimensions: {task.get('width')} × {task.get('height')}",
        f"- Byte size: {task.get('file_size')}", "",
        "## Source", "",
        f"- Read-only source path: `{task['absolute_source_path']}`",
        "- The original screenshot is immutable and never modified.", "",
        "## Image Metadata", "",
        f"- Mean brightness: {quality.get('mean_brightness')}",
        f"- Contrast (std): {quality.get('contrast_std')}",
        f"- Sharpness (Laplacian var): {quality.get('sharpness_lapvar')}",
        f"- Edge density: {quality.get('edge_density')}",
        f"- Dark UI: {quality.get('dark_ui')}",
        f"- Low contrast: {quality.get('low_contrast')}",
        f"- Blurry: {quality.get('blurry')}", "",
        "## Screenshot Type", "",
        f"- {ocr.get('screenshot_type') or 'unclassified'}", "",
        "## Categories", "",
        "### Primary Category", "",
        f"- {cls.get('primary_category') or 'Other'}", "",
        "### Subcategory", "",
        f"- {cls.get('subcategory') or '—'}", "",
        "### Secondary Categories", "",
        *([f"- {c}" for c in cls.get("secondary_categories", [])] or ["- none"]), "",
        f"- Classification rationale: {cls.get('rationale') or 'n/a'}", "",
        "## OCR Summary", "",
        f"- Consensus status: **{ocr['status']}**",
        f"- Mean engine confidence: {ocr.get('confidence')}",
        f"- Pairwise agreement: {ocr.get('agreement')}", "",
        "## OCR Engine Results", "",
    ]
    for r in results:
        lines += [f"### {r['engine']} ({r.get('engine_version')}, {r['preprocessing']})", "",
                  f"- Latency: {r.get('latency_s')}s",
                  f"- Confidence: {r.get('confidence')}",
                  f"- Regions: {len(r.get('boxes') or [])}",
                  f"- Error: {r.get('error') or 'none'}", "", "```text",
                  (r.get("text") or "[no text]").replace("```", "'''"), "```", ""]
    lines += [
        "## OCR Consensus", "",
        f"- Final interpreted text (richest structured OCR):", "", "```text",
        (ocr.get("final_text") or "[none]").replace("```", "'''"), "```", "",
        "## Visual Evidence", "",
        f"- Vision model: {config.VISION_MODEL if ocr.get('vision_text') else 'not used'}",
        "", "```text", (ocr.get("vision_text") or "[vision not used]").replace("```", "'''"),
        "```", "",
        "## Extracted Text", "",
        "See per-engine sections above; raw OCR is preserved verbatim and is evidence, "
        "not proof of accuracy.", "",
        "## Identified Entities", "",
        f"- Resource ID: {entity_id or 'UNCONFIRMED'}",
        f"- Resource name: {(entity_info or {}).get('name') or 'UNCONFIRMED'}",
        f"- Canonical URL: {(entity_info or {}).get('canonical') or '—'}", "",
        "## URLs", "",
    ]
    lines += [f"- `{u['url']}` (sources: {', '.join(u.get('sources', []))})" for u in urls] or \
             ["- none detected"]
    lines += [
        "", "## Primary Resource", "",
        f"- {(entity_info or {}).get('canonical') or 'UNCONFIRMED'}", "",
        "## Resource Description", "",
        f"- {(entity_info or {}).get('description') or 'Not verified.'}", "",
        "## Features", "",
    ]
    if github_meta:
        lines += [f"- {f}" for f in _readme_features(github_meta.get("readme_text", ""))[:20]] or ["- none extracted"]
    else:
        lines.append("- Not extracted (no verified feature source).")
    lines += ["", "## Usage", "", "- Not independently assessed.", "",
              "## Use Cases", "", "- Not independently assessed.", "",
              "## Technologies", ""]
    lang = (github_meta or {}).get("primaryLanguage") or {}
    lines.append(f"- {lang.get('name')}" if lang.get("name") else "- Not established.")
    lines += ["", "## Developer / Organization", "",
              f"- {(github_meta or {}).get('owner') or 'Not established.'}", "",
              "## Platform", "", "- Not established from cited sources.", "",
              "## License", "",
              f"- {(github_meta.get('licenseInfo') or {}).get('spdxId') if github_meta and github_meta.get('licenseInfo') else 'Not verified.'}",
              "", "## Pricing", "", "- Not verified for this record.", "",
              "## Integrations", "", "- Not established.", "",
              "## Related Resources", "", "- None recorded.", "",
              "## External Research", ""]
    if github_meta:
        lines += [f"- Official GitHub repository: {github_meta['url']}",
                  f"- Description: {github_meta.get('description') or 'not supplied'}",
                  f"- Stars/forks snapshot: {github_meta.get('stargazerCount')}/{github_meta.get('forkCount')} (dynamic)",
                  f"- Metadata updated: {github_meta.get('updatedAt')}",
                  f"- Accessed: {github_meta.get('accessed_at')}"]
    elif site_meta:
        lines += [f"- Official site: {site_meta['url']}",
                  f"- Title: {site_meta.get('title')}", f"- Accessed: {site_meta.get('accessed_at')}"]
    else:
        lines.append("- Not performed or not confidently matched; no factual association asserted.")
    lines += ["", "## Official Sources", ""]
    lines += [f"- {github_meta['url']}"] if github_meta else (
        [f"- {site_meta['url']}"] if site_meta else ["- none"])
    lines += ["", "## Secondary Sources", "", "- none recorded", "",
              "## AI Analysis", "",
              "Vision-model reading and category inference are labelled AI/vision evidence, "
              "not verified facts.", "",
              "## Confidence", "",
              f"- OCR consensus: {ocr['status']} (confidence {ocr.get('confidence')}, agreement {ocr.get('agreement')})",
              f"- Resource identity: {'MEDIUM/HIGH (web-verified)' if (github_meta or site_meta) else 'UNCONFIRMED'}", "",
              "## Uncertainty", "",
              "- Raw OCR and vision text may contain errors; inspect source pixels before relying on details.",
              "- Screenshots remain private (REVIEW_REQUIRED) pending privacy review.", "",
              "## Duplicate / Relationship Information", "",
              f"- Exact duplicates tracked in the database by SHA-256; near-duplicates by perceptual hash.", "",
              "## Provenance", "",
              f"- Output file: `{path}`",
              f"- OCR cache: `{_ocr_dir(task['task_id'])}`",
              f"- Engines: {', '.join(r['engine'] for r in results)}", "",
              "## Processing History", "",
              f"- {config.now()} — v2 multi-OCR pass ({ocr['status']}).", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def process_task(task_id: str, use_vision: bool = True) -> dict[str, Any]:
    db = connect()
    try:
        task = task_row(db, task_id)
        if task is None:
            raise SystemExit(f"Unknown task {task_id}")
        image_path = Path(task["absolute_source_path"])
        if not image_path.is_file():
            log_error(db, task_id, "source", f"missing image {image_path}")
            db.execute("UPDATE tasks SET v2_status='FAILED' WHERE task_id=?", (task_id,))
            db.commit()
            return {"task_id": task_id, "status": "FAILED", "error": "missing source"}
        # Integrity check: never process a file that changed under us.
        digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
        if digest != task["sha256"]:
            log_error(db, task_id, "integrity", "SHA-256 mismatch")
            db.execute("UPDATE tasks SET v2_status='FAILED' WHERE task_id=?", (task_id,))
            db.commit()
            return {"task_id": task_id, "status": "FAILED", "error": "sha256 mismatch"}

        db.execute("UPDATE tasks SET v2_status='OCR_PROCESSING', started_at=COALESCE(started_at,?), "
                   "processing_version=?, updated_at=? WHERE task_id=?",
                   (config.now(), config.PROCESSING_VERSION, config.now(), task_id))
        db.commit()

        quality = preprocess.analyze_quality(image_path)
        results, level = run_ocr_ladder(image_path, quality, task_id, use_vision, db)
        ocr = consensus.reconcile(results)
        ocr["screenshot_type"] = consensus.classify_type(
            ocr.get("final_text", ""), next((r.get("screenshot_type") for r in results
                                             if r["engine"] == "vision"), None))

        # Entity / URL resolution.
        urls = consensus.extract_urls(ocr.get("final_text", ""), ocr.get("vision_text", ""))
        github_meta, site_meta, verified = None, None, set()
        gh_candidates = consensus.github_candidates(ocr.get("final_text", ""), ocr.get("vision_text", ""))
        if gh_candidates:
            owner, repo = gh_candidates[0]
            github_meta = research.github_research(owner, repo)
            if github_meta:
                verified.add(github_meta["url"].lower())
                level = max(level, 6)
        if github_meta is None:
            # Resolve the resource from a URL that is actually visible in the
            # screenshot. The page is fetched and its own metadata confirms the
            # identity, so this is a cited-source fact rather than an inference.
            generic = {"github.com", "gitlab.com", "youtube.com", "youtu.be", "instagram.com",
                       "facebook.com", "twitter.com", "x.com", "reddit.com", "tiktok.com",
                       "google.com", "linkedin.com", "medium.com", "wikipedia.org"}
            name_hint = consensus.vision_product_name(ocr.get("vision_text") or "")
            site_meta, matched = _resolve_site_from_urls(urls, generic, name_hint)
            if site_meta:
                verified.add(matched.lower())
                verified.add(site_meta["url"].lower())
                level = max(level, 4)

        ocr["quality_level"] = max(ocr.get("quality_level", 0), level)
        entity_id, entity_info = resolve_entity(db, task, ocr, github_meta, site_meta)
        store_urls(db, task_id, entity_id, urls, verified)
        if entity_id:
            store_resource_details(db, entity_id, github_meta)
            db.execute("INSERT INTO evidence(task_id,entity_id,kind,label,content,created_at) "
                       "VALUES(?,?,?,?,?,?)",
                       (task_id, entity_id, "resource_identity",
                        "WEB VERIFIED" if (github_meta or site_meta) else "AI INFERENCE",
                        (entity_info or {}).get("canonical") or (entity_info or {}).get("name"),
                        config.now()))
            db.commit()

        # Persist everything.
        write_ocr_cache(task_id, results, ocr, quality, github_meta, site_meta)
        write_image_markdown(task, results, ocr, quality, github_meta, site_meta,
                             entity_id, entity_info, urls)
        db.execute(
            """INSERT INTO ocr_consensus(task_id,final_text,status,confidence,agreement,
                   engines,escalation_level,quality_level,screenshot_type,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(task_id) DO UPDATE SET final_text=excluded.final_text,
                   status=excluded.status, confidence=excluded.confidence,
                   agreement=excluded.agreement, engines=excluded.engines,
                   escalation_level=excluded.escalation_level, quality_level=excluded.quality_level,
                   screenshot_type=excluded.screenshot_type, updated_at=excluded.updated_at""",
            (task_id, ocr.get("final_text"), ocr["status"], ocr.get("confidence"),
             ocr.get("agreement"), json.dumps(ocr.get("engines")), level,
             ocr.get("quality_level", 0), ocr.get("screenshot_type"), config.now()))
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
            """UPDATE tasks SET v2_status=?, ocr_status=?, vision_status=?,
                   quality_level=?, screenshot_type=?, ocr_confidence=?,
                   status=?, completed_at=?, updated_at=?, error=NULL WHERE task_id=?""",
            (final_status, ocr["status"],
             "DONE" if ocr.get("vision_text") else "SKIPPED",
             ocr.get("quality_level", 0), ocr.get("screenshot_type"), ocr.get("confidence"),
             _legacy_status(final_status), config.now(), config.now(), task_id))
        db.execute(
            "INSERT INTO audit_log(timestamp,task_id,entity_id,action,new_value,reason) "
            "VALUES(?,?,?,?,?,?)",
            (config.now(), task_id, entity_id, "V2_MULTI_OCR",
             json.dumps({"status": final_status, "ocr_status": ocr["status"],
                         "engines": ocr.get("engines"), "quality_level": ocr.get("quality_level")}),
             "Multi-engine OCR + vision reconciliation; research applied when identity resolved."))
        db.commit()
        return {"task_id": task_id, "status": final_status, "ocr_status": ocr["status"],
                "entity_id": entity_id, "quality_level": ocr.get("quality_level"),
                "screenshot_type": ocr.get("screenshot_type"),
                "engines": ocr.get("engines"), "urls": len(urls),
                "verified": bool(github_meta or site_meta)}
    finally:
        db.close()


def _legacy_status(v2_status: str) -> str:
    """Map a v2 status onto the legacy tasks.status CHECK constraint."""
    return {
        config.STATUS_COMPLETED: "COMPLETED",
        config.STATUS_OCR_FAILED: "FAILED",
        config.STATUS_OCR_REVIEW: "NEEDS_RESEARCH",
        config.STATUS_NEEDS_RESEARCH: "NEEDS_RESEARCH",
        config.STATUS_NEEDS_VISION: "NEEDS_RESEARCH",
        config.STATUS_FAILED: "FAILED",
    }.get(v2_status, "NEEDS_RESEARCH")
