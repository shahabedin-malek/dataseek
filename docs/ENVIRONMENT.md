# Verified Environment

Inspection date: 2026-10-03 (UTC timestamp recorded in progress files).

- Operating system: Linux (observed mounted ext4 filesystems).
- Project path: `/home/chris/dataseek` (current workspace).
- Python: 3.14.4.
- Node.js: v24.21.0; npm: 11.19.0; pnpm: 11.15.1.
- Git: 2.53.0. The workspace was not a Git repository at inspection time.
- GitHub CLI: 2.46.0; `gh auth status` reported an authenticated account. No token or credential value is recorded here.
- curl: 8.18.0; wget: 1.25.0.
- Pillow: 12.1.1; ImageMagick: 7.1.2-18; SQLite Python module: 3.46.1.
- `tesseract`, `ocrmypdf`, `gocr`, `cuneiform`, `easyocr`, `paddleocr`, `torch`, `transformers`, `opencv`, `imagehash`, FastAPI, Uvicorn, and pytest were not found in the inspected PATH/Python environment.
- Google Chrome and Firefox executables are present. `sqlite3` CLI was not found.
- Environment inspection found no relevant service/API environment variables; values were never printed.
- Root filesystem: 117 GiB total, 36 GiB free at inspection.

## Source mount discrepancy

The configured path `/mnt/private-ai-data/Screenshot/` does not resolve. A read-only inventory found the actual directory `/mnt/private-ai-data/Screenshot `, including a literal trailing space in its name. It contained 907 `.jpg` files at inspection. The scanner uses the configured path first and falls back to that exact sibling directory only when the configured path is absent. It does not rename or modify the source.

Availability is a point-in-time observation. Re-run `python3 scripts/scan_sources.py` to reconcile the inventory. OCR is currently unavailable; no OCR results should be claimed until a backend is installed and verified.
