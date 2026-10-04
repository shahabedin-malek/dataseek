# DataSeek — Continuation Prompt

Updated: 2026-10-04T01:32:56+00:00

## State
- Project: `/home/chris/dataseek` · venv: `.venv` (Python 3.12)
- Source (immutable): `/mnt/private-ai-data/Screenshot `
- Processing version: `v2_multi_ocr`
- Records: 907 · processed: 907 · pending: 0
- Unique resources: 324 · URLs: 1126
- OCR statuses: {'OCR_GOOD': 875, 'OCR_CONFLICTING': 12, 'OCR_NEEDS_VISION': 20}
- Quality levels: {1: 272, 2: 331, 3: 1, 4: 145, 6: 158}
- Open errors: 0

## Delivery status
- Database: `database/dataseek.sqlite3` (healthy)
- Website: frontend in `web/`, built bundle in `site/` (dark/light, search + filters + detail pages)
- Backend/Admin: `backend/api.py` (zero-dep API + audited admin at `/admin`)
- GitHub: https://github.com/shahabedin-malek/dataseek
- Vercel: https://dataseek-gules.vercel.app (production, public)

## Exact next action
- LAST_COMPLETED: IMG-0907
- NEXT: IMG-0002
- OCR pass complete (907/907). 70 record(s) still NEEDS_RESEARCH and 16 NEEDS_VISION (no verifiable identity / no vision host).
- Upgrade unresolved records from cached OCR (no re-OCR):
  `.venv/bin/python scripts/resolve_cached.py`
- Refresh exports/progress/site: `.venv/bin/python scripts/v2/export.py` then `.venv/bin/python scripts/v2/site_build.py`.
- Verify everything: `.venv/bin/python scripts/audit.py` (must report 0 hard errors).
- When the Ollama vision host is reachable, set DATASEEK_VISION=1 to re-enable the vision layer.

## How to resume after a crash/restart
1. Read this file plus .progress/MASTER_PROGRESS.md and .progress/ERRORS.md.
2. Inspect the database with Python (the `sqlite3` CLI is not installed): `.venv/bin/python -c "import sqlite3;d=sqlite3.connect('database/dataseek.sqlite3');print(d.execute('SELECT v2_status,COUNT(*) FROM tasks GROUP BY 1').fetchall())"`.
3. Tasks left in OCR_PROCESSING are re-selected and re-processed by the batch query (they have a version stamp but no artifacts).
4. Regenerate derived files with `scripts/v2/export.py`; confirm with `scripts/audit.py`.