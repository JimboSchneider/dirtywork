"""Run the actual activity renderers with a small, text-only DOM."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_activity_diff_renderers():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the dashboard browser regression")
    script = Path(__file__).with_name("dashboard_activity.test.cjs")
    result = subprocess.run([node, "--test", str(script)], capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
