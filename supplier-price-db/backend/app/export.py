"""The offline download bundle (design §5 / §6): a ZIP the owner saves to their own machine.

Contents: a consistent SQLite snapshot plus plain CSV exports of all offers and current_best,
openable without this software — the third leg of the 3-2-1 copy story. EUR figures are
computed on read (app.money), not stored. Business data only (design §2.8).
"""
from __future__ import annotations

import csv
import io
import os
import sqlite3
import tempfile
import zipfile
from datetime import date as _date
from decimal import Decimal

from . import money, offer_types

_CENT = Decimal("0.01")


def _snapshot_sqlite(db_path: str) -> bytes:
    """A consistent copy of the live DB via SQLite's backup API (safe under WAL)."""
    fd, tmp = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        with open(tmp, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _eur(price_minor: int, rate: str | None) -> str:
    if not rate:
        return ""
    return str(money.unit_price_eur(price_minor, rate).quantize(_CENT))


def _offers_csv(conn) -> str:
    rows = conn.execute(
        "SELECT p.ean, p.description, br.name AS brand, s.name AS supplier, "
        "       b.kind, b.territory, o.currency, o.price_original, b.fx_rate_eur, "
        "       b.received_date, b.ended_at, b.moq, b.lead_time, b.notes, b.source_file "
        "FROM offers o "
        "JOIN offer_batches b ON b.batch_id = o.batch_id "
        "JOIN products p ON p.product_id = o.product_id "
        "JOIN suppliers s ON s.supplier_id = b.supplier_id "
        "LEFT JOIN brands br ON br.brand_id = p.brand_id "
        "ORDER BY p.ean, o.offer_id"
    ).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ean", "description", "brand", "supplier", "offer_type", "territory",
                "currency", "per_piece_price", "fx_rate_eur", "per_piece_price_eur",
                "offer_date", "ended_at", "moq", "lead_time", "notes", "source_ref"])
    for r in rows:
        rate = r["fx_rate_eur"] or ("1" if (r["currency"] or "").upper() == "EUR" else None)
        w.writerow([
            r["ean"], r["description"] or "", r["brand"] or "", r["supplier"] or "",
            offer_types.kind_label(r["kind"]) or "", r["territory"] or "", r["currency"],
            str(money.from_minor(r["price_original"]).quantize(_CENT)), r["fx_rate_eur"] or "",
            _eur(r["price_original"], rate), r["received_date"] or "", r["ended_at"] or "",
            r["moq"] or "", r["lead_time"] or "", r["notes"] or "", r["source_file"] or "",
        ])
    return buf.getvalue()


def _current_best_csv(conn) -> str:
    rows = conn.execute(
        "SELECT p.ean, p.description, br.name AS brand, s.name AS supplier, "
        "       cb.best_unit_price_eur, cb.updated_at "
        "FROM current_best cb "
        "JOIN products p ON p.product_id = cb.product_id "
        "LEFT JOIN suppliers s ON s.supplier_id = cb.best_supplier_id "
        "LEFT JOIN brands br ON br.brand_id = p.brand_id "
        "ORDER BY p.ean"
    ).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ean", "description", "brand", "best_supplier", "best_unit_price_eur", "updated_at"])
    for r in rows:
        w.writerow([r["ean"], r["description"] or "", r["brand"] or "", r["supplier"] or "",
                    str(money.from_minor(r["best_unit_price_eur"]).quantize(_CENT)), r["updated_at"]])
    return buf.getvalue()


def build_bundle(conn, db_path: str) -> bytes:
    """ZIP bytes: the SQLite snapshot + offers.csv + current_best.csv + a README."""
    stamp = _date.today().isoformat()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("pricedb.sqlite", _snapshot_sqlite(db_path))
        z.writestr("offers.csv", _offers_csv(conn))
        z.writestr("current_best.csv", _current_best_csv(conn))
        z.writestr("README.txt",
                   f"Riviera supplier price DB — offline export {stamp}\n\n"
                   "pricedb.sqlite   : full database (open with any SQLite tool)\n"
                   "offers.csv       : every offer line; EUR computed at each batch's rate\n"
                   "current_best.csv : cheapest live offer per product\n\n"
                   "Business data only — no personal data (design 2.8).\n")
    return out.getvalue()
