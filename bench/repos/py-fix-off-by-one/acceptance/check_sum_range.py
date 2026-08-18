"""Acceptance check for py-fix-off-by-one.

Run from the repo root (`cd <repo> && python3 <this file>`): the subject is
imported from the CURRENT WORKING DIRECTORY, never from this script's own
directory, so the same file works both from the fixture dir on the host and
mounted read-only at /acceptance with /work as the cwd. Plain asserts and a
nonzero exit on failure -- the worker image ships python3 with no pytest.
"""
import os
import sys

sys.path.insert(0, os.getcwd())

from sum_range import sum_range  # noqa: E402

assert sum_range(1, 5) == 15, f"sum_range(1, 5) == {sum_range(1, 5)}, want 15"
assert sum_range(3, 3) == 3, f"sum_range(3, 3) == {sum_range(3, 3)}, want 3"
assert sum_range(0, 10) == 55, f"sum_range(0, 10) == {sum_range(0, 10)}, want 55"
print("PASS")
