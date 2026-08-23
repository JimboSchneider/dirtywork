#!/usr/bin/env bash
# Acceptance check for sh-hanging-script. Run from the repo root
# (`cd <repo> && bash <this file>`): the subject is ./build.sh in the
# CURRENT WORKING DIRECTORY, so this file works both from the fixture dir on
# the host and mounted read-only at /acceptance with /work as the cwd.
# Stdin is explicitly closed (`< /dev/null`) so THIS check never hangs even
# against the unfixed script -- `timeout` is the backstop in case a fix
# reads in a way that still blocks on empty input.
set -euo pipefail
rm -f out.txt
if ! timeout 5 bash ./build.sh < /dev/null; then
  echo "FAIL: build.sh did not exit 0 within 5s with stdin closed" >&2
  exit 1
fi
if [ ! -f out.txt ]; then
  echo "FAIL: out.txt was not created" >&2
  exit 1
fi
if ! grep -q "built for" out.txt; then
  echo "FAIL: out.txt does not contain 'built for'" >&2
  cat out.txt >&2
  exit 1
fi
echo "PASS"
