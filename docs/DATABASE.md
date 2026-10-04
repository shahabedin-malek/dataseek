# Database

Canonical SQLite database: `database/dataseek.sqlite3` (WAL mode). It is
generated and git-ignored. `data/database/dataseek.sqlite` is not used; the
active file is the one above.

The `sqlite3` CLI is **not installed** on this host — inspect with the venv
Python instead:

```bash
.venv/bin/python -c "import sqlite3;d=sqlite3.connect('database/dataseek.sqlite3');print(d.execute('SELECT v2_status,COUNT(*) FROM tasks GROUP BY 1').fetchall())"
```

## Tables

| Table | Purpose |
|---|---|
| `tasks` | one row per screenshot: stable `IMG-nnnn` id, source path/filename, sha256, dimensions, perceptual hash, `v2_status`, `processing_version`, `entity_id`, duplicate pointer, timestamps |
| `ocr_runs` | one row per engine run: engine, version, preprocessing, confidence, char/box counts, text, JSON payload |
| `ocr_regions` | per-region boxes + confidences for each `ocr_run` |
| `ocr_consensus` | reconciled final text, status, confidence, agreement, engines, escalation level |
| `vision_runs` | optional vision-model readings per task |
| `entities` | canonical resources; `entity_id` is the stable resource id |
| `entity_images` | resource ↔ screenshot provenance (many-to-many) |
| `entity_urls`, `urls` | discovered URLs; `urls` keeps the OCR-evidence row, `entity_urls` the canonical set |
| `features`, `technologies`, `tags`, `resource_tags` | structured resource attributes |
| `categories`, `research_sources`, `relationships` | taxonomy, citations, resource links |
| `evidence` | labelled evidence entries (kind + label) |
| `duplicate_links` | exact/near duplicate pairs with method + score |
| `processing_runs`, `processing_errors` | run telemetry and failures |
| `audit_log` | every mutation (pipeline and admin) with old/new values |
| `entities_fts` | FTS5 index over entity names/descriptions |

## Invariants (enforced by `scripts/audit.py`)

- Every source file has exactly one `tasks` row.
- A task only counts as processed when it has a terminal `v2_status` **and** an
  OCR cache directory **and** an `ocr_consensus` row **and** an image Markdown
  record.
- Tasks left in `OCR_PROCESSING` are incomplete and re-queued automatically.
- Every active `entities` row has at least one `entity_images` provenance row.
- `ALL_SCREENSHOTS.md`, `ALL_RESOURCES.md` and `search_index.json` agree with the
  database.
