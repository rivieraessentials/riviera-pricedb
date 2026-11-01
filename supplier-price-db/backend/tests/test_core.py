"""The six required Phase-1 correctness tests, exercised against the pure core
(no web layer). Prices compared as Decimal values (exact), never as float."""
from decimal import Decimal

import pytest

from app import money, repo


# 1) duplicate EAN rejected; EAN with a leading zero survives as text
def test_duplicate_ean_rejected_and_leading_zero_preserved(conn):
    ean = "0012345678905"                       # leading zero would vanish if stored as a number
    pid = repo.create_product(conn, ean, description="Cream 50ml")
    row = repo.get_product_by_ean(conn, ean)
    assert isinstance(row["ean"], str)
    assert row["ean"] == ean                    # exact text, zero intact
    assert int(pid) == int(row["product_id"])

    with pytest.raises(repo.EANExistsError):    # UNIQUE constraint -> rejected
        repo.create_product(conn, ean)


# 2) a 4- and a 6-decimal price survive storage and retrieval unchanged
def test_4_and_6_decimal_prices_survive_unchanged(conn):
    sid = repo.create_supplier(conn, "Supplier")
    bid = repo.create_batch(conn, sid, "EUR", fx_rate_eur="1")
    pid = repo.create_product(conn, "4000000000004")

    quotes = ["3.9325", "1.234567", "8.185"]    # 4dp (real it's-a-10), 6dp (scale edge), 3dp (design §2.4)
    for q in quotes:
        repo.add_quoted_offer(conn, bid, pid, q, "EUR")

    stored = {str(v.price_original): v for v in repo.product_offer_history(conn, pid)}
    for q in quotes:
        # retrieved value equals the quote exactly, and the minor-unit round-trip is lossless
        assert any(Decimal(k) == Decimal(q) for k in stored), f"{q} missing"
        assert money.from_minor(money.to_minor(q)) == Decimal(q)

    # and the no-round rule: a 7-decimal price is refused, not silently rounded
    with pytest.raises(money.PriceScaleError):
        money.to_minor("1.2345678")


# 3) an FX rate stored as text is returned exactly as published
def test_fx_rate_stored_as_text_is_exact(conn):
    repo.set_fx_rate(conn, "2026-11-01", "GBP", "0.84385", "ecb")
    repo.set_fx_rate(conn, "2026-11-01", "USD", "1.12530", "ecb")   # trailing zero must be kept verbatim
    assert repo.get_fx_rate(conn, "2026-11-01", "GBP") == "0.84385"
    assert repo.get_fx_rate(conn, "2026-11-01", "USD") == "1.12530"


# 4) offers are append-only: no code path updates an existing offer's price
def test_offers_are_append_only(conn):
    # There is deliberately no update/edit/set-price function on the repo.
    banned = [n for n in dir(repo)
              if any(w in n.lower() for w in ("update", "edit", "set_price")) and "offer" in n.lower()]
    assert banned == [], f"unexpected mutating offer API: {banned}"

    sid = repo.create_supplier(conn, "S")
    pid = repo.create_product(conn, "4000000000011")
    b1 = repo.create_batch(conn, sid, "EUR", fx_rate_eur="1", received_date="2026-10-01")
    o1 = repo.add_quoted_offer(conn, b1, pid, "10.00", "EUR")

    # a "correction" is a NEW offer in a NEW batch; the first stays exactly as recorded
    b2 = repo.create_batch(conn, sid, "EUR", fx_rate_eur="1", received_date="2026-11-01")
    repo.add_quoted_offer(conn, b2, pid, "9.00", "EUR")

    hist = repo.product_offer_history(conn, pid)
    assert [Decimal(str(v.price_original)) for v in hist] == [Decimal("10.00"), Decimal("9.00")]
    raw = conn.execute("SELECT price_original FROM offers WHERE offer_id = ?", (o1,)).fetchone()
    assert raw["price_original"] == money.to_minor("10.00")   # untouched


# 5) current_best rebuilds identically from scratch
def test_current_best_rebuilds_identically(conn):
    gbp_sid = repo.create_supplier(conn, "UK")
    hk_sid = repo.create_supplier(conn, "HongKong")
    eur_sid = repo.create_supplier(conn, "EU")

    # one product, three competing offers in three currencies / bases
    pid = repo.create_product(conn, "5000000000003")
    b_gbp = repo.create_batch(conn, gbp_sid, "GBP", fx_rate_eur="0.84385")   # 1 EUR = 0.84385 GBP
    b_usd = repo.create_batch(conn, hk_sid, "USD", fx_rate_eur="1.1253")     # per box
    b_eur = repo.create_batch(conn, eur_sid, "EUR", fx_rate_eur="1")
    repo.add_quoted_offer(conn, b_gbp, pid, "6.50", "GBP")                                   # 6.50/0.84385 = ~7.70 EUR/pc
    repo.add_quoted_offer(conn, b_usd, pid, "96.00", "USD", pieces_per_box=12, price_basis="per_box")  # 8.00 USD/pc /1.1253 = ~7.11 EUR
    repo.add_quoted_offer(conn, b_eur, pid, "7.49", "EUR")                                   # 7.49 EUR/pc

    # a second product with a single offer
    pid2 = repo.create_product(conn, "5000000000010")
    repo.add_quoted_offer(conn, b_eur, pid2, "12.00", "EUR")

    n1 = repo.rebuild_current_best(conn)                 # full rebuild
    snap1 = _best_snapshot(conn)
    n2 = repo.rebuild_current_best(conn)                 # again, from scratch
    snap2 = _best_snapshot(conn)

    assert n1 == n2 == 2
    assert snap1 == snap2                                # byte-for-byte identical result

    # sanity: the USD per-box offer wins product 1 — 8.00 USD/pc ÷ 1.1253 ≈ 7.11 EUR,
    # cheaper than GBP (~7.70) and EUR (7.49). Per-box + cross-currency, computed in Decimal.
    best = repo.get_current_best(conn, pid)
    won = repo.product_offer_history(conn, pid)
    winner = next(v for v in won if v.offer_id == best["best_offer_id"])
    assert winner.currency == "USD" and winner.price_basis == "per_box"
    assert Decimal(str(winner.price_original)) == Decimal("96.00")
    assert winner.unit_price_eur < Decimal("7.49")


# 6) re-importing the same file checksum is detected
def test_duplicate_file_checksum_detected(conn):
    sid = repo.create_supplier(conn, "S")
    sha = "a" * 64
    b1 = repo.create_batch(conn, sid, "EUR", source_sha256=sha, received_date="2026-11-01")

    dup = repo.find_batch_by_sha(conn, sha)              # same bytes -> flagged
    assert dup is not None and int(dup["batch_id"]) == b1

    assert repo.find_batch_by_sha(conn, "b" * 64) is None   # a new file is not flagged


def _best_snapshot(conn):
    return [tuple(r) for r in conn.execute(
        "SELECT product_id, best_offer_id, best_unit_price_eur, best_supplier_id "
        "FROM current_best ORDER BY product_id"
    ).fetchall()]
