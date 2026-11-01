"""FastAPI app — a thin shell over the money/storage core (app.repo, app.money).

Local only: binds 127.0.0.1:8001 (see config). Cloudflare Access fronts it in
production; JWT verification here is defence-in-depth and OFF by default in dev (app.auth).
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager, contextmanager

from fastapi import Depends, FastAPI, HTTPException

from . import db, importer, money, repo
from .auth import Identity, require_identity
from .config import Config, load_config
from .models import (
    BatchCreate, BatchOut, CurrentBestOut, OffersCreate, OffersCreated,
    OfferHistoryItem, ProductOffersOut, ReviewItem, SupplierCreate, SupplierOut,
)


class ConfigError(RuntimeError):
    """Startup refused because configuration is unsafe for the current environment."""


def create_app(config: Config | None = None) -> FastAPI:
    cfg = config or load_config()
    os.makedirs(os.path.dirname(cfg.db_path) or ".", exist_ok=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = db.connect(cfg.db_path)
        try:
            db.migrate(conn)          # bring the schema up to date on startup
            # Production guard: refuse to serve while the freshness rule is still the
            # placeholder, so comparisons are never silently based on an undecided rule.
            if cfg.is_production:
                rule = repo.get_setting(conn, "freshness_rule", "all")
                if rule == "all":
                    raise ConfigError(
                        "refusing to start in production: freshness_rule is still the "
                        "placeholder 'all'. Decide O2 (offer freshness rule) and set "
                        "freshness_rule in settings before serving production traffic."
                    )
        finally:
            conn.close()
        yield

    app = FastAPI(title="Riviera Supplier Price DB", version="0.1.0", lifespan=lifespan)
    app.state.config = cfg

    @contextmanager
    def get_conn():
        conn = db.connect(cfg.db_path)
        try:
            yield conn
        finally:
            conn.close()

    # ---- health (no auth) ----
    @app.get("/healthz")
    def healthz():
        with get_conn() as conn:
            applied = db.applied_versions(conn)
            conn.execute("SELECT 1")
        return {"status": "ok", "db": "ok", "migrations": applied,
                "environment": cfg.environment, "access_enabled": cfg.access.enabled}

    # ---- suppliers ----
    @app.post("/suppliers", response_model=SupplierOut)
    def create_supplier(body: SupplierCreate, who: Identity = Depends(require_identity)):
        with get_conn() as conn:
            sid = repo.create_supplier(conn, body.name, body.default_territory, body.notes)
            return SupplierOut(supplier_id=sid, name=body.name,
                               default_territory=body.default_territory)

    # ---- batches (with duplicate-file detection) ----
    @app.post("/batches", response_model=BatchOut)
    def create_batch(body: BatchCreate, who: Identity = Depends(require_identity)):
        with get_conn() as conn:
            dup = repo.find_batch_by_sha(conn, body.source_sha256) if body.source_sha256 else None
            bid = repo.create_batch(
                conn, body.supplier_id, body.currency, received_date=body.received_date,
                territory=body.territory, kind=body.kind, source_file=body.source_file,
                source_sha256=body.source_sha256, fx_rate_eur=body.fx_rate_eur,
                fx_source=body.fx_source, fx_locked=body.fx_locked, label=body.label,
            )
            if dup is not None:
                return BatchOut(batch_id=bid, duplicate_of_batch_id=int(dup["batch_id"]),
                                warning=f"this exact file was already imported as batch "
                                        f"{dup['batch_id']} on {dup['received_date']}")
            return BatchOut(batch_id=bid)

    # ---- offers (append-only; the import path routes bad rows to review) ----
    @app.post("/offers", response_model=OffersCreated)
    def create_offers(body: OffersCreate, who: Identity = Depends(require_identity)):
        with get_conn() as conn:
            batch = conn.execute("SELECT * FROM offer_batches WHERE batch_id = ?",
                                 (body.batch_id,)).fetchone()
            if batch is None:
                raise HTTPException(404, detail=f"batch {body.batch_id} not found")
            rows = [item.model_dump() for item in body.offers]
            res = importer.import_rows(conn, body.batch_id, rows)
            return OffersCreated(
                offer_ids=res.offer_ids, products_touched=res.products_touched,
                review=[ReviewItem(**r) for r in res.review],
            )

    # ---- read a product's offer history ----
    @app.get("/products/{ean}/offers", response_model=ProductOffersOut)
    def product_offers(ean: str, who: Identity = Depends(require_identity)):
        with get_conn() as conn:
            prod = repo.get_product_by_ean(conn, ean)
            if prod is None:
                raise HTTPException(404, detail=f"no product with EAN {ean}")
            history = repo.product_offer_history(conn, int(prod["product_id"]))
            items = [
                OfferHistoryItem(
                    offer_id=v.offer_id, batch_id=v.batch_id, supplier_id=v.supplier_id,
                    currency=v.currency, price_basis=v.price_basis, pieces_per_box=v.pieces_per_box,
                    price_original=str(v.price_original), price_eur=str(v.price_eur),
                    unit_price_eur=str(v.unit_price_eur), fx_rate_eur=v.fx_rate_eur,
                    received_date=v.received_date, created_at=v.created_at,
                )
                for v in history
            ]
            cb_row = repo.get_current_best(conn, int(prod["product_id"]))
            cb = None
            if cb_row is not None:
                cb = CurrentBestOut(
                    best_offer_id=int(cb_row["best_offer_id"]),
                    best_unit_price_eur=str(money.from_minor(cb_row["best_unit_price_eur"])),
                    best_supplier_id=cb_row["best_supplier_id"],
                    updated_at=cb_row["updated_at"],
                )
            return ProductOffersOut(
                ean=str(prod["ean"]), product_id=int(prod["product_id"]),
                description=prod["description"], offers=items, current_best=cb,
            )

    return app


# Module-level app for `uvicorn app.main:app`
app = create_app()
