# Brief: code-level security pre-review (paste into a FRESH Claude Code session)

You are an independent reviewer. **You did not write this code** — review it fresh, assume nothing, and
**do not change any code**. Report each item as **PASS / FAIL / N-A** with **`file:line` evidence** (quote
the line). This is the *code-level* half of the full gate; the *server-level* half runs separately on the
live box and is out of scope here (listed at the bottom so you don't chase it).

## Repo
- Path: `C:\Users\tedjm\Documents\riviera-pricedb-git\supplier-price-db`
- Backend: `backend/app/` (FastAPI). Frontend: `frontend/` (vanilla JS + `index.html` + `styles.css`).
- Read first: `../SUPPLIER_PRICE_DB_RULES.md` (behaviour contract) and `deploy/SECURITY_GATE.md` (the full
  gate — you are doing its code-verifiable items). Do not run the server; this is a static code review.

## Code-level checks to verify (in scope)
For each, give verdict + `file:line`.

1. **Parameterised SQL — no injection.** Every DB call uses `?` placeholders; no SQL built with f-strings,
   `%`, `.format`, or `+` from request/user values. Look across `backend/app/repo.py`, `main.py`,
   `importer.py`, `db.py`. FAIL if any query interpolates a value into the SQL string.
2. **Access JWT verification (defence in depth).** The app verifies the Cloudflare Access JWT itself, not
   just trusting Cloudflare: checks `aud` == configured `aud_tag`, `iss` == `https://<team>.cloudflareaccess.com`,
   expiry, and email ∈ allow-list; reads the token from `Cf-Access-Jwt-Assertion`. Find the auth dependency
   (`require_identity`) and the JWT/JWKS code. Confirm production can't start with Access off
   (`config.py` production guard). FAIL if identity is trusted without signature/claim verification.
3. **CSP + no inline script.** `frontend/index.html` (or the response headers in `main.py`) sets a
   Content-Security-Policy with `script-src 'self'` and no `unsafe-inline` for scripts; the page uses
   **event delegation (`data-act`)**, no inline `onclick=`/`onload=` handlers, no `javascript:` URLs.
4. **SheetJS self-hosted + SRI.** `index.html` loads `vendor/xlsx.full.min.js` from same origin with an
   `integrity=` (SRI) hash — not a CDN. Confirm the hash is present and the file is vendored.
5. **XSS escaping.** All data rendered into the DOM goes through the `esc()` helper (or textContent), never
   raw `innerHTML` of un-escaped user/supplier data. Spot-check the grid, offer-detail, review-queue and
   toast render paths in `frontend/app.js`. FAIL on any un-escaped interpolation of stored data into HTML.
6. **Origin/CSRF on writes.** State-changing endpoints (POST/PATCH/DELETE) check the request `Origin`
   against `public_origin`/`allowed_origins`. Find the check in `main.py` and confirm it covers every
   write route (offers, products, batches, fx, brand ops). FAIL if any write route skips it.
7. **Input validation / limits.** Request bodies are validated (Pydantic models in `models.py`); row counts
   and prices are capped (`validate.py` — `MAX_OFFER_ROWS`, `MAX_PRICE`); bad rows are rejected or routed to
   review, not silently trusted.
8. **Uploads never stored / never executed.** The server stores **no** original upload and never runs one;
   parsing is in the browser (SheetJS) and only normalised rows reach the API. Confirm there is no code path
   writing an uploaded file to disk and no `data/uploads` use. (`.gitignore` also blocks `uploads/`.)
9. **Data minimisation — no personal data.** Saved customer requests store only EAN / target price /
   currency / optional company name — **no contact details**; uploaded request files aren't kept. Check the
   request models/repo.
10. **No secrets in the client or the repo.** No API keys/tokens/passwords in `frontend/` or served HTML
    (FX needs no key). In git: `config.toml`, `backup.env`, `*.db`, `*.sqlite`, `reports/` are gitignored and
    **not** committed (check `.gitignore` and `git log --stat`). FAIL if any real secret/DB is tracked.

## Out of scope here — leave for the live server gate (`SECURITY_GATE.md` §1,4,5)
Do **not** mark these; they can't be verified from source alone: Access actually enforcing in front of the
live URL; the app bound to `127.0.0.1` only; running as a non-root/service user; file permissions on
`config.toml`/`backup.env`/data; restic backup running + a real restore; `systemctl` state.

## Output format
A short report: the 10 items, each `PASS`/`FAIL`/`N-A` + `file:line` evidence (quote the line), then a
one-line overall verdict. **Make no code edits.** Hand the report back to the owner.
