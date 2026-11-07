# Riviera Supplier Price Database

Standalone supplier price database for Riviera Essentials. Products are keyed by
**EAN** so the data links back to the CRM without duplicating customer or supplier
records. Hosted separately from the CRM (planned: `db.rivieraessentials.com`,
behind Cloudflare Access).

**Data minimisation:** this repo and its database hold **business data only** —
EAN, prices, product details, and a supplier's *company* name. No personal data
(no contact persons, emails, phone numbers), no real customer data, and no secrets.
Tests and fixtures use **synthetic** data only.

## Layout

- `supplier-price-db/backend/` — FastAPI + SQLite backend (money core, migrations, importer, API).
- `SUPPLIER_PRICE_DB_DESIGN.md` — schema and design decisions.
- `SUPPLIER_PRICE_DB_BUILD_PLAN.md` — phased build plan.
- `SUBDOMAIN_SETUP_RUNBOOK.md` — subdomain / hosting setup.
- `scripts/price_decimal_scan.py` — price-decimal scan tool (analysis only).

## Shared documentation (lives in the CRM repo)

Some cross-cutting docs are shared with the CRM and remain in the CRM repository
(`riviera-crm-git`). Refer to them there:

- `DEPENDENCIES.md` — dependency inventory and pinning policy.
- `BACKUP_SYSTEM.md` — backup/restore system (BorgBase, restic, healthchecks).
- `RESTART_PLAN_2026-10-13.md` — restart / recovery plan.
- `ROADMAP_LEDGER.md` — roadmap ledger (LED-* entries).
