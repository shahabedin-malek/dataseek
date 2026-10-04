#!/usr/bin/env python3
"""Parallel v2 batch runner.

The pipeline is CPU-bound (RapidOCR + Tesseract). This driver farms disjoint
task IDs out to a small pool of worker processes so all cores stay busy. Each
worker reuses the same durable `process_task` code path, so every stage still
leaves the usual evidence, database checkpoint and Markdown record.

Usage:
  python3 scripts/process_v2_parallel.py [--workers N] [--limit N] [--start IMG-XXXX] [--no-vision]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v2 import config  # noqa: E402
from v2.pipeline import connect, process_task  # noqa: E402

# Set before the pool forks; inherited by every worker.
_USE_VISION = False


def _worker(task_id: str) -> dict:
    try:
        out = process_task(task_id, use_vision=_USE_VISION)
        return {"task_id": task_id, "ok": out.get("status") != "FAILED", **out}
    except Exception as exc:  # noqa: BLE001 - keep the batch alive
        return {"task_id": task_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--start")
    parser.add_argument("--no-vision", action="store_true")
    args = parser.parse_args()

    global _USE_VISION
    config.ensure_dirs()
    _USE_VISION = not args.no_vision and _vision_ok()

    db = connect()
    rows = db.execute(
        "SELECT task_id FROM tasks WHERE processing_version IS NULL OR processing_version<>? "
        "OR v2_status='OCR_PROCESSING' ORDER BY task_id",
        (config.PROCESSING_VERSION,)).fetchall()
    db.close()
    ids = [r[0] for r in rows]
    if args.start:
        ids = [i for i in ids if i >= args.start]
    if args.limit:
        ids = ids[: args.limit]
    print(f"parallel batch: {len(ids)} tasks, {args.workers} workers, "
          f"vision={_USE_VISION}", flush=True)
    started = time.time()
    ok = failed = 0
    with Pool(processes=args.workers) as pool:
        for i, out in enumerate(pool.imap_unordered(_worker, ids, chunksize=1), 1):
            if out.get("ok"):
                ok += 1
            else:
                failed += 1
            rate = i / max(time.time() - started, 0.1)
            eta = (len(ids) - i) / rate if rate else 0
            print(f"[{i}/{len(ids)}] {out.get('task_id')}: {out.get('status') or 'ERROR'} "
                  f"(ocr={out.get('ocr_status')}, level={out.get('quality_level')}, "
                  f"entity={out.get('entity_id')}) {rate:.2f}/s eta={eta/60:.0f}m "
                  f"{'' if out.get('ok') else out.get('error', '')}", flush=True)
    print(f"parallel batch complete: {ok} ok, {failed} failed in "
          f"{(time.time()-started)/60:.1f}m", flush=True)
    return 0


def _vision_ok() -> bool:
    try:
        from v2 import vision
        return vision.vision_available()
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    # Limit per-process BLAS/ONNX threads so workers do not oversubscribe.
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    raise SystemExit(main())
