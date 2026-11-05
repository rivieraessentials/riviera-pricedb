-- 0006_request_line_description — keep the product description that came in with a request line
-- (from the uploaded file or a pasted line) so it can be shown when the EAN is not yet a product
-- in the database. It is product text, not personal data (§9); the DB description still wins when
-- the product exists. ADD COLUMN is a safe in-place change.
ALTER TABLE request_lines ADD COLUMN description TEXT;
