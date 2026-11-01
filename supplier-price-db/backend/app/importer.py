"""Row-level import orchestration (the import path).

The money layer stays strict: a price needing more than the fixed scale (6 decimals)
raises money.PriceScaleError and is NEVER silently rounded. The importer catches that
here and routes the offending row to the review queue with a clear reason, so the rest
of the file still imports (design §2.1/§2.4). Nothing is dropped.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import money, repo


@dataclass
class ImportResult:
    offer_ids: list[int] = field(default_factory=list)
    products_touched: list[int] = field(default_factory=list)
    review: list[dict] = field(default_factory=list)   # [{ean, reason}]


def import_rows(conn, batch_id: int, rows: list[dict]) -> ImportResult:
    """Import a batch's rows. Returns created offers, touched products, and review items.

    Each row is a dict: ean, quoted_price (str), and optionally currency, pieces_per_box,
    price_basis, description, size, available_qty, moq, valid_until, raw_row.
    """
    batch = conn.execute("SELECT * FROM offer_batches WHERE batch_id = ?", (batch_id,)).fetchone()
    if batch is None:
        raise ValueError(f"batch {batch_id} not found")
    default_currency = batch["currency"]

    result = ImportResult()
    for row in rows:
        ean = row.get("ean")
        quoted = row.get("quoted_price")
        try:
            minor = money.to_minor(quoted)
        except money.PriceScaleError:
            # Keep the strict check in the money layer; handle the failure HERE.
            reason = f"more than {money.SCALE} decimals: {quoted}"
            repo.add_review_item(conn, batch_id, ean, reason, row.get("raw_row") or row)
            result.review.append({"ean": ean, "reason": reason})
            continue

        pid, _created = repo.get_or_create_product(
            conn, ean, description=row.get("description"), size=row.get("size"),
            first_seen_batch_id=batch_id,
        )
        oid = repo.create_offer(
            conn, batch_id, pid, minor, row.get("currency") or default_currency,
            pieces_per_box=row.get("pieces_per_box"), price_basis=row.get("price_basis", "per_piece"),
            raw_row=row.get("raw_row"), available_qty=row.get("available_qty"),
            moq=row.get("moq"), valid_until=row.get("valid_until"),
        )
        result.offer_ids.append(oid)
        if pid not in result.products_touched:
            result.products_touched.append(pid)

    if result.products_touched:
        repo.rebuild_current_best(conn, result.products_touched)
    return result
