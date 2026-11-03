# Supplier Price DB — backend (Phase 2: ingestion)

FastAPI + SQLite backend for the supplier price database, per
[`SUPPLIER_PRICE_DB_DESIGN.md`](../../SUPPLIER_PRICE_DB_DESIGN.md) and
[`SUPPLIER_PRICE_DB_BUILD_PLAN.md`](../../SUPPLIER_PRICE_DB_BUILD_PLAN.md).

**Local only.** Binds `127.0.0.1:8001`; Cloudflare Access fronts it in production.
Phase 2 implements the decided data model (2026-11-02) and the ingestion core: the
migration to the decided schema, 2dp per-piece pricing, brand resolution from the upload
form, the replacement/live rule, and synthetic-fixture tests. No UI, no deploy, no server
changes.

## Layout
```
app/
  money.py       integer minor units (×1,000,000 / 6dp) + Decimal math; NO float. Rounds to 2dp (O6)
  db.py          sqlite connection (WAL, FKs) + numbered migration runner (up/down)
  migrations/    0001_initial, 0002_review_queue, 0003_decided_schema (each + .down.sql)
  config.py      loads config.toml / PRICEDB_* env; nothing operational is hardcoded
  repo.py        data access; APPEND-ONLY (no offer-price update path); current_best rebuild
  importer.py    the import path: lowest price, per-box divide+round, brand resolution, review
  auth.py        Cloudflare Access JWT verify — defence-in-depth, OFF by default in dev
  models.py      API shapes; prices cross the wire as strings (exact, never float)
  main.py        FastAPI app: /healthz + suppliers / batches / offers / end-offer / history
tests/           pytest — core + money + importer + migrations + API + the 5 scenario tests
```

## Run
```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows; use .venv/bin on POSIX
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
curl http://127.0.0.1:8001/healthz
```

## Test
```bash
.venv/Scripts/python -m pytest -q
```

## Money model (O5 / O6)
`offers.price_original` is an **integer** at scale ×1,000,000 (6 decimals), and since O6
(2026-11-02) always holds a **per-piece price rounded to 2 decimals** (round-half-up). The
importer **divides a per-box price by pieces/box first, then rounds once** (`24.99/12 →
2.08`); pieces/box is used at import only, not stored. EUR and per-piece values are
**computed on read** (`app.money`); the only stored cache is
`current_best.best_unit_price_eur`. FX rates are **TEXT**, stored exactly as published; all
FX math is Python `Decimal`.

## current_best / live offers (O2 — decided 2026-11-02)
`rebuild_current_best()` recomputes the cheapest **live** offer per product,
deterministically (lowest per-piece EUR, ties broken by lowest `offer_id`). An offer is
**live** when its batch is the most recent (by offer date) for that **(supplier, brand,
offer type)** and, for stock offers, `ended_at IS NULL` — so a newer price list / stock
offer replaces the previous one of the same brand+type, and the **"End offer"** action stops
a stock offer counting. No age-based expiry; no `freshness_rule` setting (`§3.3`).

## Identity / Cloudflare Access (OFF by default)
`config.toml` carries an **email allow-list** (any number of addresses, never hardcoded).
JWT verification is **disabled** in local dev (`[access] enabled = false`); the configured
`dev_identity` is used. To enable it on the server you need (verified against Cloudflare's
official docs, 2026-11-01):

- **team name** → issuer `https://<team>.cloudflareaccess.com`, JWKS `…/cdn-cgi/access/certs`
- **AUD tag** of the Access application (Zero Trust → Access → Applications → your app →
  Application Audience (AUD) Tag) — exists only once the Access app is created (subdomain runbook)
- token arrives in header **`Cf-Access-Jwt-Assertion`** (RS256); claims checked: `aud`, `iss`, `exp`
- then the token's `email` must be in the allow-list

Copy `config.example.toml` → `config.toml` (gitignored) and fill these in on the server.
