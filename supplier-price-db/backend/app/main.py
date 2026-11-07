"""FastAPI app — a thin shell over the money/storage core (app.repo, app.money).

Local only: binds 127.0.0.1:8001 (see config). Cloudflare Access fronts it in
production; JWT verification here is defence-in-depth and OFF by default in dev (app.auth).
"""
from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager, contextmanager
from datetime import date as _date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db, export, fx, importer, money, offer_types, repo, sourcing, validate
from .auth import Identity, require_identity
from .config import Config, load_config
from .models import (
    BatchCreate, BatchOut, BestsellerSet, BrandAssign, BrandMerge, CurrentBestOut, EndOfferOut, FxManualRate,
    FxRatesOut, FxRefreshOut, OffersCreate, OffersCreated, OfferHistoryItem, ProductCreate,
    ProductOffersOut, ProductUpdate, RequestCreate, ReviewItem, SupplierCreate, SupplierOut,
    SupplierUpdate,
)
from .security import SecurityMiddleware

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"


def _brand_line_filter(conn, brand: str | None) -> tuple[str, list]:
    """SQL fragment + params to restrict a batch's offer lines to one brand (used by the offer
    detail view and the export). '' means all lines; '__unknown__' means products with no brand.
    So a multi-brand supplier offer is shown and exported as just the brand being viewed."""
    if brand == "__unknown__":
        return " AND p.brand_id IS NULL", []
    if brand:
        br = repo.get_brand_by_name(conn, brand)
        if br is None:
            raise HTTPException(404, detail=f"brand {brand!r} not found")
        return " AND p.brand_id = ?", [int(br["brand_id"])]
    return "", []


def create_app(config: Config | None = None) -> FastAPI:
    cfg = config or load_config()
    cfg.check_production_security()   # refuse to start in production without the Access gate
    os.makedirs(os.path.dirname(cfg.db_path) or ".", exist_ok=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = db.connect(cfg.db_path)
        try:
            db.migrate(conn)          # bring the schema up to date on startup
        finally:
            conn.close()
        yield

    app = FastAPI(title="Riviera Supplier Price DB", version="0.3.0", lifespan=lifespan)
    app.state.config = cfg
    app.add_middleware(SecurityMiddleware, allowed_origins=cfg.csrf_origins(),
                       max_body_bytes=validate.MAX_BODY_BYTES)

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
        validate.count_or_400(len(body.offers), validate.MAX_OFFER_ROWS, "offer rows")
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
            repo.rebuild_current_best(conn)   # a new rate lets no-rate batches convert — refresh cache
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
            repo.rebuild_current_best(conn)   # newly fetched rates let no-rate batches convert
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

    # ---- full database as a readable Excel workbook: one sheet per brand, products x suppliers ----
    @app.get("/export/workbook")
    def export_workbook(who: Identity = Depends(require_identity)):
        with get_conn() as conn:
            data = export.build_workbook(conn)
        stamp = _date.today().isoformat()
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="pricedb-{stamp}.xlsx"'})

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
                description=prod["description"],
                brand=repo.brand_name(conn, prod["brand_id"]),
                offers=items, current_best=cb,
            )

    # ================= frontend screens (Step B) =================

    # ---- reference lists ----
    @app.get("/brands")
    def list_brands(who: Identity = Depends(require_identity)):
        with get_conn() as conn:
            return [{"brand_id": int(r["brand_id"]), "name": r["name"]}
                    for r in repo.list_brands(conn)]

    @app.post("/brands/merge")
    def merge_brands(body: BrandMerge, who: Identity = Depends(require_identity)):
        """Merge one brand into another: all products move, the source brand is removed. EANs are
        unique per product, so duplicates are excluded automatically."""
        src = validate.text_or_400(body.from_brand, "from_brand")
        dst = validate.text_or_400(body.into_brand, "into_brand")
        with get_conn() as conn:
            a = repo.get_brand_by_name(conn, src)
            b = repo.get_brand_by_name(conn, dst)
            if a is None:
                raise HTTPException(404, detail=f"no brand named {src!r}")
            if b is None:
                raise HTTPException(404, detail=f"no brand named {dst!r}")
            if int(a["brand_id"]) == int(b["brand_id"]):
                raise HTTPException(422, detail="pick two different brands")
            moved = repo.merge_brands(conn, int(a["brand_id"]), int(b["brand_id"]))
            return {"merged_from": a["name"], "into": b["name"], "products_moved": moved}

    @app.get("/currencies")
    def currencies(who: Identity = Depends(require_identity)):
        """Currencies offerable at import: EUR first, then the configured FX symbols (so USD
        and the rest are always selectable even before any rate is fetched), then any extra
        currency that already has a stored rate."""
        with get_conn() as conn:
            rated = [r["currency"] for r in conn.execute("SELECT DISTINCT currency FROM fx_rates")]
        out: list[str] = []
        for c in ["EUR", *cfg.fx.symbols, *rated]:
            cu = (c or "").upper()
            if cu and cu not in out:
                out.append(cu)
        return {"currencies": out}

    @app.get("/suppliers")
    def list_suppliers(who: Identity = Depends(require_identity)):
        with get_conn() as conn:
            return repo.list_suppliers(conn)

    @app.patch("/suppliers/{supplier_id}")
    def update_supplier(supplier_id: int, body: SupplierUpdate,
                        who: Identity = Depends(require_identity)):
        name = validate.text_or_400(body.name, "name")
        if name is not None and not name.strip():
            raise HTTPException(422, detail="supplier name cannot be blank")
        country = validate.text_or_400(body.country, "country")
        with get_conn() as conn:
            if conn.execute("SELECT 1 FROM suppliers WHERE supplier_id = ?",
                            (supplier_id,)).fetchone() is None:
                raise HTTPException(404, detail=f"supplier {supplier_id} not found")
            repo.update_supplier(conn, supplier_id, name=name, country=country)
            return {"supplier_id": supplier_id, "ok": True}

    # ---- offers grid (live columns only — the replacement rule, §3.3) ----
    @app.get("/grid")
    def grid(brand: str | None = None, territory: str | None = None, currency: str | None = None,
             unknown: bool = False, who: Identity = Depends(require_identity)):
        with get_conn() as conn:
            if unknown:                       # the Unknown-brands tab (products with no brand yet)
                batches, rows, avail = repo.brand_grid(conn, None, territory, currency)
                brand_name = None
            else:
                if not brand:
                    raise HTTPException(422, detail="brand is required (or pass unknown=true)")
                brow = repo.get_brand_by_name(conn, brand)
                if brow is None:
                    raise HTTPException(404, detail=f"no brand named {brand!r}")
                batches, rows, avail = repo.brand_grid(conn, int(brow["brand_id"]), territory, currency)
                brand_name = brow["name"]
            for b in batches:
                b["kind_label"] = offer_types.kind_label(b.get("kind"))
            return {"brand": brand_name, "unknown": unknown, "batches": batches, "rows": rows,
                    "currencies": avail}

    # ---- bestsellers (flag + highlight; the dedicated ranking page is deferred) ----
    @app.get("/bestsellers")
    def bestsellers(brand: str | None = None, who: Identity = Depends(require_identity)):
        with get_conn() as conn:
            brand_id = None
            if brand:
                brow = repo.get_brand_by_name(conn, brand)
                if brow is None:
                    raise HTTPException(404, detail=f"no brand named {brand!r}")
                brand_id = int(brow["brand_id"])
            return repo.bestsellers(conn, brand_id)

    # ---- import log + delete a mis-imported batch ----
    @app.get("/batches")
    def list_batches(who: Identity = Depends(require_identity)):
        with get_conn() as conn:
            out = []
            for b in repo.list_batches(conn):
                b["kind_label"] = offer_types.kind_label(b.get("kind"))
                out.append(b)
            return out

    @app.get("/batches/{batch_id}/offers")
    def batch_offers(batch_id: int, brand: str | None = None,
                     who: Identity = Depends(require_identity)):
        """A batch's offer lines. `brand` restricts them to that brand, so the detail view of a
        multi-brand supplier offer shows only the brand being viewed (matching the export)."""
        with get_conn() as conn:
            b = conn.execute(
                "SELECT b.*, s.name AS supplier_name FROM offer_batches b "
                "JOIN suppliers s ON s.supplier_id = b.supplier_id WHERE b.batch_id = ?",
                (batch_id,)).fetchone()
            if b is None:
                raise HTTPException(404, detail=f"batch {batch_id} not found")
            rate = b["fx_rate_eur"] or ("1" if (b["currency"] or "").upper() == "EUR" else None)
            frag, extra = _brand_line_filter(conn, brand)
            lines = []
            for r in conn.execute(
                "SELECT o.price_original, p.ean, p.description FROM offers o "
                "JOIN products p ON p.product_id = o.product_id WHERE o.batch_id = ?" + frag +
                " ORDER BY o.price_original", [batch_id, *extra]):
                lines.append({
                    "ean": r["ean"], "description": r["description"],
                    "price_original": str(money.from_minor(r["price_original"]).quantize(money.CENT)),
                    "price_eur": (str(money.unit_price_eur(r["price_original"], rate).quantize(money.CENT))
                                  if rate else None)})
            display_brand = (brand if (brand and brand != "__unknown__")
                             else ("Unknown" if brand == "__unknown__" else b["brand"]))
            return {
                "batch_id": batch_id, "supplier": b["supplier_name"], "brand": display_brand,
                "received_date": b["received_date"], "currency": b["currency"],
                "territory": b["territory"], "kind": b["kind"],
                "kind_label": offer_types.kind_label(b["kind"]), "ended_at": b["ended_at"],
                "moq": b["moq"], "lead_time": b["lead_time"], "notes": b["notes"],
                "source_file": b["source_file"], "lines": lines}

    @app.get("/batches/{batch_id}/export")
    def export_offer(batch_id: int, margin: str = "0.07", brand: str | None = None,
                     who: Identity = Depends(require_identity)):
        """Fill the owner's offer template with this batch's lines (EAN / Description / Price).

        `margin` (decimal fraction) raises the price to a sell price: sell = cost / (1 - margin),
        in EUR (falls back to the offer's original currency if no FX rate). `brand` restricts the
        export to that brand's lines only, so a multi-brand supplier offer exports just the brand
        the owner is viewing. '__unknown__' means products with no brand yet."""
        try:
            m = Decimal(margin)
        except (InvalidOperation, TypeError, ValueError):
            raise HTTPException(422, detail="margin must be a decimal fraction, e.g. 0.07")
        if m < 0 or m >= 1:
            raise HTTPException(422, detail="margin must be between 0 and 1 (0%–99%)")
        with get_conn() as conn:
            b = conn.execute(
                "SELECT b.*, s.name AS supplier_name FROM offer_batches b "
                "JOIN suppliers s ON s.supplier_id = b.supplier_id WHERE b.batch_id = ?",
                (batch_id,)).fetchone()
            if b is None:
                raise HTTPException(404, detail=f"batch {batch_id} not found")
            rate = b["fx_rate_eur"] or ("1" if (b["currency"] or "").upper() == "EUR" else None)
            frag, extra = _brand_line_filter(conn, brand)
            sql = ("SELECT o.price_original, p.ean, p.description FROM offers o "
                   "JOIN products p ON p.product_id = o.product_id WHERE o.batch_id = ?" + frag +
                   " ORDER BY o.price_original")
            lines = []
            for r in conn.execute(sql, [batch_id, *extra]):
                cost = (money.unit_price_eur(r["price_original"], rate) if rate
                        else money.from_minor(r["price_original"]))
                sell = (cost / (Decimal(1) - m)).quantize(money.CENT)
                lines.append({"ean": r["ean"], "description": r["description"] or "",
                              "price": float(sell)})
            label_brand = "" if (not brand or brand == "__unknown__") else brand
            fname = f"{b['supplier_name'] or 'offer'}_{label_brand}_{b['received_date'] or ''}_offer.xlsx"
        content = export.fill_offer_template(lines)
        fname = re.sub(r"[^\w.-]+", "_", fname).strip("_") or "offer.xlsx"
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'})

    @app.delete("/batches/{batch_id}")
    def delete_batch(batch_id: int, who: Identity = Depends(require_identity)):
        with get_conn() as conn:
            if conn.execute("SELECT 1 FROM offer_batches WHERE batch_id = ?",
                            (batch_id,)).fetchone() is None:
                raise HTTPException(404, detail=f"batch {batch_id} not found")
            touched = repo.delete_batch(conn, batch_id)
            return {"batch_id": batch_id, "deleted": True, "products_rebuilt": len(touched)}

    # ---- review queue ----
    @app.get("/review")
    def review(who: Identity = Depends(require_identity)):
        with get_conn() as conn:
            items = [{"ean": r["ean"], "reason": r["reason"], "batch_id": r["batch_id"]}
                     for r in repo.list_review_items(conn)]
            # products that still need a brand / description (derived, like the prototype)
            for r in conn.execute(
                "SELECT p.ean, p.description, br.name AS brand FROM products p "
                "LEFT JOIN brands br ON br.brand_id = p.brand_id"):
                if not (r["brand"] or "").strip():
                    items.append({"ean": r["ean"], "reason": "needs a brand",
                                  "detail": r["description"] or "", "fix": True})
                elif not (r["description"] or "").strip():
                    items.append({"ean": r["ean"], "reason": "missing description",
                                  "detail": r["brand"], "fix": True})
            return items

    # ---- products: manual add ("Add line"), edit, bestseller flag ----
    @app.post("/products")
    def create_product(body: ProductCreate, who: Identity = Depends(require_identity)):
        ean = validate.ean_or_400(body.ean)
        desc = validate.text_or_400(body.description, "description")
        brand = validate.text_or_400(body.brand, "brand")
        with get_conn() as conn:
            if repo.get_product_by_ean(conn, ean) is not None:
                raise HTTPException(409, detail=f"a product with EAN {ean} already exists")
            brand_id = repo.get_or_create_brand(conn, brand.strip()) if brand and brand.strip() else None
            status = "active" if brand_id is not None else "needs_review"
            repo.create_product(conn, ean, description=desc, brand_id=brand_id, status=status)
            return {"ean": ean, "ok": True, "ean_check": importer.ean_check_ok(ean)}

    @app.patch("/products/{ean}")
    def update_product(ean: str, body: ProductUpdate, who: Identity = Depends(require_identity)):
        ean = validate.ean_or_400(ean)
        desc = validate.text_or_400(body.description, "description")
        brand = validate.text_or_400(body.brand, "brand")
        with get_conn() as conn:
            if repo.get_product_by_ean(conn, ean) is None:
                raise HTTPException(404, detail=f"no product with EAN {ean}")
            set_brand = "brand" in body.model_fields_set
            brand_id = None
            if set_brand and brand and brand.strip():
                brand_id = repo.get_or_create_brand(conn, brand.strip())
            repo.update_product(conn, ean,
                                description=desc if "description" in body.model_fields_set else None,
                                brand_id=brand_id, set_brand=set_brand)
            touched = repo.get_product_by_ean(conn, ean)
            repo.rebuild_current_best(conn, [int(touched["product_id"])])
            return {"ean": ean, "ok": True}

    @app.post("/products/assign-brand")
    def assign_brand_bulk(body: BrandAssign, who: Identity = Depends(require_identity)):
        """Bulk-assign one brand to several products (the Unknown-brands tab: select rows, pick a brand).
        Creates the brand if new. Brand is per-product metadata and does not affect prices, so the
        current_best cache is untouched. Returns how many moved and any EANs not found."""
        brand = validate.text_or_400(body.brand, "brand")
        if not brand or not brand.strip():
            raise HTTPException(422, detail="brand is required")
        validate.count_or_400(len(body.eans), validate.MAX_OFFER_ROWS, "eans")
        with get_conn() as conn:
            brand_id = repo.get_or_create_brand(conn, brand.strip())
            assigned, missing = 0, []
            for raw in body.eans:
                prod = repo.get_product_by_ean(conn, str(raw).strip())
                if prod is None:
                    missing.append(str(raw))
                    continue
                repo.assign_brand(conn, int(prod["product_id"]), brand_id)
                assigned += 1
            return {"brand": brand.strip(), "assigned": assigned, "missing": missing}

    @app.post("/products/{ean}/bestseller")
    def set_bestseller(ean: str, body: BestsellerSet, who: Identity = Depends(require_identity)):
        ean = validate.ean_or_400(ean)
        with get_conn() as conn:
            if repo.get_product_by_ean(conn, ean) is None:
                raise HTTPException(404, detail=f"no product with EAN {ean}")
            repo.set_bestseller(conn, ean, body.is_bestseller)
            return {"ean": ean, "is_bestseller": body.is_bestseller}

    # ---- customer requests (§9): inputs stored, result recomputed live, never stored ----
    @app.get("/customers")
    def list_customers(who: Identity = Depends(require_identity)):
        with get_conn() as conn:
            return repo.distinct_customers(conn)

    def _request_payload(conn, req) -> dict:
        lines = repo.request_lines(conn, int(req["request_id"]))
        computed = sourcing.compute(
            conn,
            [{"ean": ln["ean"], "target_price": ln["target_price"], "currency": ln["currency"],
              "description": ln["description"]}
             for ln in lines],
            req["margin"])
        return {
            "request_id": int(req["request_id"]), "customer": req["customer"],
            "brand": req["brand"], "request_date": req["request_date"],
            "margin": req["margin"], "count": computed["count"],
            "ok": computed["ok"], "no": computed["no"], "unknown": computed["unknown"],
            "results": computed["results"],
        }

    @app.post("/requests")
    def create_request(body: RequestCreate, who: Identity = Depends(require_identity)):
        validate.count_or_400(len(body.lines), validate.MAX_REQUEST_LINES, "request lines")
        if not body.lines:
            raise HTTPException(422, detail="a request needs at least one line")
        margin = validate.margin_or_400(body.margin)
        customer = validate.text_or_400(body.customer, "customer")
        brand = validate.text_or_400(body.brand, "brand")
        clean: list[dict] = []
        for ln in body.lines:
            e = validate.ean_or_400(ln.ean)
            if ln.target_price is None or str(ln.target_price).strip() == "":
                tp = None                                  # no target -> supply overview
            else:
                tp = money.import_price_minor(validate.price_or_400(ln.target_price, "target_price"))
            desc = validate.text_or_400(ln.description, "description") if ln.description else None
            clean.append({"ean": e, "target_price": tp,
                          "currency": (ln.currency or "EUR").upper(), "description": desc})
        with get_conn() as conn:
            rid = repo.create_request(conn, customer=customer, brand=brand,
                                      request_date=body.request_date, margin=str(margin),
                                      lines=clean)
            return _request_payload(conn, repo.get_request(conn, rid))

    @app.get("/requests")
    def list_requests(who: Identity = Depends(require_identity)):
        with get_conn() as conn:
            return [_request_payload(conn, r) for r in repo.list_requests(conn)]

    @app.get("/requests/{request_id}")
    def get_request(request_id: int, who: Identity = Depends(require_identity)):
        with get_conn() as conn:
            req = repo.get_request(conn, request_id)
            if req is None:
                raise HTTPException(404, detail=f"request {request_id} not found")
            return _request_payload(conn, req)

    @app.delete("/requests/{request_id}")
    def delete_request(request_id: int, who: Identity = Depends(require_identity)):
        with get_conn() as conn:
            if repo.get_request(conn, request_id) is None:
                raise HTTPException(404, detail=f"request {request_id} not found")
            repo.delete_request(conn, request_id)
            return {"request_id": request_id, "deleted": True}

    # ---- the single-page frontend (served from this origin only; see CSP) ----
    if FRONTEND_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(str(FRONTEND_DIR / "index.html"))

    return app


# Module-level app for `uvicorn app.main:app`
app = create_app()
