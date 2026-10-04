# Architecture

DataSeek is a local-first, screenshot-derived knowledge search engine. Python
3.12 in `.venv` runs the pipeline; SQLite is the single source of truth; the
frontend and a zero-dependency backend read derived artifacts.

## Pipeline (per screenshot, generation `v2_multi_ocr`)

```
source image (read-only, /mnt/private-ai-data/Screenshot )
  -> image quality analysis            (v2/preprocess.py)
  -> preprocessing variants on demand  (CLAHE / grayscale, data/preprocess/)
  -> OCR engine A: RapidOCR (PP-OCRv6 ONNX, CPU)      \
  -> OCR engine B: Tesseract 5.5 (TSV + confidences)   } v2/engines.py
  -> (vision layer disabled by policy; OCR-only)      /
  -> reconciliation + confidence + agreement   (v2/consensus.py)
  -> URL + GitHub candidate extraction
  -> entity resolution:
        GitHub repo  -> authenticated `gh` metadata + README
        visible URL  -> fetch page, confirm identity from its own metadata
  -> classification                            (v2/taxonomy.py)
  -> durable outputs: OCR cache, Markdown record, database checkpoint
```

Every engine run is written verbatim to `data/ocr/<IMG-nnnn>/<engine>_<variant>.json`
and mirrored into the `ocr_runs` / `ocr_regions` tables. Reconciliation never
lets majority voting decide alone; raw engine text is retained as evidence.

## Orchestration

- `scripts/process_v2.py` — single-task and batch CLI.
- `scripts/process_v2_parallel.py` — multiprocessing driver (used only when cores
  are free; RapidOCR/Tesseract oversubscribe threads under concurrency).
- `scripts/run_batch_supervisor.sh` — resilient loop that keeps the batch running
  and auto-resumes until no tasks remain.
- `scripts/resolve_cached.py` — upgrades unresolved screenshots from cached OCR
  (URL fetch + research only, no re-OCR).
- `scripts/enrich_candidates.py` — corroborates GitHub-canonical candidate
  resources with repository metadata + README (features, license, language).
- `scripts/resolve_urls_broader.py` — retries *every* stored URL (not just the
  top 3) for records that still have no resource; creates one only on a
  confirmed page-metadata match.
- `scripts/recompute_consensus.py` — re-reconciles from cached OCR after logic
  changes.
- `scripts/dedupe.py` — exact (SHA-256) and near (dHash) duplicate detection.
- `scripts/run_finalize.sh` — waits for the batch, then runs resolve_cached, export,
  site_build and audit in order (stages that must not run concurrently with the batch).
- `scripts/v2/export.py` — regenerates ALL_SCREENSHOTS.md, ALL_RESOURCES.md,
  search index, site bundle and the `.progress/` files.
- `scripts/audit.py` — final integrity audit.

## Interfaces

- `backend/api.py` — standard-library HTTP API + audited admin (`/admin`).
- `web/` — frontend source; `site/` — buildable static bundle for Vercel
  (only extracted knowledge; originals and OCR caches are never published).

## Evidence discipline

Findings are labelled by source: SCREENSHOT FACT, OCR EVIDENCE, VISION EVIDENCE,
WEB VERIFIED, OFFICIAL SOURCE, SECONDARY SOURCE, AI INFERENCE, UNCERTAIN,
CONFLICTING. AI inference is never promoted to fact. A resource is only created
when a screenshot-evidenced GitHub repo or visible URL can be confirmed.
