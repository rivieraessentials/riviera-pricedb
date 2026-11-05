-- Rollback 0006: rebuild request_lines without the description column.
CREATE TABLE request_lines_old (
    line_id      INTEGER PRIMARY KEY,
    request_id   INTEGER NOT NULL REFERENCES requests(request_id) ON DELETE CASCADE,
    ean          TEXT NOT NULL,
    target_price INTEGER,
    currency     TEXT NOT NULL DEFAULT 'EUR'
);
INSERT INTO request_lines_old (line_id, request_id, ean, target_price, currency)
    SELECT line_id, request_id, ean, target_price, currency FROM request_lines;
DROP TABLE request_lines;
ALTER TABLE request_lines_old RENAME TO request_lines;
CREATE INDEX ix_request_lines_request ON request_lines (request_id);
