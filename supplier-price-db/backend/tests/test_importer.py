"""The import path routes an over-precision (24/7-style) row to review and still
imports the rest of the file. The strict check stays in the money layer."""
from app import importer, repo


def test_import_routes_over_precision_row_to_review(conn):
    sid = repo.create_supplier(conn, "Supplier")
    bid = repo.create_batch(conn, sid, "EUR", fx_rate_eur="1")

    rows = [
        {"ean": "4000000000035", "quoted_price": "12.50"},
        {"ean": "4000000000042", "quoted_price": "3.9325"},
        {"ean": "4000000000059", "quoted_price": "3.4285714285714"},  # 24/7 = 3.428571... (>6dp)
    ]
    res = importer.import_rows(conn, bid, rows)

    # the two clean rows imported; the 24/7 row did not
    assert len(res.offer_ids) == 2
    assert len(res.review) == 1
    assert res.review[0]["ean"] == "4000000000059"
    assert res.review[0]["reason"] == "more than 6 decimals: 3.4285714285714"

    # persisted to the review queue, and NOT created as an offer/product
    rq = repo.list_review_items(conn, bid)
    assert len(rq) == 1 and rq[0]["ean"] == "4000000000059"
    assert rq[0]["reason"] == "more than 6 decimals: 3.4285714285714"
    assert repo.get_product_by_ean(conn, "4000000000059") is None

    # the good products exist and current_best was built for them
    good = repo.get_product_by_ean(conn, "4000000000042")
    assert good is not None
    assert repo.get_current_best(conn, int(good["product_id"])) is not None
