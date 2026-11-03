-- Rollback 0004_frontend_support.
DROP INDEX IF EXISTS ix_request_lines_request;
DROP TABLE IF EXISTS request_lines;
DROP TABLE IF EXISTS requests;
ALTER TABLE suppliers DROP COLUMN country;
