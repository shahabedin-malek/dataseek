# DataSeek — Continuation Prompt

Updated: 2026-10-03T20:44:13+00:00

## State
- Project: `/home/chris/dataseek` · venv: `.venv` (Python 3.12)
- Source (immutable): `/mnt/private-ai-data/Screenshot `
- Processing version: `v2_multi_ocr`
- Screenshots: 907 · processed: 61 · pending: 846
- Unique resources: 67 · URLs: 40
- OCR statuses: {'OCR_CONFLICTING': 19, 'OCR_GOOD': 38, 'NONE': 849, 'OCR_NEEDS_VISION': 1}
- Quality levels: {0: 849, 1: 1, 2: 27, 3: 23, 5: 2, 6: 5}
- Open errors: 0

## Exact next action
- LAST_COMPLETED: IMG-0060
- NEXT: IMG-0002
- Run: `.venv/bin/python scripts/process_v2.py batch --limit 100` (resumes the oldest unprocessed tasks).
- Then: `.venv/bin/python scripts/v2/export.py` to refresh exports/progress.
- When the Ollama vision host is reachable, set DATASEEK_VISION=1 to re-enable the vision layer.

## How to resume after a crash/restart
1. Read this file plus .progress/MASTER_PROGRESS.md and .progress/ERRORS.md.
2. Inspect the database: `sqlite3 database/dataseek.sqlite3` (`SELECT v2_status, COUNT(*) FROM tasks GROUP BY 1`).
3. Any task left in OCR_PROCESSING is re-processed automatically on the next batch run.
4. Regenerate derived files with `scripts/v2/export.py`.