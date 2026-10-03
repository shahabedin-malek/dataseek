#!/usr/bin/env python3
"""Create DataSeek's durable project scaffold and provenance-first SQLite schema."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRS = (
    "progress", "tasks", "data/images", "data/entities", "data/research",
    "data/master", "database", "docs", "scripts", "web", "backend", "logs",
    "continuation", "reports", "exports",
)
UTC_NOW = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_initial(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content.rstrip() + "\n", encoding="utf-8")


def main() -> None:
    for directory in DIRS:
        (ROOT / directory).mkdir(parents=True, exist_ok=True)

    write_initial(ROOT / ".gitignore", """.venv/\n__pycache__/\n*.py[cod]\n.env\n.env.*\n!.env.example\nnode_modules/\n.next/\n.vercel/\n*.sqlite\n*.sqlite3\n*.db\nlogs/*\n!logs/.gitkeep\ndata/images/*.jpg\ndata/images/*.jpeg\ndata/images/*.png\ndata/images/*.webp\ndata/images/*.json\ndata/entities/*.md\ndata/research/*\ndata/master/ALL_SCRAPED_DATA.json\ndata/master/ALL_SCRAPED_DATA.csv\nexports/*\n""")
    write_initial(ROOT / ".env.example", "# No secrets are currently required for local manifest scanning.\n# Configure deployment/authentication values only when those services are selected.\n")
    write_initial(ROOT / "README.md", """# DataSeek\n\nA local-first visual information research and search system. The project is designed to inventory screenshots, preserve image-to-fact provenance, identify exact duplicates, and progressively build a private-by-default searchable knowledge base.\n\n## Current status\n\nThe durable project scaffold and source-manifest tooling are being established. See [`continuation/CONTINUE.md`](continuation/CONTINUE.md) and [`progress/PROGRESS.md`](progress/PROGRESS.md) for verified counts, blockers, and the next action. The source screenshots are not copied into this repository.\n\n## Local setup\n\nRequires Python 3.10+ and Pillow for image dimensions and perceptual hashes. See [`docs/LOCAL_SETUP.md`](docs/LOCAL_SETUP.md). Source images are treated as read-only.\n\n## Privacy\n\nUnreviewed records default to `REVIEW_REQUIRED`. Never publish private screenshots, personal information, or source assets without explicit review.\n""")
    write_initial(ROOT / "docs/ENVIRONMENT.md", """# Verified Environment\n\nInspection date: 2026-10-03 (UTC timestamp recorded in progress files).\n\n- Operating system: Linux (observed mounted ext4 filesystems).\n- Project path: `/home/chris/dataseek` (current workspace).\n- Python: 3.14.4.\n- Node.js: v24.21.0; npm: 11.19.0; pnpm: 11.15.1.\n- Git: 2.53.0. The workspace was not a Git repository at inspection time.\n- GitHub CLI: 2.46.0; `gh auth status` reported an authenticated account. No token or credential value is recorded here.\n- curl: 8.18.0; wget: 1.25.0.\n- Pillow: 12.1.1; ImageMagick: 7.1.2-18; SQLite Python module: 3.46.1.\n- `tesseract`, `ocrmypdf`, `gocr`, `cuneiform`, `easyocr`, `paddleocr`, `torch`, `transformers`, `opencv`, `imagehash`, FastAPI, Uvicorn, and pytest were not found in the inspected PATH/Python environment.\n- Google Chrome and Firefox executables are present. `sqlite3` CLI was not found.\n- Environment inspection found no relevant service/API environment variables; values were never printed.\n- Root filesystem: 117 GiB total, 36 GiB free at inspection.\n\n## Source mount discrepancy\n\nThe configured path `/mnt/private-ai-data/Screenshot/` does not resolve. A read-only inventory found the actual directory `/mnt/private-ai-data/Screenshot `, including a literal trailing space in its name. It contained 907 `.jpg` files at inspection. The scanner uses the configured path first and falls back to that exact sibling directory only when the configured path is absent. It does not rename or modify the source.\n\nAvailability is a point-in-time observation. Re-run `python3 scripts/scan_sources.py` to reconcile the inventory. OCR is currently unavailable; no OCR results should be claimed until a backend is installed and verified.\n""")

    docs = {
        "LOCAL_SETUP.md": "# Local Setup\n\nRun from `/home/chris/dataseek`. Python 3.14.4 and Pillow 12.1.1 are available. Inspect `docs/ENVIRONMENT.md` for verified tools and the screenshot mount discrepancy.\n\nRun `python3 scripts/bootstrap_project.py` to create missing directories/schema, then `python3 scripts/scan_sources.py` to read-only scan the source directory. No package installation is needed for manifest scanning. Original screenshots must remain untouched.\n",
        "DEPLOYMENT.md": "# Deployment\n\nNot configured or deployed. No hosting provider, domain, production data policy, or backend architecture has been selected or verified. Keep all screenshots and records local/private pending privacy review.\n",
        "DATABASE.md": "# Database\n\nThe local canonical database is SQLite at `database/dataseek.sqlite3` (generated, ignored by Git). The bootstrap defines images/tasks/entities/URLs/research sources/relationships/audit history and an FTS5 entity index where SQLite supports FTS5. Image paths, task states, and source provenance remain explicit.\n",
        "INGESTION.md": "# Ingestion\n\n`python3 scripts/scan_sources.py` recursively enumerates supported image files, calculates SHA-256, dimensions, and an 8x8 difference hash, and assigns stable `IMG-NNNN` task IDs. Source files are opened read-only. Exact hash duplicates point at a canonical task and are not silently discarded. OCR and semantic research are not performed by the inventory scan.\n",
        "SEARCH.md": "# Search\n\nSearch is not yet implemented. The SQLite schema reserves a full-text index; search endpoints and a visual frontend must be built after ingestion and privacy controls are functional.\n",
        "ADMIN.md": "# Administration\n\nAuthenticated admin functionality, audit-backed edits, soft deletion, merge/split, and restore are not yet implemented. Do not expose a public write API before authentication and authorization exist.\n",
        "ARCHITECTURE.md": "# Architecture\n\nCurrent stage: local-first Python source inventory and SQLite persistence. Original images stay at their source mount. Per-image analysis and entity research must preserve evidence citations and differentiate screenshot facts, external research, AI analysis, and uncertainty. Future frontend/API and deployment choices are pending.\n",
        "DATA_MODEL.md": "# Data Model\n\nCore relations: tasks/images → entities; screenshot provenance via entity-image links; external research via research_sources; canonical/observed links via entity_urls; entity relationships; and audit_log. Uncertain visibility defaults to `REVIEW_REQUIRED`. Task IDs are stable `IMG-NNNN` values assigned from sorted relative source paths.\n",
        "PRIVACY.md": "# Privacy\n\nAll source screenshots are private source material. Keep them read-only and never commit or deploy them. Every image begins `REVIEW_REQUIRED`. Do not publish credentials, personal contact details, private messages, personal addresses, or other sensitive information. Public eligibility requires explicit review; uncertain material remains private.\n",
    }
    for name, text in docs.items():
        write_initial(ROOT / "docs" / name, text)

    progress = {
        "progress/PROGRESS.md": "# DataSeek Progress\n\n- Project root: `/home/chris/dataseek`\n- Source configured: `/mnt/private-ai-data/Screenshot/` (missing); observed fallback `/mnt/private-ai-data/Screenshot ` (trailing space).\n- Existing recovery prompt read: `RESUME-PROMPT.md`; no previous task/database state was present.\n- Current phase: environment inspection and durable pipeline scaffold.\n- Counts are pending the first manifest scan; do not assume 907 until scanner reconciliation.\n- No image content analysis, OCR, external research, website, GitHub repository, or deployment has yet been completed.\n",
        "progress/SOURCE_MANIFEST.md": "# Source Manifest\n\nNot scanned yet. Run `python3 scripts/scan_sources.py`; it will record one stable task per actual supported image and the verified source path. The configured path is currently missing and a directory with a trailing-space suffix was discovered.\n",
        "progress/TASK_QUEUE.md": "# Task Queue\n\nTasks have not yet been generated. Run `python3 scripts/scan_sources.py` to enumerate the actual image files.\n",
        "progress/DEDUPLICATION.md": "# Deduplication\n\nNo image hashes have been scanned yet. Exact-file duplicate relationships will be recorded during source inventory. Entity-level duplicate decisions require content analysis and will not be inferred from image similarity alone.\n",
        "progress/ERRORS.md": "# Errors and Blockers\n\n- Configured screenshot directory `/mnt/private-ai-data/Screenshot/` is absent. An apparent intended source exists at `/mnt/private-ai-data/Screenshot ` (trailing space); inventory will use it read-only and document the discrepancy.\n- No OCR executable or OCR Python package was detected. OCR-dependent tasks must remain pending/unverified until capability is available.\n- The workspace did not initially contain a Git repository or application code.\n",
        "progress/DECISIONS.md": "# Decisions\n\n- Preserve `/mnt/private-ai-data/Screenshot ` exactly as mounted; do not rename it or mutate its contents.\n- Prefer stdlib Python, Pillow, and SQLite initially; avoid adding dependencies before the source inventory exists.\n- Use `IMG-NNNN` stable task IDs and sort source-relative paths for initial deterministic assignment.\n- Default visibility to `REVIEW_REQUIRED`; never publish source images by default.\n- Hash/dHash duplicate detection is not proof of a shared product/entity; only exact-byte matches are auto-marked as file duplicates.\n- Do not create a remote GitHub repository or deploy before implementation, privacy review, and a verified project identity.\n",
        "progress/STATISTICS.md": "# Statistics\n\nCounts are unverified until `python3 scripts/scan_sources.py` completes.\n\n- Total image tasks: pending scan\n- Completed analyses: 0\n- Pending analyses: pending scan\n- Exact file duplicates: pending scan\n- Unique entities/research records: 0\n- OCR: unavailable in inspected environment\n- Database: schema scaffold pending\n- Website/GitHub/Vercel: not implemented/not configured/not deployed\n",
        "continuation/LAST_STATE.md": "# Last State\n\n- Date: 2026-10-03\n- Project: `/home/chris/dataseek`\n- Configured screenshots: `/mnt/private-ai-data/Screenshot/` (not found)\n- Observed fallback: `/mnt/private-ai-data/Screenshot ` (literal trailing space); observed file count: 907 JPG files\n- Manifest/task outputs: not yet generated\n- Completed image analyses: 0\n- Current task: none; initial source scan is next\n- Database: scaffold pending\n- Website: not started\n- GitHub: CLI authenticated, repository not created\n- Vercel: not inspected/configured; no deployment\n- Blocker: OCR unavailable; source path typo/mount discrepancy documented\n- Next exact action: `python3 scripts/bootstrap_project.py && python3 scripts/scan_sources.py`\n",
        "continuation/CONTINUE.md": "# Ready-to-paste continuation prompt\n\nResume DataSeek at `/home/chris/dataseek`. Do not restart or reprocess valid completed work. First inspect `progress/PROGRESS.md`, `progress/TASK_QUEUE.md`, `progress/SOURCE_MANIFEST.md`, `progress/STATISTICS.md`, `continuation/LAST_STATE.md`, and this file, then reconcile the filesystem/database. Original screenshots are read-only. The configured `/mnt/private-ai-data/Screenshot/` path was missing; the observed directory is `/mnt/private-ai-data/Screenshot ` with a literal trailing space and was observed to contain 907 JPG files. Run `python3 scripts/bootstrap_project.py` if required and then `python3 scripts/scan_sources.py` to establish exact manifest/task counts. OCR was unavailable at last inspection, so do not claim OCR or content understanding. The first task is the source inventory, not image research. Continue with the first valid pending `IMG-NNNN`; persist results after each image. Keep uncertain visibility REVIEW_REQUIRED and never publish private screenshots. GitHub CLI authentication exists but no repository has been created; no website or deployment exists. Record every blocker and update continuation files with verified counts.\n",
    }
    for relative, text in progress.items():
        write_initial(ROOT / relative, text)

    for relative, text in {
        "data/master/ALL_SCRAPED_DATA.md": "# DataSeek — Master Image Research Records\n\nNo screenshots have been analyzed yet. Source inventory is pending.\n",
        "data/master/ALL_SCRAPED_DATA.json": '{"schema_version":1,"records":[]}\n',
        "data/master/ALL_SCRAPED_DATA.csv": "image_id,filename,entity_id,entity_name,type,urls,description,usage,features,research,confidence,duplicate_status,source_evidence\n",
    }.items():
        write_initial(ROOT / relative, text)

    db = ROOT / "database" / "dataseek.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.executescript("""
        PRAGMA foreign_keys = ON;
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY, source_filename TEXT NOT NULL, absolute_source_path TEXT NOT NULL,
            relative_source_path TEXT NOT NULL, extension TEXT NOT NULL, file_size INTEGER NOT NULL,
            width INTEGER, height INTEGER, sha256 TEXT NOT NULL, perceptual_hash TEXT,
            status TEXT NOT NULL CHECK(status IN ('PENDING','IN_PROGRESS','COMPLETED','DUPLICATE','NOT_RELEVANT','NEEDS_RESEARCH','RETRY','FAILED','UNREADABLE','VERIFIED')),
            created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT, retry_count INTEGER NOT NULL DEFAULT 0,
            duplicate_of TEXT REFERENCES tasks(task_id), entity_id TEXT, research_status TEXT NOT NULL DEFAULT 'PENDING',
            output_file TEXT, error TEXT, notes TEXT, visibility TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
            FOREIGN KEY(entity_id) REFERENCES entities(entity_id) DEFERRABLE INITIALLY DEFERRED
        );
        CREATE TABLE IF NOT EXISTS entities (
            entity_id TEXT PRIMARY KEY, name TEXT NOT NULL, canonical_name TEXT, entity_type TEXT,
            category TEXT, subcategory TEXT, short_description TEXT, detailed_description TEXT,
            confidence TEXT CHECK(confidence IS NULL OR confidence IN ('HIGH','MEDIUM','LOW')),
            visibility TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED', is_invalid INTEGER NOT NULL DEFAULT 0,
            deleted_at TEXT, deleted_by TEXT, deletion_reason TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS entity_images (
            entity_id TEXT NOT NULL REFERENCES entities(entity_id), task_id TEXT NOT NULL REFERENCES tasks(task_id),
            evidence TEXT, PRIMARY KEY(entity_id, task_id)
        );
        CREATE TABLE IF NOT EXISTS entity_urls (
            url_id INTEGER PRIMARY KEY, entity_id TEXT NOT NULL REFERENCES entities(entity_id),
            url TEXT NOT NULL, url_type TEXT, verified INTEGER NOT NULL DEFAULT 0,
            source_task_id TEXT REFERENCES tasks(task_id), research_source_id INTEGER, UNIQUE(entity_id,url)
        );
        CREATE TABLE IF NOT EXISTS research_sources (
            research_source_id INTEGER PRIMARY KEY, entity_id TEXT NOT NULL REFERENCES entities(entity_id),
            url TEXT, title TEXT, source_type TEXT, accessed_at TEXT NOT NULL, notes TEXT,
            source_task_id TEXT REFERENCES tasks(task_id)
        );
        CREATE TABLE IF NOT EXISTS relationships (
            relationship_id INTEGER PRIMARY KEY, source_entity_id TEXT NOT NULL REFERENCES entities(entity_id),
            target_entity_id TEXT NOT NULL REFERENCES entities(entity_id), relation_type TEXT NOT NULL,
            evidence TEXT, source_task_id TEXT REFERENCES tasks(task_id), UNIQUE(source_entity_id,target_entity_id,relation_type)
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            audit_id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, entity_id TEXT REFERENCES entities(entity_id),
            task_id TEXT REFERENCES tasks(task_id), actor TEXT, action TEXT NOT NULL,
            old_value TEXT, new_value TEXT, reason TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_sha256 ON tasks(sha256);
        CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
        """)
        try:
            connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(entity_id UNINDEXED, name, aliases, description, ocr_text, urls, features, usage, tags, category, technologies, people, organizations)")
        except sqlite3.OperationalError:
            pass
    print(f"Project scaffold ready: {ROOT}")
    print(f"SQLite schema initialized: {db}")


if __name__ == "__main__":
    main()
