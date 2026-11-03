"""FastAPI app — a thin shell over the money/storage core (app.repo, app.money).

Local only: binds 127.0.0.1:8001 (see config). Cloudflare Access fronts it in
production; JWT verification here is defence-in-depth and OFF by default in dev (app.auth).
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager, contextmanager
from datetime import date as _date
from decimal import Decimal, InvalidOperation

from fastapi import Depends, FastAPI, HTTPException, Response

from . import db, export, fx, importer, money, offer_types, repo
from .auth import Identity, require_identity
from .config import Config, load_config
from .models import (
    BatchCreate, BatchOut, CurrentBestOut, EndOfferOut, FxManualRate, FxRatesOut,
    FxRefreshOut, OffersCreate, OffersCreated, OfferHistoryItem, ProductOffersOut,
    ReviewItem, SupplierCreate, SupplierOut,
)


def create_app(config: Config | None = None) -> FastAPI:
    cfg = config or load_config()
    os.makedirs(os.path.dirname(cfg.db_path) or ".", exist_ok=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = db.connect(cfg.db_path)
        try:
            db.migrate(conn)          # bring the schema up to date on startup
        finally:
            conn.close()
        yield

    app = FastAPI(title="Riviera Supplier Price DB", version="0.2.0", lifespan=lifespan)
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
                territory=body.territory, kind=body.kind, brand=body.brand, moq=body.moq,
                lead_time=body.lead_time, notes=body.notes, source_file=body.source_file,
                source_sha256=body.source_sha256, fx_rate_eur=body.fx_rate_eur,
                fx_source=body.fx_source, fx_locked=body.fx_locked, label=body.label,
            )
            if dup is not None:
                return BatchOut(batch_id=bid, duplicate_of_batch_id=int(dup["batch_id"]),
                                warning=f"this exact file was already imported as batch "
                                        f"{dup['batch_id']} on {dup['received_date']}")
            return BatchOut(batch_id=bid)

    # ---- offers (append-only; the import path routes uncertain rows to review) ----
    @app.post("/offers", response_model=OffersCreated)
    def create_offers(body: OffersCreate, who: Identity = Depends(require_identity)):
        with get_conn() as conn:
            batch = conn.execute("SELECT * FROM offer_batches WHERE batch_id = ?",
                                 (body.batch_id,)).fetchone()
            if batch is None:
                raise HTTPException(404, detail=f"batch {body.batch_id} not found")
            brand_id = None
            if body.brand_mode == "single" and body.brand:
                brand_id = repo.get_or_create_brand(conn, body.brand)
            rows = [item.model_dump() for item in body.offers]
            res = importer.import_rows(conn, body.batch_id, rows,
                                       brand_mode=body.brand_mode, brand_id=brand_id)
            return OffersCreated(
                offer_ids=res.offer_ids, products_touched=res.products_touched,
                review=[ReviewItem(**r) for r in res.review],
            )

    # ---- end a stock offer (§8): stops it counting as live, never a price edit ----
    @app.post("/batches/{batch_id}/end-offer", response_model=EndOfferOut)
    def end_offer(batch_id: int, ended_at: str, who: Identity = Depends(require_identity)):
        with get_conn() as conn:
            batch = conn.execute("SELECT * FROM offer_batches WHERE batch_id = ?",
                                 (batch_id,)).fetchone()
            if batch is None:
                raise HTTPException(404, detail=f"batch {batch_id} not found")
            try:
                repo.end_offer(conn, batch_id, ended_at)
            except ValueError as exc:
                raise HTTPException(400, detail=str(exc))
            touched = [int(r[0]) for r in conn.execute(
                "SELECT DISTINCT product_id FROM offers WHERE batch_id = ?", (batch_id,))]
            n = repo.rebuild_current_best(conn, touched) if touched else 0
            return EndOfferOut(batch_id=batch_id, ended_at=ended_at, products_rebuilt=n)

    # ---- FX rates: read, manual override, on-demand refresh (daily fetch is a server job) ----
    @app.get("/fx/rates", response_model=FxRatesOut)
    def fx_rates(rate_date: str | None = None, who: Identity = Depends(require_identity)):
        with get_conn() as conn:
            if rate_date is None:
                row = conn.execute("SELECT MAX(rate_date) AS d FROM fx_rates").fetchone()
                rate_date = (row["d"] if row else None) or ""
            rows = conn.execute("SELECT currency, rate FROM fx_rates WHERE rate_date = ?",
                                (rate_date,)).fetchall()
            return FxRatesOut(rate_date=rate_date, rates={r["currency"]: r["rate"] for r in rows})

    @app.post("/fx/rates", response_model=FxRatesOut)
    def fx_set_manual(body: FxManualRate, who: Identity = Depends(require_identity)):
        try:
            if Decimal(body.rate.strip()) <= 0:
                raise InvalidOperation
        except (InvalidOperation, ArithmeticError):
            raise HTTPException(400, detail="rate must be a positive decimal (1 EUR = rate * currency)")
        with get_conn() as conn:
            repo.set_fx_rate(conn, body.rate_date, body.currency.upper(), body.rate.strip(), "manual")
            rows = conn.execute("SELECT currency, rate FROM fx_rates WHERE rate_date = ?",
                                (body.rate_date,)).fetchall()
            return FxRatesOut(rate_date=body.rate_date, rates={r["currency"]: r["rate"] for r in rows})

    @app.post("/fx/refresh", response_model=FxRefreshOut)
    def fx_refresh(who: Identity = Depends(require_identity)):
        with get_conn() as conn:
            try:
                rdate, n, source = fx.update_fx_rates(conn, cfg.fx)
            except Exception as exc:
                raise HTTPException(502, detail=f"FX fetch failed: {exc}")
            return FxRefreshOut(rate_date=rdate, written=n, source=source)

    # ---- offline download bundle (3-2-1 copy, design §6): SQLite snapshot + CSV exports ----
    @app.get("/export")
    def export_bundle(who: Identity = Depends(require_identity)):
        with get_conn() as conn:
            data = export.build_bundle(conn, cfg.db_path)
        stamp = _date.today().isoformat()
        return Response(content=data, media_type="application/zip",
                        headers={"Content-Disposition":
                                 f'attachment; filename="pricedb-export-{stamp}.zip"'})

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
                    currency=v.currency, kind=v.kind, kind_label=offer_types.kind_label(v.kind),
                    price_original=str(v.price_original), price_eur=str(v.price_eur),
                    unit_price_eur=str(v.unit_price_eur), fx_rate_eur=v.fx_rate_eur,
                    received_date=v.received_date, ended_at=v.ended_at, created_at=v.created_at,
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
