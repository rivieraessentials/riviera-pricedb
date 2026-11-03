"""The money path — integers and Decimal only, never float (design §1.1, O5/O6 §12).

Prices are stored as INTEGER minor units at a fixed scale of 6 decimals (×1,000,000).
Since O6 (2026-11-02) every stored price is a PER-PIECE value ROUNDED TO 2 DECIMALS
(round-half-up); the 6dp scale is kept only as headroom so no future scale migration is
needed. EUR values are COMPUTED ON READ here and are never written back as a source of
truth (the one cache, current_best.best_unit_price_eur, is a rounded integer).

Rounding note (O6, §2.4): round-half-up is Python's decimal.ROUND_HALF_UP, which rounds
halves away from zero — identical to Excel's display rounding for all prices (≥ 0). The
per-box rule is DIVIDE FIRST, then round ONCE (never round-then-divide).
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, InvalidOperation, localcontext

SCALE = 6                       # decimals of headroom (raw scan 2026-11-01: suppliers quote up to 4)
MULTIPLIER = Decimal(10) ** SCALE   # 1_000_000
CENT = Decimal("0.01")          # prices are rounded to 2 decimals (O6 §2.4)
_PREC = 40                      # generous working precision for intermediate division


class PriceScaleError(ValueError):
    """A value cannot be represented at SCALE, or a float reached the money path."""


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
    """An EXACT value (str/Decimal/int) -> integer minor units.

    Used for values already known to fit the scale (e.g. a 2dp price produced by
    round_price). Raises PriceScaleError if the value needs more than SCALE decimals —
    a guard that a non-rounded value never silently slips through. The IMPORT path does
    not rely on this to round; it rounds explicitly via round_price / import_price_minor.
    """
    d = _as_decimal(value)
    if not d.is_finite():
        raise PriceScaleError(f"price is not finite: {value!r}")
    with localcontext() as ctx:
        ctx.prec = _PREC
        scaled = d * MULTIPLIER
        if scaled != scaled.to_integral_value():
            raise PriceScaleError(f"value {d} needs more than {SCALE} decimals")
        return int(scaled)


def round_price(value, pieces_per_box: int | None = None) -> Decimal:
    """The per-piece price of one row, rounded to 2 decimals (O6 §2.4).

    If pieces_per_box is given the box price is DIVIDED FIRST, then rounded ONCE
    (e.g. 24.99 / 12 = 2.0825 -> 2.08). Round-half-up (ties away from zero).
    """
    d = _as_decimal(value)
    if not d.is_finite():
        raise PriceScaleError(f"price is not finite: {value!r}")
    with localcontext() as ctx:
        ctx.prec = _PREC
        if pieces_per_box is not None:
            ppb = int(pieces_per_box)
            if ppb <= 0:
                raise PriceScaleError(f"pieces_per_box must be positive: {pieces_per_box!r}")
            d = d / Decimal(ppb)
        return d.quantize(CENT, rounding=ROUND_HALF_UP)


def import_price_minor(value, pieces_per_box: int | None = None) -> int:
    """Import entry point: per-piece 2dp price as integer minor units (§2.4)."""
    return to_minor(round_price(value, pieces_per_box))


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


def unit_price_eur(price_minor: int, fx_rate_text: str) -> Decimal:
    """Per-single-piece EUR for one offer — the comparison key (computed on read).

    Since O6 the stored price_original is ALREADY a per-piece price (per-box division
    happened at import, §1.2), so the comparison key is simply the EUR conversion.
    """
    return price_to_eur(price_minor, fx_rate_text)
