"""Build the deployable static site into `site/` for Vercel.

Only extracted knowledge is published. Original screenshots and OCR caches stay
local and are never copied into the deployable bundle.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

# Allow both `python -m v2.site_build` and `python scripts/v2/site_build.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v2 import config  # noqa: E402


def build_site() -> dict:
    config.ensure_dirs()
    site = config.ROOT / "site"
    data_dir = site / "data"
    if site.exists():
        shutil.rmtree(site)
    data_dir.mkdir(parents=True)
    for name in ("index.html", "app.js", "styles.css"):
        shutil.copy2(config.ROOT / "web" / name, site / name)
    # Copy the site data bundle produced by export.py.
    src = config.ROOT / "web" / "data" / "dataseek.json"
    if not src.is_file():
        from v2 import export
        export.write_exports()
    shutil.copy2(src, data_dir / "dataseek.json")
    (site / "vercel.json").write_text(json.dumps({
        "cleanUrls": True,
        "headers": [{"source": "/data/(.*)", "headers": [
            {"key": "Cache-Control", "value": "public, max-age=300"}]}],
    }, indent=2), encoding="utf-8")
    (site / "robots.txt").write_text(
        "User-agent: *\nAllow: /\nSitemap: /sitemap.xml\n", encoding="utf-8")
    size = sum(f.stat().st_size for f in site.rglob("*") if f.is_file())
    return {"site": str(site), "bytes": size,
            "files": [str(p.relative_to(site)) for p in sorted(site.rglob("*")) if p.is_file()]}


if __name__ == "__main__":
    print(json.dumps(build_site(), indent=2))