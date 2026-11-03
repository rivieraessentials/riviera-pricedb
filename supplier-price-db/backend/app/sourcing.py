"""Customer-request sourcing (design §9) — recomputed LIVE, never stored.

A request keeps only its inputs (EAN, target price, currency, optional company/brand and the
min margin). This module answers it against the CURRENT cheapest live offer per product
(current_best, which already honours the replacement/End-offer rule, §3.3) every time it is
viewed, so a saved request always reflects today's prices rather than a frozen snapshot.

Per line, against a minimum margin m (a decimal fraction, e.g. 0.07 for 7%):
  * unknown  — the EAN is not a product in the database;
  * ok       — the cheapest live supplier meets the margin: buy_eur <= target_eur * (1 - m);
  * no       — the product exists but no live supplier meets the margin (covers "no live
               offer at all" and "a rate is needed to compare a non-EUR target").
The product description shown comes from the database, never from the request.
"""
from __future__ import annotations

from decimal import Decimal

from . import money, repo


def _latest_rates(conn) -> tuple[str, dict[str, str]]:
    row = conn.execute("SELECT MAX(rate_date) AS d FROM fx_rates").fetchone()
    rate_date = (row["d"] if row else None) or ""
    rows = conn.execute("SELECT currency, rate FROM fx_rates WHERE rate_date = ?",
                        (rate_date,)).fetchall()
    return rate_date, {r["currency"]: r["rate"] for r in rows}


def _target_eur(target_minor: int, currency: str, rates: dict[str, str]) -> Decimal | None:
    """Convert a per-piece target in `currency` to EUR (1 EUR = rate * currency).
    Returns None when a non-EUR target has no rate to convert it with."""
    amount = money.from_minor(target_minor)          # exact Decimal in the target's own currency
    cur = (currency or "EUR").upper()
    if cur == "EUR":
        return amount
    rate = rates.get(cur)
    if not rate:
        return None
    try:
        r = Decimal(str(rate))
    except (ArithmeticError, ValueError):
        return None
    if r <= 0:
        return None
    return amount / r


def compute(conn, lines: list[dict], margin) -> dict:
    """lines: [{ean, target_price (minor units int), currency}]. margin: decimal fraction."""
    m = margin if isinstance(margin, Decimal) else Decimal(str(margin))
    _, rates = _latest_rates(conn)
    results: list[dict] = []
    ok = no = unknown = 0

    for ln in lines:
        ean = str(ln.get("ean") or "").strip()
        target_minor = int(ln.get("target_price") or 0)
        currency = (ln.get("currency") or "EUR").upper()
        prod = repo.get_product_by_ean(conn, ean)
        if prod is None:
            unknown += 1
            results.append({"ean": ean, "description": None, "status": "unknown",
                            "target_eur": None, "supplier": None, "buy_eur": None,
                            "margin_pct": None, "currency": currency})
            continue

        desc = prod["description"]
        target_eur = _target_eur(target_minor, currency, rates)
        cb = repo.get_current_best(conn, int(prod["product_id"]))
        buy_eur = money.from_minor(cb["best_unit_price_eur"]) if cb is not None else None
        supplier = None
        if cb is not None and cb["best_supplier_id"] is not None:
            row = conn.execute("SELECT name FROM suppliers WHERE supplier_id = ?",
                               (cb["best_supplier_id"],)).fetchone()
            supplier = row["name"] if row else None

        qualifies = (
            cb is not None and target_eur is not None
            and buy_eur <= (target_eur * (Decimal(1) - m))
        )
        if qualifies:
            ok += 1
            margin_pct = (target_eur - buy_eur) / target_eur * Decimal(100)
            status = "ok"
        else:
            no += 1
            status = "no"
            margin_pct = None

        results.append({
            "ean": ean, "description": desc, "status": status,
            "target_eur": str(target_eur.quantize(money.CENT)) if target_eur is not None else None,
            "supplier": supplier,
            "buy_eur": str(buy_eur.quantize(money.CENT)) if buy_eur is not None else None,
            "margin_pct": str(margin_pct.quantize(Decimal("0.1"))) if margin_pct is not None else None,
            "currency": currency,
        })

    return {"results": results, "ok": ok, "no": no, "unknown": unknown, "count": len(lines)}
