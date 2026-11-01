# Supplier Price DB — backend (Phase 1 skeleton)

FastAPI + SQLite backend for the supplier price database, per
[`SUPPLIER_PRICE_DB_DESIGN.md`](../../SUPPLIER_PRICE_DB_DESIGN.md) and
[`SUPPLIER_PRICE_DB_BUILD_PLAN.md`](../../SUPPLIER_PRICE_DB_BUILD_PLAN.md).

**Local only.** Binds `127.0.0.1:8001`; Cloudflare Access fronts it in production.
Phase 1 is the skeleton: schema, migrations, the money/storage core, a minimal API, and
tests. No UI, no deploy, no server changes.

## Layout
```
app/
  money.py       integer minor units (×1,000,000 / 6dp) + Decimal math; NO float in the money path
  db.py          sqlite connection (WAL, FKs) + numbered migration runner (up/down)
  migrations/    0001_initial.sql  (+ .down.sql) — design §1.1 schema, verbatim
  config.py      loads config.toml / PRICEDB_* env; nothing operational is hardcoded
  repo.py        data access; APPEND-ONLY (no offer-price update path); current_best rebuild
  auth.py        Cloudflare Access JWT verify — defence-in-depth, OFF by default in dev
  models.py      API shapes; prices cross the wire as strings (exact, never float)
  main.py        FastAPI app: /healthz + suppliers / batches / offers / product history
tests/           pytest — the 6 required cases + money + migrations + API
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

## Money model (O5)
`offers.price_original` is an **integer** at scale ×1,000,000 (6 decimals). The importer
**must not round** — a value needing more than 6 decimals raises `PriceScaleError` and
would go to the review queue. EUR and per-piece values are **computed on read**
(`app.money`); the only stored cache is `current_best.best_unit_price_eur`. FX rates are
**TEXT**, stored exactly as published; all FX math is Python `Decimal`.

## current_best / freshness (O2 — undecided)
`rebuild_current_best()` recomputes the cheapest live offer per product, deterministically
(lowest per-piece EUR, ties broken by lowest `offer_id`). "Live" is governed by the
`freshness_rule` setting, whose **placeholder default is `all`** (every offer counts) until
the end user decides O2. Other rules raise `NotImplementedError` rather than guess.

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
