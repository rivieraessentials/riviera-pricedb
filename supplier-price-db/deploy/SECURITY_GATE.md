# Security gate — instructions for an INDEPENDENT reviewer

**Who runs this:** a reviewer (human or an agent) who **did not write this code**. Do not let the author
drive it. **When:** after the server setup is complete and the app is reachable at
`https://prices.rivieraessentials.com`, **before** real use (cutover). **Goal:** confirm the measures
below are actually in force on the *running server*, not just present in the source.

**How to report:** for each item, give a verdict — **PASS**, **FAIL**, or **N/A** — with the evidence
(the command run + its output, or the request/response). The gate **passes only if every applicable
item is PASS**. Any FAIL blocks cutover until fixed and re-checked.

---

## 1. Authentication is really on (highest priority)
- [ ] **Access gate present.** In a fresh private browser, `https://prices.rivieraessentials.com` shows
      the **Cloudflare Access login** before any app HTML. PASS = you cannot see the app without logging in.
- [ ] **Only allow-listed emails get in.** Logging in with an email **not** in the allow-list is refused.
- [ ] **App enforces identity in code too (defence in depth).** A direct request to the origin that bypasses
      Cloudflare (e.g. on the box: `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8001/grid`)
      is **rejected (401/403)** when it carries no valid `Cf-Access-Jwt-Assertion` — the app verifies the
      JWT (aud == configured aud_tag, iss == `https://<team>.cloudflareaccess.com`, not expired, email in
      allow-list), it does not trust Cloudflare alone.
- [ ] **Production refuses to start insecure.** Confirm `environment = "production"` and that the service
      is running; the app aborts startup if Access is off / team / aud / allow-list / public_origin are
      missing (by design). Evidence: `systemctl status` active, and `/healthz` shows the right environment.
- [ ] **Bound to localhost only.** `ss -ltnp | grep 8001` shows it listening on `127.0.0.1:8001`, not `0.0.0.0`.

## 2. Web hardening
- [ ] **CSP.** Response headers include a Content-Security-Policy with `script-src 'self'` (no `unsafe-inline`
      for scripts); the page uses no inline event handlers.
- [ ] **SheetJS is self-hosted + pinned with SRI.** `index.html` loads `vendor/xlsx.full.min.js` (same origin)
      with an `integrity=` hash — not a CDN.
- [ ] **CSRF / Origin check on writes.** A state-changing request (`POST /offers`, `DELETE /batches/...`)
      with a **wrong/missing `Origin`** is rejected; with the correct `public_origin` it is accepted. (Test
      against a throwaway batch, not real data.)
- [ ] **No secrets in the page or client.** View source + network: no API keys, tokens, or credentials.

## 3. Data handling
- [ ] **Parameterised SQL.** Grep the backend: every query uses `?` placeholders; no f-strings/`%`/`+`
      building SQL from request values.
- [ ] **Uploads validated and never stored.** Only `.xlsx/.xls/.csv`, size-capped; the server stores **no**
      original upload (parsing is in the browser); the server never executes an upload. Confirm there is no
      on-disk copy of uploaded files after an import.
- [ ] **Data minimisation.** No personal data stored (no contact details on saved customer requests — only
      EAN / target price / currency / optional company name, per design).

## 4. Process & file permissions
- [ ] **Not root.** `systemctl show -p User pricedb.service` (system) is a dedicated non-root user, or the
      user-service runs as the normal login user — never root.
- [ ] **Config & secrets locked down.** `config.toml` is not world-readable (≤ 640); `backup.env` is `600`;
      the data dir and DB are owned by the service user and not world-readable.
- [ ] **Repo has no real secrets.** `config.toml`, `backup.env`, any `*.db`/`*.sqlite`, and `reports/` are
      **not** committed (check `.gitignore` and `git log`).

## 5. Backups are real and restorable
- [ ] **Off-site backup runs.** `systemctl list-timers | grep pricedb` shows the backup timer scheduled;
      the last run succeeded; a snapshot exists in BorgBase (`restic snapshots --tag pricedb`).
- [ ] **Restore actually works (not assumed).** Run `pricedb-restore-drill.sh` (or restore `latest` by hand
      to a scratch dir, open read-only, count rows). PASS = it restores and parses with a sane row count.
- [ ] **Append-only on the box.** The server's restic key cannot delete/prune (an attempt to
      `restic forget` from the server is refused); the full key lives off the server.
- [ ] **Monitoring.** A missed/failed nightly backup raises an alert to the same inbox as the CRM backups.

---

### What counts as a pass
All items in sections 1–5 that apply are **PASS**, with evidence attached. Section 1 has **no exceptions**
— any FAIL there blocks cutover outright. Hand the full report (verdicts + evidence) back to the owner;
the author fixes any FAIL, and the reviewer re-checks just those items before go-live.
