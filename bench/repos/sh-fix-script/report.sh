#!/usr/bin/env bash
# Prints a count report for the arguments it is given. BUG: no trailing
# newline, so the output does not match acceptance/expected_output.txt.
set -euo pipefail
count=0
for _ in "$@"; do
  count=$((count + 1))
done
printf 'files: %d' "$count"
