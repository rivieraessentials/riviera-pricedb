"""Data access for the price DB. Pure stdlib (sqlite3 + Decimal) so it is fully
testable without the web layer.

APPEND-ONLY by design (LED-1159 #6, design §1.1): there is deliberately NO function
that updates an existing offer's price. A correction is a NEW offer in a NEW batch;
history is never rewritten. current_best is the one derived cache and is rebuilt, not
edited in place.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal

from . import money


class EANExistsError(ValueError):
    """Attempted to create a second product with an EAN that already exists."""


# ---------------------------------------------------------------- reference data

def create_brand(conn: sqlite3.Connection, name: str, aliases: list[str] | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO brands (name, aliases_json) VALUES (?, ?)",
        (name, json.dumps(aliases or [])),
    )
    return int(cur.lastrowid)


def create_supplier(conn: sqlite3.Connection, name: str,
                    default_territory: str | None = None, notes: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO suppliers (name, default_territory, notes) VALUES (?, ?, ?)",
        (name, default_territory, notes),
    )
    return int(cur.lastrowid)


# ---------------------------------------------------------------- products (EAN = identity)

def create_product(conn: sqlite3.Connection, ean: str, *, description: str | None = None,
                   brand_id: int | None = None, size: str | None = None,
                   status: str = "active", first_seen_batch_id: int | None = None) -> int:
    """Create a product. EAN is stored as TEXT (leading zeros survive). A duplicate EAN
    raises EANExistsError — the UNIQUE constraint makes duplicates impossible."""
    ean = str(ean)
    try:
        cur = conn.execute(
            "INSERT INTO products (ean, description, brand_id, size, status, first_seen_batch_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ean, description, brand_id, size, status, first_seen_batch_id),
        )
    except sqlite3.IntegrityError as exc:
        if "products.ean" in str(exc) or "UNIQUE" in str(exc).upper():
            raise EANExistsError(f"product with EAN {ean!r} already exists") from exc
        raise
    return int(cur.lastrowid)


def get_product_by_ean(conn: sqlite3.Connection, ean: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM products WHERE ean = ?", (str(ean),)).fetchone()


def get_or_create_product(conn: sqlite3.Connection, ean: str, **defaults) -> tuple[int, bool]:
    row = get_product_by_ean(conn, ean)
    if row is not None:
        return int(row["product_id"]), False
    return create_product(conn, ean, **defaults), True


# ---------------------------------------------------------------- batches + checksum

def find_batch_by_sha(conn: sqlite3.Connection, sha256: str) -> sqlite3.Row | None:
    """Duplicate-file detection (§2.1): the first batch whose uploaded bytes match."""
    if not sha256:
        return None
    return conn.execute(
        "SELECT * FROM offer_batches WHERE source_sha256 = ? ORDER BY batch_id LIMIT 1",
        (sha256,),
    ).fetchone()


def create_batch(conn: sqlite3.Connection, supplier_id: int, currency: str, *,
                received_date: str | None = None, territory: str | None = None,
                kind: str | None = None, source_file: str | None = None,
                source_sha256: str | None = None, fx_rate_eur: str | None = None,
                fx_source: str | None = None, fx_locked: int = 0,
                label: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO offer_batches (supplier_id, received_date, currency, territory, kind, "
        " source_file, source_sha256, fx_rate_eur, fx_source, fx_locked, label) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (supplier_id, received_date, currency, territory, kind, source_file,
         source_sha256, fx_rate_eur, fx_source, int(fx_locked), label),
    )
    return int(cur.lastrowid)


# ---------------------------------------------------------------- offers (append-only)

def create_offer(conn: sqlite3.Connection, batch_id: int, product_id: int,
                price_original_minor: int, currency: str, *,
                pieces_per_box: int | None = None, price_basis: str = "per_piece",
                raw_row: dict | str | None = None, available_qty: int | None = None,
                moq: int | None = None, valid_until: str | None = None) -> int:
    """Insert one offer. price_original_minor MUST already be integer minor units
    (use money.to_minor at the import boundary). No float ever reaches this row."""
    if not isinstance(price_original_minor, int) or isinstance(price_original_minor, bool):
        raise TypeError("price_original_minor must be int minor units (use money.to_minor)")
    raw = raw_row if (raw_row is None or isinstance(raw_row, str)) else json.dumps(raw_row)
    cur = conn.execute(
        "INSERT INTO offers (batch_id, product_id, price_original, currency, pieces_per_box, "
        " price_basis, raw_row, available_qty, moq, valid_until) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (batch_id, product_id, price_original_minor, currency, pieces_per_box,
         price_basis, raw, available_qty, moq, valid_until),
    )
    return int(cur.lastrowid)


def add_quoted_offer(conn: sqlite3.Connection, batch_id: int, product_id: int,
                    quoted_price, currency: str, **kw) -> int:
    """Convenience: convert a quoted price (str/Decimal/int) to minor units EXACTLY
    (raises money.PriceScaleError if it would need rounding) and append the offer."""
    return create_offer(conn, batch_id, product_id, money.to_minor(quoted_price), currency, **kw)


@dataclass
class OfferView:
    offer_id: int
    batch_id: int
    product_id: int
    supplier_id: int
    currency: str
    price_basis: str
    pieces_per_box: int | None
    price_original: Decimal      # as quoted, exact
    price_eur: Decimal           # computed on read
    unit_price_eur: Decimal      # per single piece, computed on read (the comparison key)
    fx_rate_eur: str | None
    received_date: str | None
    created_at: str


def _batch_rate(row: sqlite3.Row) -> str | None:
    """Rate to use for EUR conversion: the batch's import-date rate, or '1' for EUR."""
    rate = row["fx_rate_eur"]
    if rate is None and (row["currency"] or "").upper() == "EUR":
        return "1"
    return rate


def product_offer_history(conn: sqlite3.Connection, product_id: int) -> list[OfferView]:
    """Full append-only offer history for a product, EUR and per-piece computed on read."""
    rows = conn.execute(
        "SELECT o.*, b.supplier_id AS b_supplier, b.fx_rate_eur AS b_fx, b.currency AS b_currency, "
        "       b.received_date AS b_received "
        "FROM offers o JOIN offer_batches b ON b.batch_id = o.batch_id "
        "WHERE o.product_id = ? ORDER BY o.offer_id",
        (product_id,),
    ).fetchall()
    views: list[OfferView] = []
    for r in rows:
        rate = r["b_fx"] if r["b_fx"] is not None else ("1" if (r["currency"] or "").upper() == "EUR" else None)
        price_eur = money.price_to_eur(r["price_original"], rate) if rate else Decimal("NaN")
        unit = (money.unit_price_eur(r["price_original"], rate, r["pieces_per_box"], r["price_basis"])
                if rate else Decimal("NaN"))
        views.append(OfferView(
            offer_id=r["offer_id"], batch_id=r["batch_id"], product_id=r["product_id"],
            supplier_id=r["b_supplier"], currency=r["currency"], price_basis=r["price_basis"],
            pieces_per_box=r["pieces_per_box"], price_original=money.from_minor(r["price_original"]),
            price_eur=price_eur, unit_price_eur=unit, fx_rate_eur=r["b_fx"],
            received_date=r["b_received"], created_at=r["created_at"],
        ))
    return views


# ---------------------------------------------------------------- fx + settings

def set_fx_rate(conn: sqlite3.Connection, rate_date: str, currency: str,
               rate_text: str, source: str | None = None) -> None:
    conn.execute(
        "INSERT INTO fx_rates (rate_date, currency, rate, source) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(rate_date, currency) DO UPDATE SET rate = excluded.rate, source = excluded.source",
        (rate_date, currency, str(rate_text), source),
    )


def get_fx_rate(conn: sqlite3.Connection, rate_date: str, currency: str) -> str | None:
    row = conn.execute(
        "SELECT rate FROM fx_rates WHERE rate_date = ? AND currency = ?", (rate_date, currency)
    ).fetchone()
    return row["rate"] if row else None


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# ---------------------------------------------------------------- current_best (rebuildable cache)

def _live_offer_rows(conn: sqlite3.Connection, product_id: int | None) -> list[sqlite3.Row]:
    """Offers that still count for 'best price', per the freshness_rule setting.

    freshness_rule is an OPEN decision (O2). Only the PLACEHOLDER 'all' (every offer
    counts) is implemented; the real rules wait on the end user so we don't invent one.
    """
    rule = (get_setting(conn, "freshness_rule", "all") or "all").strip()
    if rule != "all":
        raise NotImplementedError(
            f"freshness_rule {rule!r} not implemented yet — O2 is undecided; "
            f"current_best currently supports only the placeholder 'all'"
        )
    sql = ("SELECT o.*, b.supplier_id AS b_supplier, b.fx_rate_eur AS b_fx, b.currency AS b_currency "
           "FROM offers o JOIN offer_batches b ON b.batch_id = o.batch_id")
    params: tuple = ()
    if product_id is not None:
        sql += " WHERE o.product_id = ?"
        params = (product_id,)
    return conn.execute(sql, params).fetchall()


def rebuild_current_best(conn: sqlite3.Connection, product_ids: list[int] | None = None) -> int:
    """Recompute the cheapest live offer per product and write current_best.

    Full rebuild (product_ids=None) clears and recomputes everything; a scoped rebuild
    touches only the named products. Deterministic: lowest unit_price_eur wins, ties
    broken by lowest offer_id. Returns the number of current_best rows written.
    """
    conn.execute("BEGIN")
    try:
        if product_ids is None:
            conn.execute("DELETE FROM current_best")
            targets: list[int] | None = None
        else:
            targets = [int(p) for p in product_ids]
            for pid in targets:
                conn.execute("DELETE FROM current_best WHERE product_id = ?", (pid,))

        # gather candidate offers
        best: dict[int, tuple[Decimal, int, int, int]] = {}  # product_id -> (unit_eur, offer_id, supplier, minor)
        rows: list[sqlite3.Row] = []
        if targets is None:
            rows = _live_offer_rows(conn, None)
        else:
            for pid in targets:
                rows.extend(_live_offer_rows(conn, pid))

        for r in rows:
            rate = r["b_fx"] if r["b_fx"] is not None else ("1" if (r["b_currency"] or "").upper() == "EUR" else None)
            if not rate:
                continue  # cannot convert to EUR without a rate; skip rather than guess
            unit = money.unit_price_eur(r["price_original"], rate, r["pieces_per_box"], r["price_basis"])
            pid = int(r["product_id"])
            cand = (unit, int(r["offer_id"]), int(r["b_supplier"]), r["price_original"])
            cur = best.get(pid)
            if cur is None or unit < cur[0] or (unit == cur[0] and cand[1] < cur[1]):
                best[pid] = cand

        written = 0
        for pid, (unit, offer_id, supplier_id, _minor) in best.items():
            conn.execute(
                "INSERT INTO current_best (product_id, best_offer_id, best_unit_price_eur, best_supplier_id) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(product_id) DO UPDATE SET best_offer_id=excluded.best_offer_id, "
                " best_unit_price_eur=excluded.best_unit_price_eur, best_supplier_id=excluded.best_supplier_id, "
                " updated_at=datetime('now')",
                (pid, offer_id, money.eur_to_minor(unit), supplier_id),
            )
            written += 1
        conn.execute("COMMIT")
        return written
    except Exception:
        conn.execute("ROLLBACK")
        raise


def get_current_best(conn: sqlite3.Connection, product_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM current_best WHERE product_id = ?", (product_id,)).fetchone()
