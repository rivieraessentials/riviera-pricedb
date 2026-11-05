"""Data access for the price DB. Pure stdlib (sqlite3 + Decimal) so it is fully
testable without the web layer.

APPEND-ONLY by design (LED-1159 #6, design §1.1): there is deliberately NO function
that updates an existing offer's price. A correction is a NEW offer in a NEW batch;
history is never rewritten. current_best is the one derived cache and is rebuilt, not
edited in place. The "End offer" action marks a stock-offer BATCH ended (ended_at) — it
changes which offers are *live*, never an offer's recorded price.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal

from . import money


class EANExistsError(ValueError):
    """Attempted to create a second product with an EAN that already exists."""


# ---------------------------------------------------------------- reference data

def create_brand(conn: sqlite3.Connection, name: str) -> int:
    cur = conn.execute("INSERT INTO brands (name) VALUES (?)", (name,))
    return int(cur.lastrowid)


def get_brand_by_name(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM brands WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()


def get_or_create_brand(conn: sqlite3.Connection, name: str) -> int:
    """Resolve a brand name to its id (case-insensitive), creating it if new."""
    row = get_brand_by_name(conn, name)
    if row is not None:
        return int(row["brand_id"])
    return create_brand(conn, name)


def brand_name(conn: sqlite3.Connection, brand_id: int | None) -> str | None:
    if brand_id is None:
        return None
    row = conn.execute("SELECT name FROM brands WHERE brand_id = ?", (brand_id,)).fetchone()
    return row["name"] if row else None


def create_supplier(conn: sqlite3.Connection, name: str,
                    default_territory: str | None = None, notes: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO suppliers (name, default_territory, notes) VALUES (?, ?, ?)",
        (name, default_territory, notes),
    )
    return int(cur.lastrowid)


# ---------------------------------------------------------------- products (EAN = identity)

def create_product(conn: sqlite3.Connection, ean: str, *, description: str | None = None,
                   brand_id: int | None = None, status: str = "active",
                   first_seen_batch_id: int | None = None) -> int:
    """Create a product. EAN is stored as TEXT (leading zeros survive). A duplicate EAN
    raises EANExistsError — the UNIQUE constraint makes duplicates impossible."""
    ean = str(ean)
    try:
        cur = conn.execute(
            "INSERT INTO products (ean, description, brand_id, status, first_seen_batch_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (ean, description, brand_id, status, first_seen_batch_id),
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


def assign_brand(conn: sqlite3.Connection, product_id: int, brand_id: int) -> None:
    """Set a product's brand and clear its needs_review status (brand resolution, §2.5).

    Only fills a brand; it never rewrites an offer. Used when the picked brand fills a
    product that had none, and by the review screen to resolve a 'needs a brand' product.
    """
    conn.execute(
        "UPDATE products SET brand_id = ?, status = 'active' WHERE product_id = ?",
        (brand_id, product_id),
    )


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
                kind: str | None = None, brand: str | None = None,
                moq: str | None = None, lead_time: str | None = None,
                notes: str | None = None, source_file: str | None = None,
                source_sha256: str | None = None, fx_rate_eur: str | None = None,
                fx_source: str | None = None, fx_locked: int = 0,
                label: str | None = None) -> int:
    """Create an offer batch (one supplier upload = one grid column).

    kind is a STRUCTURED choice: 'price_list' | 'stock_offer' (drives §3.3 replacement and
    the End-offer action). moq/lead_time/notes are FREE TEXT, display-only (O3, items 26-27).
    """
    cur = conn.execute(
        "INSERT INTO offer_batches (supplier_id, received_date, currency, territory, kind, "
        " brand, moq, lead_time, notes, source_file, source_sha256, fx_rate_eur, fx_source, "
        " fx_locked, label) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (supplier_id, received_date, currency, territory, kind, brand, moq, lead_time,
         notes, source_file, source_sha256, fx_rate_eur, fx_source, int(fx_locked), label),
    )
    return int(cur.lastrowid)


def end_offer(conn: sqlite3.Connection, batch_id: int, ended_at: str) -> None:
    """The "End offer" action (§8): mark a stock-offer batch ended so it stops counting
    as live (§3.3). Not a price edit — history is untouched; only `live` membership changes.
    """
    row = conn.execute("SELECT kind FROM offer_batches WHERE batch_id = ?", (batch_id,)).fetchone()
    if row is None:
        raise ValueError(f"batch {batch_id} not found")
    if (row["kind"] or "") != "stock_offer":
        raise ValueError("End offer applies to stock offers only; price lists are replaced, not ended")
    conn.execute("UPDATE offer_batches SET ended_at = ? WHERE batch_id = ?", (ended_at, batch_id))


# ---------------------------------------------------------------- offers (append-only)

def create_offer(conn: sqlite3.Connection, batch_id: int, product_id: int,
                price_original_minor: int, currency: str) -> int:
    """Insert one offer. price_original_minor MUST already be an integer minor-unit
    PER-PIECE 2dp price (use money.import_price_minor at the import boundary). No float
    ever reaches this row."""
    if not isinstance(price_original_minor, int) or isinstance(price_original_minor, bool):
        raise TypeError("price_original_minor must be int minor units (use money.import_price_minor)")
    cur = conn.execute(
        "INSERT INTO offers (batch_id, product_id, price_original, currency) VALUES (?, ?, ?, ?)",
        (batch_id, product_id, price_original_minor, currency),
    )
    return int(cur.lastrowid)


def add_quoted_offer(conn: sqlite3.Connection, batch_id: int, product_id: int,
                    quoted_price, currency: str, *,
                    pieces_per_box: int | None = None, price_basis: str = "per_piece") -> int:
    """Convenience: turn a quoted price into a per-piece 2dp price (dividing by
    pieces_per_box first when per_box, §1.2/§2.4) and append the offer."""
    ppb = pieces_per_box if price_basis == "per_box" else None
    return create_offer(conn, batch_id, product_id,
                        money.import_price_minor(quoted_price, ppb), currency)


@dataclass
class OfferView:
    offer_id: int
    batch_id: int
    product_id: int
    supplier_id: int
    currency: str
    kind: str | None
    price_original: Decimal      # the stored per-piece 2dp price
    price_eur: Decimal           # computed on read
    unit_price_eur: Decimal      # per single piece, computed on read (the comparison key)
    fx_rate_eur: str | None
    received_date: str | None
    ended_at: str | None
    created_at: str


def _rate_for(currency: str | None, fx_rate_eur: str | None) -> str | None:
    """Rate to use for EUR conversion: the batch's rate, or '1' for EUR."""
    if fx_rate_eur is not None:
        return fx_rate_eur
    return "1" if (currency or "").upper() == "EUR" else None


def product_offer_history(conn: sqlite3.Connection, product_id: int) -> list[OfferView]:
    """Full append-only offer history for a product, EUR / per-piece computed on read."""
    rows = conn.execute(
        "SELECT o.*, b.supplier_id AS b_supplier, b.fx_rate_eur AS b_fx, b.currency AS b_currency, "
        "       b.kind AS b_kind, b.received_date AS b_received, b.ended_at AS b_ended "
        "FROM offers o JOIN offer_batches b ON b.batch_id = o.batch_id "
        "WHERE o.product_id = ? ORDER BY o.offer_id",
        (product_id,),
    ).fetchall()
    views: list[OfferView] = []
    for r in rows:
        rate = _rate_for(r["b_currency"], r["b_fx"])
        price_eur = money.price_to_eur(r["price_original"], rate) if rate else Decimal("NaN")
        unit = money.unit_price_eur(r["price_original"], rate) if rate else Decimal("NaN")
        views.append(OfferView(
            offer_id=r["offer_id"], batch_id=r["batch_id"], product_id=r["product_id"],
            supplier_id=r["b_supplier"], currency=r["currency"], kind=r["b_kind"],
            price_original=money.from_minor(r["price_original"]),
            price_eur=price_eur, unit_price_eur=unit, fx_rate_eur=r["b_fx"],
            received_date=r["b_received"], ended_at=r["b_ended"], created_at=r["created_at"],
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


# ---------------------------------------------------------------- live offers (O2 §3.3)

def live_offer_ids(conn: sqlite3.Connection, product_id: int) -> list[int]:
    """The offers that still count for 'best price' for one product (DECIDED O2, §3.3).

    Replacement is per (supplier, brand, offer type). Because one EAN = one product = one
    brand, grouping a product's own offers by (supplier, kind) already pins brand. Within
    each group only the offers from the MOST RECENT batch (by received_date) are live — a
    newer price list / stock offer replaces the previous one of the same kind. A stock
    offer counts only while its batch's ended_at IS NULL. No age-based expiry.
    """
    rows = conn.execute(
        "SELECT o.offer_id, b.supplier_id, b.kind, b.received_date, b.ended_at "
        "FROM offers o JOIN offer_batches b ON b.batch_id = o.batch_id "
        "WHERE o.product_id = ?",
        (product_id,),
    ).fetchall()
    groups: dict[tuple, list[sqlite3.Row]] = {}
    for r in rows:
        groups.setdefault((r["supplier_id"], r["kind"]), []).append(r)

    live: list[int] = []
    for (_supplier, kind), grp in groups.items():
        newest = max((g["received_date"] or "") for g in grp)   # NULL date sorts oldest
        for g in grp:
            if (g["received_date"] or "") != newest:
                continue                                        # superseded by a newer offer
            if kind == "stock_offer" and g["ended_at"] is not None:
                continue                                        # ended stock offer stops counting
            live.append(int(g["offer_id"]))
    return live


# ---------------------------------------------------------------- current_best (rebuildable cache)

def _products_with_offers(conn: sqlite3.Connection) -> list[int]:
    return [int(r[0]) for r in conn.execute("SELECT DISTINCT product_id FROM offers")]


def rebuild_current_best(conn: sqlite3.Connection, product_ids: list[int] | None = None) -> int:
    """Recompute the cheapest LIVE offer per product and write current_best.

    Full rebuild (product_ids=None) clears and recomputes everything; a scoped rebuild
    touches only the named products. Deterministic: lowest unit_price_eur wins, ties
    broken by lowest offer_id. A product with no live offer has its current_best removed.
    Returns the number of current_best rows written.
    """
    conn.execute("BEGIN")
    try:
        if product_ids is None:
            conn.execute("DELETE FROM current_best")
            targets = _products_with_offers(conn)
        else:
            targets = [int(p) for p in product_ids]
            for pid in targets:
                conn.execute("DELETE FROM current_best WHERE product_id = ?", (pid,))

        written = 0
        for pid in targets:
            live = set(live_offer_ids(conn, pid))
            if not live:
                continue
            best: tuple[Decimal, int, int] | None = None   # (unit_eur, offer_id, supplier_id)
            for v in product_offer_history(conn, pid):
                if v.offer_id not in live:
                    continue
                if v.unit_price_eur != v.unit_price_eur:    # NaN -> no rate, cannot convert
                    continue
                cand = (v.unit_price_eur, v.offer_id, v.supplier_id)
                if best is None or cand[0] < best[0] or (cand[0] == best[0] and cand[1] < best[1]):
                    best = cand
            if best is None:
                continue
            unit, offer_id, supplier_id = best
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


# ---------------------------------------------------------------- review queue

def add_review_item(conn: sqlite3.Connection, batch_id: int | None, ean: str | None,
                   reason: str) -> int:
    """Record a row that needs a human decision. Only the EAN + reason are stored — never
    raw cells (data minimisation §2.8; there is no raw_row column any more)."""
    cur = conn.execute(
        "INSERT INTO review_queue (batch_id, ean, reason) VALUES (?, ?, ?)",
        (batch_id, ean, reason),
    )
    return int(cur.lastrowid)


def list_review_items(conn: sqlite3.Connection, batch_id: int | None = None) -> list[sqlite3.Row]:
    if batch_id is None:
        return conn.execute("SELECT * FROM review_queue ORDER BY review_id").fetchall()
    return conn.execute(
        "SELECT * FROM review_queue WHERE batch_id = ? ORDER BY review_id", (batch_id,)
    ).fetchall()


# ---------------------------------------------------------------- frontend read/write (Step B)
#
# The screens below back the MVP UI. They add NO price-mutation path: products gain only a
# description / brand / bestseller flag, suppliers a name / country, and a whole mis-imported
# batch can be removed (an explicit owner action, not a price edit — append-only still governs
# every recorded price). Customer requests store inputs only (§9); results are never stored.

def list_brands(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT brand_id, name FROM brands ORDER BY name COLLATE NOCASE").fetchall()


def list_suppliers(conn: sqlite3.Connection) -> list[dict]:
    """Suppliers with the Suppliers-screen derivations: brands offered + live/total offer count."""
    out: list[dict] = []
    for s in conn.execute("SELECT * FROM suppliers ORDER BY name COLLATE NOCASE").fetchall():
        brands = [r["brand"] for r in conn.execute(
            "SELECT DISTINCT brand FROM offer_batches WHERE supplier_id = ? AND brand IS NOT NULL "
            "ORDER BY brand COLLATE NOCASE", (s["supplier_id"],))]
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM offers o JOIN offer_batches b ON b.batch_id = o.batch_id "
            "WHERE b.supplier_id = ?", (s["supplier_id"],)).fetchone()["n"]
        out.append({"supplier_id": int(s["supplier_id"]), "name": s["name"],
                    "country": s["country"], "default_territory": s["default_territory"],
                    "brands": brands, "offers": int(n)})
    return out


def update_supplier(conn: sqlite3.Connection, supplier_id: int, *,
                   name: str | None = None, country: str | None = None) -> None:
    if name is not None:
        conn.execute("UPDATE suppliers SET name = ? WHERE supplier_id = ?", (name.strip(), supplier_id))
    if country is not None:
        conn.execute("UPDATE suppliers SET country = ? WHERE supplier_id = ?",
                     (country.strip() or None, supplier_id))


def update_product(conn: sqlite3.Connection, ean: str, *, description: str | None = None,
                  brand_id: int | None = None, set_brand: bool = False) -> None:
    """Edit a product's description and/or brand (size is not stored — dropped 2026-11-02).
    set_brand=True applies brand_id (which may be None to clear); otherwise brand is untouched."""
    if description is not None:
        conn.execute("UPDATE products SET description = ? WHERE ean = ?",
                     (description.strip() or None, str(ean)))
    if set_brand:
        status = "active" if brand_id is not None else "needs_review"
        conn.execute("UPDATE products SET brand_id = ?, status = ? WHERE ean = ?",
                     (brand_id, status, str(ean)))


def set_bestseller(conn: sqlite3.Connection, ean: str, flag: bool) -> None:
    conn.execute("UPDATE products SET is_bestseller = ? WHERE ean = ?",
                 (1 if flag else 0, str(ean)))


def list_batches(conn: sqlite3.Connection) -> list[dict]:
    """Every batch with supplier name and line count — the Import log (newest first)."""
    rows = conn.execute(
        "SELECT b.*, s.name AS supplier_name, "
        "  (SELECT COUNT(*) FROM offers o WHERE o.batch_id = b.batch_id) AS n_offers "
        "FROM offer_batches b JOIN suppliers s ON s.supplier_id = b.supplier_id "
        "ORDER BY b.received_date DESC, b.batch_id DESC").fetchall()
    return [dict(r) for r in rows]


def delete_batch(conn: sqlite3.Connection, batch_id: int) -> list[int]:
    """Remove a whole mis-imported batch and its offers (prototype 'Delete offer'). Returns the
    product ids whose current_best must be rebuilt. A hard removal — not a price edit; the
    append-only rule governs recorded prices, not the undo of an entire wrong upload."""
    touched = [int(r[0]) for r in conn.execute(
        "SELECT DISTINCT product_id FROM offers WHERE batch_id = ?", (batch_id,))]
    conn.execute("BEGIN")
    try:
        # clear the derived cache first: its best_offer_id FK points at offers we're deleting.
        for pid in touched:
            conn.execute("DELETE FROM current_best WHERE product_id = ?", (pid,))
        conn.execute("DELETE FROM offers WHERE batch_id = ?", (batch_id,))
        conn.execute("DELETE FROM review_queue WHERE batch_id = ?", (batch_id,))
        # a product first seen in this batch keeps its row; just drop the dangling reference.
        conn.execute("UPDATE products SET first_seen_batch_id = NULL WHERE first_seen_batch_id = ?",
                     (batch_id,))
        conn.execute("DELETE FROM offer_batches WHERE batch_id = ?", (batch_id,))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    if touched:
        rebuild_current_best(conn, touched)   # re-derive best from whatever live offers remain
    return touched


def brand_grid(conn: sqlite3.Connection, brand_id: int | None,
              territory: str | None = None) -> tuple[list[dict], list[dict]]:
    """The Offers grid for one brand: LIVE offer columns (the replacement rule, §3.3) and a row
    per product with that supplier's live per-piece EUR price. 'Best' is the cheapest shown cell.
    Superseded and ended offers are not columns here (they remain in each product's history).

    brand_id=None is the **Unknown brands** tab: products not yet assigned a brand (e.g. the
    not-yet-matched rows of a multi-brand upload). They stay here until a branded offer for the
    same EAN arrives and assigns the brand (importer §2.5), which moves them to that brand's tab.
    """
    if brand_id is None:
        prods = conn.execute(
            "SELECT product_id, ean, description, is_bestseller FROM products "
            "WHERE brand_id IS NULL ORDER BY description COLLATE NOCASE").fetchall()
    else:
        prods = conn.execute(
            "SELECT product_id, ean, description, is_bestseller FROM products "
            "WHERE brand_id = ? ORDER BY description COLLATE NOCASE", (brand_id,)).fetchall()

    batch_meta: dict[int, dict] = {}
    rows: list[dict] = []
    for p in prods:
        pid = int(p["product_id"])
        live = set(live_offer_ids(conn, pid))
        prices: dict[int, str] = {}
        best: Decimal | None = None
        best_bid: int | None = None
        for v in product_offer_history(conn, pid):
            if v.offer_id not in live:
                continue
            if v.unit_price_eur != v.unit_price_eur:      # NaN -> no rate
                continue
            bid = v.batch_id
            if bid not in batch_meta:
                b = conn.execute(
                    "SELECT b.*, s.name AS supplier_name FROM offer_batches b "
                    "JOIN suppliers s ON s.supplier_id = b.supplier_id WHERE b.batch_id = ?",
                    (bid,)).fetchone()
                if territory not in (None, "all") and (b["territory"] or "") != territory:
                    continue                              # filtered-out column: skip this cell too
                batch_meta[bid] = {
                    "batch_id": bid, "supplier": b["supplier_name"], "supplier_id": b["supplier_id"],
                    "received_date": b["received_date"], "kind": b["kind"],
                    "territory": b["territory"], "ended_at": b["ended_at"]}
            elif territory not in (None, "all") and batch_meta[bid]["territory"] != territory:
                continue
            prices[bid] = str(v.unit_price_eur.quantize(money.CENT))
            if best is None or v.unit_price_eur < best:
                best, best_bid = v.unit_price_eur, bid
        rows.append({"ean": p["ean"], "description": p["description"],
                     "is_bestseller": bool(p["is_bestseller"]),
                     "prices": prices,
                     "best_eur": str(best.quantize(money.CENT)) if best is not None else None,
                     "best_batch_id": best_bid})

    batches = sorted(batch_meta.values(),
                     key=lambda b: (b["received_date"] or "", b["batch_id"]), reverse=True)
    return batches, rows


def bestsellers(conn: sqlite3.Connection, brand_id: int | None = None) -> list[dict]:
    """Flagged products with their three cheapest LIVE suppliers, low→high (Bestsellers screen)."""
    q = ("SELECT p.product_id, p.ean, p.description, br.name AS brand FROM products p "
         "LEFT JOIN brands br ON br.brand_id = p.brand_id WHERE p.is_bestseller = 1")
    args: tuple = ()
    if brand_id is not None:
        q += " AND p.brand_id = ?"
        args = (brand_id,)
    out: list[dict] = []
    for p in conn.execute(q + " ORDER BY p.description COLLATE NOCASE", args).fetchall():
        pid = int(p["product_id"])
        live = set(live_offer_ids(conn, pid))
        offers = []
        for v in product_offer_history(conn, pid):
            if v.offer_id not in live or v.unit_price_eur != v.unit_price_eur:
                continue
            sup = conn.execute("SELECT name FROM suppliers WHERE supplier_id = ?",
                               (v.supplier_id,)).fetchone()
            offers.append({"supplier": sup["name"] if sup else None,
                           "unit_price_eur": v.unit_price_eur})
        offers.sort(key=lambda o: o["unit_price_eur"])
        out.append({"ean": p["ean"], "description": p["description"], "brand": p["brand"],
                    "offers": [{"supplier": o["supplier"],
                                "unit_price_eur": str(o["unit_price_eur"].quantize(money.CENT))}
                               for o in offers[:3]]})
    return out


# ---------------------------------------------------------------- customer requests (§9)

def create_request(conn: sqlite3.Connection, *, customer: str | None, brand: str | None,
                  request_date: str | None, margin: str,
                  lines: list[dict]) -> int:
    """Store a request's INPUTS only (EAN, target minor units, currency). No result is stored."""
    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            "INSERT INTO requests (customer, brand, request_date, margin) VALUES (?, ?, ?, ?)",
            ((customer or "").strip() or None, (brand or "").strip() or None,
             request_date, str(margin)))
        rid = int(cur.lastrowid)
        for ln in lines:
            conn.execute(
                "INSERT INTO request_lines (request_id, ean, target_price, currency) "
                "VALUES (?, ?, ?, ?)",
                (rid, str(ln["ean"]), int(ln["target_price"]), (ln.get("currency") or "EUR").upper()))
        conn.execute("COMMIT")
        return rid
    except Exception:
        conn.execute("ROLLBACK")
        raise


def get_request(conn: sqlite3.Connection, request_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM requests WHERE request_id = ?", (request_id,)).fetchone()


def request_lines(conn: sqlite3.Connection, request_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM request_lines WHERE request_id = ? ORDER BY line_id", (request_id,)).fetchall()


def list_requests(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT r.*, (SELECT COUNT(*) FROM request_lines l WHERE l.request_id = r.request_id) "
        "AS n_lines FROM requests r ORDER BY r.request_id DESC").fetchall()


def delete_request(conn: sqlite3.Connection, request_id: int) -> None:
    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM request_lines WHERE request_id = ?", (request_id,))
        conn.execute("DELETE FROM requests WHERE request_id = ?", (request_id,))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def distinct_customers(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT customer FROM requests WHERE customer IS NOT NULL "
        "ORDER BY customer COLLATE NOCASE")]
