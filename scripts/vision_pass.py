#!/usr/bin/env python3
"""Apply the vision model to images that were processed while it was unavailable.

Runs vision, stores vision_runs, then re-reconciles consensus from the cached OCR
(no expensive re-OCR). Safe to re-run; skips tasks that already have a vision run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2 import config, vision  # noqa: E402
from v2.pipeline import connect, _record_vision  # noqa: E402


def main() -> int:
    if not vision.vision_available():
        print(f"vision model not reachable at {config.OLLAMA_HOST}; nothing to do")
        return 0
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    db = connect()
    rows = db.execute(
        """SELECT t.task_id, t.absolute_source_path FROM tasks t
           WHERE t.processing_version=? AND t.ocr_status IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM vision_runs v WHERE v.task_id=t.task_id
                             AND v.raw_response IS NOT NULL AND v.raw_response<>'')
           ORDER BY t.task_id LIMIT ?""",
        (config.PROCESSING_VERSION, limit)).fetchall()
    print(f"vision pass: {len(rows)} images")
    done = 0
    for r in rows:
        path = Path(r["absolute_source_path"])
        if not path.is_file():
            continue
        result = vision.run_vision(path)
        if result.get("error"):
            print(f"  {r['task_id']}: vision error {result['error']}")
            continue
        # Persist the raw engine result alongside the other engines, then record it.
        out = config.OCR_DIR / r["task_id"] / f"vision_{result['preprocessing']}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        _record_vision(db, r["task_id"], result)
        done += 1
        if done % 10 == 0:
            print(f"  {done}/{len(rows)}…")
    db.close()
    print(f"vision pass complete: {done} images enriched")
    if done:
        print("Now run: .venv/bin/python scripts/recompute_consensus.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())