#!/usr/bin/env bash
# Acceptance check for py-big-fixture. Run from the repo root
# (`cd <repo> && bash <this file>`): the subject is ./fixtures/rows.csv in
# the CURRENT WORKING DIRECTORY, so this file works both from the fixture
# dir on the host and mounted read-only at /acceptance with /work as the
# cwd. Only checks the shape README.md's schema promises (row count, header,
# field count, id ordering) -- not every derived column's exact value.
set -euo pipefail
FILE="fixtures/rows.csv"

if [ ! -f "$FILE" ]; then
  echo "FAIL: $FILE does not exist" >&2
  exit 1
fi

lines=$(wc -l < "$FILE" | tr -d ' ')
if [ "$lines" -ne 401 ]; then
  echo "FAIL: $FILE has $lines lines, want 401 (1 header + 400 data rows)" >&2
  exit 1
fi

header="$(sed -n '1p' "$FILE")"
if [ "$header" != "id,name,email,plan,created_at,balance" ]; then
  echo "FAIL: header line is '$header', want 'id,name,email,plan,created_at,balance'" >&2
  exit 1
fi

bad_fields=$(awk -F',' 'NR>1 && NF!=6 {print NR": "NF" fields"}' "$FILE")
if [ -n "$bad_fields" ]; then
  echo "FAIL: rows without exactly 6 fields:" >&2
  echo "$bad_fields" >&2
  exit 1
fi

bad_ids=$(awk -F',' 'NR>1 { expected=NR-1; if ($1 != expected) print NR": id="$1" want "expected }' "$FILE")
if [ -n "$bad_ids" ]; then
  echo "FAIL: ids not 1..400 in order:" >&2
  echo "$bad_ids" >&2
  exit 1
fi

echo "PASS"
