#!/usr/bin/env python3
"""Measure the RapidOCR latency/accuracy tradeoff when downscaling screenshots."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from PIL import Image  # noqa: E402

from v2 import config, engines  # noqa: E402

db = sqlite3.connect(config.DB_PATH)
db.row_factory = sqlite3.Row
ids = sys.argv[1:] or ["IMG-0020", "IMG-0025", "IMG-0030"]
for tid in ids:
    row = db.execute("SELECT source_filename,width,height FROM tasks WHERE task_id=?", (tid,)).fetchone()
    if not row:
        continue
    path = config.source_root() / row["source_filename"]
    im = Image.open(path).convert("RGB")
    for maxside in (0, 1600, 1280):
        img = im.copy()
        if maxside:
            img.thumbnail((maxside, maxside))
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            img.save(tmp.name, quality=92)
            name = tmp.name
        try:
            t = time.time()
            r = engines.run_rapidocr(Path(name))
            dt = time.time() - t
        finally:
            os.unlink(name)
        print(f"{tid} {row['width']}x{row['height']} maxside={maxside or 'full'} "
              f"-> {dt:.1f}s conf={r['confidence']} chars={len(r['text'])} boxes={len(r['boxes'])}")
