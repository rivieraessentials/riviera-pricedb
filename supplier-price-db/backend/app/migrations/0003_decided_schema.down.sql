-- Rollback of 0003_decided_schema. Restores the 0001 column STRUCTURE (data in dropped
-- columns cannot be recovered — pre-production, synthetic data only).
ALTER TABLE offer_batches DROP COLUMN brand;
ALTER TABLE offer_batches DROP COLUMN moq;
ALTER TABLE offer_batches DROP COLUMN lead_time;
ALTER TABLE offer_batches DROP COLUMN notes;
ALTER TABLE offer_batches DROP COLUMN ended_at;

ALTER TABLE products ADD COLUMN size TEXT;

ALTER TABLE offers ADD COLUMN pieces_per_box INTEGER;
ALTER TABLE offers ADD COLUMN price_basis    TEXT NOT NULL DEFAULT 'per_piece';
ALTER TABLE offers ADD COLUMN raw_row        TEXT;
ALTER TABLE offers ADD COLUMN available_qty  INTEGER;
ALTER TABLE offers ADD COLUMN moq            INTEGER;
ALTER TABLE offers ADD COLUMN valid_until    TEXT;

INSERT INTO settings (key, value) VALUES ('freshness_rule', 'all'), ('freshness_days', '');
UPDATE settings SET value = 'import_date' WHERE key = 'fx_display_basis';
