# Supplier Price Database — Rules & Decisions

The single reference for how this system must behave. These are the owner's instructions and the
decisions taken from them. When building or changing anything, follow this file; where code and this
file disagree, this file wins and the code is the bug. Keep it updated as new rules are agreed.

Related docs: [`SUPPLIER_PRICE_DB_DESIGN.md`](SUPPLIER_PRICE_DB_DESIGN.md) (schema/§ design),
[`SUPPLIER_PRICE_DB_BUILD_PLAN.md`](SUPPLIER_PRICE_DB_BUILD_PLAN.md) (MVP/go-live + security gate),
[`SUBDOMAIN_SETUP_RUNBOOK.md`](SUBDOMAIN_SETUP_RUNBOOK.md) (server setup/hardening).

---

## 1. Scope & spec

- **The prototype is the specification.** Port its screens and behaviour as-is onto the server
  backend. Do **not** add features, settings or screens the prototype doesn't have.
- Deviate only where a decision below requires it; where the prototype and a decision conflict,
  follow the decision and note it in one line in the docs (§7.6 of the build plan).
- Don't ask the owner unless a choice could **lose or hide data**.
- Dropped from scope: **CSV export of request results**.
- "Manual exchange rate wins over the daily fetch" is kept only as a **check that existing
  behaviour works**, not as new functionality.

## 2. Core data decisions (deviations from the prototype)

- **Server storage**, not the browser. SQLite on the server (WAL, `foreign_keys=ON`).
- **No CRM button or CRM link** anywhere in this app. It is standalone.
- **Prices rounded to 2 decimals.** A per-box price is divided by pieces-per-box **then** rounded
  (divide first, round once).
- **Brand chosen in the upload form** — single brand, or multi-brand matched by EAN.
- **No personal data stored. Uploaded files are not kept** (data minimisation, design §2.8): only
  EAN / description / price are read from each row; nothing else is stored, there is no `raw_row`.
- **SheetJS self-hosted with SRI** (no external runtime resources). Currently 0.20.3 under
  `frontend/vendor/` with an `integrity=` hash.

## 3. Prices, money & currency

- `offers.price_original` is an INTEGER in minor units ×1,000,000, always a **2dp per-piece** value
  in the offer's own currency. EUR is computed on read.
- FX rates are stored as **exact TEXT**. Convention: `1 EUR = rate * currency`, so `eur = amount / rate`.
- **Currencies: EUR and USD** offered in the upload form (EUR default). Other currencies may appear
  in stored data and are converted via FX.
- **Original currency is shown** alongside EUR in the Offers grid.
- **Currency filter** in the Offers grid (next to All / T1 / T2) so offers can be compared per currency.
- **FX fallback (retroactive rates):** a batch uses its own rate if set; EUR is rate 1; otherwise the
  daily `fx_rates` rate on/before the batch date (latest available otherwise). So a rate added later
  applies to earlier offers.

## 4. Replacement / live-offer rule (design §3.3)

- A supplier's **newer price replaces their older price** for the same product (same supplier, same kind).
- The newest received-date batch's offers are the **live** ones; older ones are superseded.
- **Stock offers can be ended**; ended stock offers are excluded from the live comparison.
- A product's live offers group by `(supplier, kind)`; the grid shows one live column per supplier.

## 5. Brands & classification

- **Multi-brand files:** match each row to a brand **by EAN**. Only the Wella rows go under Wella, etc.
  (Early bug: a multi-brand file imported as single-brand forced every EAN under one brand — fixed.)
- **Unknown-brands tab:** EANs with no known brand go here (not forced under some brand). When a new
  branded offer later contains a matching EAN, it moves to that brand's tab. Match by EAN,
  **excluding EAN duplicates**.
- Some multi-brand files **have a brand column, some don't.** Detect a brand column when present;
  otherwise classify by EAN against known products. Brand resolution never comes silently from the
  file — see brand approval below.
- **New-brand approval:** when an import would create brands that don't exist yet, show a confirm
  dialog listing the candidate brands; **only the ticked ones are created** and become real brand
  tabs. (Bug fixed: ticked brands must actually be created.)
- Ability to **add a brand manually** (type a name that isn't in the list yet), so a request can be
  logged today for a brand not yet in the system and matched to a full offer uploaded later.
- **Merge duplicate brands:** "⇄ Merge brands" in Offers combines two brands (e.g. "J.P. Gaultier"
  into "JEAN PAUL GAULTIER") — every product moves to the kept brand and the source is removed. EAN
  is unique per product, so duplicates are excluded by construction. `POST /brands/merge`.
- **Compact brand overview:** an A–Z letter index; click a letter to expand its brands. The brand
  picker is a **searchable combobox** (the list is long) that also accepts a typed new name.

## 6. Customer requests (design §9)

- A request stores **inputs only** (EAN, optional target price, currency, optional customer + brand,
  min margin). The result is **recomputed live** on every view — never a frozen snapshot — so a saved
  request always reflects today's offers and matches **current and future** uploads.
- **Arranged per customer and per brand, saved for later.** History shows **which customer** and
  **what date**.
- The brand picker on a request offers **"Multi-brand"** (first in the list) for a request that spans
  brands; matching stays by EAN regardless of the label. The typed customer / brand / date / margin /
  currency must survive an **Excel upload** (the upload must not wipe fields already filled in).
- **Request history is an accordion** grouped by customer: click a customer to expand/collapse its
  requests, with Expand all / Collapse all — navigable without scrolling when there are many.
- A request file may be headed **EAN / DESC / QTY / TARGET**; the exact target price (e.g. 15,65) is
  read even when it sits under the wrong header or uses a comma decimal — never rounded to a whole
  number. "TARGET"/"cible"/"objectif" are recognised as the target column, "DESC" as the description.
- **No personal data** — customer is an optional company name only, no contact details.
- **Target price is optional:**
  - With a target: margin view — "ok" when the cheapest live supplier meets the margin
    (`buy_eur <= target_eur * (1 - margin)`), else "no".
  - Without a target (**EAN-only request**): supply overview — list **every live supplier that can
    supply the EAN, cheapest first** (best in green). Status "ok" if any supplier can supply it.
- A request can be uploaded as a file with **EAN + description and no price** — read the EAN column,
  leave the target empty; never read a stray number from the description as a price.
- **Product description on a request line:** show the database description when the EAN is a known
  product; otherwise show the **description submitted with the request** (so an EAN-only request still
  names what was asked for). The submitted description is stored with the line (migration 0006).

## 7. Import / column reading

- The browser parses the file and POSTs only normalized rows; the server validates and stores.
- **EAN is the key.** New EANs are added; **duplicates are never created.** EAN check-digit is
  validated (EAN-8/-13, UPC-A padded); bad rows go to the **review queue**, never silently dropped.
- Read the **lowest** price among a row's price columns.
- **Size vs description (owner rule):** the **size** of a product is a numeric value followed by a
  unit (`ml`, `l`, `g`, `gr`, `kg`, `oz`, …), e.g. `100 ml`, `15 Gr`, `200ml`, `20 ml + Stick 2 g`.
  The **product description is mainly text.** When reading an uploaded file, pick the description from
  the **text column, never the size column** — a size value must not be stored as the description.
  Size itself is **not stored** (dropped from the data model).
- **Self-healing descriptions:** on re-import, if a product's stored description is empty or is a size
  value and the new row carries real text, update it to the text. A good existing name is never
  overwritten.
- **Brand is resolved from the upload form, never silently from the file.** An EAN already under a
  different brand goes to review (no overwrite, no offer).
- **Headerless files** are supported: a row that contains a real EAN is treated as data, never a
  header (so a product name containing a keyword substring — e.g. "net" in "nettoyant" — is not
  mistaken for a header row). A bare number (e.g. "6.01") is a price, not a size.
- **Multilingual headers:** price columns are recognised in several languages (`price`, `prix`,
  `precio`/`precios`, `preço`/`preco`/`preços`, `prezzo`, `preis`, `tarif`, `pvp`, `srp`, …) and the
  description likewise (`description`, `descripcion`, `descrizione`, `désignation`, …). A price column
  found by its header is read even when **every value is a whole number** (e.g. 35, 40) — the
  decimal-only fallback applies only when no price header was recognised. (Bug fixed: a Spanish D&G
  file headed `EAN CODE / DESCRIPCION / PRECIO` with integer prices scanned 0 sheets.)

## 7a. Editing a product

- The product **description is editable in place**: click the Product cell in the Offers grid, type,
  press Enter to save (Esc cancels). Clicking the **EAN cell** opens the full editor (brand +
  description). Re-import also self-heals a size/empty description (§7).

## 7b. Exporting an offer (owner's template)

- The offer-detail **⤓ Export** returns the owner's own **offer template** with the offer filled in,
  looking **exactly** like the owner's file — same layout, colours, borders, fonts and formulas.
- **Built on the server**, not in the browser: `GET /batches/{id}/export?margin=&brand=` loads the
  template with **openpyxl** and fills it. This is deliberate — the browser SheetJS (community) writer
  **drops all fonts, fills and borders** (it writes number formats only), so a browser-built file lost
  the colours/layout. openpyxl preserves every style and formula of the untouched cells.
- The template is **`backend/app/assets/offer_template.xlsx`** — converted **once** from the owner's
  supplied `.xls` via Excel (lossless). Data rows start at **row 10** (header row 9); columns
  **A = EAN, B = Description, C = Price, D = Quantity, E = Total**. The export writes **A, B and C
  only**; **Quantity (D)** and the header block (Offer / MOQ / Terms / ExW / Lead time / Currency) are
  left for the owner. **Total (E) keeps its `= D*C` formula** per row.
- **Margin is asked each time** (default 7%). Price = **offer cost ÷ (1 − margin)**, in **EUR**,
  rounded to 2 decimals — the same margin basis as customer requests. (Falls back to the offer's
  original-currency per-piece price if the batch has no EUR rate, e.g. a USD offer with no USD FX.)
- **Brand-scoped:** the export includes **only the lines of the brand currently being viewed**
  (`brand=` = the active Offers tab). So a **multi-brand supplier offer exports just that one brand**,
  never the whole multi-brand list. `brand=__unknown__` exports the no-brand-yet products.
- The **offer-detail view is brand-scoped the same way**: opening a multi-brand supplier offer from a
  brand's tab shows only that brand's lines (and the Lines count reflects it). `/batches/{id}/offers`
  takes the same `brand=` filter, so the detail view and the export always agree.

### 7b.1 Extract the Offers grid to Excel (comparison dump)

- Separate from the template export above, the Offers toolbar has **⤓ Extract to Excel** (`extractGrid`).
  It downloads the **current grid exactly as shown** — active brand, search / territory / currency
  filters, column order, sort and compare mode — as a plain `.xlsx`: columns **EAN, Product**, then
  **Δ% (B vs A)** in compare mode or **Best price** otherwise, then **one column per supplier offer**.
- This one is **built in the browser** with the vendored SheetJS, *not* on the server, because it is a
  plain data dump with **no template styling to preserve** (unlike §7b). Prices are written as real
  **numbers** in the **display currency** (so Excel can sort/filter them), 2-dp number-formatted.
- Read-only: it only reads the grid already loaded on screen — no API write, no DB change.

## 8. Uploading (UX)

- Files can be added with the **Add file** button **and** by **drag-and-drop** — dropping an `.xlsx`
  anywhere imports it, and the Add file button is itself a drop target.

## 9. Security measures (built into Step B from the start — build plan §7.5, gate = step E)

1. **Cloudflare Access JWT verification ON in production** (allow-list from config, both owner emails).
   Off only in local development. **Production start must fail** if it's off or the AUD tag is missing.
2. **XSS:** all content from supplier files is rendered as **text, never via innerHTML**. There is a
   test with a description containing `<script>` and `<img onerror=…>`.
3. **Server-side validation on every endpoint** (EAN digits/length, price limits, required fields,
   request-size / row-count limits). **Parameterised SQL only.**
4. **CSRF:** state-changing endpoints accept **JSON only** and check the **Origin** header against the
   app's own origin.
5. **No external resources at runtime** except what's required; SheetJS self-hosted with SRI; a strict
   **Content-Security-Policy** allowing only our own origin.
6. **Server hardening:** run as the **normal user (not root)**; `/data/pricedb` readable only by that
   user; **systemd with restart**; logs **without row contents**; **restic backup** of the DB plus a
   **restore test**.
7. **Export bundle:** the UI notes that the download contains the **full database** and belongs on the
   **encrypted drive**.
8. **Independent security review** of the finished code by a **separate agent that didn't write it**,
   before cutover. Report findings to the owner; fix before go-live.

Report what was done per point.

## 10. Operations & working discipline (how Claude works on this)

- **Never test against the owner's live DB** (`backend/data/pricedb.db`). Verify on a **throwaway DB**
  (e.g. port 8002 with `PRICEDB_DB_PATH` pointed at the scratchpad). Back up the live DB before any
  migration.
- **Migrations are numbered** `NNNN_name.sql` + `.down.sql`, tracked in `schema_migrations`, run on
  startup. They must be **idempotent and reversible** (rollback + re-apply tested). Current head: 0006.
- `uvicorn --reload` is **unreliable here** (orphaned workers hold the port with stale code). Use plain
  `uvicorn` and **restart explicitly** after backend changes; confirm via `/healthz` (`migrations`)
  and `/static/app.js` content after a restart.
- After a change: run the test suite, commit, push, restart the live server on **:8001**, and confirm
  the owner's data is intact (brand/request/offer counts).
- **Git attribution** (current): commits end with `Co-Authored-By: Claude Opus 4.8
  <noreply@anthropic.com>`; PR descriptions end with the Claude Code generated-with line.
- **`reports/` stays out of git** until after the pre-push history scrub, then only sanitised copies.

## 11. Open / pending (not started without the owner asking)

- Independent security review (step E) before cutover.
- Step C real-file acceptance test (owner provides ~10 real supplier files).
