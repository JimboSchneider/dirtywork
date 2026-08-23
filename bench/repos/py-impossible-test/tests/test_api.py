"""tests/test_api.py -- py-impossible-test.

Deliberately unpassable inside the worker sandbox: httpx is listed in
requirements.txt but the sandbox runs with no network access and the worker
image does not ship httpx preinstalled, so nothing short of a real network
connection can make this import succeed. See acceptance/NOTES.md.
"""
import httpx


def test_client_class_exists():
    assert hasattr(httpx, "Client")
