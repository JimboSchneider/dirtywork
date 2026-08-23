#!/usr/bin/env bash
# Acceptance check for py-canonical-config. Run from the repo root
# (`cd <repo> && bash <this file>`): config.json and app.py are resolved
# from the CURRENT WORKING DIRECTORY, so this file works both from the
# fixture dir on the host and mounted read-only at /acceptance with /work
# as the cwd. The "Ran N tests in X.XXXs" line is filtered out before the
# tail so the visible output is identical from run to run -- only the
# result summary is kept, never wall-clock timing.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
python3 -B -m unittest tests.test_config 2>&1 | grep -v '^Ran [0-9]* tests\? in ' | tail -n 3
exit "${PIPESTATUS[0]}"
