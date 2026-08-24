#!/usr/bin/env bash
# Acceptance check for py-big-fixture. Run from the repo root
# (`cd <repo> && bash <this file>`): the subject is ./fixtures/rows.csv in
# the CURRENT WORKING DIRECTORY, so this file works both from the fixture
# dir on the host and mounted read-only at /acceptance with /work as the
# cwd. Enforces every rule README.md states (row count, header, byte-level
# LF/newline shape, and every derived column per row), not just the file's
# gross shape. `-I` isolates the checker from cwd/PYTHONPATH; stdlib only.
# On any mismatch it prints exactly one line naming the first bad row and
# column and stops there, so the output is short and identical run to run
# for the same input.
set -euo pipefail
FILE="fixtures/rows.csv"

if [ ! -f "$FILE" ]; then
  echo "FAIL: row 0 col -: $FILE does not exist"
  exit 1
fi

python3 -I - "$FILE" <<'PYEOF'
import datetime
import sys

path = sys.argv[1]
with open(path, "rb") as fh:
    raw = fh.read()

if b"\r" in raw:
    print("FAIL: row 0 col -: file must use LF line endings (found CR)")
    sys.exit(1)

if not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
    print("FAIL: row 0 col -: file must end with exactly one trailing newline")
    sys.exit(1)

text = raw.decode("utf-8")
lines = text.split("\n")
if lines and lines[-1] == "":
    lines = lines[:-1]

for i, line in enumerate(lines, start=1):
    if line == "":
        print(f"FAIL: row {i} col -: blank line")
        sys.exit(1)

if not lines:
    print("FAIL: row 0 col -: file is empty")
    sys.exit(1)

EXPECTED_HEADER = "id,name,email,plan,created_at,balance"
if lines[0] != EXPECTED_HEADER:
    print(f"FAIL: row 1 col header: expected '{EXPECTED_HEADER}'")
    sys.exit(1)

rows = lines[1:]
if len(rows) != 400:
    print(f"FAIL: row 0 col -: expected 400 data rows, found {len(rows)}")
    sys.exit(1)

PLAN_BY_REMAINDER = {1: "free", 2: "pro", 0: "enterprise"}
START_DATE = datetime.date(2024, 1, 1)

for offset, row in enumerate(rows):
    csv_row_num = offset + 2  # +1 for 1-indexing, +1 for the header row
    fields = row.split(",")
    if len(fields) != 6:
        print(f"FAIL: row {csv_row_num} col -: expected 6 fields, found {len(fields)}")
        sys.exit(1)

    fid, name, email, plan, created_at, balance = fields
    expected_id = offset + 1

    if fid != str(expected_id):
        print(f"FAIL: row {csv_row_num} col id: expected '{expected_id}', got '{fid}'")
        sys.exit(1)

    expected_name = f"User{expected_id}"
    if name != expected_name:
        print(f"FAIL: row {csv_row_num} col name: expected '{expected_name}', got '{name}'")
        sys.exit(1)

    expected_email = f"{expected_name.lower()}@example.com"
    if email != expected_email:
        print(f"FAIL: row {csv_row_num} col email: expected '{expected_email}', got '{email}'")
        sys.exit(1)

    expected_plan = PLAN_BY_REMAINDER[expected_id % 3]
    if plan != expected_plan:
        print(f"FAIL: row {csv_row_num} col plan: expected '{expected_plan}', got '{plan}'")
        sys.exit(1)

    expected_created_at = (START_DATE + datetime.timedelta(days=expected_id - 1)).isoformat()
    if created_at != expected_created_at:
        print(f"FAIL: row {csv_row_num} col created_at: expected '{expected_created_at}', got '{created_at}'")
        sys.exit(1)

    expected_balance = format(round(expected_id * 3.33, 2), ".2f")
    if balance != expected_balance:
        print(f"FAIL: row {csv_row_num} col balance: expected '{expected_balance}', got '{balance}'")
        sys.exit(1)

print("PASS")
PYEOF
