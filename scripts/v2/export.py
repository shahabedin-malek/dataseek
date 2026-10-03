"""Regenerate every derived artifact from the database.

Produces: ALL_RESOURCES.md, ALL_SCREENSHOTS.md, search index JSON, the static
site data bundle, and the .progress/ continuation + recovery files.
"""
from __future__ import annotations

import json
from collections import Counter

from . import config, query


def write_exports() -> dict:
    config.ensure_dirs()
    db = query.connect()
    try:
        resources = query.all_resources(db)
        screenshots = query.all_screenshots(db)
        stats = query.statistics(db)
        by_id = {r["entity_id"]: r for r in resources}

        # ---- ALL_RESOURCES.md ----
        lines = ["# DataSeek — All Resources", "",
                 f"Generated {config.now()} · processing `{config.PROCESSING_VERSION}`", "",
                 f"Unique resources: **{len(resources)}** · screenshots: **{stats['screenshots']}**", ""]
        for r in resources:
            lines += [f"## {r['name']} (`{r['entity_id']}`)", "",
                      f"- Type: {r['type'] or 'unknown'}",
                      f"- Category: {r['primary_category']}"
                      + (f" > {r['subcategory']}" if r["subcategory"] else ""),
                      f"- Secondary categories: {', '.join(r['secondary_categories']) or '—'}",
                      f"- Confidence: {r['confidence'] or '—'}",
                      f"- Source screenshots: {r['source_count']} ({', '.join(r['screenshots'][:12]) or 'none'})",
                      f"- URLs: {', '.join(r['urls'][:6]) or '—'}",
                      f"- GitHub: {r['github_url'] or '—'}",
                      f"- Tags: {', '.join(r['tags']) or '—'}",
                      f"- Technologies: {', '.join(r['technologies']) or '—'}",
                      "", (r["short_description"] or "No verified description."), ""]
            if r["features"]:
                lines += ["**Features (from official README/source):**", ""]
                lines += [f"- {f}" for f in r["features"][:15]] + [""]
        (config.EXPORTS / "ALL_RESOURCES.md").write_text("\n".join(lines), encoding="utf-8")

        # ---- ALL_SCREENSHOTS.md (one section per screenshot) ----
        slines = ["# DataSeek — All Screenshots", "",
                  f"Generated {config.now()} · one section per screenshot", "",
                  f"Total screenshots: **{len(screenshots)}**", ""]
        for s in screenshots:
            ent = s["entity"]
            excerpt = " ".join((s["final_text"] or "").split())[:400]
            slines += [f"## {s['task_id']} — {s['filename']}", "",
                       f"- Status: {s['status']} · OCR: {s['ocr_status'] or '—'} · type: {s['screenshot_type'] or '—'}",
                       f"- Quality level: {s['quality_level']} · confidence: {s['confidence']}",
                       f"- Resource: {ent['name'] if ent else 'UNCONFIRMED'}"
                       + (f" (`{ent['entity_id']}`)" if ent else ""),
                       f"- URLs: {', '.join(s['urls'][:8]) or 'none'}",
                       f"- Engines: {', '.join(e['engine'] for e in s['engines']) or 'none'}",
                       f"- Dimensions: {s['width']}×{s['height']} · SHA-256 `{s['sha256'][:16]}…`",
                       f"- OCR excerpt (UNVERIFIED): {excerpt or '[no text]'}", ""]
        (config.EXPORTS / "ALL_SCREENSHOTS.md").write_text("\n".join(slines), encoding="utf-8")

        # ---- Search index + site bundle ----
        index = {
            "generated_at": config.now(),
            "processing_version": config.PROCESSING_VERSION,
            "statistics": stats,
            "resources": resources,
            "screenshots": [
                {k: v for k, v in s.items() if k not in ("final_text", "vision_text")}
                | {"text": " ".join((s["final_text"] or "").split())[:1500]}
                for s in screenshots
            ],
        }
        (config.EXPORTS / "search_index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")

        # Site data bundle (smaller: drops per-screenshot OCR bulk).
        site = {
            "generated_at": config.now(),
            "statistics": stats,
            "resources": resources,
            "screenshots": [
                {k: v for k, v in s.items() if k not in ("final_text", "vision_text")}
                for s in screenshots
            ],
        }
        web_data = config.ROOT / "web" / "data"
        web_data.mkdir(parents=True, exist_ok=True)
        (web_data / "dataseek.json").write_text(
            json.dumps(site, ensure_ascii=False, indent=1), encoding="utf-8")

        # ---- Progress files ----
        _write_progress(stats, resources, screenshots, by_id)
        return {"resources": len(resources), "screenshots": len(screenshots), "stats": stats}
    finally:
        db.close()


def _write_progress(stats: dict, resources: list[dict], screenshots: list[dict],
                    by_id: dict) -> None:
    db = query.connect()
    counts = Counter((s["status"] or "PENDING") for s in screenshots)
    ocr_counts = Counter((s["ocr_status"] or "NONE") for s in screenshots)
    levels = Counter(s["quality_level"] for s in screenshots)
    errors = db.execute(
        "SELECT task_id,stage,message,retry_count,created_at FROM processing_errors "
        "WHERE resolved=0 ORDER BY error_id DESC LIMIT 200").fetchall()
    next_pending = db.execute(
        "SELECT task_id FROM tasks WHERE v2_status IS NULL OR v2_status IN "
        "('PENDING','OCR_PROCESSING','NEEDS_RESEARCH','NEEDS_VISION') ORDER BY task_id LIMIT 1"
    ).fetchone()
    last_done = db.execute(
        "SELECT task_id FROM tasks WHERE processing_version=? AND completed_at IS NOT NULL "
        "ORDER BY completed_at DESC LIMIT 1", (config.PROCESSING_VERSION,)).fetchone()
    db.close()

    total = stats["screenshots"]
    done = stats["v2_processed"]
    pending = total - done

    (config.PROGRESS / "MASTER_PROGRESS.md").write_text("\n".join([
        "# DataSeek Progress", "",
        f"- Updated: {config.now()}",
        f"- Processing version: `{config.PROCESSING_VERSION}`",
        f"- Source: `{config.source_root()}` (immutable, trailing-space path)",
        f"- Screenshots: {total} · v2 processed: {done} · pending: {pending}",
        f"- Unique resources: {stats['resources']}",
        f"- Categories represented: {stats['categories']}",
        f"- GitHub repositories: {stats['github_repositories']}",
        f"- AI tools: {stats['ai_tools']}",
        f"- URLs discovered: {stats['urls']}",
        f"- Open errors: {stats['open_errors']}",
        f"- OCR engine status counts: {dict(ocr_counts)}",
        f"- Quality level distribution: {dict(sorted(levels.items()))}",
        "", "## Engines",
        "- RapidOCR (PP-OCRv6 ONNX, CPU) — primary, high confidence",
        "- Tesseract 5.5 (TSV confidences) — secondary witness",
        "- Vision (qwen3.5:4b via Ollama) — optional, LAN service, used when reachable",
        "", "## Remaining",
        "- Finish v2 OCR pass over all screenshots.",
        "- Apply vision layer when the Ollama service is reachable.",
        "- Re-run research for unresolved entities.",
    ]), encoding="utf-8")

    (config.PROGRESS / "OCR_PROGRESS.md").write_text("\n".join([
        "# OCR Progress", "",
        f"- v2 processed: {done}/{total}",
        f"- Status distribution: {dict(ocr_counts)}",
        f"- Quality levels: {dict(sorted(levels.items()))}",
        f"- Per-image OCR cache: `{config.OCR_DIR}` (one directory per task, one JSON per engine)",
    ]), encoding="utf-8")

    (config.PROGRESS / "TASK_STATUS.md").write_text("\n".join([
        "# Task Status", "",
        f"Total: {total}",
        "", "| Status | Count |", "|---|---:|",
        *[f"| {k} | {v} |" for k, v in sorted(counts.items())],
    ]), encoding="utf-8")

    (config.PROGRESS / "TASK_QUEUE.md").write_text("\n".join([
        "# Task Queue", "",
        f"Total: {total} · pending: {pending}",
        "", "| Task | Filename | Status | OCR | Resource |", "|---|---|---|---|---|",
        *[f"| {s['task_id']} | `{s['filename']}` | {s['status']} | {s['ocr_status'] or '—'} | "
          f"{s['entity']['name'] if s['entity'] else '—'} |" for s in screenshots],
    ]), encoding="utf-8")

    (config.PROGRESS / "ERRORS.md").write_text("\n".join([
        "# Errors and Blockers", "",
        f"- Open errors: {stats['open_errors']}",
        *([f"- {e['task_id'] or '—'} · {e['stage']} · retry={e['retry_count']} · {e['message']} "
           f"({e['created_at']})" for e in errors] or ["- none"]),
        "", "## Known environment issues",
        "- The source directory path ends with a literal space (`/mnt/private-ai-data/Screenshot `).",
        "- The Ollama vision host is a LAN service and can be unavailable; the pipeline degrades to OCR-only.",
    ]), encoding="utf-8")

    dups = query.connect().execute(
        "SELECT a.task_id,a.duplicate_of FROM tasks a WHERE a.duplicate_of IS NOT NULL ORDER BY a.task_id"
    ).fetchall()
    (config.PROGRESS / "DUPLICATES.md").write_text("\n".join([
        "# Duplicate Index", "",
        f"- Exact SHA-256 duplicates: {len(dups)}",
        *[f"- {d['task_id']} → {d['duplicate_of']}" for d in dups],
        "- Near-duplicates are tracked in `duplicate_links`.",
    ]), encoding="utf-8")

    (config.PROGRESS / "RESOURCE_INDEX.md").write_text("\n".join([
        "# Resource Index", "",
        *[f"- {r['entity_id']}: {r['name']} — {r['primary_category']}"
          + (f" > {r['subcategory']}" if r["subcategory"] else "")
          + f" — sources: {r['source_count']}" for r in resources],
    ]), encoding="utf-8")

    (config.PROGRESS / "RESEARCH_PROGRESS.md").write_text("\n".join([
        "# Research Progress", "",
        f"- Resources: {stats['resources']}",
        f"- Web-verified (HIGH/MEDIUM): {stats['verified_resources']}",
        f"- Unverified candidates (LOW): "
        f"{sum(1 for r in resources if r['confidence'] == 'LOW')}",
        f"- Research cache: `{config.RESEARCH_DIR}`",
    ]), encoding="utf-8")

    (config.PROGRESS / "DECISIONS.md").write_text("\n".join([
        "# Decisions", "",
        "- Originals at `/mnt/private-ai-data/Screenshot ` are immutable; all variants live under data/preprocess.",
        "- v2 generation `v2_multi_ocr` supersedes the legacy Tesseract-only pass; legacy outputs retained.",
        "- RapidOCR is the primary engine (highest confidence on this no-AVX2 CPU); Tesseract is a second witness.",
        "- Vision reconciliation never lets majority voting alone decide; raw engine output is always preserved.",
        "- A resource is only created from a candidate product name when OCR and vision independently agree; "
        "it is marked LOW/NEEDS_RESEARCH, never verified.",
        "- GitHub identities require the public repo metadata + README to match; other sites require the official "
        "page to contain the resource name.",
        "- Unreviewed data default to REVIEW_REQUIRED / private.",
    ]), encoding="utf-8")

    (config.PROGRESS / "CATEGORY_TAXONOMY.md").write_text("\n".join([
        "# Category Taxonomy", "",
        "Root categories and subcategories (see docs/CATEGORY_TAXONOMY.md for the full list).",
        "", *[f"- {c['category']}: {c['count']} resources" for c in stats["category_breakdown"]],
    ]), encoding="utf-8")

    last = last_done["task_id"] if last_done else "none"
    nxt = next_pending["task_id"] if next_pending else "none"
    (config.PROGRESS / "CONTINUATION_PROMPT.md").write_text("\n".join([
        "# DataSeek — Continuation Prompt", "",
        f"Updated: {config.now()}",
        "", "## State",
        f"- Project: `{config.ROOT}` · venv: `.venv` (Python 3.12)",
        f"- Source (immutable): `{config.source_root()}`",
        f"- Processing version: `{config.PROCESSING_VERSION}`",
        f"- Screenshots: {total} · processed: {done} · pending: {pending}",
        f"- Unique resources: {stats['resources']} · URLs: {stats['urls']}",
        f"- OCR statuses: {dict(ocr_counts)}",
        f"- Quality levels: {dict(sorted(levels.items()))}",
        f"- Open errors: {stats['open_errors']}",
        "", "## Delivery status",
        "- Database: `database/dataseek.sqlite3` (healthy)",
        "- Website: frontend in `web/`, built bundle in `site/` (dark/light, search + filters + detail pages)",
        "- Backend/Admin: `backend/api.py` (zero-dep API + audited admin at `/admin`)",
        "- GitHub: https://github.com/shahabedin-malek/dataseek",
        "- Vercel: https://dataseek-gules.vercel.app (production, public)",
        "", "## Exact next action",
        f"- LAST_COMPLETED: {last}",
        f"- NEXT: {nxt}",
        "- Run: `.venv/bin/python scripts/process_v2.py batch --limit 100` "
        "(resumes the oldest unprocessed tasks).",
        "- Then: `.venv/bin/python scripts/v2/export.py` to refresh exports/progress.",
        "- When the Ollama vision host is reachable, set DATASEEK_VISION=1 to re-enable the vision layer.",
        "", "## How to resume after a crash/restart",
        "1. Read this file plus .progress/MASTER_PROGRESS.md and .progress/ERRORS.md.",
        "2. Inspect the database: `sqlite3 database/dataseek.sqlite3` "
        "(`SELECT v2_status, COUNT(*) FROM tasks GROUP BY 1`).",
        "3. Any task left in OCR_PROCESSING is re-processed automatically on the next batch run.",
        "4. Regenerate derived files with `scripts/v2/export.py`.",
    ]), encoding="utf-8")


if __name__ == "__main__":
    result = write_exports()
    print(json.dumps({"resources": result["resources"], "screenshots": result["screenshots"]}))
