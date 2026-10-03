# DataSeek — Continuation Prompt

Updated: 2026-10-03T21:20:54+00:00

## State
- Project: `/home/chris/dataseek` · venv: `.venv` (Python 3.12)
- Source (immutable): `/mnt/private-ai-data/Screenshot `
- Processing version: `v2_multi_ocr`
- Screenshots: 907 · processed: 213 · pending: 694
- Unique resources: 83 · URLs: 274
- OCR statuses: {'OCR_GOOD': 181, 'NONE': 697, 'OCR_CONFLICTING': 14, 'OCR_NEEDS_VISION': 15}
- Quality levels: {0: 697, 1: 61, 2: 96, 6: 53}
- Open errors: 0

## Delivery status
- Database: `database/dataseek.sqlite3` (healthy)
- Website: frontend in `web/`, built bundle in `site/` (dark/light, search + filters + detail pages)
- Backend/Admin: `backend/api.py` (zero-dep API + audited admin at `/admin`)
- GitHub: https://github.com/shahabedin-malek/dataseek
- Vercel: https://dataseek-gules.vercel.app (production, public)

## Exact next action
- LAST_COMPLETED: IMG-0212
- NEXT: IMG-0002
- Run: `.venv/bin/python scripts/process_v2.py batch --limit 100` (resumes the oldest unprocessed tasks).
- Then: `.venv/bin/python scripts/v2/export.py` to refresh exports/progress.
- When the Ollama vision host is reachable, set DATASEEK_VISION=1 to re-enable the vision layer.

## How to resume after a crash/restart
1. Read this file plus .progress/MASTER_PROGRESS.md and .progress/ERRORS.md.
2. Inspect the database: `sqlite3 database/dataseek.sqlite3` (`SELECT v2_status, COUNT(*) FROM tasks GROUP BY 1`).
3. Any task left in OCR_PROCESSING is re-processed automatically on the next batch run.
4. Regenerate derived files with `scripts/v2/export.py`.