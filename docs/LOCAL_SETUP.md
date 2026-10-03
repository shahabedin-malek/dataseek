# Local Setup

Run from `/home/chris/dataseek`. Python 3.14.4 and Pillow 12.1.1 are available. Inspect `docs/ENVIRONMENT.md` for verified tools and the screenshot mount discrepancy.

Run `python3 scripts/bootstrap_project.py` to create missing directories/schema, then `python3 scripts/scan_sources.py` to read-only scan the source directory. No package installation is needed for manifest scanning. Original screenshots must remain untouched.
