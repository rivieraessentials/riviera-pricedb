"""Owner-specified replacement tests (final rule, 2026-11-03).

One rule for both offer types, single- and multi-brand alike: a new offer replaces, for
each brand present in the file, that supplier's previous offer of the SAME TYPE for that
brand. Different brands never replace each other; regular and stock offers never replace
each other; a stock offer additionally drops out when ended. (design §3.3 / O2.)

  1. a regular offer replaces the previous regular offer per brand (single- & multi-brand)
  2. a stock offer replaces the previous stock offer per brand (single- & multi-brand)
  3. a new Clarins stock offer replaces neither the supplier's Clinique stock offer nor
     their Clarins regular offer
  4. an ended stock offer drops out of the best price
"""
from app import repo
from app.importer import import_rows
from app.offer_types import REGULAR, STOCK

from .synth import make_ean


def _batch(conn, sid, kind, date, brand):
    return repo.create_batch(conn, sid, "EUR", fx_rate_eur="1", kind=kind,
                             received_date=date, brand=brand)


def _live(conn, ean):
    return set(repo.live_offer_ids(conn, int(repo.get_product_by_ean(conn, ean)["product_id"])))


def _offer_on(conn, batch_id, ean):
    pid = int(repo.get_product_by_ean(conn, ean)["product_id"])
    row = conn.execute("SELECT offer_id FROM offers WHERE batch_id = ? AND product_id = ?",
                       (batch_id, pid)).fetchone()
    return int(row["offer_id"]) if row else None


# 1a) regular replaces regular per brand — single-brand upload
def test_regular_replaces_per_brand_single(conn):
    sid = repo.create_supplier(conn, "S")
    bx = repo.get_or_create_brand(conn, "BrandX")
    ean = make_ean("600000000001")

    b_old = _batch(conn, sid, REGULAR, "2026-10-01", "BrandX")
    import_rows(conn, b_old, [{"ean": ean, "quoted_price": "10.00"}], brand_id=bx)
    b_new = _batch(conn, sid, REGULAR, "2026-11-01", "BrandX")
    import_rows(conn, b_new, [{"ean": ean, "quoted_price": "9.00"}], brand_id=bx)

    assert _live(conn, ean) == {_offer_on(conn, b_new, ean)}            # old regular replaced
    pid = int(repo.get_product_by_ean(conn, ean)["product_id"])
    assert int(repo.get_current_best(conn, pid)["best_offer_id"]) == _offer_on(conn, b_new, ean)


# 1b) regular replaces regular per brand — multi-brand upload (each brand independently)
def test_regular_replaces_per_brand_multi(conn):
    sid = repo.create_supplier(conn, "S")
    bx = repo.get_or_create_brand(conn, "BrandX")
    by = repo.get_or_create_brand(conn, "BrandY")
    bz = repo.get_or_create_brand(conn, "BrandZ")
    ean_x, ean_y, ean_z = (make_ean("600000000010"), make_ean("600000000011"),
                           make_ean("600000000012"))

    import_rows(conn, _batch(conn, sid, REGULAR, "2026-10-01", "BrandX"),
                [{"ean": ean_x, "quoted_price": "10.00"}], brand_id=bx)
    import_rows(conn, _batch(conn, sid, REGULAR, "2026-10-01", "BrandY"),
                [{"ean": ean_y, "quoted_price": "20.00"}], brand_id=by)
    b_zold = _batch(conn, sid, REGULAR, "2026-10-01", "BrandZ")
    import_rows(conn, b_zold, [{"ean": ean_z, "quoted_price": "30.00"}], brand_id=bz)

    # one multi-brand regular upload refreshes X and Y, but not Z (absent from the file)
    b_multi = _batch(conn, sid, REGULAR, "2026-11-01", "(multi-brand)")
    import_rows(conn, b_multi, [
        {"ean": ean_x, "quoted_price": "9.00"},
        {"ean": ean_y, "quoted_price": "19.00"},
    ], brand_mode="multi")

    assert _live(conn, ean_x) == {_offer_on(conn, b_multi, ean_x)}      # X refreshed
    assert _live(conn, ean_y) == {_offer_on(conn, b_multi, ean_y)}      # Y refreshed
    assert _live(conn, ean_z) == {_offer_on(conn, b_zold, ean_z)}       # Z untouched


# 2a) stock replaces stock per brand — single-brand upload
def test_stock_replaces_per_brand_single(conn):
    sid = repo.create_supplier(conn, "S")
    bx = repo.get_or_create_brand(conn, "BrandX")
    ean = make_ean("600000000020")

    b_old = _batch(conn, sid, STOCK, "2026-10-01", "BrandX")
    import_rows(conn, b_old, [{"ean": ean, "quoted_price": "8.00"}], brand_id=bx)
    b_new = _batch(conn, sid, STOCK, "2026-11-01", "BrandX")
    import_rows(conn, b_new, [{"ean": ean, "quoted_price": "7.00"}], brand_id=bx)

    assert _live(conn, ean) == {_offer_on(conn, b_new, ean)}            # old stock replaced


# 2b) stock replaces stock per brand — multi-brand upload
def test_stock_replaces_per_brand_multi(conn):
    sid = repo.create_supplier(conn, "S")
    bx = repo.get_or_create_brand(conn, "BrandX")
    by = repo.get_or_create_brand(conn, "BrandY")
    ean_x, ean_y = make_ean("600000000030"), make_ean("600000000031")

    # establish the two products under their brands
    import_rows(conn, _batch(conn, sid, REGULAR, "2026-09-01", "BrandX"),
                [{"ean": ean_x, "quoted_price": "12.00"}], brand_id=bx)
    import_rows(conn, _batch(conn, sid, REGULAR, "2026-09-01", "BrandY"),
                [{"ean": ean_y, "quoted_price": "22.00"}], brand_id=by)

    b_sold = _batch(conn, sid, STOCK, "2026-10-01", "(multi-brand)")
    import_rows(conn, b_sold, [
        {"ean": ean_x, "quoted_price": "9.00"},
        {"ean": ean_y, "quoted_price": "19.00"},
    ], brand_mode="multi")
    b_snew = _batch(conn, sid, STOCK, "2026-11-01", "(multi-brand)")
    import_rows(conn, b_snew, [
        {"ean": ean_x, "quoted_price": "8.00"},
        {"ean": ean_y, "quoted_price": "18.00"},
    ], brand_mode="multi")

    for ean in (ean_x, ean_y):
        live = _live(conn, ean)
        assert _offer_on(conn, b_snew, ean) in live                    # new stock is live
        assert _offer_on(conn, b_sold, ean) not in live                # old stock replaced


# 3) a new Clarins stock offer replaces neither the Clinique stock offer nor the Clarins regular
def test_clarins_stock_does_not_replace_clinique_stock_or_clarins_regular(conn):
    sid = repo.create_supplier(conn, "S")
    clinique = repo.get_or_create_brand(conn, "Clinique")
    clarins = repo.get_or_create_brand(conn, "Clarins")
    ean_c = make_ean("600000000040")   # Clinique product
    ean_r = make_ean("600000000041")   # Clarins product

    b_clin_stock = _batch(conn, sid, STOCK, "2026-10-01", "Clinique")
    import_rows(conn, b_clin_stock, [{"ean": ean_c, "quoted_price": "5.00"}], brand_id=clinique)
    b_clar_reg = _batch(conn, sid, REGULAR, "2026-10-02", "Clarins")
    import_rows(conn, b_clar_reg, [{"ean": ean_r, "quoted_price": "20.00"}], brand_id=clarins)

    # the new Clarins stock offer
    b_clar_stock = _batch(conn, sid, STOCK, "2026-11-01", "Clarins")
    import_rows(conn, b_clar_stock, [{"ean": ean_r, "quoted_price": "15.00"}], brand_id=clarins)

    # Clinique stock offer: untouched by the Clarins stock upload
    assert _live(conn, ean_c) == {_offer_on(conn, b_clin_stock, ean_c)}
    # Clarins product: the regular offer AND the new stock offer are both live (different types)
    assert _live(conn, ean_r) == {_offer_on(conn, b_clar_reg, ean_r),
                                  _offer_on(conn, b_clar_stock, ean_r)}


# 4) an ended stock offer drops out of the best price
def test_ended_stock_offer_drops_out_of_best(conn):
    sid = repo.create_supplier(conn, "S")
    bp = repo.get_or_create_brand(conn, "BrandP")
    ean = make_ean("600000000050")

    b_reg = _batch(conn, sid, REGULAR, "2026-10-01", "BrandP")
    import_rows(conn, b_reg, [{"ean": ean, "quoted_price": "10.00"}], brand_id=bp)
    b_stock = _batch(conn, sid, STOCK, "2026-10-05", "BrandP")
    import_rows(conn, b_stock, [{"ean": ean, "quoted_price": "7.00"}], brand_id=bp)

    pid = int(repo.get_product_by_ean(conn, ean)["product_id"])
    # the cheaper stock offer is the current best while live
    assert int(repo.get_current_best(conn, pid)["best_offer_id"]) == _offer_on(conn, b_stock, ean)

    repo.end_offer(conn, b_stock, "2026-11-15")
    repo.rebuild_current_best(conn, [pid])

    assert _offer_on(conn, b_stock, ean) not in _live(conn, ean)        # ended stock gone
    assert int(repo.get_current_best(conn, pid)["best_offer_id"]) == _offer_on(conn, b_reg, ean)
