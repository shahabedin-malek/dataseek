# Search

Search is implemented over the resource corpus and shipped as a static index.

## Server-side (`scripts/v2/query.py`)

`query.search(db, q, category, resource_type, verified_only, limit)` scans the
resource records and scores matches over: name, canonical name, description,
primary/secondary category, subcategory, tags, URLs, features, technologies and
`entity_id`. Matching is case-insensitive substring plus token-AND fallback
(light fuzzy: all query tokens present). Ordering favours prefix matches, then
name matches, then description matches, then source count.

Results are exposed by the backend at `GET /api/search?q=&category=&type=&verified=1&limit=`.

## Client-side (static site)

`scripts/v2/export.py` writes `data/exports/search_index.json` and the lighter
`web/data/dataseek.json` site bundle. The frontend (`web/app.js`, mirrored into
`site/`) performs instant client-side filtering and rendering for the deployed
static site.

## FTS

`entities_fts` (SQLite FTS5) indexes entity names and descriptions and is
available for full-text queries in addition to the scan-based search.

## Rebuild

```bash
.venv/bin/python scripts/v2/export.py     # regenerate index + site bundle
.venv/bin/python scripts/v2/site_build.py  # copy web/ -> site/
```
