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


def _import_desc(client, supplier_id, brand, ean, price, desc, *, date="2026-11-01"):
    batch = client.post("/batches", json={
        "supplier_id": supplier_id, "currency": "EUR", "kind": "price_list", "brand": brand,
        "received_date": date, "territory": "T2", "fx_rate_eur": "1"}).json()
    client.post("/offers", json={
        "batch_id": batch["batch_id"], "brand_mode": "single", "brand": brand,
        "offers": [{"ean": ean, "quoted_price": str(price), "description": desc}]})
    return batch["batch_id"]


def test_size_description_is_corrected_on_reimport(client):
    s1 = _supplier(client, "Alpha")
    ean = make_ean("500000000209")
    _import_desc(client, s1, "Bioderma", ean, "9.00", "100 ml", date="2026-10-01")  # size slipped in
    assert client.get(f"/products/{ean}/offers").json()["description"] == "100 ml"
    _import_desc(client, s1, "Bioderma", ean, "9.00", "Sensibio H2O", date="2026-11-01")  # real name
    assert client.get(f"/products/{ean}/offers").json()["description"] == "Sensibio H2O"
    # a good name is never overwritten by a later file
    _import_desc(client, s1, "Bioderma", ean, "9.00", "Other Label", date="2026-11-02")
    assert client.get(f"/products/{ean}/offers").json()["description"] == "Sensibio H2O"


def test_merge_brands_moves_products_and_removes_source(client):
    s1 = _supplier(client, "Alpha")
    e1 = make_ean("500000000407"); e2 = make_ean("500000000414")
    _import(client, s1, "JEAN PAUL GAULTIER", e1, "9.00")
    _import(client, s1, "J.P. Gaultier", e2, "8.00")
    before = {b["name"] for b in client.get("/brands").json()}
    assert {"JEAN PAUL GAULTIER", "J.P. Gaultier"} <= before
    r = client.post("/brands/merge", json={"from_brand": "J.P. Gaultier", "into_brand": "JEAN PAUL GAULTIER"})
    assert r.status_code == 200, r.text
    assert r.json()["products_moved"] == 1
    names = {b["name"] for b in client.get("/brands").json()}
    assert "J.P. Gaultier" not in names and "JEAN PAUL GAULTIER" in names
    # both EANs now live under the kept brand
    assert client.get(f"/products/{e1}/offers").json()["brand"] == "JEAN PAUL GAULTIER"
    assert client.get(f"/products/{e2}/offers").json()["brand"] == "JEAN PAUL GAULTIER"
    # guards
    assert client.post("/brands/merge", json={"from_brand": "JEAN PAUL GAULTIER", "into_brand": "JEAN PAUL GAULTIER"}).status_code == 422
    assert client.post("/brands/merge", json={"from_brand": "Nope", "into_brand": "JEAN PAUL GAULTIER"}).status_code == 404


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


def test_usd_offer_without_rate_appears_once_rate_added(client):
    s = _supplier(client, "PassionCo")
    ean = make_ean("500000000155")
    b = client.post("/batches", json={"supplier_id": s, "currency": "USD", "kind": "price_list",
                                      "brand": "Color WOW", "fx_rate_eur": None, "fx_locked": 0}).json()
    client.post("/offers", json={"batch_id": b["batch_id"], "brand_mode": "single", "brand": "Color WOW",
                                 "offers": [{"ean": ean, "quoted_price": "12.00"}]})
    # no USD rate yet: the product exists but has no EUR price and no column in the grid
    g = client.get("/grid", params={"brand": "Color WOW"}).json()
    assert [r for r in g["rows"] if r["ean"] == ean][0]["best_eur"] is None and len(g["batches"]) == 0
    # add a USD rate -> the batch converts via the daily-rate fallback, no re-import
    client.post("/fx/rates", json={"rate_date": "2026-11-05", "currency": "USD", "rate": "1.25"})
    g2 = client.get("/grid", params={"brand": "Color WOW"}).json()
    row = [r for r in g2["rows"] if r["ean"] == ean][0]
    assert row["best_eur"] == "9.60" and len(g2["batches"]) == 1   # 12.00 USD / 1.25 = 9.60 EUR


def test_grid_currency_filter_and_original_price(client):
    s_eur = _supplier(client, "EurSup"); s_usd = _supplier(client, "UsdSup")
    ean = make_ean("500000000162")
    _import(client, s_eur, "Color WOW", ean, "10.00")                 # EUR offer
    client.post("/fx/rates", json={"rate_date": "2026-11-05", "currency": "USD", "rate": "1.25"})
    b = client.post("/batches", json={"supplier_id": s_usd, "currency": "USD", "kind": "price_list",
                                      "brand": "Color WOW", "fx_rate_eur": None, "fx_locked": 0}).json()
    client.post("/offers", json={"batch_id": b["batch_id"], "brand_mode": "single", "brand": "Color WOW",
                                 "offers": [{"ean": ean, "quoted_price": "15.00"}]})
    g = client.get("/grid", params={"brand": "Color WOW"}).json()
    assert set(g["currencies"]) == {"EUR", "USD"}                     # both offered -> filter available
    usd = client.get("/grid", params={"brand": "Color WOW", "currency": "USD"}).json()
    assert len(usd["batches"]) == 1 and usd["batches"][0]["currency"] == "USD"
    bid = str(usd["batches"][0]["batch_id"])
    row = [r for r in usd["rows"] if r["ean"] == ean][0]
    assert row["orig"][bid] == "15.00" and row["prices"][bid] == "12.00"   # 15 USD /1.25 = 12.00 EUR


def test_currencies_offers_eur_and_usd(client):
    cur = client.get("/currencies").json()["currencies"]
    assert cur[0] == "EUR" and "USD" in cur          # configured symbols are offerable at import


def test_multibrand_import_files_by_file_brand_column(client):
    s = _supplier(client, "StanCo")
    b = client.post("/batches", json={"supplier_id": s, "currency": "EUR", "kind": "price_list",
                                      "brand": "(multi-brand)", "fx_rate_eur": "1"}).json()
    e_w = make_ean("500000000124"); e_f = make_ean("500000000131"); e_u = make_ean("500000000148")
    # multi-brand with a file brand column: the frontend sends a per-row brand only for known /
    # owner-approved names; a row with no brand goes to Unknown.
    client.post("/offers", json={"batch_id": b["batch_id"], "brand_mode": "multi", "brand": None,
        "offers": [{"ean": e_w, "quoted_price": "5.00", "brand": "Wella"},
                   {"ean": e_f, "quoted_price": "6.00", "brand": "Filorga"},
                   {"ean": e_u, "quoted_price": "7.00"}]})
    assert any(r["ean"] == e_w for r in client.get("/grid", params={"brand": "Wella"}).json()["rows"])
    assert any(r["ean"] == e_f for r in client.get("/grid", params={"brand": "Filorga"}).json()["rows"])
    unk = client.get("/grid", params={"unknown": "true"}).json()["rows"]
    assert any(r["ean"] == e_u for r in unk) and not any(r["ean"] in (e_w, e_f) for r in unk)


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


def test_request_without_target_lists_suppliers_cheapest_first(client):
    s1 = _supplier(client, "Alpha"); s2 = _supplier(client, "Beta")
    ean = make_ean("500000000179")
    _import(client, s1, "Wella", ean, "9.00")
    _import(client, s2, "Wella", ean, "6.00")
    req = client.post("/requests", json={"lines": [{"ean": ean, "currency": "EUR"}]}).json()  # no target
    assert req["ok"] == 1 and req["count"] == 1
    line = req["results"][0]
    assert line["status"] == "ok" and line["target_eur"] is None
    assert [(x["supplier"], x["unit_price_eur"]) for x in line["suppliers"]] == [("Beta", "6.00"), ("Alpha", "9.00")]
    # saved + recomputed live: a cheaper supplier later becomes the top of the list
    s3 = _supplier(client, "Gamma"); _import(client, s3, "Wella", ean, "5.00")
    again = client.get(f"/requests/{req['request_id']}").json()
    assert again["results"][0]["suppliers"][0] == {"supplier": "Gamma", "unit_price_eur": "5.00"}


def test_request_shows_submitted_description_for_unknown_ean(client):
    # an EAN not in the DB keeps the description sent with the request line, and it survives a reload
    ean = make_ean("500000000308")
    req = client.post("/requests", json={
        "lines": [{"ean": ean, "description": "Filorga NCEF-Shot 15ml"}]}).json()
    line = req["results"][0]
    assert line["status"] == "unknown" and line["description"] == "Filorga NCEF-Shot 15ml"
    again = client.get(f"/requests/{req['request_id']}").json()
    assert again["results"][0]["description"] == "Filorga NCEF-Shot 15ml"
    # once the EAN becomes a real product, the DB description wins
    s1 = _supplier(client, "Alpha"); _import(client, s1, "Wella", ean, "9.00")
    after = client.get(f"/requests/{req['request_id']}").json()
    assert after["results"][0]["status"] == "ok"
    assert after["results"][0]["description"] == "Prod " + ean


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


def test_offer_export_fills_template_and_filters_brand(client):
    """Export fills the owner's .xlsx template (styling + Total formula preserved) and, for a
    multi-brand supplier offer, exports only the brand being viewed, with the margin applied."""
    import io

    import openpyxl

    s = _supplier(client, "D&G Supplier")
    ean_a = make_ean("500000000101")
    ean_b = make_ean("500000000102")
    _import(client, s, "Alpha", ean_a, "10.00", date="2026-10-01")   # establish the two brands
    _import(client, s, "Beta", ean_b, "20.00", date="2026-10-01")
    multi = client.post("/batches", json={
        "supplier_id": s, "currency": "EUR", "kind": "price_list", "brand": "(multi-brand)",
        "received_date": "2026-11-01", "territory": "T2", "fx_rate_eur": "1"}).json()["batch_id"]
    client.post("/offers", json={"batch_id": multi, "brand_mode": "multi", "brand": None, "offers": [
        {"ean": ean_a, "quoted_price": "11.00", "description": "ALPHA PROD"},
        {"ean": ean_b, "quoted_price": "21.00", "description": "BETA PROD"}]})

    r = client.get(f"/batches/{multi}/export", params={"margin": "0.07", "brand": "Alpha"})
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    ws = openpyxl.load_workbook(io.BytesIO(r.content)).active
    # template fidelity: the header keeps its bold + fill, and the Total formula survives
    assert ws["A9"].value == "EAN" and ws["A9"].font.bold and ws["A9"].fill.patternType == "solid"
    assert ws["E10"].value == "=D10*C10"
    # only Alpha's line, from row 10, margin applied: 11.00 / (1-0.07) = 11.83
    assert ws["A10"].value == ean_a and ws["B10"].value == "Prod " + ean_a
    assert round(float(ws["C10"].value), 2) == 11.83
    assert ws["A11"].value is None                       # Beta's line is excluded

    ws2 = openpyxl.load_workbook(io.BytesIO(
        client.get(f"/batches/{multi}/export", params={"margin": "0.07", "brand": "Beta"}).content)).active
    assert ws2["A10"].value == ean_b and ws2["A11"].value is None
    assert round(float(ws2["C10"].value), 2) == 22.58    # 21.00 / 0.93

    # a margin of 100%+ is rejected
    assert client.get(f"/batches/{multi}/export", params={"margin": "1.5"}).status_code == 422

    # the offer DETAIL view is brand-scoped too: all lines without a brand, one line with it
    allp = client.get(f"/batches/{multi}/offers").json()
    assert len(allp["lines"]) == 2
    only = client.get(f"/batches/{multi}/offers", params={"brand": "Alpha"}).json()
    assert len(only["lines"]) == 1 and only["lines"][0]["ean"] == ean_a
    assert only["brand"] == "Alpha"                      # header shows the viewed brand
