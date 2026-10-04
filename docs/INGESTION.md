# Ingestion

## Source inventory

`scripts/scan_sources.py` enumerates the read-only source directory
(`/mnt/private-ai-data/Screenshot `, note the literal trailing space), computes
SHA-256, dimensions, and an 8x8 difference hash, and assigns stable `IMG-nnnn`
task ids from the sorted relative paths. Source files are opened read-only.

## Processing generation `v2_multi_ocr`

The legacy Tesseract-only pass is superseded. Run the v2 pipeline:

```bash
nohup bash scripts/run_batch_supervisor.sh > logs/v2_supervisor.log 2>&1 &   # resilient batch
.venv/bin/python scripts/process_v2.py process IMG-0001 --no-vision        # single task
.venv/bin/python scripts/process_v2.py status                              # engine availability
```

Each task moves through: quality analysis → preprocessing → RapidOCR → Tesseract
→ (optional vision) → consensus → entity/URL resolution → research →
classification → Markdown + OCR cache + database checkpoint.

## After a batch

```bash
.venv/bin/python scripts/resolve_cached.py   # upgrade unresolved from cached OCR
.venv/bin/python scripts/recompute_consensus.py
.venv/bin/python scripts/dedupe.py
.venv/bin/python scripts/v2/export.py
.venv/bin/python scripts/audit.py
```

Tasks left in `OCR_PROCESSING` are incomplete and are automatically re-selected
by the batch query on the next run.
