-- 0002_review_queue — rows the importer could not safely import land here instead of
-- failing the whole file (design §2.1/§2.4: nothing is silently dropped or rounded).
CREATE TABLE review_queue (
    review_id  INTEGER PRIMARY KEY,
    batch_id   INTEGER REFERENCES offer_batches(batch_id),
    ean        TEXT,
    reason     TEXT NOT NULL,          -- e.g. 'more than 6 decimals: 3.4285714285714'
    raw_row    TEXT,                   -- JSON of the original row, for the review screen
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX ix_review_batch ON review_queue (batch_id);
