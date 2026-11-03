"""The import path: lowest price taken and rounded to 2dp, uncertain rows routed to the
review list (never a silent import), and data minimisation — only ean/description/price
are ever read from a row."""
from decimal import Decimal

from app import importer, repo

from .synth import make_bad_ean, make_ean


def test_lowest_price_rounded_and_bad_rows_reviewed(conn):
    sid = repo.create_supplier(conn, "Supplier")
    bid = repo.create_batch(conn, sid, "EUR", fx_rate_eur="1", kind="price_list", brand="BrandX")
    brand = repo.get_or_create_brand(conn, "BrandX")

    good = make_ean("400000000003")
    bad = make_bad_ean("400000000004")               # check digit deliberately wrong
    rows = [
        {"ean": good, "prices": ["12.50", "9.995", "13.00"]},   # lowest 9.995 -> 10.00 (2dp, half-up)
        {"ean": bad, "quoted_price": "5.00"},                   # EAN checksum fails -> review
        {"ean": make_ean("400000000005"), "quoted_price": ""},  # no price -> review
    ]
    res = importer.import_rows(conn, bid, rows, brand_id=brand)

    assert len(res.offer_ids) == 1                   # only the good row imported
    reasons = {r["reason"] for r in res.review}
    assert reasons == {"EAN checksum failed", "no price found"}

    hist = repo.product_offer_history(conn, int(repo.get_product_by_ean(conn, good)["product_id"]))
    assert Decimal(str(hist[0].price_original)) == Decimal("10.00")   # lowest, rounded once
    assert repo.get_product_by_ean(conn, bad) is None                 # bad EAN created no product


def test_per_box_without_pieces_goes_to_review(conn):
    sid = repo.create_supplier(conn, "S")
    bid = repo.create_batch(conn, sid, "EUR", fx_rate_eur="1", kind="price_list", brand="B")
    brand = repo.get_or_create_brand(conn, "B")
    ean = make_ean("400000000006")
    res = importer.import_rows(conn, bid,
                               [{"ean": ean, "quoted_price": "24.99", "price_basis": "per_box"}],
                               brand_id=brand)
    assert res.offer_ids == []
    assert res.review[0]["reason"] == "per-box row with no pieces-per-box"


def test_data_minimisation_only_known_fields_read(conn):
    """Extra keys a caller passes (a sales-rep column, a contact email) are ignored and
    never stored — there is no raw_row and the offers table has no such columns (§2.8)."""
    sid = repo.create_supplier(conn, "S")
    bid = repo.create_batch(conn, sid, "EUR", fx_rate_eur="1", kind="price_list", brand="B")
    brand = repo.get_or_create_brand(conn, "B")
    ean = make_ean("400000000007")
    importer.import_rows(conn, bid, [{
        "ean": ean, "quoted_price": "5.00", "description": "Serum",
        "sales_rep": "A Person", "contact_email": "rep@example.com", "phone": "+31 6 1234 5678",
    }], brand_id=brand)

    offer_cols = {r[1] for r in conn.execute("PRAGMA table_info(offers)")}
    assert offer_cols == {"offer_id", "batch_id", "product_id", "price_original", "currency", "created_at"}
    # the junk is nowhere in the stored row set
    dumped = " ".join(str(tuple(r)) for r in conn.execute("SELECT * FROM offers"))
    for junk in ("A Person", "rep@example.com", "1234 5678"):
        assert junk not in dumped
