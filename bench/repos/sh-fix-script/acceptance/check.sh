#!/usr/bin/env bash
# Acceptance check for sh-fix-script. Run from the repo root
# (`cd <repo> && bash <this file>`): the subject is ./report.sh in the CURRENT
# WORKING DIRECTORY, the expectation is read from this script's own directory,
# so the same file works from the fixture dir on the host and mounted
# read-only at /acceptance with /work as the cwd.
# cmp compares raw bytes: "$(...)" strips trailing newlines and would hide the
# exact bug this task is about.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
actual="$(mktemp)"
trap 'rm -f "$actual"' EXIT
bash ./report.sh a b c > "$actual"
if ! cmp -s "$actual" "$here/expected_output.txt"; then
  echo "FAIL: report.sh output does not match expected_output.txt byte for byte" >&2
  od -c "$actual" >&2
  exit 1
fi
echo "PASS"
