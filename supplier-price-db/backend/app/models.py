"""API request/response shapes. Prices cross the wire as STRINGS, never floats, so
exact decimal values are preserved end to end (design O5 §12). Stored prices are
per-piece and rounded to 2dp at import (O6 §2.4)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class SupplierCreate(BaseModel):
    name: str
    default_territory: str | None = None
    notes: str | None = None


class SupplierOut(BaseModel):
    supplier_id: int
    name: str
    default_territory: str | None = None


class BatchCreate(BaseModel):
    supplier_id: int
    currency: str
    received_date: str | None = None
    territory: str | None = None
    kind: str | None = None                # 'price_list' | 'stock_offer' (structured)
    brand: str | None = None               # single brand label, or '(multi-brand)' (§2.5)
    moq: str | None = None                 # FREE TEXT, display-only (O3)
    lead_time: str | None = None           # free text; "business info only"
    notes: str | None = None               # free text; "business info only"
    source_file: str | None = None         # neutral reference only (§2.8), never a filename
    source_sha256: str | None = None
    fx_rate_eur: str | None = None         # TEXT, exactly as published (1 EUR = rate * currency)
    fx_source: str | None = None
    fx_locked: int = 0
    label: str | None = None


class BatchOut(BaseModel):
    batch_id: int
    duplicate_of_batch_id: int | None = None   # set if source_sha256 was already imported (§2.1)
    warning: str | None = None


class OfferIn(BaseModel):
    ean: str
    quoted_price: str | None = Field(None, description="price as quoted, exact decimal string")
    prices: list[str] | None = None          # several price columns; the lowest positive is used (§2.4)
    description: str | None = None
    brand: str | None = None                 # multi-brand only: this row's brand from the file column
                                             # (sent only for known or owner-approved brands; §2.5)
    # per-box rows only — used to divide at import, NOT stored (§1.2):
    pieces_per_box: int | None = None
    price_basis: str = "per_piece"           # 'per_piece' | 'per_box'


class OffersCreate(BaseModel):
    batch_id: int
    brand_mode: str = "single"               # 'single' (brand from the form) | 'multi' (by existing EAN)
    brand: str | None = None                 # the picked brand name for a single-brand upload
    offers: list[OfferIn]


class ReviewItem(BaseModel):
    ean: str | None = None
    reason: str


class OffersCreated(BaseModel):
    offer_ids: list[int]
    products_touched: list[int]
    review: list[ReviewItem] = []    # rows that need a human decision (§2.6)


class EndOfferOut(BaseModel):
    batch_id: int
    ended_at: str
    products_rebuilt: int


class OfferHistoryItem(BaseModel):
    offer_id: int
    batch_id: int
    supplier_id: int
    currency: str
    kind: str | None = None           # stored code: 'price_list' | 'stock_offer'
    kind_label: str | None = None     # display name (offer_types.kind_label), e.g. "Regular offer"
    price_original: str        # the stored per-piece 2dp price
    price_eur: str             # computed on read
    unit_price_eur: str        # per single piece, computed on read
    fx_rate_eur: str | None = None
    received_date: str | None = None
    ended_at: str | None = None
    created_at: str


class FxManualRate(BaseModel):
    rate_date: str                 # ISO date the rate applies to
    currency: str
    rate: str                      # TEXT, exactly as entered; 1 EUR = rate * currency


class FxRatesOut(BaseModel):
    rate_date: str
    rates: dict[str, str] = {}     # {currency: rate_text}


class FxRefreshOut(BaseModel):
    rate_date: str | None = None
    written: int
    source: str                    # 'frankfurter' | 'ecb'


class SupplierUpdate(BaseModel):
    name: str | None = None
    country: str | None = None


class ProductCreate(BaseModel):
    ean: str
    description: str | None = None
    brand: str | None = None               # brand name; created if new (the "Add line" form)


class ProductUpdate(BaseModel):
    description: str | None = None
    brand: str | None = None               # "" clears the brand; omit to leave unchanged


class BestsellerSet(BaseModel):
    is_bestseller: bool


class RequestLineIn(BaseModel):
    ean: str
    target_price: str                      # exact decimal string, the target's own currency
    currency: str = "EUR"


class RequestCreate(BaseModel):
    customer: str | None = None            # OPTIONAL company name only (no contact details, §9)
    brand: str | None = None
    request_date: str | None = None
    margin: str = "0.07"                   # min margin as a decimal fraction
    lines: list[RequestLineIn]


class CurrentBestOut(BaseModel):
    best_offer_id: int
    best_unit_price_eur: str
    best_supplier_id: int | None = None
    updated_at: str


class ProductOffersOut(BaseModel):
    ean: str
    product_id: int
    description: str | None = None
    brand: str | None = None
    offers: list[OfferHistoryItem]
    current_best: CurrentBestOut | None = None
