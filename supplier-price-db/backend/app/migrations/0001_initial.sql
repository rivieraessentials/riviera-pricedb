-- 0001_initial — the committed design §1.1 schema, verbatim in field names/types.
-- Money: offers.price_original is INTEGER minor units at ×1,000,000 / 6dp (O5 §12).
-- FX: offer_batches.fx_rate_eur and fx_rates.rate are TEXT, stored exactly as published.
-- EUR / per-piece values are computed on read (app.money), never stored here.

CREATE TABLE brands (
    brand_id      INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,            -- canonical name, unique case-insensitively
    aliases_json  TEXT NOT NULL DEFAULT '[]'  -- JSON array of supplier spellings (SQLite has no array type)
);
CREATE UNIQUE INDEX ux_brands_name_nocase ON brands (name COLLATE NOCASE);

CREATE TABLE suppliers (
    supplier_id       INTEGER PRIMARY KEY,
    name              TEXT NOT NULL,
    default_territory TEXT,                 -- 'T1' (non-European) | 'T2' (European)
    notes             TEXT
);

CREATE TABLE products (
    product_id          INTEGER PRIMARY KEY,
    ean                 TEXT NOT NULL UNIQUE,   -- identity; kept as TEXT so leading zeros survive
    description         TEXT,
    brand_id            INTEGER REFERENCES brands(brand_id),   -- nullable until resolved
    size                TEXT,                   -- '100ml' etc; intrinsic to the EAN
    status              TEXT NOT NULL DEFAULT 'active',   -- 'active' | 'needs_review'
    is_bestseller       INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    first_seen_batch_id INTEGER REFERENCES offer_batches(batch_id)
);

CREATE TABLE offer_batches (
    batch_id       INTEGER PRIMARY KEY,
    supplier_id    INTEGER NOT NULL REFERENCES suppliers(supplier_id),
    received_date  TEXT,                    -- date specified on import (ISO text)
    currency       TEXT NOT NULL,           -- EUR/GBP/USD/SGD/CAD/HKD (batch default)
    territory      TEXT,                    -- T1 / T2
    kind           TEXT,                    -- 'price list' | 'stock offer' | ...
    source_file    TEXT,                    -- original filename
    source_sha256  TEXT,                    -- checksum of the uploaded bytes; re-upload warns (§2.1)
    fx_rate_eur    TEXT,                    -- import-date rate, TEXT, exactly as published (1 EUR = rate * currency)
    fx_source      TEXT,                    -- 'frankfurter' | 'ecb' | 'manual'
    fx_locked      INTEGER NOT NULL DEFAULT 0,
    label          TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
-- A re-uploaded identical file is DETECTED (not blocked at the DB) so the wizard can warn.
CREATE INDEX ix_batches_sha ON offer_batches (source_sha256);

CREATE TABLE offers (
    offer_id       INTEGER PRIMARY KEY,
    batch_id       INTEGER NOT NULL REFERENCES offer_batches(batch_id),
    product_id     INTEGER NOT NULL REFERENCES products(product_id),
    price_original INTEGER NOT NULL,        -- LOWEST quoted price, minor units at ×1,000,000 / 6dp. NEVER float.
    currency       TEXT NOT NULL,
    pieces_per_box INTEGER,
    price_basis    TEXT NOT NULL DEFAULT 'per_piece',   -- 'per_piece' | 'per_box'
    raw_row        TEXT,                    -- JSON of the original row; for price cells keeps .w (shown) and .z (format)
    available_qty  INTEGER,                 -- nullable (OPEN §12, reserved/unused)
    moq            INTEGER,                 -- nullable (OPEN §12)
    valid_until    TEXT,                    -- nullable ISO date (OPEN §12, feeds freshness rule)
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX ix_offers_product ON offers (product_id);
CREATE INDEX ix_offers_batch ON offers (batch_id);

CREATE TABLE fx_rates (
    rate_date  TEXT NOT NULL,
    currency   TEXT NOT NULL,
    rate       TEXT NOT NULL,               -- 1 EUR = rate * currency, TEXT exactly as published
    source     TEXT,
    PRIMARY KEY (rate_date, currency)
);

CREATE TABLE current_best (
    product_id          INTEGER PRIMARY KEY REFERENCES products(product_id),
    best_offer_id       INTEGER NOT NULL REFERENCES offers(offer_id),
    best_unit_price_eur INTEGER NOT NULL,   -- rounded per-piece EUR in minor units; the ONLY stored cache
    best_supplier_id    INTEGER REFERENCES suppliers(supplier_id),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Seed defaults. freshness_rule is an OPEN decision (O2) — this is a clearly marked
-- PLACEHOLDER meaning "every offer counts" until the end user decides.
INSERT INTO settings (key, value) VALUES
    ('freshness_rule',   'all'),          -- PLACEHOLDER (O2 undecided): no offer is filtered out
    ('freshness_days',   ''),             -- unset until freshness_rule = 'age'
    ('fx_display_basis', 'import_date'),  -- PLACEHOLDER (O4 undecided)
    ('sourcing_margin',  '0.07'),
    ('display_currency', 'EUR');
