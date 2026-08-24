#!/bin/bash
# tools/soak_sampler.sh -- 5s sampler loop (macOS only), issue #48. Appends
# ts,free_gb,inactive_gb,lms_models_loaded,lms_status to OUT_CSV every 5s.
#   tools/soak_sampler.sh OUT_CSV          # start (writes OUT_CSV.pid)
#   tools/soak_sampler.sh OUT_CSV --stop   # stop the loop named in OUT_CSV.pid
set -euo pipefail
OUT="${1:?usage: soak_sampler.sh OUT_CSV [--stop]}"
PIDFILE="${OUT}.pid"
LMS="${HOME}/.lmstudio/bin/lms"
if [ "${2:-}" = "--stop" ]; then
  [ -f "$PIDFILE" ] || { echo "no pidfile at $PIDFILE" >&2; exit 1; }
  kill "$(cat "$PIDFILE")" 2>/dev/null || true
  rm -f "$PIDFILE"
  echo "stopped sampler for $OUT"
  exit 0
fi
PAGE_SIZE=$(vm_stat | awk '/page size of/ {print $8}')
[ -f "$OUT" ] || echo "ts,free_gb,inactive_gb,lms_models_loaded,lms_status" > "$OUT"
echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT
while true; do
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  vs=$(vm_stat)
  free=$(awk '/Pages free/ {gsub("[.]","",$3); print $3}' <<<"$vs")
  inact=$(awk '/Pages inactive/ {gsub("[.]","",$3); print $3}' <<<"$vs")
  free_gb=$(awk -v p="$free" -v s="$PAGE_SIZE" 'BEGIN{printf "%.2f", p*s/1073741824}')
  inact_gb=$(awk -v p="$inact" -v s="$PAGE_SIZE" 'BEGIN{printf "%.2f", p*s/1073741824}')
  if out=$("$LMS" ps 2>/dev/null); then
    loaded=$(grep -cE '^[A-Za-z0-9]' <<<"$out" || true)
    loaded=$((loaded > 0 ? loaded - 1 : 0))   # subtract the header row
    status=$(tr '\n' ';' <<<"$out" | cut -c1-160)
  else
    loaded=0
    status="lms_unavailable"
  fi
  echo "${ts},${free_gb},${inact_gb},${loaded},\"${status}\"" >> "$OUT"
  sleep 5
done
