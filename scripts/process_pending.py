#!/usr/bin/env python3
"""Process pending DataSeek screenshots serially with durable checkpoints."""
from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
from pathlib import Path

from scan_sources import ROOT
from process_image import refresh_exports


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="Maximum images this run (0 means until interrupted/queue empty)")
    parser.add_argument("--retry-failed", action="store_true", help="Also retry FAILED/RETRY tasks")
    parser.add_argument("--timeout", type=int, default=120, help="Per-image OCR timeout in seconds")
    args = parser.parse_args()
    if args.limit < 0 or args.timeout <= 0:
        parser.error("--limit must be nonnegative and --timeout positive")
    allowed = ["PENDING"] + (["RETRY", "FAILED"] if args.retry_failed else [])
    research_pending = ["NEEDS_RESEARCH"]
    research_placeholders = ",".join("?" for _ in research_pending)
    placeholders = ",".join("?" for _ in allowed)
    processed = 0
    while args.limit == 0 or processed < args.limit:
        with sqlite3.connect(ROOT / "database" / "dataseek.sqlite3") as connection:
            row = connection.execute(f"SELECT task_id FROM tasks WHERE status IN ({placeholders}) ORDER BY task_id LIMIT 1", allowed).fetchone()
            if row is None:
                row = connection.execute(f"SELECT task_id FROM tasks WHERE status IN ({research_placeholders}) AND research_status='PENDING' ORDER BY task_id LIMIT 1", research_pending).fetchone()
        if row is None:
            with sqlite3.connect(ROOT / "database" / "dataseek.sqlite3") as connection:
                stale = connection.execute("SELECT task_id,output_file FROM tasks WHERE status='IN_PROGRESS' ORDER BY task_id").fetchall()
                for stale_id, stale_output in stale:
                    output_path = Path(stale_output) if stale_output else None
                    has_ocr = bool(output_path and output_path.is_file() and "## Source facts — raw OCR (unverified transcription)" in output_path.read_text(encoding="utf-8"))
                    if not has_ocr:
                        connection.execute("UPDATE tasks SET status='PENDING',error='Recovered stale IN_PROGRESS record; no completed OCR evidence exists.' WHERE task_id=?", (stale_id,))
                connection.commit()
                if stale:
                    refresh_exports(connection)
                    row = connection.execute(f"SELECT task_id FROM tasks WHERE status IN ({placeholders}) ORDER BY task_id LIMIT 1", allowed).fetchone()
                    if row is None:
                        row = connection.execute(f"SELECT task_id FROM tasks WHERE status IN ({research_placeholders}) AND research_status='PENDING' ORDER BY task_id LIMIT 1", research_pending).fetchone()
            if row is None:
                print("No eligible pending screenshot tasks remain.")
                break
        task_id = row[0]
        print(f"\n--- Processing {task_id} ({processed + 1}{f'/{args.limit}' if args.limit else ''}) ---", flush=True)
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "process_image.py"), task_id, "--timeout", str(args.timeout)], cwd=ROOT, check=False, capture_output=True, text=True)
        if result.stdout:
            print(result.stdout.rstrip(), flush=True)
        if result.stderr:
            print(result.stderr.rstrip(), file=sys.stderr, flush=True)
        if result.returncode != 0:
            with sqlite3.connect(ROOT / "database" / "dataseek.sqlite3") as connection:
                state = connection.execute("SELECT status,error FROM tasks WHERE task_id=?", (task_id,)).fetchone()
                if state and state[0] == "IN_PROGRESS":
                    error = (state[1] or "Uncaught processor interruption; no completion checkpoint was written.")[:2000]
                    connection.execute("UPDATE tasks SET status='PENDING',error=? WHERE task_id=?", (f"Recovered incomplete task after processor exit {result.returncode}: {error}", task_id))
                    connection.commit()
                    refresh_exports(connection)
            if result.returncode not in (1,):
                print(f"Task processor exited {result.returncode} for {task_id}; incomplete work was returned to PENDING if it lacked a completion checkpoint.", file=sys.stderr)
                return result.returncode
        with sqlite3.connect(ROOT / "database" / "dataseek.sqlite3") as connection:
            state = connection.execute("SELECT status,output_file FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if state and state[0] == "IN_PROGRESS":
                output = Path(state[1]) if state[1] else None
                complete_ocr = False
                if output and output.is_file():
                    complete_ocr = "## Source facts — raw OCR (unverified transcription)" in output.read_text(encoding="utf-8")
                if not complete_ocr:
                    print(f"Recovered incomplete task {task_id}; no valid OCR checkpoint exists, returning to PENDING.", file=sys.stderr)
                    connection.execute("UPDATE tasks SET status='PENDING',error='Recovered interrupted IN_PROGRESS record; no valid completed OCR output.' WHERE task_id=?", (task_id,))
                    connection.commit()
                    refresh_exports(connection)
                    processed += 1
                    continue
        processed += 1
        print(f"Checkpoint finished for {task_id}; {processed} task(s) attempted this run.", flush=True)
        if result.returncode == 1:
            # Failed OCR is checkpointed RETRY; continue other images rather than block the queue.
            continue
    print(f"Run complete: attempted {processed} image(s). State remains persistent in SQLite and progress exports.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
