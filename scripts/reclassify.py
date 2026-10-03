#!/usr/bin/env python3
"""Re-run taxonomy classification over all resources from their stored evidence.

Used after taxonomy changes; it never touches OCR output or original images.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2 import config, taxonomy  # noqa: E402
from v2.pipeline import connect  # noqa: E402


def main() -> int:
    db = connect()
    changed = 0
    rows = db.execute("SELECT entity_id,name,short_description,resource_type,"
                      "primary_category,subcategory FROM entities "
                      "WHERE is_invalid=0 AND deleted_at IS NULL").fetchall()
    for r in rows:
        text = " ".join(filter(None, [r["name"], r["short_description"]]))
        # Include OCR text from the resource's source screenshots when available.
        ocr = db.execute(
            "SELECT c.final_text FROM ocr_consensus c JOIN entity_images i ON i.task_id=c.task_id "
            "WHERE i.entity_id=? LIMIT 5", (r["entity_id"],)).fetchall()
        text += " " + " ".join(x["final_text"] or "" for x in ocr)
        cls = taxonomy.classify(text, None, r["resource_type"])
        if cls["primary_category"] != r["primary_category"] or cls["subcategory"] != r["subcategory"]:
            db.execute("UPDATE entities SET primary_category=?, subcategory=?, "
                       "secondary_categories=?, updated_at=? WHERE entity_id=?",
                       (cls["primary_category"], cls["subcategory"],
                        json.dumps(cls["secondary_categories"]), config.now(), r["entity_id"]))
            changed += 1
    db.commit()
    db.close()
    print(f"reclassified {changed} of {len(rows)} resources")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())