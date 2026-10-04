"""Regenerate every derived artifact from the database.

Produces: ALL_RESOURCES.md, ALL_SCREENSHOTS.md, search index JSON, the static
site data bundle, and the .progress/ continuation + recovery files.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

# Allow both `python -m v2.export` and `python scripts/v2/export.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v2 import config, query  # noqa: E402


# Fields published for one evidence record. The internal source filename,
# SHA-256 and perceptual hash are deliberately excluded: they are local
# provenance artefacts, not published knowledge.
PUBLIC_RECORD_FIELDS = (
    "task_id", "record_type", "ocr_status", "confidence", "quality_level",
    "entity", "urls", "engines", "status", "visibility", "width", "height",
)


def public_record(record: dict) -> dict:
    return {k: record.get(k) for k in PUBLIC_RECORD_FIELDS}


def write_exports() -> dict:
    config.ensure_dirs()
    db = query.connect()
    try:
        resources = query.all_resources(db)
        screenshots = query.all_records(db)
        stats = query.statistics(db)
        by_id = {r["entity_id"]: r for r in resources}

        # ---- ALL_RESOURCES.md ----
        lines = ["# DataSeek — All Resources", "",
                 f"Generated {config.now()} · processing `{config.PROCESSING_VERSION}`", "",
                 f"Unique resources: **{len(resources)}** · source records: **{stats['records']}**", ""]
        for r in resources:
            lines += [f"## {r['name']} (`{r['entity_id']}`)", "",
                      f"- Type: {r['type'] or 'unknown'}",
                      f"- Category: {r['primary_category']}"
                      + (f" > {r['subcategory']}" if r["subcategory"] else ""),
                      f"- Secondary categories: {', '.join(r['secondary_categories']) or '—'}",
                      f"- Confidence: {r['confidence'] or '—'}",
                      f"- Source records: {r['source_count']} ({', '.join(r['records'][:12]) or 'none'})",
                      f"- URLs: {', '.join(r['urls'][:6]) or '—'}",
                      f"- GitHub: {r['github_url'] or '—'}",
                      f"- Tags: {', '.join(r['tags']) or '—'}",
                      f"- Technologies: {', '.join(r['technologies']) or '—'}",
                      "", (r["short_description"] or "No verified description."), ""]
            if r["features"]:
                lines += ["**Features (from official README/source):**", ""]
                lines += [f"- {f}" for f in r["features"][:15]] + [""]
        (config.EXPORTS / "ALL_RESOURCES.md").write_text("\n".join(lines), encoding="utf-8")

        # ---- ALL_SCREENSHOTS.md (one section per source record) ----
        filenames = {r[0]: r[1] for r in db.execute(
            "SELECT task_id, source_filename FROM tasks")}
        slines = ["# DataSeek — All Source Records", "",
                  f"Generated {config.now()} · one section per source record", "",
                  f"Total records: **{len(screenshots)}**", ""]
        for s in screenshots:
            ent = s["entity"]
            excerpt = " ".join((s["final_text"] or "").split())[:400]
            slines += [f"## {s['task_id']} — {filenames.get(s['task_id'], '')}", "",
                       f"- Status: {s['status']} · OCR: {s['ocr_status'] or '—'} · type: {s['record_type'] or '—'}",
                       f"- Quality level: {s['quality_level']} · confidence: {s['confidence']}",
                       f"- Resource: {ent['name'] if ent else 'UNCONFIRMED'}"
                       + (f" (`{ent['entity_id']}`)" if ent else ""),
                       f"- URLs: {', '.join(s['urls'][:8]) or 'none'}",
                       f"- Engines: {', '.join(e['engine'] for e in s['engines']) or 'none'}",
                       f"- Dimensions: {s['width']}×{s['height']}",
                       f"- OCR excerpt (UNVERIFIED): {excerpt or '[no text]'}", ""]
        (config.EXPORTS / "ALL_SCREENSHOTS.md").write_text("\n".join(slines), encoding="utf-8")

        # ---- Search index + site bundle ----
        index = {
            "generated_at": config.now(),
            "processing_version": config.PROCESSING_VERSION,
            "statistics": stats,
            "resources": resources,
            "records": [
                public_record(s) | {"text": " ".join((s["final_text"] or "").split())[:1500]}
                for s in screenshots
            ],
        }
        (config.EXPORTS / "search_index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")

        # Site data bundle (smaller: drops per-record OCR bulk).
        site = {
            "generated_at": config.now(),
            "statistics": stats,
            "resources": resources,
            "records": [public_record(s) for s in screenshots],
        }
        web_data = config.ROOT / "web" / "data"
        web_data.mkdir(parents=True, exist_ok=True)
        (web_data / "dataseek.json").write_text(
            json.dumps(site, ensure_ascii=False, indent=1), encoding="utf-8")

        # ---- Progress files ----
        _write_progress(stats, resources, screenshots, by_id)
        return {"resources": len(resources), "records": len(screenshots), "stats": stats}
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

    total = stats["records"]
    done = stats["v2_processed"]
    pending = total - done
    needs_research = counts.get(config.STATUS_NEEDS_RESEARCH, 0)
    needs_vision = counts.get(config.STATUS_NEEDS_VISION, 0)

    (config.PROGRESS / "MASTER_PROGRESS.md").write_text("\n".join([
        "# DataSeek Progress", "",
        f"- Updated: {config.now()}",
        f"- Processing version: `{config.PROCESSING_VERSION}`",
        f"- Source: `{config.source_root()}` (immutable, trailing-space path)",
        f"- Records: {total} · v2 processed: {done} · pending: {pending}",
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
        "- Vision layer — disabled by project policy (OCR-only; no image leaves the host)",
        "", "## Remaining",
        ("- Finish the v2 OCR pass over all source records." if pending > 0
         else f"- OCR pass complete ({done}/{total}); no pending records."),
        (f"- {needs_research} record(s) NEEDS_RESEARCH (candidate identity, not yet web-verified)."
         if needs_research else "- No records awaiting research."),
        (f"- {needs_vision} record(s) were flagged NEEDS_VISION but the vision layer is disabled by "
         "policy; they are held for review rather than guessed."
         if needs_vision else "- Vision layer disabled by policy; no record depends on it."),
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
        "", "| Record | Type | Status | OCR | Resource |", "|---|---|---|---|---|",
        *[f"| {s['task_id']} | {s['record_type'] or '—'} | {s['status']} | {s['ocr_status'] or '—'} | "
          f"{s['entity']['name'] if s['entity'] else '—'} |" for s in screenshots],
    ]), encoding="utf-8")

    (config.PROGRESS / "ERRORS.md").write_text("\n".join([
        "# Errors and Blockers", "",
        f"- Open errors: {stats['open_errors']}",
        *([f"- {e['task_id'] or '—'} · {e['stage']} · retry={e['retry_count']} · {e['message']} "
           f"({e['created_at']})" for e in errors] or ["- none"]),
        "", "## Known environment issues",
        "- The source directory path ends with a literal space (`/mnt/private-ai-data/Screenshot `).",
        "- The vision layer is disabled by project policy (Ollama is not permitted); the pipeline is OCR-only.",
        "- Vercel production is live at `https://dataseek-gules.vercel.app`. The project Root Directory "
        "must stay set to `site`; if it is reset to the repository root the alias returns NOT_FOUND "
        "while `/site/index.html` still resolves.",
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
        "- A resource may also be resolved from a URL that is visibly present in the screenshot: "
        "the page is fetched and its own metadata (og:site_name/title) supplies the name "
        "(`name_source=page_metadata`), so identity is cited, not inferred.",
        "- Tasks stamped with a processing version but left in OCR_PROCESSING are treated as "
        "incomplete and re-queued, because they have no artifacts.",
        "- urls.entity_id is backfilled/upserted once a screenshot's resource is resolved.",
        "- A GitHub owner rename is never accepted silently: the returned canonical URL must match the "
        "visible one, so truncated OCR cannot resolve to an unrelated repository.",
        "- resolve_cached also treats a github.com/owner/repo URL already extracted from the media as "
        "candidate evidence (a cited link, not an inference).",
        "- Rows with no verifiable identity stay unresolved (level 1-2) rather than guessed.",
        "- The vision layer is disabled by project policy (Ollama is not permitted); OCR-only is terminal.",
        "- Records flagged NEEDS_VISION when vision was still wired up were reclassified to OCR_REVIEW "
        "(held for review) rather than left permanently blocked.",
        "- GitHub-canonical candidates are corroborated with repository metadata + README via the "
        "authenticated gh CLI; README features and the license fall back to the human name when no "
        "SPDX id exists.",
        "- The Vercel project `dataseek` deploys from Root Directory `site`, so Git pushes publish the "
        "built static bundle rather than the repository root.",
    ]), encoding="utf-8")

    (config.PROGRESS / "CATEGORY_TAXONOMY.md").write_text("\n".join([
        "# Category Taxonomy", "",
        "Root categories and subcategories (see docs/CATEGORY_TAXONOMY.md for the full list).",
        "", *[f"- {c['category']}: {c['count']} resources" for c in stats["category_breakdown"]],
    ]), encoding="utf-8")

    last = last_done["task_id"] if last_done else "none"
    nxt = next_pending["task_id"] if next_pending else "none"
    if pending <= 0:
        ocr_action = (f"- OCR pass complete ({done}/{total}). "
                      f"{needs_research} record(s) still NEEDS_RESEARCH and "
                      f"{needs_vision} NEEDS_VISION (no verifiable identity / no vision host).")
    else:
        ocr_action = ("- Run the OCR pass via the resilient supervisor (auto-resumes until done):\n"
                      "  `nohup bash scripts/run_batch_supervisor.sh > logs/v2_supervisor.log 2>&1 &`")
    (config.PROGRESS / "CONTINUATION_PROMPT.md").write_text("\n".join([
        "# DataSeek — Continuation Prompt", "",
        f"Updated: {config.now()}",
        "", "## State",
        f"- Project: `{config.ROOT}` · venv: `.venv` (Python 3.12)",
        f"- Source (immutable): `{config.source_root()}`",
        f"- Processing version: `{config.PROCESSING_VERSION}`",
        f"- Records: {total} · processed: {done} · pending: {pending}",
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
        ocr_action,
        "- Upgrade unresolved records from cached OCR (no re-OCR):",
        "  `.venv/bin/python scripts/resolve_cached.py`",
        "- Refresh exports/progress/site: `.venv/bin/python scripts/v2/export.py` "
        "then `.venv/bin/python scripts/v2/site_build.py`.",
        "- Verify everything: `.venv/bin/python scripts/audit.py` (must report 0 hard errors).",
        "- Vision layer is disabled by project policy (Ollama not permitted); OCR-only is the terminal design.",
        "", "## How to resume after a crash/restart",
        "1. Read this file plus .progress/MASTER_PROGRESS.md and .progress/ERRORS.md.",
        "2. Inspect the database with Python (the `sqlite3` CLI is not installed): "
        "`.venv/bin/python -c \"import sqlite3;d=sqlite3.connect('database/dataseek.sqlite3');"
        "print(d.execute('SELECT v2_status,COUNT(*) FROM tasks GROUP BY 1').fetchall())\"`.",
        "3. Tasks left in OCR_PROCESSING are re-selected and re-processed by the batch"
        " query (they have a version stamp but no artifacts).",
        "4. Regenerate derived files with `scripts/v2/export.py`; confirm with `scripts/audit.py`.",
    ]), encoding="utf-8")


if __name__ == "__main__":
    result = write_exports()
    print(json.dumps({"resources": result["resources"], "records": result["records"]}))
