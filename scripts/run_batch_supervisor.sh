#!/usr/bin/env bash
# Resilient v2 batch supervisor.
#
# Runs the multi-OCR batch over every remaining screenshot, restarting
# automatically if a run exits early (crash, OOM, killed). Loops until the
# database reports no remaining unprocessed tasks. Single instance only.
set -u
cd /home/chris/dataseek

LOCK=/tmp/dataseek_supervisor.lock
if [ -e "$LOCK" ] && kill -0 "$(cat "$LOCK")" 2>/dev/null; then
  echo "supervisor already running: $(cat "$LOCK")"
  exit 0
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

PY=.venv/bin/python
LOG_DIR=logs
mkdir -p "$LOG_DIR"

remaining() {
  "$PY" - <<'PY'
import sqlite3
db = sqlite3.connect("database/dataseek.sqlite3")
n = db.execute(
    "SELECT COUNT(*) FROM tasks WHERE processing_version IS NULL "
    "OR processing_version<>'v2_multi_ocr' OR v2_status='OCR_PROCESSING'"
).fetchone()[0]
db.close()
print(n)
PY
}

round=0
while true; do
  n=$(remaining)
  if [ "$n" -le 0 ]; then
    echo "$(date -Is) all tasks processed; exiting supervisor"
    break
  fi
  round=$((round + 1))
  echo "$(date -Is) round $round: $n remaining; starting batch"
  "$PY" -u scripts/process_v2.py batch --limit "$n" --no-vision \
    >> "$LOG_DIR/v2_supervisor.log" 2>&1
  echo "$(date -Is) round $round exited with $?"
  sleep 5
done
