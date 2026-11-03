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
  offer_types.py the two offer types: stored code (price_list/stock_offer) -> display name
  fx.py          daily FX fetch (Frankfurter -> ECB fallback), base EUR, no key; `python -m app.fx`
  export.py      the offline download bundle (SQLite snapshot + offers/current_best CSVs)
  sourcing.py    customer-request sourcing (§9) — recomputed live from current_best, never stored
  validate.py    server-side input validation + limits (EANs, prices, row counts, body size)
  security.py    CSP + hardening headers; Origin / JSON guard on state-changing requests
  auth.py        Cloudflare Access JWT verify — defence-in-depth, OFF by default in dev
  models.py      API shapes; prices cross the wire as strings (exact, never float)
  main.py        FastAPI app: health + suppliers / batches / offers / end-offer / history
                 + fx + export + grid / bestsellers / review / products / requests; serves the
                 single-page frontend ("/" + /static)
  migrations/    … + 0004_frontend_support (suppliers.country + the request store)
tests/           pytest — core + money + importer + migrations + API + scenario + replacement
                 + fx + export + frontend-API + security tests
../frontend/     the UI (index.html, styles.css, app.js, vendor/xlsx.full.min.js) — served by main.py
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

## Exchange rates & offline export (Phase 2 / Step A)

- **Daily FX fetch** (`app.fx`): base EUR, **no API key** — **Frankfurter** primary, **ECB** daily XML
  fallback. Rates stored as TEXT exactly as published (`1 EUR = rate * currency`; O5 §12). Run it from a
  systemd timer / cron on the server (design §4.2): `python -m app.fx`. On-demand from the UI via
  `POST /fx/refresh`; read with `GET /fx/rates[?rate_date=…]`.
- **Manual override** (`POST /fx/rates` → `source='manual'`): the weekend/holiday escape hatch, a day-level
  rate. The per-batch override (`offer_batches.fx_rate_eur` + `fx_locked`) is separate and wins for that batch.
- **Offline download bundle** (`GET /export`): a ZIP with a consistent **SQLite snapshot** (backup API, WAL-safe)
  plus plain **`offers.csv`** and **`current_best.csv`** — the 3-2-1 local copy (design §6). Business data only.

## Frontend (Step B)

The single-page UI lives in `../frontend` and is served by this app: `GET /` returns `index.html`, and
`/static/*` serves `styles.css`, `app.js` and `vendor/xlsx.full.min.js`. It ports the prototype's seven
screens onto the API — Offers grid (live columns only, the replacement rule), Bestsellers, Customer requests
(sourcing), Suppliers, Import log, Review queue, Currency & FX — and talks to the API with same-origin JSON.
All data lives on the server; `localStorage` holds only the grid zoom. **SheetJS 0.20.3** is self-hosted with
SRI (no CDN at runtime); its Apache-2.0 notice is `vendor/xlsx.LICENSE.txt`. See the build plan §7.6 for the
(decision-driven) differences from the prototype.

New endpoints driving it: `/brands`, `/suppliers` (+ `PATCH`), `/grid`, `/bestsellers`, `/batches` (+
`GET {id}/offers`, `DELETE {id}`), `/review`, `/products` (`POST`, `PATCH {ean}`, `POST {ean}/bestseller`),
`/customers`, and `/requests` (`POST`/`GET`/`GET {id}`/`DELETE`). A request stores inputs only; the result is
recomputed live by `app.sourcing`.

## Security (built into Step B — owner, 2026-11-03)

- **Production Access gate:** `config.check_production_security()` makes the app refuse to start in
  `environment = production` unless Access JWT is ON (team + AUD tag), the email allow-list is non-empty, and
  `public_origin` is set. OFF only in local dev. See `config.example.toml` and the subdomain runbook.
- **CSP + headers** (`app.security`): a strict `Content-Security-Policy` (this origin only; no inline scripts),
  plus `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` on every response.
- **Origin / JSON / size guard:** state-changing requests must send JSON and an Origin matching the app's own
  (CSRF defence); a 16 MB body cap.
- **Validation** (`app.validate`): EAN digits/length (+ check digit on import), positive prices under a cap,
  row-count limits, parameterised SQL only — clear 4xx on bad input.
- **XSS:** the frontend renders every supplier/user value as text (no inline handlers, no `innerHTML` of data).
- An **independent security review by a separate agent** is the pre-cutover gate (build plan step E).

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
