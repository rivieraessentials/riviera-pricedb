"""API request/response shapes. Prices cross the wire as STRINGS, never floats, so
exact decimal values are preserved end to end (design O5 §12)."""
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
    kind: str | None = None
    source_file: str | None = None
    source_sha256: str | None = None
    fx_rate_eur: str | None = None     # TEXT, exactly as published (1 EUR = rate * currency)
    fx_source: str | None = None
    fx_locked: int = 0
    label: str | None = None


class BatchOut(BaseModel):
    batch_id: int
    duplicate_of_batch_id: int | None = None   # set if source_sha256 was already imported (§2.1)
    warning: str | None = None


class OfferIn(BaseModel):
    ean: str
    quoted_price: str = Field(..., description="price as quoted, exact decimal string, e.g. '3.9325'")
    currency: str | None = None        # defaults to the batch currency
    pieces_per_box: int | None = None
    price_basis: str = "per_piece"
    description: str | None = None
    size: str | None = None
    available_qty: int | None = None
    moq: int | None = None
    valid_until: str | None = None
    raw_row: dict | None = None


class OffersCreate(BaseModel):
    batch_id: int
    offers: list[OfferIn]


class ReviewItem(BaseModel):
    ean: str | None = None
    reason: str


class OffersCreated(BaseModel):
    offer_ids: list[int]
    products_touched: list[int]
    review: list[ReviewItem] = []    # rows that could not be safely imported (e.g. >6 decimals)


class OfferHistoryItem(BaseModel):
    offer_id: int
    batch_id: int
    supplier_id: int
    currency: str
    price_basis: str
    pieces_per_box: int | None = None
    price_original: str        # exact, as quoted
    price_eur: str             # computed on read
    unit_price_eur: str        # per single piece, computed on read
    fx_rate_eur: str | None = None
    received_date: str | None = None
    created_at: str


class CurrentBestOut(BaseModel):
    best_offer_id: int
    best_unit_price_eur: str
    best_supplier_id: int | None = None
    updated_at: str


class ProductOffersOut(BaseModel):
    ean: str
    product_id: int
    description: str | None = None
    offers: list[OfferHistoryItem]
    current_best: CurrentBestOut | None = None
