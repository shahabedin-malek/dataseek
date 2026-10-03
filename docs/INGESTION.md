# Ingestion

`python3 scripts/scan_sources.py` recursively enumerates supported image files, calculates SHA-256, dimensions, and an 8x8 difference hash, and assigns stable `IMG-NNNN` task IDs. Source files are opened read-only. Exact hash duplicates point at a canonical task and are not silently discarded. OCR and semantic research are not performed by the inventory scan.
