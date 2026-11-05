"""API-level checks: /healthz is ok, a test offer can be created and read back (rounded
to 2dp), duplicate checksums warn, and a bad-EAN row is routed to review."""
from decimal import Decimal

from .synth import make_bad_ean, make_ean


def test_healthz_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["db"] == "ok"
    assert body["migrations"] == [1, 2, 3, 4, 5]   # +0005_optional_target
    assert body["environment"] == "development"
    assert body["access_enabled"] is False      # OFF by default in dev


def test_create_and_read_offer_via_api(client):
    supplier = client.post("/suppliers", json={"name": "Zwolle HK"}).json()
    batch = client.post("/batches", json={
        "supplier_id": supplier["supplier_id"], "currency": "USD",
        "kind": "price_list", "brand": "BrandA",
        "fx_rate_eur": "1.1253", "received_date": "2026-11-01",
        "source_sha256": "c" * 64,
    }).json()

    # a 4dp quote crosses the wire as a string and is stored rounded to 2dp (18.0037 -> 18.00)
    created = client.post("/offers", json={
        "batch_id": batch["batch_id"], "brand_mode": "single", "brand": "BrandA",
        "offers": [{"ean": "0085715291448", "quoted_price": "18.0037"}],
    })
    assert created.status_code == 200, created.text
    assert len(created.json()["offer_ids"]) == 1

    read = client.get("/products/0085715291448/offers")
    assert read.status_code == 200
    data = read.json()
    assert data["ean"] == "0085715291448"                   # leading zero preserved
    # exact value, rounded to 2dp at import (18.0037 -> 18.00); the string form may be "18"
    assert Decimal(data["offers"][0]["price_original"]) == Decimal("18.00")
    assert data["current_best"] is not None                 # cache rebuilt on import


def test_duplicate_checksum_warns_via_api(client):
    supplier = client.post("/suppliers", json={"name": "S"}).json()
    sha = "d" * 64
    b1 = client.post("/batches", json={"supplier_id": supplier["supplier_id"],
                                       "currency": "EUR", "source_sha256": sha}).json()
    b2 = client.post("/batches", json={"supplier_id": supplier["supplier_id"],
                                       "currency": "EUR", "source_sha256": sha}).json()
    assert b2["duplicate_of_batch_id"] == b1["batch_id"]
    assert "already imported" in (b2["warning"] or "")


def test_bad_ean_row_goes_to_review_via_api(client):
    supplier = client.post("/suppliers", json={"name": "S"}).json()
    batch = client.post("/batches", json={"supplier_id": supplier["supplier_id"],
                                          "currency": "EUR", "kind": "price_list",
                                          "brand": "BrandA", "fx_rate_eur": "1"}).json()
    bad = make_bad_ean("400000000004")
    good = make_ean("400000000003")
    r = client.post("/offers", json={
        "batch_id": batch["batch_id"], "brand_mode": "single", "brand": "BrandA",
        "offers": [
            {"ean": bad, "quoted_price": "9.99"},     # checksum fails -> review
            {"ean": good, "quoted_price": "9.99"},    # fine -> imported
        ],
    })
    assert r.status_code == 200                        # the file imports; the bad row is not fatal
    body = r.json()
    assert len(body["offer_ids"]) == 1
    assert len(body["review"]) == 1
    assert body["review"][0]["ean"] == bad
    assert body["review"][0]["reason"] == "EAN checksum failed"
