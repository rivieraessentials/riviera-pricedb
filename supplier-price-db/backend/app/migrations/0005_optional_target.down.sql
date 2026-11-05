-- Rollback 0005: restore NOT NULL on target_price (a NULL target becomes 0).
CREATE TABLE request_lines_old (
    line_id      INTEGER PRIMARY KEY,
    request_id   INTEGER NOT NULL REFERENCES requests(request_id) ON DELETE CASCADE,
    ean          TEXT NOT NULL,
    target_price INTEGER NOT NULL,
    currency     TEXT NOT NULL DEFAULT 'EUR'
);
INSERT INTO request_lines_old (line_id, request_id, ean, target_price, currency)
    SELECT line_id, request_id, ean, COALESCE(target_price, 0), currency FROM request_lines;
DROP TABLE request_lines;
ALTER TABLE request_lines_old RENAME TO request_lines;
CREATE INDEX ix_request_lines_request ON request_lines (request_id);
