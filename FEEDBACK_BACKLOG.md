# Price DB — post-go-live feedback backlog

Owner feedback after the first days of real use (go-live 2026-11-08, `db.rivieraessentials.com`).
Status: ☐ open · ◐ needs an owner decision · ✅ done.

## 🐞 Bugs / blocking real use
- ✅ **Kenra (Bolmar) won't upload** — 11-digit UPC-A codes (Excel dropped the leading zero) weren't
  recognised as EANs, so the EAN column wasn't detected and the file failed. Fixed 2026-11-12: `eanCanon`
  (frontend) + `importer.canon_ean` (backend) left-pad 11/12-digit UPC-A to a valid EAN-13. Verified on the
  real file (69/75 codes recovered, all check-digit valid); owner to confirm the full import in the app.
- ✅ **Table doesn't fit / right-hand columns cut off; doesn't adapt on window resize** (2026-11-12) — the
  Offers grid now fills the remaining viewport height (flex) so its horizontal scrollbar is always pinned
  at the bottom and reachable; supplier columns scroll inside the grid with EAN + Product frozen, and it
  reflows on any window size. `.content.is-grid` in styles.css + a class toggle in render().
- ✅ **Can't merge "L'Oréal Professionnel" vs "L'ORÉAL PROFESSIONNEL"** (2026-11-12) — the Merge-brands
  dialog refused because its guard compared names with `toLowerCase()`, which folds accents, so the two
  variants looked identical. Fixed to compare the exact names; the two accent-case variants now merge
  (backend already handled it — covered by a new test). `doMergeBrands()` in app.js.
- ☐ **Prevent future case/accent brand duplicates** — SQLite NOCASE folds only ASCII, so `get_or_create_brand`
  treats "L'Oréal"/"L'ORÉAL" as different and creates both. Make brand matching Unicode-case-insensitive
  (casefold) so duplicates don't form in the first place. Needs care with existing data; separate change.

## 🎨 UI improvements
- ✅ **No-brand import warning** (2026-11-12) — when Multi-brand is chosen but the file has no brand
  column, the wizard now warns that every new EAN will go to Unknown and nudges you to pick the one
  brand (the Ieva/Latina case). `confirmNoBrand()` in the import flow.
- ✅ **Brand + supplier dropdowns** (2026-11-12) — import wizard: brands and suppliers are alphabetical,
  and the special options (🔀 Multi-brand, ➕ New brand, ➕ New supplier) are pinned at the **top** of each
  dropdown instead of after the whole list. (brandList already sorted; suppliers now sorted too.)
- ✅ **Best sellers restyle** (2026-11-12) — brand navigation now matches Offers (All brands + an A–Z
  letter bar that expands into that letter's brands), and the table uses the clean Offers grid style
  (normal-case headers, consistent size, tabular prices with the cheapest in green) instead of the old
  small-caps `.tbl`. Previewed in the browser pane. (bsLetter state + bsletter handler.)
- ✅ **Column widths adjustable + remember column order/widths** (2026-11-12) — the per-brand column order
  (the ◀▶ moves) now persists in localStorage, so the grid keeps the last order between sessions; and the
  EAN/Product columns have a drag grip on their header's right edge to resize them, width saved too (both
  display prefs, like the zoom level). Validated the drag in a browser-pane repro. Supplier price columns
  are sized by their short numbers (not resized). *(A saved order resets gracefully after a re-import,
  since columns are keyed by batch id.)*
- ✅ **Respect hidden rows** in the uploaded Excel file (2026-11-12) — rows hidden in the Excel file are
  now dropped on import. The parsers read with `cellStyles:true` (so SheetJS exposes `!rows[i].hidden`)
  and filter them out (`sheetRows()` helper). Verified against a generated hidden-row file.
- ✅ **Best-sellers to the top of a brand tab** (2026-11-12) — in the Offers grid, a brand's ★ best-sellers
  now float to the top of the list (they were appearing mid-list), with a subtle divider line below the
  block; within the best-seller and the non-best-seller groups the current sort order is kept (stable sort
  applied after the user's sort). Brand tabs only — the Unknown tab is unchanged. Frontend only; no
  prototype to follow (`prototype.html` was superseded, design note 14). `best-row` + new `bs-sep` in app.js/styles.css.

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
