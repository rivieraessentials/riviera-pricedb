"""The money path: integers + Decimal only, correct FX direction, no float, and the
O6 rule — prices are rounded to 2dp (round-half-up), per-box divided then rounded once."""
from decimal import Decimal

import pytest

from app import money


def test_float_is_rejected():
    with pytest.raises(money.PriceScaleError):
        money.to_minor(3.9325)          # a float must never enter the money path
    with pytest.raises(money.PriceScaleError):
        money.price_to_eur(1_000_000, 1.1253)
    with pytest.raises(money.PriceScaleError):
        money.import_price_minor(3.9325)


def test_minor_roundtrip_is_lossless():
    for q in ("0", "0.000001", "8.185", "3.9325", "1.234567", "1250.5"):
        assert money.from_minor(money.to_minor(q)) == Decimal(q)


def test_fx_direction_currency_per_eur():
    # rate convention: 1 EUR = rate * currency, so price_eur = price / rate
    usd = money.to_minor("11.253")
    assert money.price_to_eur(usd, "1.1253") == Decimal("10")   # 11.253 USD / 1.1253 = 10 EUR


def test_round_price_half_up_to_2dp():
    assert money.round_price("8.185") == Decimal("8.19")        # tie rounds up / away from zero
    assert money.round_price("8.184") == Decimal("8.18")
    assert money.round_price("3.9325") == Decimal("3.93")       # format-less / many-dp cell -> 2dp
    assert money.round_price("1.234567") == Decimal("1.23")


def test_import_price_per_box_divides_then_rounds_once():
    # 24.99 per box of 12 = 2.0825 -> rounded ONCE -> 2.08
    assert money.import_price_minor("24.99", 12) == money.to_minor("2.08")
    # 96.00 / 12 = 8.00 exactly
    assert money.import_price_minor("96.00", 12) == money.to_minor("8.00")
    # per-piece (no pieces_per_box): just rounded to 2dp
    assert money.import_price_minor("7.499") == money.to_minor("7.50")


def test_eur_to_minor_rounds_half_up():
    assert money.eur_to_minor(Decimal("1.0000005")) == 1000001   # half rounds up
    assert money.eur_to_minor(Decimal("1.0000004")) == 1000000
