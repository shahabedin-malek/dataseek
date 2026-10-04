#!/usr/bin/env bash
# Resilient DataSeek finalizer.
#
# Waits for the v2 OCR batch supervisor to finish, then runs the downstream
# stages that must NOT run concurrently with the batch (they write to the same
# SQLite database):
#   1. resolve cached identities (no re-OCR)
#   2. rebuild exports + search index + web/site bundles
#   3. run the integrity audit
#
# Single instance only. Safe to launch repeatedly.
set -u
cd /home/chris/dataseek

LOCK=/tmp/dataseek_finalize.lock
if [ -e "$LOCK" ] && kill -0 "$(cat "$LOCK")" 2>/dev/null; then
  echo "finalizer already running: $(cat "$LOCK")"
  exit 0
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

PY=.venv/bin/python
LOG=logs/finalize.log
mkdir -p logs
echo "=== $(date -Is) finalizer started (pid $$) ===" >> "$LOG"

# Wait until neither the supervisor nor an active batch process remains.
while pgrep -f "run_batch_supervisor.sh" >/dev/null 2>&1 || \
      pgrep -f "process_v2.py batch" >/dev/null 2>&1; do
  sleep 20
done
echo "$(date -Is) OCR batch finished; starting finalize stages" >> "$LOG"

# Re-run cached resolution until it stops finding new identities (bounded).
for round in 1 2 3; do
  echo "$(date -Is) resolve_cached round $round" >> "$LOG"
  "$PY" -u scripts/resolve_cached.py >> "$LOG" 2>&1
  # Stop early once a round resolves nothing (printed "done: 0 resolved").
  if tail -n 40 "$LOG" | grep -q "done: 0 resolved"; then
    break
  fi
done

echo "$(date -Is) enriching candidate resources (official repo metadata)" >> "$LOG"
"$PY" -u scripts/enrich_candidates.py >> "$LOG" 2>&1

echo "$(date -Is) rebuilding exports" >> "$LOG"
"$PY" -u scripts/v2/export.py >> "$LOG" 2>&1
echo "$(date -Is) building site bundle" >> "$LOG"
"$PY" -u scripts/v2/site_build.py >> "$LOG" 2>&1
echo "$(date -Is) running audit" >> "$LOG"
"$PY" -u scripts/audit.py >> "$LOG" 2>&1
echo "=== $(date -Is) finalizer complete (audit exit logged above) ===" >> "$LOG"
