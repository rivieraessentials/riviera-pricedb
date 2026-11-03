"""Synthetic test data only (data minimisation §2.8 / the git rule): no real supplier or
customer values. Helpers to mint valid / invalid EAN-13 codes for fixtures."""
from __future__ import annotations


def make_ean(base12: str) -> str:
    """Append the correct EAN-13 check digit to a 12-digit base -> a VALID EAN-13."""
    assert len(base12) == 12 and base12.isdigit(), "base must be 12 digits"
    d = [int(c) for c in base12]
    s = sum(x * (1 if i % 2 == 0 else 3) for i, x in enumerate(d))
    return base12 + str((10 - s % 10) % 10)


def make_bad_ean(base12: str) -> str:
    """A 13-digit code whose check digit is deliberately WRONG (always fails §2.6)."""
    good = make_ean(base12)
    return base12 + str((int(good[-1]) + 1) % 10)
