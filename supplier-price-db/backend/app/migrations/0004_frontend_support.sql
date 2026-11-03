-- 0004_frontend_support — server-side storage the MVP frontend (Step B) needs. Pre-production,
-- synthetic data only; applied/rolled back like any other numbered migration.
--
-- suppliers.country : the free-text country shown on the Suppliers screen (a prototype field;
--                     territory stays per-offer). Business data, not personal data.
-- requests/*        : the customer-request store, DATA-MINIMISED per design §9 and the owner's
--                     decision (2026-11-03). Only EAN, target price and currency are kept, with
--                     an OPTIONAL customer COMPANY name and an optional brand label. NO contact
--                     details are stored, the uploaded file is never kept, and the sourcing
--                     RESULT is not stored — it is recomputed live from current_best on each view.

ALTER TABLE suppliers ADD COLUMN country TEXT;

CREATE TABLE requests (
    request_id   INTEGER PRIMARY KEY,
    customer     TEXT,                              -- OPTIONAL company name only (no contact details, §9)
    brand        TEXT,                              -- optional brand label the request is about
    request_date TEXT,                              -- ISO date the request was made
    margin       TEXT NOT NULL DEFAULT '0.07',      -- min margin as a decimal fraction, exactly as entered
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE request_lines (
    line_id      INTEGER PRIMARY KEY,
    request_id   INTEGER NOT NULL REFERENCES requests(request_id) ON DELETE CASCADE,
    ean          TEXT NOT NULL,
    target_price INTEGER NOT NULL,                  -- per-piece 2dp target in minor units ×1,000,000, in `currency`
    currency     TEXT NOT NULL DEFAULT 'EUR'
);
CREATE INDEX ix_request_lines_request ON request_lines (request_id);
