"""The two offer types — one place that maps the stored code to the owner's final
display names (decided 2026-11-03).

The stored `offer_batches.kind` code is UNCHANGED (`price_list` | `stock_offer`): it is an
internal token, so renaming it would churn the schema, every migration and every test for
no behavioural gain. What changed on 2026-11-03 is the human label shown in the upload form
and everywhere the type is displayed:

    price_list   ->  "Regular offer (longer validity)"
    stock_offer  ->  "Stock offer (limited in time and/or quantity)"

Replacement rule (design §3.3 / O2, final 2026-11-03): a new offer replaces, for each brand
present in the file, that supplier's previous offer of the SAME TYPE for that brand — single-
and multi-brand alike. Different brands never replace each other; regular and stock offers
never replace each other. Stock offers additionally drop out when ended (`ended_at`). No date
expiry, no quantity tracking. (This is exactly what repo.live_offer_ids implements.)
"""
from __future__ import annotations

REGULAR = "price_list"      # stored code for a Regular offer
STOCK = "stock_offer"       # stored code for a Stock offer

KINDS = (REGULAR, STOCK)

# Short labels (grid headers, badges) and the long form-field labels (the upload wizard).
KIND_LABELS = {
    REGULAR: "Regular offer",
    STOCK: "Stock offer",
}
KIND_FORM_LABELS = {
    REGULAR: "Regular offer (longer validity)",
    STOCK: "Stock offer (limited in time and/or quantity)",
}


def kind_label(kind: str | None, *, form: bool = False) -> str | None:
    """Display label for a stored kind code; None stays None, unknown codes pass through."""
    if kind is None:
        return None
    table = KIND_FORM_LABELS if form else KIND_LABELS
    return table.get(kind, kind)
