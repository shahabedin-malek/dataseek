"""Guard: the published site must not reveal how the data was sourced.

The public interface presents extracted knowledge and evidence records. The
word "screenshot" must not appear in any site chrome, page copy, or frontend
code, so that a visitor cannot infer the origin of the underlying media.

Resource *data* is exempt: a real product may genuinely be named or hosted at a
URL containing the word, and censoring that would falsify the record.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = "screenshot"

# Files that constitute the public site's own text and code (not data payloads).
SITE_SOURCES = [
    ROOT / "web" / "index.html",
    ROOT / "web" / "app.js",
    ROOT / "web" / "styles.css",
    ROOT / "backend" / "admin.html",
]

# Field names in the exported data that would expose the source format.
FORBIDDEN_KEYS = ("screenshot_type", "screenshots", "filename")


class PublicBrandingTests(unittest.TestCase):
    def test_frontend_sources_never_mention_the_source_format(self):
        for path in SITE_SOURCES:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8").lower()
            self.assertNotIn(FORBIDDEN, text, f"{path.name} leaks the source format")

    def test_public_data_bundle_uses_neutral_field_names(self):
        import json
        for rel in ("site/data/dataseek.json", "web/data/dataseek.json"):
            path = ROOT / rel
            if not path.is_file():
                continue
            blob = json.dumps(json.loads(path.read_text(encoding="utf-8"))).lower()
            for key in FORBIDDEN_KEYS:
                self.assertNotIn(f'"{key}"', blob, f"{rel} exposes internal field {key!r}")

    def test_public_data_bundle_has_no_source_filenames(self):
        import json
        for rel in ("site/data/dataseek.json", "web/data/dataseek.json"):
            path = ROOT / rel
            if not path.is_file():
                continue
            records = json.loads(path.read_text(encoding="utf-8")).get("records", [])
            for rec in records:
                self.assertNotIn("filename", rec)
                self.assertNotIn("sha256", rec)


if __name__ == "__main__":
    unittest.main()