#!/usr/bin/env python3
"""DataSeek v2 pipeline CLI.

Usage:
  python3 scripts/process_v2.py process IMG-0001 [--no-vision]
  python3 scripts/process_v2.py batch --limit 5 [--start IMG-0001] [--no-vision]
  python3 scripts/process_v2.py status
  python3 scripts/process_v2.py benchmark IMG-0001 IMG-0002 ...
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v2 import config, engines, vision  # noqa: E402
from v2.pipeline import connect, process_task  # noqa: E402


def cmd_process(args: argparse.Namespace) -> int:
    out = process_task(args.task_id, use_vision=not args.no_vision)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    db = connect()
    # A task left in OCR_PROCESSING was interrupted before its artifacts were
    # written, so it must be retried even though its version stamp is set.
    rows = db.execute(
        "SELECT task_id FROM tasks WHERE (processing_version IS NULL OR processing_version<>? "
        "OR v2_status='OCR_PROCESSING') ORDER BY task_id",
        (config.PROCESSING_VERSION,)).fetchall()
    db.close()
    ids = [r[0] for r in rows]
    if args.start:
        ids = [i for i in ids if i >= args.start]
    if args.limit:
        ids = ids[:args.limit]
    print(f"batch: {len(ids)} tasks to process with {config.PROCESSING_VERSION}")
    done = failed = 0
    for i, task_id in enumerate(ids, 1):
        try:
            out = process_task(task_id, use_vision=not args.no_vision)
            state = out.get("status")
            if state == "FAILED":
                failed += 1
            else:
                done += 1
            print(f"[{i}/{len(ids)}] {task_id}: {state} "
                  f"(ocr={out.get('ocr_status')}, level={out.get('quality_level')}, "
                  f"entity={out.get('entity_id')})")
        except Exception as exc:  # noqa: BLE001 - keep the batch alive
            failed += 1
            print(f"[{i}/{len(ids)}] {task_id}: ERROR {type(exc).__name__}: {exc}",
                  file=sys.stderr)
    print(f"batch complete: {done} ok, {failed} failed")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    db = connect()
    total = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    v2 = db.execute("SELECT COUNT(*) FROM tasks WHERE processing_version=?",
                    (config.PROCESSING_VERSION,)).fetchone()[0]
    entities = db.execute("SELECT COUNT(*) FROM entities WHERE is_invalid=0").fetchone()[0]
    urls = db.execute("SELECT COUNT(*) FROM urls").fetchone()[0]
    errors = db.execute("SELECT COUNT(*) FROM processing_errors WHERE resolved=0").fetchone()[0]
    print(f"total tasks:      {total}")
    print(f"v2 processed:     {v2}")
    print(f"entities:         {entities}")
    print(f"urls:             {urls}")
    print(f"open errors:      {errors}")
    print(f"rapidocr:         {engines.rapidocr_available()}")
    print(f"tesseract:        {engines.TESSERACT_VERSION}")
    print(f"vision available: {vision.vision_available()} ({config.VISION_MODEL} @ {config.OLLAMA_HOST})")
    db.close()
    return 0


def cmd_benchmark(args: argparse.Namespace) -> int:
    from v2 import consensus, preprocess
    src = config.source_root()
    if src is None:
        print("source not mounted", file=sys.stderr)
        return 2
    db = connect()
    report = []
    for task_id in args.task_ids:
        row = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            print(f"unknown task {task_id}", file=sys.stderr)
            continue
        image = Path(row["absolute_source_path"])
        quality = preprocess.analyze_quality(image)
        t = time.time()
        rapid = engines.run_rapidocr(image, "original")
        tess = engines.run_tesseract(image, "original", psm=3)
        rec = consensus.reconcile([rapid, tess])
        report.append({
            "task_id": task_id, "quality": quality,
            "rapidocr": {"chars": len(rapid.get("text") or ""), "conf": rapid.get("confidence"),
                         "latency": rapid.get("latency_s"), "error": rapid.get("error")},
            "tesseract": {"chars": len(tess.get("text") or ""), "conf": tess.get("confidence"),
                          "latency": tess.get("latency_s"), "error": tess.get("error")},
            "agreement": rec.get("agreement"), "status": rec.get("status"),
            "total_s": round(time.time() - t, 1),
        })
        print(f"{task_id}: rapid={report[-1]['rapidocr']} tess={report[-1]['tesseract']} "
              f"agreement={rec.get('agreement')} status={rec.get('status')}")
    out = config.DATA / "ocr" / "benchmark.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"benchmark written to {out}")
    db.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("process")
    p.add_argument("task_id")
    p.add_argument("--no-vision", action="store_true")
    p.set_defaults(func=cmd_process)
    b = sub.add_parser("batch")
    b.add_argument("--limit", type=int, default=5)
    b.add_argument("--start")
    b.add_argument("--no-vision", action="store_true")
    b.set_defaults(func=cmd_batch)
    s = sub.add_parser("status")
    s.set_defaults(func=cmd_status)
    bm = sub.add_parser("benchmark")
    bm.add_argument("task_ids", nargs="+")
    bm.set_defaults(func=cmd_benchmark)
    args = parser.parse_args()
    config.ensure_dirs()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
