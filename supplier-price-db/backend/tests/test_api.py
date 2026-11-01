"""API-level checks: /healthz is ok, and a test offer can be created and read back."""


def test_healthz_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["db"] == "ok"
    assert body["migrations"] == [1, 2]         # 0001_initial + 0002_review_queue
    assert body["environment"] == "development"
    assert body["access_enabled"] is False      # OFF by default in dev


def test_create_and_read_offer_via_api(client):
    supplier = client.post("/suppliers", json={"name": "Zwolle HK"}).json()
    batch = client.post("/batches", json={
        "supplier_id": supplier["supplier_id"], "currency": "USD",
        "fx_rate_eur": "1.1253", "received_date": "2026-11-01",
        "source_file": "hk.xlsx", "source_sha256": "c" * 64,
    }).json()

    # a real 4dp value (HongKong USD 18.0037) crosses the wire as a string, stored exactly
    created = client.post("/offers", json={
        "batch_id": batch["batch_id"],
        "offers": [{"ean": "0085715291448", "quoted_price": "18.0037", "currency": "USD"}],
    })
    assert created.status_code == 200, created.text
    assert len(created.json()["offer_ids"]) == 1

    read = client.get("/products/0085715291448/offers")
    assert read.status_code == 200
    data = read.json()
    assert data["ean"] == "0085715291448"                 # leading zero preserved
    assert data["offers"][0]["price_original"] == "18.0037"   # exact, no rounding
    assert data["current_best"] is not None                # cache was rebuilt on import


def test_duplicate_checksum_warns_via_api(client):
    supplier = client.post("/suppliers", json={"name": "S"}).json()
    sha = "d" * 64
    b1 = client.post("/batches", json={"supplier_id": supplier["supplier_id"],
                                       "currency": "EUR", "source_sha256": sha}).json()
    b2 = client.post("/batches", json={"supplier_id": supplier["supplier_id"],
                                       "currency": "EUR", "source_sha256": sha}).json()
    assert b2["duplicate_of_batch_id"] == b1["batch_id"]
    assert "already imported" in (b2["warning"] or "")


def test_over_precision_row_goes_to_review_via_api(client):
    supplier = client.post("/suppliers", json={"name": "S"}).json()
    batch = client.post("/batches", json={"supplier_id": supplier["supplier_id"],
                                          "currency": "EUR", "fx_rate_eur": "1"}).json()
    r = client.post("/offers", json={
        "batch_id": batch["batch_id"],
        "offers": [
            {"ean": "4000000000028", "quoted_price": "1.2345678"},   # 7dp -> review
            {"ean": "4000000000035", "quoted_price": "9.99"},        # fine -> imported
        ],
    })
    assert r.status_code == 200                 # the file imports; the bad row is NOT a hard failure
    body = r.json()
    assert len(body["offer_ids"]) == 1
    assert len(body["review"]) == 1
    assert body["review"][0]["ean"] == "4000000000028"
    assert body["review"][0]["reason"] == "more than 6 decimals: 1.2345678"
