"""The offline export bundle: a valid ZIP whose SQLite snapshot opens and whose CSVs parse."""
import csv
import io
import sqlite3
import zipfile

from app import db, export, repo
from app.importer import import_rows
from app.offer_types import REGULAR

from .synth import make_ean


def test_build_bundle_has_members_that_parse(tmp_path):
    path = str(tmp_path / "e.db")
    conn = db.connect(path)
    db.migrate(conn)
    sid = repo.create_supplier(conn, "Acme")
    bx = repo.get_or_create_brand(conn, "BrandX")
    ean = make_ean("700000000001")
    b = repo.create_batch(conn, sid, "USD", fx_rate_eur="1.25", kind=REGULAR,
                          received_date="2026-11-01", brand="BrandX")
    import_rows(conn, b, [{"ean": ean, "quoted_price": "10.00", "description": "Thing"}], brand_id=bx)

    data = export.build_bundle(conn, path)
    conn.close()

    zf = zipfile.ZipFile(io.BytesIO(data))
    assert set(zf.namelist()) >= {"pricedb.sqlite", "offers.csv", "current_best.csv", "README.txt"}

    offers = list(csv.DictReader(io.StringIO(zf.read("offers.csv").decode("utf-8"))))
    assert any(row["ean"] == ean and row["offer_type"] == "Regular offer"
               and row["per_piece_price"] == "10.00" and row["per_piece_price_eur"] == "8.00"
               for row in offers)                                    # 10.00 / 1.25 = 8.00

    best = list(csv.DictReader(io.StringIO(zf.read("current_best.csv").decode("utf-8"))))
    assert any(row["ean"] == ean and row["best_unit_price_eur"] == "8.00" for row in best)

    snap = tmp_path / "snap.sqlite"                                  # the snapshot opens + holds the data
    snap.write_bytes(zf.read("pricedb.sqlite"))
    c2 = sqlite3.connect(str(snap))
    assert c2.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 1
    c2.close()


def test_export_endpoint_returns_zip(client):
    supplier = client.post("/suppliers", json={"name": "Acme"}).json()
    batch = client.post("/batches", json={"supplier_id": supplier["supplier_id"],
                                          "currency": "EUR", "kind": "price_list",
                                          "brand": "BrandX", "fx_rate_eur": "1"}).json()
    client.post("/offers", json={
        "batch_id": batch["batch_id"], "brand_mode": "single", "brand": "BrandX",
        "offers": [{"ean": make_ean("700000000002"), "quoted_price": "5.00"}]})
    r = client.get("/export")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert {"offers.csv", "current_best.csv", "pricedb.sqlite"} <= set(zf.namelist())
