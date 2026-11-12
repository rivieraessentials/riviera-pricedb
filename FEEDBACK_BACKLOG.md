# Price DB — post-go-live feedback backlog

Owner feedback after the first days of real use (go-live 2026-11-08, `db.rivieraessentials.com`).
Status: ☐ open · ◐ needs an owner decision · ✅ done.

## 🐞 Bugs / blocking real use
- ✅ **Kenra (Bolmar) won't upload** — 11-digit UPC-A codes (Excel dropped the leading zero) weren't
  recognised as EANs, so the EAN column wasn't detected and the file failed. Fixed 2026-11-12: `eanCanon`
  (frontend) + `importer.canon_ean` (backend) left-pad 11/12-digit UPC-A to a valid EAN-13. Verified on the
  real file (69/75 codes recovered, all check-digit valid); owner to confirm the full import in the app.
- ☐ **Table doesn't fit / right-hand columns cut off; doesn't adapt on window resize** — Offers grid needs
  to fit + scroll horizontally and reflow on min/max.
- ☐ **Can't merge "L'Oréal Professionnel" vs "L'ORÉAL PROFESSIONNEL"** — case-only duplicate brands; need a
  merge and case-insensitive brand matching going forward.

## 🎨 UI improvements
- ✅ **No-brand import warning** (2026-11-12) — when Multi-brand is chosen but the file has no brand
  column, the wizard now warns that every new EAN will go to Unknown and nudges you to pick the one
  brand (the Ieva/Latina case). `confirmNoBrand()` in the import flow.
- ☐ **Brand dropdown**: sort A–Z, with **new + multi-brand pinned on top**; **suppliers** the same.
- ☐ **Best sellers**: group by brand like Offers, real columns, normal case (not CAPS), same text size.
- ☐ **Column widths adjustable**, and **remember column order + widths** between sessions.
- ☐ **Respect hidden rows** in the uploaded Excel file (rows hidden in the file stay out).

## ⚖️ Needs an owner decision (conflict with the append-only "a correction is a new offer, never an edit" rule)
- ◐ **Edit a price manually in Offers** — true inline edit, or a "manual offer" that supersedes and keeps
  history?
- ◐ **Delete a brand's rows (multi-select)** + **highlight discontinued rows** — hard delete, or a
  "discontinued/hidden" flag that keeps the data (reversible, safer)?

## 🧱 Bigger redesign
- ☐ **Customer requests**: column-per-supplier grid (like Offers), with **suppliable references on top**.

## Parked (separate from this feedback)
- ☐ **Runbook docs fix** — the deploy/subdomain runbooks say "edit `/etc/cloudflared/config.yml`", but the
  `riviera-crm` tunnel is **dashboard-managed** (Published Application routes). Correct the docs to say "add
  the Public Hostname route in the dashboard (`db → http://localhost:8001`)".
- ☐ **Duplicate `crm` tunnel route** — two identical `crm → :8000` Published Application routes in the
  dashboard; harmless, tidy later (carefully, no CRM risk).
- ☐ **Off-site backup** — interim on-box snapshot only; BorgBase deferred, **two-week deadline from go-live**
  (by ~2026-11-22) or the CRM-restic fallback (needs CRM approval).
