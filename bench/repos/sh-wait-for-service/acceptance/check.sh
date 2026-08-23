#!/usr/bin/env bash
# Acceptance check for sh-wait-for-service. Run from the repo root
# (`cd <repo> && bash <this file>`): the subject is ./build.sh in the
# CURRENT WORKING DIRECTORY, so this file works both from the fixture dir on
# the host and mounted read-only at /acceptance with /work as the cwd.
# `timeout` isn't guaranteed to exist on every host this runs on, so the
# bounded wait is plain bash: poll once a second for up to 20s, then TERM
# and (after a short grace period) KILL the still-running job.
set -uo pipefail
rm -f out.txt

bash ./build.sh &
pid=$!

finished=""
i=0
while [ "$i" -lt 20 ]; do
  if ! kill -0 "$pid" 2>/dev/null; then
    finished=1
    break
  fi
  sleep 1
  i=$((i + 1))
done

if [ -n "$finished" ]; then
  wait "$pid"
  rc=$?
else
  kill "$pid" 2>/dev/null
  sleep 2
  kill -9 "$pid" 2>/dev/null
  wait "$pid" 2>/dev/null
  rc=124
fi

if [ "$rc" -ne 0 ]; then
  echo "FAIL: build.sh did not exit 0 within 20s" >&2
  exit 1
fi

grep -q 'built for' out.txt || { echo "FAIL: out.txt missing/wrong" >&2; exit 1; }
echo PASS
