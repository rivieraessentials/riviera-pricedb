"""The screens the MVP frontend drives: suppliers, the live grid, bestsellers, the import
log + delete, product edit / bestseller flag, review, and customer requests (inputs stored,
result recomputed live). Synthetic data only."""
from .synth import make_ean


def _supplier(client, name):
    return client.post("/suppliers", json={"name": name}).json()["supplier_id"]


def _import(client, supplier_id, brand, ean, price, *, kind="price_list", date="2026-11-01",
            currency="EUR", fx="1", mode="single"):
    batch = client.post("/batches", json={
        "supplier_id": supplier_id, "currency": currency, "kind": kind, "brand": brand,
        "received_date": date, "territory": "T2", "fx_rate_eur": fx}).json()
    client.post("/offers", json={
        "batch_id": batch["batch_id"], "brand_mode": mode, "brand": None if mode == "multi" else brand,
        "offers": [{"ean": ean, "quoted_price": str(price), "description": "Prod " + ean}]})
    return batch["batch_id"]


def test_grid_shows_only_live_columns(client):
    s1 = _supplier(client, "Alpha")
    s2 = _supplier(client, "Beta")
    ean = make_ean("500000000001")
    # Alpha supersedes its own older regular offer; Beta is a second live column.
    _import(client, s1, "Wella", ean, "10.00", date="2026-10-01")
    _import(client, s1, "Wella", ean, "9.00", date="2026-11-01")   # newer -> replaces the 10.00
    _import(client, s2, "Wella", ean, "8.50", date="2026-10-15")
    grid = client.get("/grid", params={"brand": "Wella"}).json()
    assert len(grid["batches"]) == 2                               # one live column per supplier
    row = grid["rows"][0]
    assert row["best_eur"] == "8.50"                               # cheapest live (Beta)
    # the superseded 10.00 is gone; Alpha's live cell is 9.00
    assert "9.00" in row["prices"].values() and "10.00" not in row["prices"].values()


def test_grid_territory_filter(client):
    s1 = _supplier(client, "Alpha")
    ean = make_ean("500000000018")
    b = client.post("/batches", json={"supplier_id": s1, "currency": "EUR", "kind": "price_list",
                                      "brand": "Elemis", "territory": "T1", "fx_rate_eur": "1"}).json()
    client.post("/offers", json={"batch_id": b["batch_id"], "brand_mode": "single",
                                 "brand": "Elemis", "offers": [{"ean": ean, "quoted_price": "5.00"}]})
    assert len(client.get("/grid", params={"brand": "Elemis", "territory": "T1"}).json()["batches"]) == 1
    assert len(client.get("/grid", params={"brand": "Elemis", "territory": "T2"}).json()["batches"]) == 0


def test_bestseller_flag_and_screen(client):
    s1 = _supplier(client, "Alpha")
    ean = make_ean("500000000025")
    _import(client, s1, "Phyto", ean, "4.00")
    client.post(f"/products/{ean}/bestseller", json={"is_bestseller": True})
    bs = client.get("/bestsellers").json()
    assert len(bs) == 1 and bs[0]["ean"] == ean
    assert bs[0]["offers"][0]["unit_price_eur"] == "4.00"
    # grid marks it a bestseller row
    assert client.get("/grid", params={"brand": "Phyto"}).json()["rows"][0]["is_bestseller"] is True


def test_suppliers_update_name_and_country(client):
    sid = _supplier(client, "Old Name")
    r = client.patch(f"/suppliers/{sid}", json={"name": "New Name", "country": "Italy"})
    assert r.status_code == 200
    sup = [s for s in client.get("/suppliers").json() if s["supplier_id"] == sid][0]
    assert sup["name"] == "New Name" and sup["country"] == "Italy"


def test_import_log_and_delete_batch(client):
    s1 = _supplier(client, "Alpha")
    ean = make_ean("500000000032")
    bid = _import(client, s1, "Lierac", ean, "7.00")
    log = client.get("/batches").json()
    assert any(b["batch_id"] == bid and b["n_offers"] == 1 for b in log)
    assert client.get("/grid", params={"brand": "Lierac"}).json()["rows"][0]["best_eur"] == "7.00"
    assert client.delete(f"/batches/{bid}").json()["deleted"] is True
    assert client.get("/grid", params={"brand": "Lierac"}).json()["rows"][0]["best_eur"] is None


def test_product_edit_description_and_brand(client):
    s1 = _supplier(client, "Alpha")
    ean = make_ean("500000000049")
    _import(client, s1, "Eucerin", ean, "3.00")
    client.patch(f"/products/{ean}", json={"description": "Nicer name"})
    got = client.get(f"/products/{ean}/offers").json()
    assert got["description"] == "Nicer name" and got["brand"] == "Eucerin"


def test_add_line_manual_product(client):
    ean = make_ean("500000000056")
    r = client.post("/products", json={"ean": ean, "description": "Hand added", "brand": "Bioderma"})
    assert r.status_code == 200 and r.json()["ean_check"] is True
    assert client.get(f"/products/{ean}/offers").json()["brand"] == "Bioderma"
    # duplicate EAN is refused
    assert client.post("/products", json={"ean": ean, "brand": "Bioderma"}).status_code == 409


def test_review_lists_missing_brand(client):
    s1 = _supplier(client, "Alpha")
    ean = make_ean("500000000063")
    # multi-brand import of an unknown EAN -> needs a brand -> shows in review
    _import(client, s1, "(multi-brand)", ean, "2.00", mode="multi")
    reasons = [r["reason"] for r in client.get("/review").json()]
    assert any("brand" in r for r in reasons)


def test_unknown_brand_tab_then_resolved_by_later_branded_offer(client):
    s1 = _supplier(client, "Alpha")
    ean = make_ean("500000000117")
    # multi-brand import of an unknown EAN -> no brand -> shows in the Unknown-brands tab
    _import(client, s1, "(multi-brand)", ean, "3.00", mode="multi")
    unk = client.get("/grid", params={"unknown": "true"}).json()
    assert unk["unknown"] is True and any(r["ean"] == ean for r in unk["rows"])
    assert client.get("/grid", params={"brand": "Filorga"}).status_code == 404   # brand not created yet
    # a branded offer for a NEW brand with the same EAN arrives -> product is assigned that brand,
    # and both suppliers' prices now compare under it
    s2 = _supplier(client, "Beta")
    _import(client, s2, "Filorga", ean, "2.50", mode="single")
    g = client.get("/grid", params={"brand": "Filorga"}).json()
    row = [r for r in g["rows"] if r["ean"] == ean]
    assert row and row[0]["best_eur"] == "2.50" and len(row[0]["prices"]) == 2   # Alpha + Beta compare
    assert not any(r["ean"] == ean for r in client.get("/grid", params={"unknown": "true"}).json()["rows"])


def test_request_stores_inputs_and_recomputes_live(client):
    s1 = _supplier(client, "Alpha")
    ean = make_ean("500000000070")
    unknown = make_ean("500000000087")
    _import(client, s1, "Wella", ean, "9.00")              # best live = 9.00 EUR
    # target 10.00, 7% margin: need buy <= 9.30 -> 9.00 qualifies
    req = client.post("/requests", json={
        "customer": "ACME Retail", "brand": "Wella", "margin": "0.07",
        "lines": [{"ean": ean, "target_price": "10.00", "currency": "EUR"},
                  {"ean": unknown, "target_price": "5.00", "currency": "EUR"}]}).json()
    assert req["ok"] == 1 and req["unknown"] == 1 and req["count"] == 2
    rid = req["request_id"]
    # now a cheaper supplier arrives; the SAME stored request recomputes live
    s2 = _supplier(client, "Beta")
    _import(client, s2, "Wella", ean, "6.00")
    again = client.get(f"/requests/{rid}").json()
    ok_line = [r for r in again["results"] if r["status"] == "ok"][0]
    assert ok_line["buy_eur"] == "6.00" and ok_line["supplier"] == "Beta"
    assert client.get("/customers").json() == ["ACME Retail"]
    assert client.delete(f"/requests/{rid}").json()["deleted"] is True
    assert client.get("/requests").json() == []


def test_request_no_supplier_meets_margin(client):
    s1 = _supplier(client, "Alpha")
    ean = make_ean("500000000094")
    _import(client, s1, "Wella", ean, "9.80")
    req = client.post("/requests", json={
        "margin": "0.07", "lines": [{"ean": ean, "target_price": "10.00", "currency": "EUR"}]}).json()
    assert req["ok"] == 0 and req["no"] == 1
    line = req["results"][0]
    assert line["status"] == "no" and line["buy_eur"] == "9.80"


def test_request_non_eur_target_uses_fx(client):
    s1 = _supplier(client, "Alpha")
    ean = make_ean("500000000100")
    _import(client, s1, "Wella", ean, "9.00")              # 9.00 EUR live
    client.post("/fx/rates", json={"rate_date": "2026-11-03", "currency": "GBP", "rate": "0.80"})
    # target 15 GBP -> 18.75 EUR; 7% margin needs buy <= 17.44 -> 9.00 qualifies
    req = client.post("/requests", json={
        "margin": "0.07", "lines": [{"ean": ean, "target_price": "15.00", "currency": "GBP"}]}).json()
    assert req["ok"] == 1 and req["results"][0]["target_eur"] == "18.75"
