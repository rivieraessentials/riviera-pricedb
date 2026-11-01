-- Rollback of 0001_initial. Drop in reverse dependency order.
DROP TABLE IF EXISTS settings;
DROP TABLE IF EXISTS current_best;
DROP TABLE IF EXISTS fx_rates;
DROP TABLE IF EXISTS offers;
DROP TABLE IF EXISTS offer_batches;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS suppliers;
DROP TABLE IF EXISTS brands;
