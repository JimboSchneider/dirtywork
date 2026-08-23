#!/usr/bin/env bash
# Acceptance check for py-rename-symbol. Run from the repo root
# (`cd <repo> && bash <this file>`): the subject (ledger.py, tests/) is
# resolved from the CURRENT WORKING DIRECTORY, so this file works both from
# the fixture dir on the host and mounted read-only at /acceptance with
# /work as the cwd. Stdlib unittest only -- the worker image ships no
# pytest. The repo-wide grep is scoped to the two source files the task asks
# the model to touch, not the whole tree, so it doesn't trip over this
# script's own comments or bench.json's task description (both mention the
# old name by necessity). -B (also PYTHONDONTWRITEBYTECODE) keeps this run
# from leaving __pycache__/*.pyc behind when run against the fixture dir
# directly on the host, not just inside the acceptance container.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
python3 -B -m unittest -v tests.test_ledger
if grep -RnE 'calc_total' ledger.py tests/test_ledger.py; then
  echo "FAIL: calc_total still present in ledger.py or tests/test_ledger.py" >&2
  exit 1
fi
echo "PASS"
