# tests/test_lifecycle.py
from __future__ import annotations

import pytest

from dirtywork.sandbox import lifecycle


def test_init_worker_git_raises_valueerror_for_invalid_layout():
    """init_worker_git should raise ValueError for invalid layout."""
    def fake_run(argv, timeout=None):
        class FakeCaptured:
            returncode = 0
            output = b""
        return FakeCaptured()

    with pytest.raises(ValueError, match="layout must be 'env' or 'gitfile', got 'bogus'"):
        lifecycle.init_worker_git(fake_run, "test-container", branch="dirtywork/test",
                                   base_commit="abc123" * 5, restart=False, layout="bogus")
