-- 0005_optional_target — a customer request can be just a list of EANs to source, with no
-- target price (NULL target = "list the suppliers that can supply this EAN, cheapest first").
-- SQLite can't drop a NOT NULL in place, so rebuild request_lines. Nothing references it, so
-- the rebuild is safe under foreign_keys=ON.
CREATE TABLE request_lines_new (
    line_id      INTEGER PRIMARY KEY,
    request_id   INTEGER NOT NULL REFERENCES requests(request_id) ON DELETE CASCADE,
    ean          TEXT NOT NULL,
    target_price INTEGER,                              -- NULL = no target (supply overview)
    currency     TEXT NOT NULL DEFAULT 'EUR'
);
INSERT INTO request_lines_new (line_id, request_id, ean, target_price, currency)
    SELECT line_id, request_id, ean, target_price, currency FROM request_lines;
DROP TABLE request_lines;
ALTER TABLE request_lines_new RENAME TO request_lines;
CREATE INDEX ix_request_lines_request ON request_lines (request_id);
