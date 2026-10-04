# Deployment

## GitHub

- Repository: `git@github.com:shahabedin-malek/dataseek.git`
- Push with `gh`/git only after: `git status`, `git diff`, secret scan, and a
  large-file check. Never commit API keys, tokens, credentials, the private
  screenshot collection, the SQLite database, OCR caches or research caches
  (see `.gitignore`).

## Vercel (static site)

- The deployable bundle is `site/` (built by `scripts/v2/site_build.py`).
- Production: https://dataseek-gules.vercel.app
- Only extracted knowledge is published: `index.html`, `app.js`, `styles.css`,
  `data/dataseek.json`, `robots.txt`, `vercel.json`.
- Original screenshots and OCR caches are **never** copied into `site/`.

### Rebuild and deploy

```bash
.venv/bin/python scripts/v2/export.py       # refresh index + web/data bundle
.venv/bin/python scripts/v2/site_build.py   # rebuild site/
# then deploy site/ to Vercel (only when the database and audit are healthy)
```

## Pre-deploy checks

Before deploying, confirm the pipeline is stable:

```bash
.venv/bin/python scripts/audit.py           # must report 0 hard errors
.venv/bin/python -m pytest -q               # tests pass
```

Do not deploy while the data pipeline is inconsistent, and never publish private
source screenshots unless explicitly configured to do so.

## Current deployment status

- GitHub: pushed and current (`main`).
- Vercel: **live** at https://dataseek-gules.vercel.app (project `dataseek`).
- The project's **Root Directory is `site`**, so every Git push to `main`
  publishes the built static bundle. If the Root Directory is ever reset to the
  repository root, the production alias returns `NOT_FOUND` even though
  `/site/index.html` still resolves — set it back to `site` and redeploy.
- `site/.vercel` (CLI link) and `site/.env.local` (OIDC token) are ignored and
  never published.
