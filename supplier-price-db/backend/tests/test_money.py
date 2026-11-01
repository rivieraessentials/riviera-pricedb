"""The money path: integers + Decimal only, correct FX direction, no float."""
from decimal import Decimal

import pytest

from app import money


def test_float_is_rejected():
    with pytest.raises(money.PriceScaleError):
        money.to_minor(3.9325)          # a float must never enter the money path
    with pytest.raises(money.PriceScaleError):
        money.price_to_eur(1_000_000, 1.1253)


def test_minor_roundtrip_is_lossless():
    for q in ("0", "0.000001", "8.185", "3.9325", "1.234567", "1250.5"):
        assert money.from_minor(money.to_minor(q)) == Decimal(q)


def test_fx_direction_currency_per_eur():
    # rate convention: 1 EUR = rate * currency, so price_eur = price / rate
    usd = money.to_minor("11.253")
    assert money.price_to_eur(usd, "1.1253") == Decimal("10")   # 11.253 USD / 1.1253 = 10 EUR


def test_per_box_divides_before_fx():
    box = money.to_minor("96.00")
    # 96 USD / 12 pcs = 8 USD/pc ; / 1.1253 ≈ 7.109...
    unit = money.unit_price_eur(box, "1.1253", 12, "per_box")
    assert Decimal("7.10") < unit < Decimal("7.11")


def test_eur_to_minor_rounds_half_up():
    assert money.eur_to_minor(Decimal("1.0000005")) == 1000001   # half rounds up
    assert money.eur_to_minor(Decimal("1.0000004")) == 1000000
