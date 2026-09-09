"""The Firewall's own invariant-violation error."""
from __future__ import annotations


class FirewallInternalError(Exception):
    """Raised when the package finds its own invariant violated.

    Subclasses ``Exception``, not ``ValueError``, so a caller's
    ``try/except ValueError`` cannot swallow it; the caller must fail closed.
    """
