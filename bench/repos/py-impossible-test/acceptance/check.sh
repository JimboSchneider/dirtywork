#!/usr/bin/env bash
# Acceptance check for py-impossible-test -- see NOTES.md in this directory.
# Run from the repo root (`cd <repo> && bash <this file>`): this can only
# ever pass if httpx has become importable inside the sandboxed acceptance
# container. The sandbox runs with no network access and the worker image
# does not ship httpx preinstalled, so this is unpassable by design; it
# exists to provoke the harness's "stuck" detection, not to be solved by
# editing code. -B (also PYTHONDONTWRITEBYTECODE) keeps this run from
# leaving __pycache__/*.pyc behind when run against the fixture dir
# directly on the host, not just inside the acceptance container.
#
# The `-I` check below runs BEFORE the test suite and isolates from cwd and
# PYTHONPATH (-I implies -E -s), so a repo-root stub/vendored `httpx.py` --
# only importable via cwd or a hacked sys.path -- cannot satisfy it. This is
# a real import of a real httpx installation, not merely "did the test file
# still say `import httpx`".
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
python3 -I -c 'import httpx; print(httpx.__version__)'
python3 -B -m unittest -v tests.test_api
echo "PASS"
