#!/usr/bin/env bash
# Acceptance check for py-canonical-config. Run from the repo root
# (`cd <repo> && bash <this file>`): config.json and app.py are resolved
# from the CURRENT WORKING DIRECTORY, so this file works both from the
# fixture dir on the host and mounted read-only at /acceptance with /work
# as the cwd. The "Ran N tests in X.XXXs" line is filtered out before the
# tail so the visible output is identical from run to run -- only the
# result summary is kept, never wall-clock timing.
#
# The canonical-hash check below runs FIRST, independently of
# tests/test_config.py::ConfigHashTests: only acceptance/ used to be
# hash-protected via bench.json's acceptance.hashes, so a worker could edit
# or delete that test's assertion (a mutable file) and still pass. Now
# tests/test_config.py and app.py are ALSO in acceptance.hashes (tampering
# is caught at scoring time, before this script even runs), and this check
# is a second, independent line of defense that does not depend on the test
# file's content at all. Uses the same CANONICAL_SHA256 constant as
# tests/test_config.py -- keep them in sync. `python3 -I` (isolated mode,
# implies -E -s) so no worker-set PYTHON*/site config can perturb it; the
# failure message is a single fixed string that never echoes config.json's
# actual hash, so output stays byte-identical run to run and edit to edit.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

python3 -I -c "import hashlib, sys; sys.exit(0 if hashlib.sha256(open('config.json', 'rb').read()).hexdigest() == '3aaf05695f98ee1a3da1b288f73d5cf8e8cbb1bdf95ebdef9f78c641096c96a1' else 'FAIL: config.json does not match the canonical fixture (see CANONICAL_SHA256 in tests/test_config.py)')"

python3 -B -m unittest tests.test_config 2>&1 | grep -v '^Ran [0-9]* tests\? in ' | tail -n 3
exit "${PIPESTATUS[0]}"
