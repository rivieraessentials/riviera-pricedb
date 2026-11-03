"""Row-level import orchestration (the import path) — design §2.

The browser parses the file and POSTs only normalized rows (EAN, description, price(s),
and pieces/box ONLY when a per-box row must be divided — §2.7/§2.8). This module takes
those rows and, per §2.4-§2.6:

  * reads the LOWEST price, divides a per-box row by pieces/box, rounds ONCE to 2dp;
  * validates the EAN check-digit;
  * resolves brand from the UPLOAD FORM, never from the file (§2.5):
      - single-brand: the picked brand applies to all rows; an EAN already under a
        DIFFERENT brand goes to review (no overwrite, no offer);
      - multi-brand:  each row's brand is the existing product's; an unknown EAN is kept
        as a new needs-review product (excluded from brand comparisons) and listed for review;
  * sends anything uncertain to the review list — never a silent import, never a silent round.

Data minimisation (§2.8): only ean / description / price are read from each row; any other
key a caller passes is ignored and never stored. There is no raw_row.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from . import money, repo


@dataclass
class ImportResult:
    offer_ids: list[int] = field(default_factory=list)
    products_touched: list[int] = field(default_factory=list)
    review: list[dict] = field(default_factory=list)   # [{ean, reason}]


def ean_check_ok(code: str) -> bool:
    """EAN-8 / EAN-13 check-digit validation (§2.6). A 12-digit UPC-A is padded to 13."""
    code = (code or "").strip().replace(" ", "").replace("-", "")
    if not code.isdigit():
        return False
    if len(code) == 12:                       # UPC-A -> EAN-13 with a leading zero
        code = "0" + code
    if len(code) not in (8, 13):
        return False
    d = [int(c) for c in code]
    body, chk = d[:-1], d[-1]
    if len(code) == 13:                       # weights from left: 1,3,1,3,...
        s = sum(x * (1 if i % 2 == 0 else 3) for i, x in enumerate(body))
    else:                                     # EAN-8 weights from left: 3,1,3,1,...
        s = sum(x * (3 if i % 2 == 0 else 1) for i, x in enumerate(body))
    return (10 - s % 10) % 10 == chk


def _lowest_price(row: dict) -> Decimal | None:
    """Lowest positive price among the row's price field(s) (§2.4 step 4). Returns None
    when no parseable positive price is present."""
    raw = row.get("prices")
    if raw is None:
        raw = [row.get("quoted_price")]
    candidates: list[Decimal] = []
    for v in raw:
        if v is None or v == "":
            continue
        try:
            d = money._as_decimal(v)
        except money.PriceScaleError:
            continue
        if d.is_finite() and d > 0:
            candidates.append(d)
    return min(candidates) if candidates else None


def import_rows(conn, batch_id: int, rows: list[dict], *,
               brand_mode: str = "single", brand_id: int | None = None) -> ImportResult:
    """Import a batch's rows (§2). brand_mode is 'single' or 'multi' (§2.5).

    Each row dict may carry: ean, quoted_price (or prices=[...]), description, and — for a
    per-box row only — price_basis='per_box' with pieces_per_box. Nothing else is read.
    """
    batch = conn.execute("SELECT * FROM offer_batches WHERE batch_id = ?", (batch_id,)).fetchone()
    if batch is None:
        raise ValueError(f"batch {batch_id} not found")
    currency = batch["currency"]
    result = ImportResult()

    def review(ean, reason):
        repo.add_review_item(conn, batch_id, ean, reason)
        result.review.append({"ean": ean, "reason": reason})

    def touch(pid):
        if pid not in result.products_touched:
            result.products_touched.append(pid)

    for row in rows:
        ean = (row.get("ean") or "").strip()

        if not ean_check_ok(ean):
            review(ean or None, "EAN checksum failed")
            continue

        price = _lowest_price(row)
        if price is None:
            review(ean, "no price found")
            continue

        basis = row.get("price_basis", "per_piece")
        ppb = row.get("pieces_per_box")
        if basis == "per_box" and (not ppb or int(ppb) <= 0):
            review(ean, "per-box row with no pieces-per-box")
            continue

        minor = money.import_price_minor(price, ppb if basis == "per_box" else None)
        existing = repo.get_product_by_ean(conn, ean)

        if brand_mode == "multi":
            # brand comes from the existing product; unknown EAN -> new needs-review product.
            if existing is not None:
                pid = int(existing["product_id"])
            else:
                pid = repo.create_product(conn, ean, description=row.get("description"),
                                          brand_id=None, status="needs_review",
                                          first_seen_batch_id=batch_id)
                # price is KEPT but the product is excluded from brand comparisons until assigned.
                review(ean, "new product — assign a brand")
            result.offer_ids.append(repo.create_offer(conn, batch_id, pid, minor, currency))
            touch(pid)
            continue

        # single-brand upload: the picked brand applies to every row.
        if existing is not None:
            existing_brand = existing["brand_id"]
            if brand_id is not None and existing_brand is not None and int(existing_brand) != int(brand_id):
                conflict = repo.brand_name(conn, int(existing_brand)) or "another brand"
                review(ean, f"EAN already under brand {conflict} — confirm")  # no overwrite, no offer
                continue
            pid = int(existing["product_id"])
            if brand_id is not None and existing_brand is None:
                repo.assign_brand(conn, pid, brand_id)   # fill a missing brand, never overwrite
        else:
            status = "active" if brand_id is not None else "needs_review"
            pid = repo.create_product(conn, ean, description=row.get("description"),
                                      brand_id=brand_id, status=status,
                                      first_seen_batch_id=batch_id)

        result.offer_ids.append(repo.create_offer(conn, batch_id, pid, minor, currency))
        touch(pid)

    if result.products_touched:
        repo.rebuild_current_best(conn, result.products_touched)
    return result
