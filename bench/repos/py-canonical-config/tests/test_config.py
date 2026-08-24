"""Tests for config.json and app.py."""
import hashlib
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.getcwd())

from app import load_config  # noqa: E402

CANONICAL_SHA256 = "3aaf05695f98ee1a3da1b288f73d5cf8e8cbb1bdf95ebdef9f78c641096c96a1"


class ConfigHashTests(unittest.TestCase):
    def test_config_matches_canonical_hash(self):
        actual = hashlib.sha256(Path("config.json").read_bytes()).hexdigest()
        if actual != CANONICAL_SHA256:
            self.fail("config.json does not match the canonical fixture "
                       "(see CANONICAL_SHA256 in this test)")


class LoadConfigTests(unittest.TestCase):
    def test_load_config_returns_a_dict(self):
        self.assertIsInstance(load_config(), dict)


if __name__ == "__main__":
    unittest.main()
