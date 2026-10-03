# DataSeek

A screenshot-derived knowledge search engine. DataSeek turns a private collection of
screenshots into a structured, searchable, visual database of the **resources** those
screenshots show — tools, GitHub repositories, apps, websites and extensions — with full
provenance back to the source images and the OCR/vision evidence behind every claim.

The original screenshots are treated as **immutable source evidence** and never modified
or published.

## How it works

```
screenshot → quality analysis → multi-OCR (RapidOCR + Tesseract)
           → optional vision reading → consensus reconciliation
           → entity / URL resolution → web research → category
           → Markdown record + SQLite + exports + progress checkpoint
```

See [`docs/OCR_ARCHITECTURE.md`](docs/OCR_ARCHITECTURE.md) and
[`docs/OCR_TOOL_EVALUATION.md`](docs/OCR_TOOL_EVALUATION.md).

## Processing generations

- `v2_multi_ocr` — the current generation (this repository).
- Legacy Tesseract-only output is retained under `legacy/` as low-confidence evidence and
  never overrides v2 results.

## Layout

| Path | Purpose |
|---|---|
| `scripts/v2/` | v2 pipeline (config, schema, preprocess, engines, vision, consensus, taxonomy, research, pipeline, query, export, site_build) |
| `scripts/process_v2.py` | CLI: `process`, `batch`, `status`, `benchmark` |
| `scripts/v2/export.py` | Regenerate all exports, search index and progress files |
| `scripts/reclassify.py` | Re-run classification after taxonomy changes |
| `backend/api.py` | Zero-dependency HTTP API + admin backend |
| `backend/admin.html` | Admin UI (edit / merge / restore / reprocess, audited) |
| `web/` | Frontend source (served locally by the API) |
| `site/` | Built static site for deployment |
| `data/` | OCR cache, Markdown records, research cache, exports, database |
| `.progress/` | Continuation prompt, progress, errors, decisions, taxonomy |

## Local setup

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python rapidocr onnxruntime pillow opencv-python
```

Tesseract 5 (`apt install tesseract-ocr`) is the secondary engine. The vision layer uses a
local/LAN Ollama model (`OLLAMA_HOST`, default `qwen3.5:4b`); it is optional and the
pipeline degrades gracefully to OCR-only when unreachable.

## Usage

```bash
.venv/bin/python scripts/process_v2.py status
.venv/bin/python scripts/process_v2.py process IMG-0001
.venv/bin/python scripts/process_v2.py batch --limit 100
.venv/bin/python -c "import sys;sys.path.insert(0,'scripts');from v2 import export;export.write_exports()"
.venv/bin/python backend/api.py --port 8787      # site + API + /admin
```

## Privacy

Unreviewed records default to `REVIEW_REQUIRED`. Original screenshots, raw per-image OCR
and the SQLite database stay local (see `.gitignore`). Only extracted resource knowledge
is published by the built site. OCR and vision output is evidence, **not verified fact**;
claims carry labels such as `OCR EVIDENCE`, `VISION EVIDENCE`, `WEB VERIFIED`,
`AI INFERENCE` and an explicit confidence.
