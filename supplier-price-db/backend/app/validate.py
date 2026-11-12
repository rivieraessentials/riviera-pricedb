"""Server-side input validation (owner security point 3). Every endpoint validates its
own input and rejects bad data with a clear 4xx; SQL is always parameterised (callers
never build SQL from input). Limits keep a malformed or oversized upload from hurting us.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import HTTPException

from . import importer

MAX_OFFER_ROWS = 20000        # rows accepted in one /offers call
MAX_REQUEST_LINES = 5000      # lines accepted in one customer request
MAX_PRICE = Decimal("1000000")   # a per-piece price over €1,000,000 is a data error
MAX_TEXT = 2000               # longest free-text field we store (description, notes, …)
MAX_BODY_BYTES = 16 * 1024 * 1024   # 16 MB request-body ceiling


def ean_or_400(ean: str | None) -> str:
    """Canonicalise (11/12-digit UPC-A -> 13) then check digits + length (8/13). Used where a
    human may enter an odd-but-real code; returns the canonical EAN so every entry point that
    stores a product uses one key."""
    e = importer.canon_ean(ean)
    if not e.isdigit() or len(e) not in (8, 13):
        raise HTTPException(422, detail=f"EAN must be 8, 12 or 13 digits, got {ean!r}")
    return e


def checked_ean_or_400(ean: str | None) -> str:
    """Digits + length + EAN check digit (§2.6)."""
    e = ean_or_400(ean)
    if not importer.ean_check_ok(e):
        raise HTTPException(422, detail=f"EAN check digit failed for {ean!r}")
    return e


def price_or_400(value, field: str = "price") -> Decimal:
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError, TypeError):
        raise HTTPException(422, detail=f"{field} is not a valid number: {value!r}")
    if not d.is_finite() or d <= 0:
        raise HTTPException(422, detail=f"{field} must be a positive number")
    if d > MAX_PRICE:
        raise HTTPException(422, detail=f"{field} exceeds the sane limit of {MAX_PRICE}")
    return d


def margin_or_400(value) -> Decimal:
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError, TypeError):
        raise HTTPException(422, detail=f"margin is not a valid number: {value!r}")
    if not d.is_finite() or d < 0 or d >= 1:
        raise HTTPException(422, detail="margin must be a fraction in [0, 1), e.g. 0.07 for 7%")
    return d


def text_or_400(value, field: str = "field") -> str | None:
    if value is None:
        return None
    s = str(value)
    if len(s) > MAX_TEXT:
        raise HTTPException(422, detail=f"{field} is too long (max {MAX_TEXT} characters)")
    return s


def count_or_400(n: int, limit: int, what: str) -> None:
    if n > limit:
        raise HTTPException(413, detail=f"too many {what}: {n} (max {limit})")
