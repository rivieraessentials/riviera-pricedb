-- 0003_decided_schema — bring the Phase-1 skeleton to the committed §1.1 schema after
-- the end-user decisions of 2026-11-02 (design §11 items 18-28, §12). Pre-production,
-- synthetic data only: this is a structural migration, applied/rolled back like any other.
--
-- offers:        store ONLY a per-piece 2dp price. Drop everything used to reproduce the
--                as-quoted figure (O6 §2.4) or reserved for undecided options (O2/O3/O7).
-- offer_batches: the per-upload facts now live here — brand, MOQ (free text), lead time,
--                notes, and ended_at for the "End offer" action on stock offers.
-- products:      size is not needed for price comparison (removed 2026-11-02).
-- settings:      O2 decided (no freshness rule/age), O7 decided; FX default basis = today (O4).

-- offers: drop the columns no longer stored (none is indexed, so DROP COLUMN is safe).
ALTER TABLE offers DROP COLUMN pieces_per_box;
ALTER TABLE offers DROP COLUMN price_basis;
ALTER TABLE offers DROP COLUMN raw_row;
ALTER TABLE offers DROP COLUMN available_qty;
ALTER TABLE offers DROP COLUMN moq;
ALTER TABLE offers DROP COLUMN valid_until;

-- products: size is not stored any more.
ALTER TABLE products DROP COLUMN size;

-- offer_batches: the decided per-upload fields.
ALTER TABLE offer_batches ADD COLUMN brand     TEXT;   -- single brand, or '(multi-brand)' (§2.5)
ALTER TABLE offer_batches ADD COLUMN moq       TEXT;   -- FREE TEXT, display-only, both kinds (O3 §8)
ALTER TABLE offer_batches ADD COLUMN lead_time TEXT;   -- free text; UI hint "business info only"
ALTER TABLE offer_batches ADD COLUMN notes     TEXT;   -- free text; UI hint "business info only"
ALTER TABLE offer_batches ADD COLUMN ended_at  TEXT;   -- stock offers only; set by "End offer" (§3.3/§8)

-- settings: O2/O7 decided -> drop the placeholders; O4 -> default FX basis is today's rate.
DELETE FROM settings WHERE key IN ('freshness_rule', 'freshness_days');
UPDATE settings SET value = 'today' WHERE key = 'fx_display_basis';
