#!/usr/bin/env bash
# Detached v2 batch runner with a single-instance lock.
set -u
cd /home/chris/dataseek
LOCK=/tmp/dataseek_batch.lock
if [ -e "$LOCK" ] && kill -0 "$(cat "$LOCK")" 2>/dev/null; then
  echo "batch already running: $(cat "$LOCK")"
  exit 0
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT
exec .venv/bin/python scripts/process_v2.py batch --limit "${1:-907}"
