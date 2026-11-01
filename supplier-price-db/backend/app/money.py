"""The money path — integers and Decimal only, never float (design §1.1, O5 §12).

Prices are stored as INTEGER minor units at a fixed scale of 6 decimals
(×1,000,000). EUR and per-piece values are COMPUTED ON READ here and are never
written back as a source of truth (the one cache, current_best.best_unit_price_eur,
is a rounded integer produced by eur_to_minor()).
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, InvalidOperation, localcontext

SCALE = 6                       # decimals of headroom (raw scan 2026-11-01: suppliers quote up to 4)
MULTIPLIER = Decimal(10) ** SCALE   # 1_000_000
_PREC = 40                      # generous working precision for intermediate division


class PriceScaleError(ValueError):
    """A quoted price cannot be represented exactly at SCALE, or a float reached the money path."""


def _as_decimal(value) -> Decimal:
    # Floats are rejected on purpose: they are the one thing that silently corrupts money.
    if isinstance(value, bool):
        raise PriceScaleError(f"bool is not a price: {value!r}")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except InvalidOperation as exc:
            raise PriceScaleError(f"not a valid decimal price: {value!r}") from exc
    raise PriceScaleError(f"float is not allowed in the money path: {value!r} ({type(value).__name__})")


def to_minor(value) -> int:
    """Quoted price (str/Decimal/int) -> integer minor units, EXACTLY.

    Raises PriceScaleError if the value has more than SCALE fractional digits: the
    importer must never silently round a quoted price (design §2.4 pt 5). Such a row
    goes to the review queue instead.
    """
    d = _as_decimal(value)
    if not d.is_finite():
        raise PriceScaleError(f"price is not finite: {value!r}")
    with localcontext() as ctx:
        ctx.prec = _PREC
        scaled = d * MULTIPLIER
        if scaled != scaled.to_integral_value():
            raise PriceScaleError(
                f"price {d} needs more than {SCALE} decimals; importer must not round (design §2.4)"
            )
        return int(scaled)


def from_minor(minor: int) -> Decimal:
    """Exact Decimal value of an integer minor-unit price."""
    with localcontext() as ctx:
        ctx.prec = _PREC
        return Decimal(int(minor)) / MULTIPLIER


def eur_to_minor(eur: Decimal) -> int:
    """Round a computed EUR Decimal to integer minor units (ROUND_HALF_UP).

    Only for the derived current_best cache — a computed per-piece EUR is a division
    result and inherently rounded, so it is never treated as a stored source of truth.
    """
    with localcontext() as ctx:
        ctx.prec = _PREC
        return int((eur * MULTIPLIER).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def price_to_eur(price_minor: int, fx_rate_text: str) -> Decimal:
    """Convert a price in minor units to EUR, using a rate stored as TEXT.

    Rate convention (design §1.1 fx_rates): 1 EUR = rate * currency, so
    price_eur = price_in_currency / rate. EUR batches carry rate "1".
    """
    rate = _as_decimal(fx_rate_text)
    if rate <= 0:
        raise PriceScaleError(f"fx rate must be positive: {fx_rate_text!r}")
    with localcontext() as ctx:
        ctx.prec = _PREC
        return from_minor(price_minor) / rate


def unit_price_eur(price_minor: int, fx_rate_text: str, pieces_per_box, price_basis: str) -> Decimal:
    """Per-single-piece EUR for one offer (the comparison key). Computed on read.

    per_box prices are divided by pieces_per_box first (design §1.2); default basis
    is per_piece when absent.
    """
    with localcontext() as ctx:
        ctx.prec = _PREC
        per_piece = from_minor(price_minor)
        if price_basis == "per_box":
            if not pieces_per_box or int(pieces_per_box) <= 0:
                raise PriceScaleError("per_box price needs a positive pieces_per_box")
            per_piece = per_piece / Decimal(int(pieces_per_box))
        rate = _as_decimal(fx_rate_text)
        if rate <= 0:
            raise PriceScaleError(f"fx rate must be positive: {fx_rate_text!r}")
        return per_piece / rate
