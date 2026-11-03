"""The required Phase-2 scenario tests (design §7), exercised on the pure core
(importer + repo), synthetic fixtures only:

  1. a Clinique list does NOT replace the same supplier's Clarins list;
  2. a new Clarins price list replaces the old Clarins price list, but NOT the stock offer;
  3. a multi-brand upload matches known EANs and sends an unknown EAN to review;
  4. a single-brand upload with an EAN already under another brand goes to review (no overwrite);
  5. a per-box price is divided then rounded once (24.99 / 12 -> 2.08).
"""
from decimal import Decimal

from app import importer, repo

from .synth import make_ean


def _batch(conn, sid, kind, date, brand):
    return repo.create_batch(conn, sid, "EUR", fx_rate_eur="1", kind=kind,
                             received_date=date, brand=brand)


# 1) different brands never replace each other
def test_different_brands_never_replace(conn):
    sid = repo.create_supplier(conn, "S")
    clinique = repo.get_or_create_brand(conn, "Clinique")
    clarins = repo.get_or_create_brand(conn, "Clarins")
    ean_c = make_ean("500000000001")
    ean_r = make_ean("500000000002")

    b1 = _batch(conn, sid, "price_list", "2026-10-01", "Clinique")
    importer.import_rows(conn, b1, [{"ean": ean_c, "quoted_price": "10.00"}], brand_id=clinique)
    b2 = _batch(conn, sid, "price_list", "2026-10-05", "Clarins")
    importer.import_rows(conn, b2, [{"ean": ean_r, "quoted_price": "20.00"}], brand_id=clarins)
    b3 = _batch(conn, sid, "price_list", "2026-11-01", "Clinique")   # newer Clinique list
    importer.import_rows(conn, b3, [{"ean": ean_c, "quoted_price": "9.00"}], brand_id=clinique)

    pid_c = int(repo.get_product_by_ean(conn, ean_c)["product_id"])
    pid_r = int(repo.get_product_by_ean(conn, ean_r)["product_id"])
    hist_c = {v.received_date: v.offer_id for v in repo.product_offer_history(conn, pid_c)}
    hist_r = repo.product_offer_history(conn, pid_r)

    # Clinique: the newer list replaced the older one
    assert repo.live_offer_ids(conn, pid_c) == [hist_c["2026-11-01"]]
    # Clarins: untouched by the Clinique uploads
    assert repo.live_offer_ids(conn, pid_r) == [hist_r[0].offer_id]
    assert int(repo.get_current_best(conn, pid_c)["best_offer_id"]) == hist_c["2026-11-01"]


# 2) a new price list replaces the old list, but not the same-brand stock offer
def test_new_price_list_replaces_old_list_not_stock_offer(conn):
    sid = repo.create_supplier(conn, "S")
    clarins = repo.get_or_create_brand(conn, "Clarins")
    ean_r = make_ean("500000000002")

    b_old = _batch(conn, sid, "price_list", "2026-10-01", "Clarins")
    importer.import_rows(conn, b_old, [{"ean": ean_r, "quoted_price": "20.00"}], brand_id=clarins)
    b_so = _batch(conn, sid, "stock_offer", "2026-10-10", "Clarins")
    importer.import_rows(conn, b_so, [{"ean": ean_r, "quoted_price": "15.00"}], brand_id=clarins)
    b_new = _batch(conn, sid, "price_list", "2026-11-01", "Clarins")
    importer.import_rows(conn, b_new, [{"ean": ean_r, "quoted_price": "18.00"}], brand_id=clarins)

    pid = int(repo.get_product_by_ean(conn, ean_r)["product_id"])
    hist = {(v.kind, v.received_date): v.offer_id for v in repo.product_offer_history(conn, pid)}
    live = set(repo.live_offer_ids(conn, pid))

    assert hist[("price_list", "2026-10-01")] not in live          # old list replaced
    assert live == {hist[("price_list", "2026-11-01")], hist[("stock_offer", "2026-10-10")]}

    # ending the stock offer drops it; the live price list stays
    repo.end_offer(conn, b_so, "2026-11-15")
    assert set(repo.live_offer_ids(conn, pid)) == {hist[("price_list", "2026-11-01")]}


# 3) multi-brand upload: known EANs keep their brand; an unknown EAN goes to review
def test_multi_brand_matches_known_and_reviews_unknown(conn):
    sid = repo.create_supplier(conn, "S")
    bx = repo.get_or_create_brand(conn, "BrandX")
    by = repo.get_or_create_brand(conn, "BrandY")
    ean_a, ean_b, ean_u = make_ean("500000000010"), make_ean("500000000011"), make_ean("500000000012")

    importer.import_rows(conn, _batch(conn, sid, "price_list", "2026-10-01", "BrandX"),
                         [{"ean": ean_a, "quoted_price": "5.00"}], brand_id=bx)
    importer.import_rows(conn, _batch(conn, sid, "price_list", "2026-10-01", "BrandY"),
                         [{"ean": ean_b, "quoted_price": "6.00"}], brand_id=by)

    b = _batch(conn, sid, "price_list", "2026-11-01", "(multi-brand)")
    res = importer.import_rows(conn, b, [
        {"ean": ean_a, "quoted_price": "4.50"},
        {"ean": ean_b, "quoted_price": "5.50"},
        {"ean": ean_u, "quoted_price": "7.00"},
    ], brand_mode="multi")

    assert len(res.offer_ids) == 3                                  # all three prices kept
    assert int(repo.get_product_by_ean(conn, ean_a)["brand_id"]) == bx   # brand unchanged
    assert int(repo.get_product_by_ean(conn, ean_b)["brand_id"]) == by
    assert [r["ean"] for r in res.review] == [ean_u]
    assert res.review[0]["reason"] == "new product — assign a brand"
    u = repo.get_product_by_ean(conn, ean_u)
    assert u["brand_id"] is None and u["status"] == "needs_review"   # excluded until assigned


# 4) single-brand upload: an EAN already under another brand -> review, no overwrite
def test_single_brand_ean_under_other_brand_reviews(conn):
    sid = repo.create_supplier(conn, "S")
    bx = repo.get_or_create_brand(conn, "BrandX")
    by = repo.get_or_create_brand(conn, "BrandY")
    ean_a = make_ean("500000000010")

    importer.import_rows(conn, _batch(conn, sid, "price_list", "2026-10-01", "BrandX"),
                         [{"ean": ean_a, "quoted_price": "5.00"}], brand_id=bx)

    b = _batch(conn, sid, "price_list", "2026-11-01", "BrandY")
    res = importer.import_rows(conn, b, [{"ean": ean_a, "quoted_price": "4.00"}], brand_id=by)

    assert res.offer_ids == []                                     # no offer imported
    assert len(res.review) == 1 and res.review[0]["ean"] == ean_a
    assert "already under brand BrandX" in res.review[0]["reason"]
    assert int(repo.get_product_by_ean(conn, ean_a)["brand_id"]) == bx   # not overwritten

    pid = int(repo.get_product_by_ean(conn, ean_a)["product_id"])
    assert b not in {v.batch_id for v in repo.product_offer_history(conn, pid)}


# 5) per-box price is divided then rounded ONCE
def test_per_box_divided_then_rounded_once(conn):
    sid = repo.create_supplier(conn, "S")
    bp = repo.get_or_create_brand(conn, "BrandP")
    ean_p = make_ean("500000000020")

    b = _batch(conn, sid, "price_list", "2026-11-01", "BrandP")
    res = importer.import_rows(conn, b, [
        {"ean": ean_p, "quoted_price": "24.99", "price_basis": "per_box", "pieces_per_box": 12},
    ], brand_id=bp)

    assert len(res.offer_ids) == 1 and res.review == []
    pid = int(repo.get_product_by_ean(conn, ean_p)["product_id"])
    hist = repo.product_offer_history(conn, pid)
    assert Decimal(str(hist[0].price_original)) == Decimal("2.08")   # 24.99/12 = 2.0825 -> 2.08
