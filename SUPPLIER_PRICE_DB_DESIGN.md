# Supplier Price Database — Design (design only, nothing built yet)

**Date:** 2026-10-18 · **end-user decisions applied 2026-11-02**
**Author:** Ted
**Status:** Design for review. No code, no deployment. The in-CRM attempt is discarded; this is a **standalone comparison tool**. **Intake is manual file drop — Excel (.xlsx/.xls) and CSV** (PDF dropped — see §11). **Deployment: hosted on your domain behind the CRM's existing login, exactly like the CRM — not a laptop-only program, so nothing critical lives only on the laptop (see §6).** It **shares the server, the Cloudflare Access login and the backups with the CRM, but exchanges no data with it** (O1 closed — §11/§12).

---

## 0. What this is, in one paragraph

A standalone price-intelligence tool that ingests supplier offers (**Excel files, dropped in manually**), stores every offered price as **append-only history** in its original currency, and lets you answer one question fast: *who is cheapest?* — per product (EAN), per brand, and across your bestsellers. Everything displays in **EUR** by default with a currency toggle. It is a **comparison tool only: it does not connect to the CRM.** Once you have found the best price here, you upload that supplier's Excel into the CRM via its **Purchases tab**, the way you do today — outside this system.

**Core principles that shaped every decision below:**

1. **EAN is the one source of truth for identity.** One EAN = one product, no duplicates. Descriptions are cosmetic and may collide.
2. **Offers are immutable history.** You never edit a price; a new offer is a new row (a new "column" in the Excel-like view). This is what makes "compare past and future offers" work.
3. **Store the comparison figure, not the paperwork.** Per offer line we keep only EAN, product description and the **per-piece price**. We do not keep the raw spreadsheet rows, the original file, pack sizes, or anything that isn't needed to compare prices (§1, §2.8).
4. **No CRM link.** This tool never reads from or writes to the CRM. The handoff is manual and outside the system: find the best price here, then upload that supplier's Excel into the CRM's Purchases tab as usual (O1 closed — §11).
5. **Data minimisation — the price DB holds NO personal data.** Business data only: EAN, prices, product description and brand, and per upload the supplier's **COMPANY** name, T1/T2, currency, offer date, offer type and MOQ. No contact persons, no personal emails/phones, no names of individuals. Supplier offer files often carry a contact block or a sales-rep column; none of that enters the system. The concrete rules are in **§2.8**; original files are **not kept** (O7 decided — §11/§12), so no uploaded file ever sits on disk holding a contact block.

---

## 1. Data model

### 1.1 Tables

```
brands
  brand_id        PK
  name            TEXT      -- canonical name, unique (case-insensitive)
  aliases_json    TEXT      -- JSON array of supplier spellings ("L'Oréal"/"Loreal"/"LOREAL PARIS").
                            -- SQLite has NO array type; stored as JSON text and parsed in memory (brands are few).

suppliers
  supplier_id     PK
  name            TEXT      -- the supplier COMPANY name only (never a contact person — §2.8)
  notes           TEXT      -- business notes only (UI hint: "business info only — no personal details")
                            -- Territory (T1/T2) is NOT here: it is set per upload, on offer_batches (prototype behaviour).

products                    -- one row per EAN, forever
  product_id      PK
  ean             TEXT  UNIQUE NOT NULL   -- the identity. no duplicates, enforced by the DB
  description     TEXT      -- product description as first seen / best filled; informational only
  brand_id        FK -> brands  (nullable until resolved)
  status          TEXT      -- 'active' | 'needs_review' (new EAN with no brand yet)
  is_bestseller   INTEGER   -- you flag these; drives visual highlight + a dedicated ranking
  created_at, first_seen_batch_id
  -- NOTE: `size` is NOT stored (removed 2026-11-02). It is not needed for price comparison.

offer_batches               -- one batch = one "column" in the grid = one supplier upload
  batch_id        PK
  supplier_id     FK
  received_date   DATE      -- the OFFER DATE you specify on import
  brand           TEXT      -- the upload's brand as the prototype stores it: a single brand for the whole
                            -- upload, or '(multi-brand)' when brands come per row from the file (§2.5).
  currency        TEXT      -- EUR/GBP/USD/SGD/CAD/HKD (asked on the upload form)
  territory       TEXT      -- 'T1' (non-European) | 'T2' (European) — set per upload. Label/filter only, no VAT math.
  kind            TEXT      -- 'price_list' | 'stock_offer'. A structured two-option field (NOT free text).
                            -- Stored code unchanged; display names (final 2026-11-03): price_list = "Regular
                            -- offer (longer validity)", stock_offer = "Stock offer (limited in time and/or
                            -- quantity)" (app/offer_types.py). Drives the replacement rule (§3.3 / O2 / item 29).
  moq             TEXT      -- minimum order quantity, FREE TEXT typed by the owner on the upload form
                            -- (e.g. "6240 pcs", "€25,000"), for BOTH kinds. DISPLAY-ONLY: never parsed,
                            -- never used in ranking (O3 — §12, updated 2026-11-02 integer -> text).
  lead_time       TEXT      -- free text (e.g. "2-3 weeks"). UI hint: "business info only — no personal details".
  notes           TEXT      -- free text. UI hint: "business info only — no personal details".
  ended_at        DATE      -- nullable; stock offers only. Set by the "End offer" action (§8). While NULL a
                            -- stock offer counts; once set it stops counting. Price lists ignore this (O2).
  source_sha256   TEXT      -- checksum of the uploaded file; a re-upload of the same bytes warns
                            -- "already imported" (§2.1). A hash is not data, so it is kept (O7).
  source_file     TEXT      -- NEUTRAL reference only: "<supplier company> <received_date> <sha256-prefix>".
                            -- NEVER the original filename (data minimisation, §2.8). The original file itself
                            -- is NOT stored (O7 decided — extract and discard).
  fx_rate_eur     TEXT      -- the import-date rate, stored EXACTLY as published (O5 §12: TEXT; FX math in Python Decimal). see §4
  fx_source       TEXT      -- 'frankfurter' | 'ecb' | 'manual'
  fx_locked       INTEGER   -- 1 if you overrode the rate manually (manual wins, and is saved here)
  label           TEXT      -- optional column header text, e.g. "Supplier X — 2026-10-12 stock"

offers                      -- APPEND-ONLY price history. one row per (batch, product)
  offer_id        PK
  batch_id        FK
  product_id      FK
  price_original  INTEGER   -- the PER-PIECE price, ROUNDED to 2 decimals (§2.4 / O6), as INTEGER MINOR UNITS
                            -- at scale ×1,000,000 / 6 decimals (O5 §12; NEVER float). The 6dp scale is kept
                            -- for headroom, but the stored value is always a 2dp price (e.g. 2.08 -> 2080000).
                            -- If the file quotes per box, the box price is divided by pieces/box FIRST, then
                            -- rounded once (§1.2). Pieces/box is used during import only and is NOT stored.
  currency        TEXT      -- usually = batch currency, but stored per-row for safety
  created_at
  -- price_eur / unit_price_eur are NOT stored. The EUR figure is COMPUTED ON READ from price_original +
  -- currency + the relevant fx_rate (§4.2), and DISPLAYED at 2 decimals. current_best (below) is the only cache.
  -- REMOVED 2026-11-02: pieces_per_box, price_basis, raw_row, available_qty, moq(->batch), valid_until(->ended_at).

fx_rates                    -- daily cache of fetched rates (base EUR)
  rate_date       DATE
  currency        TEXT
  rate            TEXT      -- 1 EUR = rate * currency; stored EXACTLY as published (O5 §12: TEXT; Python Decimal math).
  source          TEXT
  PRIMARY KEY (rate_date, currency)

current_best                -- MAINTAINED cache: cheapest LIVE offer per product (see §3.3)
  product_id      PK
  best_offer_id   FK
  best_unit_price_eur  INTEGER  -- per-piece EUR as minor units; a DERIVED, rebuildable cache value (the ONLY stored cache — O5 §12)
  best_supplier_id FK
  updated_at
  -- "LIVE" is DECIDED (O2, §3.3): replacement is per (supplier, BRAND, kind). A newer price list replaces that
  -- supplier's previous price list FOR THE SAME BRAND only; a newer stock offer replaces that supplier's
  -- previous stock offer for the same brand only; different brands never replace each other. A stock offer
  -- stops counting once ended_at is set. No age-based expiry, no age warnings.

settings                    -- small key/value config, editable in the UI. Examples:
  key             TEXT PK   --   'fx_display_basis' ('today' default | 'import_date'), 'display_currency' (EUR),
  value           TEXT      --   'sourcing_margin' (0.07).
                            -- REMOVED 2026-11-02: 'freshness_rule'/'freshness_days' (O2 decided, no longer a
                            -- setting) and 'upload_retention'/'upload_retention_days' (O7 decided — don't keep).
```

### 1.2 Per-piece price only — pack size is used at import, not stored

The old design kept both a consumer `size` and a per-offer `pieces_per_box` + `price_basis`, to reproduce a supplier's exact as-quoted figure. **That is gone (decided 2026-11-02).** This is a comparison tool, so it stores only what comparison needs: **one per-piece price per offer line.**

**How the files quote it, and what the importer does:**

- Usually the price is **per piece** → store it directly (rounded to 2dp, §2.4).
- Sometimes it's **per box**, flagged by a column (or told to the wizard). In that case the importer **divides the box price by pieces-per-box, then rounds once** — e.g. `€24.99 / 12 = 2.0825 → 2.08`. **Rounding happens once, after dividing, never round-then-divide** (decided 2026-11-02).
- **Pieces-per-box is read from the file for that division only and is NOT stored.** Neither is `size`, nor the price basis, nor the raw row.

So every `offers.price_original` is a **per-piece price rounded to 2 decimals**. All ranking and all display use this one figure (converted to EUR on read, §4). There is no "as-quoted vs per-piece" toggle any more, because the as-quoted and pack figures are not kept.

**EAN rule enforced in the schema:** `ean` is `UNIQUE NOT NULL`. Duplicate EAN is impossible at the database level. Two descriptions pointing at two different EANs are two products; two descriptions sharing an EAN collapse to one product.

**Territory (T1/T2):** stored **per upload** on `offer_batches` (prototype behaviour — "territory is per offer, set at import, not per supplier"), as a **label/filter only — no duty or VAT math**.

---

## 2. Ingestion pipeline (Excel)

### 2.1 Shape of the flow

```
file you drop in (.xlsx / .xls / .csv)
        │
        ▼
 [1] parse the workbook/CSV in the browser (SheetJS — §2.7). All sheets are read and merged (§2.3).
        │
        ▼
 [2] per-file import wizard — you set, once per upload (matches the prototype's form, §2.2):
       • Supplier (company) *        • Brand: single (from the form) | multi-brand (by existing EAN)
       • Territory T1/T2 *           • Currency
       • Offer date                 • Offer type: price list | stock offer
       • MOQ (free text) · Lead time (free text) · Notes (free text)
        │
        ▼
 [3] column mapping → EAN, description, price(s), and pieces/box ONLY when a per-box row must be divided
       • remember the mapping per supplier as a reusable template
        │
        ▼
 [4] per row: take LOWEST price (§2.4) → if per box, divide by pieces/box → ROUND to 2dp (§2.4) → validate (§2.6)
        │
        ├─ clean row, EAN exists        ─► append new offer to that product
        ├─ clean row, EAN is new        ─► create product (status=needs_review if no brand) + offer
        └─ uncertain / failed validation ─► REVIEW LIST (never silently imported)
        │
        ▼
 [5] for each brand in this upload, supersede this supplier's previous offer of the SAME brand+type; recompute current_best (§3.3)
```

Nothing is destructive: an import only ever **appends** offers and **creates** products; a newer price list / stock offer from a supplier simply **replaces** the previous one of that kind for ranking purposes (§3.3), without deleting history. **Duplicate guard:** the SHA-256 of each uploaded file is stored on its batch (`source_sha256`); dropping a file whose checksum was already imported shows a **"this exact file was already imported on &lt;date&gt;"** warning. The file's **bytes are then discarded** — only the hash is kept (O7).

> **FLAG — offer type vs the prototype.** The prototype's upload form has a **free-text** "Notes (stock, promotion…)" field that becomes the batch `kind` ("offer"/"price list"/"special offer"). The decided model needs a **structured two-option** field (`price_list` | `stock_offer`), because the replacement rule (§3.3) and the "End offer" action (§8) depend on knowing which it is. So this one field changes from the prototype's free text to a fixed choice. **Final names (owner, 2026-11-03):** the two choices are **"Regular offer (longer validity)"** (stored code `price_list`) and **"Stock offer (limited in time and/or quantity)"** (stored code `stock_offer`). The prototype's third free-text value ("special offer"/"offer") is **not** a separate type — at import the owner picks one of the two; since prototype data is not seeded (item 14), there is nothing to convert. Everything else on the form follows the prototype (§2.2).

### 2.2 Why an import wizard, not silent auto-import

Because brand names are often missing and files mix brands, a fully automatic importer would guess wrong and quietly corrupt your rankings. The wizard front-loads the things only you know — **supplier, brand, territory, currency, offer date, offer type, MOQ** — once per file, then does the row-level work automatically and sends only genuinely ambiguous rows to a review list. This is exactly the prototype's "Import offer" wizard (supplier, brand, territory, currency, received date, notes, lead time, MOQ); the decided model keeps those fields and makes **offer type** a structured choice, while **MOQ, lead time and notes stay free text** (O3; §11 items 26–27) and **brand always comes from the form, never from a file column** (§2.5).

### 2.3 Multi-tab Excel

**Multi-tab workbooks still merge into one batch** (one upload = one column): every worksheet's rows are read and concatenated. Tabs are **not** separate columns, and brand is **never** taken from a tab. Brand comes from the upload form and resolves per §2.5 — never from the file.

> **Tab-per-brand workbooks are NOT supported** (decided 2026-11-02) — and not needed. A workbook that puts one brand per sheet, with no other brand signal, is handled like any multi-brand upload: each row's brand is taken from the existing product by EAN (§2.5), and unknown EANs go to the review list with a clear reason.

### 2.4 "Always take the lowest price", then round to 2 decimals

A file may show several price columns (retail, wholesale, other). Rule, applied per row:

1. Identify all numeric price columns (the wizard lets you tag which columns are prices; remembered per supplier).
2. Parse each with EU/US locale awareness (`12,50` → `12.50`).
3. Discard non-positive / blank / obviously-not-a-price values.
4. **Take the minimum of the remaining** as the quoted price; if the row is **per box**, divide by pieces-per-box (§1.2).
5. **Round to 2 decimals (decided 2026-11-02 — O6).** Round **all** prices to 2 decimals — per-piece prices, and cells with **no** number format too. Use **round-half-up** (ties round up / away from zero). This **matches Excel's display rounding**, which is half-away-from-zero (verified 2026-11-02: Excel's display and its `ROUND()` both round halves away from zero; Python's `decimal.ROUND_HALF_UP` is defined the same way, and since all prices are ≥ 0 the two are identical — no divergence). **Ties are accepted.** Store the rounded value as `price_original` (integer minor units at the ×1,000,000 / 6dp scale — the scale is kept, but the value is a 2dp price). FX conversion happens **on read** and is **displayed at 2dp** (§4.2).

> This **replaces** the earlier "importer must not round / keep displayed text (.w/.z)" rule and closes **O6**. Because every stored price is already a clean 2dp figure, there is no 6dp-vs-displayed gap to preserve and **no `raw_row`** is stored.

### 2.5 Brand resolution — always from the upload form, never from the file (DECIDED 2026-11-02)

Brand is **never** read from the file's contents (the prototype's "brand from a file column" path is **removed**). It comes from the upload form and resolves per row:

- **Single-brand upload** → the brand the owner picks applies to **all** rows. If a row's EAN **already exists under a DIFFERENT brand**, that row goes to the **review list** ("EAN already under brand X — confirm") and the product's brand is **never overwritten**. New EANs are created under the picked brand.
- **Multi-brand upload** (one tab, several brands mixed) → each row's brand is taken from the **existing product with that EAN**. An **unknown EAN** goes to the review list as **"new product — assign a brand"**: the price is kept but **excluded from brand comparisons** until you assign a brand. *(Multi-brand default confirmed by the owner 2026-11-03 — item 29.)*
- **Tab-per-brand workbooks are not supported** (§2.3) and not needed; multi-tab workbooks still merge into one batch.

Brand lives on `products.brand_id` (one brand per EAN — an EAN is one product); a multi-brand upload sets `offer_batches.brand = '(multi-brand)'`. Because brand never comes from file cell content, there is no file-driven brand-approval step.

### 2.6 Validation & the review list (uncertain matches)

Even with Excel (no OCR), typos happen. A row goes to review, never to silent import, when any of:

- **EAN fails its check-digit** — catches a mistyped/transposed barcode. Validate every EAN:

  ```python
  def ean_check_ok(code: str) -> bool:
      code = code.strip()
      if not code.isdigit() or len(code) not in (8, 13):
          return False
      d = [int(c) for c in code]
      body, chk = d[:-1], d[-1]
      if len(code) == 13:   # weights from left: 1,3,1,3,...
          s = sum(x * (1 if i % 2 == 0 else 3) for i, x in enumerate(body))
      else:                 # EAN-8 weights from left: 3,1,3,1,...
          s = sum(x * (3 if i % 2 == 0 else 1) for i, x in enumerate(body))
      return (10 - s % 10) % 10 == chk
  ```
  (Strip spaces/hyphens first; a 12-digit UPC-A is padded to EAN-13 with a leading zero before validating.)

  **Read EAN columns as TEXT, never as numbers.** Excel stores long numbers as floats, so a barcode read
  numerically loses leading zeros or arrives as scientific notation (`5.41e+12`). The importer forces the
  EAN column to text on read, strips spaces/hyphens, pads 12-digit UPC-A → 13, then checksum-validates.
- No parseable price in any price column.
- New EAN with no brand (goes to the "needs brand" sub-list).
- **A per-box row with no pieces-per-box to divide by** (can't compute a per-piece price).
- **Single-brand upload, EAN already under a different brand** — flagged "EAN already under brand X — confirm"; the product's brand is never overwritten (§2.5).
- **Multi-brand upload, unknown EAN** — flagged "new product — assign a brand"; the price is kept but excluded from brand comparisons until assigned (§2.5).
- A near-duplicate description maps to a *different* EAN than a very similar existing product — flagged as "possible EAN typo, confirm".

Each review item carries the **reason** and the mapped row fields (never unmapped cells — §2.8), so you're correcting "EAN checksum failed" or "no price found", not staring at a raw cell.

> **Note:** the earlier ">6 decimals routes to review" rule is **removed** — prices are now rounded to 2dp at import (§2.4), so there is no high-precision value left to route.

### 2.7 Accepted file types and which reader handles them

Accepted: **`.xlsx`, `.xls`, `.csv`** (manual drop; PDF remains dropped, §11).

**Recommended architecture — parse in the browser, send only minimised data, keep nothing.** The prototype already
parses all three formats client-side with **SheetJS** (xlsx 0.18.5), which reads `.xlsx`, `.xls` and `.csv`
alike, and its column-detection is already tuned on your real files. The production app keeps that, with
data minimisation (§2.8): the browser parses the file, reads **only rows below the detected header and only
the mapped columns**, computes the per-piece 2dp price, and POSTs **only those normalized rows** — unmapped
columns and any contact block above/below the table are dropped in the browser and never sent. **The original
file is NOT uploaded or stored (O7 decided — §12): it is parsed in the browser and discarded.** Only its
SHA-256 (computed in the browser) is sent, for duplicate detection. Parsing client-side also makes the
server-side `.xls` library question moot.

**Build requirement for SheetJS.** SheetJS Community Edition is **Apache-2.0**, and its official docs name
**`cdn.sheetjs.com` as the authoritative source** (current version **0.20.3**); the docs install from that
CDN and do **not** direct users to the public npm registry. So the production app **self-hosts a pinned
SheetJS build with Subresource Integrity (SRI)** and keeps the Apache-2.0 attribution notice — rather than
loading it from a third-party CDN (the prototype's cdnjs `0.18.5`). This also closes the security track's
"CDN libraries without SRI" item (LED-1147). Sources: [SheetJS licence](https://docs.sheetjs.com/docs/miscellany/license/) ·
[install / CDN](https://docs.sheetjs.com/docs/getting-started/installation/standalone/) ·
[version](https://docs.sheetjs.com/docs/api/).

**If parsing is ever moved server-side (Python), the readers are format-specific — verified 2026-10-31, not assumed:**

| Format | Reader | Note |
|---|---|---|
| `.xlsx`, `.xlsm` | **openpyxl** | **openpyxl does NOT read `.xls`** — it raises `InvalidFileException` on the old format. |
| `.xls` (legacy BIFF) | **xlrd** | xlrd ≥ 2.0 reads **`.xls` only** (it dropped `.xlsx` in 2.0 for security). Use `pandas.read_excel(..., engine='xlrd')`. |
| `.csv` | stdlib `csv` / `pandas.read_csv` | locale-aware decimal parsing (`12,50` → `12.50`). |

Either way the **security rule is the same (§6):** validate type and size, parse defensively, never execute an
upload. (The server no longer stores the original bytes — O7.)

### 2.8 Data minimisation (no personal data in the price DB)

The price DB stores **business data only** (core principle 5). Concretely, enforced by the importer:

1. **Only normalized fields reach the DB.** Per offer line the server receives **EAN, description and the
   per-piece 2dp price** — nothing else. There is **no `raw_row`**; unmapped columns are dropped **at parse
   time in the browser** and never sent.
2. **Read only the table.** The importer reads **only rows below the detected header row** and **only mapped
   columns**. A contact block, letter or signature **above or below** the table (names, emails, phone numbers)
   never enters the system.
3. **Neutral source reference.** `offer_batches.source_file` stores a neutral reference —
   `"<supplier company> <received_date> <sha256-prefix>"` — **never the original filename** (filenames often
   embed a person's name). The original file itself is **not kept** (O7).
4. **Free-text fields** (`notes`, `label`): the UI shows the hint **"business info only — no personal details"**
   next to each free-text input.
5. **Logging.** Logs record **counts and reasons only** (e.g. "batch 7: 142 offers, 3 to review: EAN checksum").
   **Never** log row contents or cell values.
6. **No stored originals.** Uploaded files are parsed in the browser and **discarded** (O7 decided). Only the
   SHA-256 checksum is kept (a hash is not data), for the duplicate-upload warning.

Supplier identity is the **company** name only (a `suppliers` row); there is no field anywhere for a supplier
contact person.

---

## 3. SQLite — confirmed, with real benchmark timings

### 3.1 Verdict: **yes, SQLite is the right choice** for this volume, comfortably.

Your volume is ~2,000 lines/day ≈ ~730k rows/year. I benchmarked at **5,000,000 offer rows** (≈ 7 years of history), with 250,000 products, 60 suppliers, 4,000 batches — all well beyond where you'll be for years.

### 3.2 Measured results (Python 3.14, stdlib `sqlite3`, this machine, 2026-10-18)

- Database file: **368 MB** for 5M offers (tiny; trivial to back up).
- Bulk load of 5M rows: ~114 s one-time; index build ~64 s one-time (only matters for the synthetic test, not daily use).

| Query | Real timing |
|---|---|
| Drill down one product's full history, cheapest first | **1.9 ms** |
| Compare two specific offers (the %-diff tool) | **1.7 ms** |
| Best price across **all bestsellers** | **173 ms** |
| Full comparison grid for one brand (all its offers) | **244 ms** |
| Ingest a 200-line offer (insert + commit) | **162 ms** |
| Best price per product across **all 250k products** (full scan) | **3,873 ms** |
| Best price per brand across everything (full scan) | **4,831 ms** |

**Reading of this:** everything you do interactively — drill-downs, the %-diff tool, one brand's grid, the bestseller ranking, importing a file — is **milliseconds**. Only the two *whole-database* rankings take ~4–5 s, because they scan all 5M rows every time.

### 3.3 The fix for those two: a maintained `current_best` table

You don't need to re-scan 5M rows to know today's cheapest per product. Maintain a small `current_best` row per product, updated on each import for only the touched products. Then the global "cheapest everywhere", "best per brand", and the §9 sourcing feature read from `current_best` (250k rows, indexed) and return in **milliseconds**. The append-only `offers` table remains the full history behind it. **`current_best` is the only maintained cache** (rebuildable from `offers` at any time); EUR and per-piece figures everywhere else are **computed on read**, not stored (O5 §12).

**The replacement rule that decides which offers are "live" is DECIDED (O2, 2026-11-02) — no age-based expiry, no age warnings. Replacement is per supplier + per brand + per offer type:**

- **A new price list replaces that supplier's previous price list for the SAME brand only;** a new **stock offer** replaces that supplier's previous stock offer for the **same brand** only. **Different brands never replace each other** (a Clinique list never touches the same supplier's Clarins list; a new Clarins price list never touches that supplier's Clarins *stock* offer).
- **Multi-brand uploads** replace, **for each brand present in the file**, that supplier's previous offer of the same type **for that brand**. *(Confirmed by the owner 2026-11-03 — item 29.)*
- **Stock offers count until you end them.** A stock offer stays live until the **"End offer"** action (§8) sets `ended_at`; from then it stops counting. Price lists are only replaced by a newer list of the same brand, never ended manually.

`current_best` is rebuilt from `offers` by taking, for each product, the cheapest per-piece EUR among the **live** offers — an offer is live when its batch is the most recent (by offer date) for that **(supplier, brand, offer type)** and, for stock offers, `ended_at` is NULL. No schema field is needed for age, and there is **no "either rule as a setting" placeholder** — this single rule is implemented directly.

**Conclusion:** SQLite handles many millions of rows here with ms-level interactivity, server-side on your domain host (same box as the CRM), as a single file. (WAL mode on; single user, so no write-contention concerns.)

---

## 4. Exchange rates

### 4.1 Source (verified against live 2026 docs/endpoints)

- **Primary: Frankfurter** (`frankfurter.dev`) — ECB-based, free, **no API key**, EUR-native, EU-hosted, self-hostable via Docker.
- **Fallback: ECB `eurofxref-daily.xml`** directly — the canonical source, same data.

**All six of your currencies are confirmed present** (checked against the live currency list and today's values): EUR base, plus GBP, USD, **SGD, CAD, HKD**.

Example fetch (all six, base EUR):
```
https://api.frankfurter.dev/v1/latest?base=EUR&symbols=GBP,USD,SGD,CAD,HKD
→ {"base":"EUR","date":"2026-10-16","rates":{"CAD":1.5935,"GBP":0.84385,"SGD":1.4395,...}}
```

### 4.2 Behaviour

- **Scheduled server-side fetch**, once per working day, by a **cron/systemd timer on the server** (the same mechanism as the nightly backup). It writes the day's rates into `fx_rates` (base EUR). No key, no quota.
- **Default view = today's rate; the original currency is always shown alongside (DECIDED 2026-11-02 — O4).** The comparison is shown converted at **today's rate** by default, and every price also shows its **original currency** next to the EUR figure, so you always see both. The import-date basis is still available as a non-default option (`settings.fx_display_basis`).
- **Manual override wins and is saved with the batch.** On import you can type a rate; if you do, `fx_locked = 1`, `fx_source = 'manual'`, and that exact rate is stored on the batch and used for every row in it — permanently and reproducibly. The manual override **wins over the default view** whenever set.
- **Displayed at 2 decimals.** FX conversion is computed on read in Python `Decimal` and the EUR figure is **displayed rounded to 2dp** (§2.4).
- **Weekend/holiday caveat:** neither ECB nor Frankfurter publishes rates on non-working days (Frankfurter returns the last working-day rate). The manual override is the escape hatch.
- **Two rates kept so either basis is computable (no migration needed):** the **import-date rate** on the batch (`fx_rate_eur`) AND the **daily `fx_rates` table**. The default is today's rate (O4); the import-date basis remains available.
- Attribution: if you ever publish the rates externally, cite "Source: European Central Bank". Internal single-user use needs nothing.

---

## 5. No CRM link — comparison tool only (O1 closed, 2026-11-02)

**There is no link between this tool and the CRM.** The price DB never reads from or writes to the CRM, and nothing is sent in either direction. It is a standalone comparison tool.

**The handoff is manual, the way you work today:** find the best price here, then upload that supplier's Excel into the CRM via its **Purchases tab**, outside this system.

This closes open decision **O1** as **"no link"** and withdraws everything that depended on a link: the earlier (a) read-only lookup / (b) additive CRM endpoint / (c) whole-collection re-import options, the "Create CRM offer" hand-off, the CRM lookup link, and the §9 "one click to CRM offer" step are all removed.

The two systems still **share infrastructure** — the same server, the same Cloudflare Access login, and the same off-site backups (§6) — but that is hosting, not a data link. *(Historical note: reading the CRM's import code earlier showed its only JSON import is `Object.assign(S, data)`, a whole-collection replace — one more reason a data link was never worth the risk. Kept for the record; nothing here depends on it.)*

---

## 6. Security & backup

**Deployment model — same as the CRM, so laptop failure loses nothing.** The CRM is a **FastAPI backend served from your domain behind Cloudflare Access** (the login), reached only through a **Cloudflare Tunnel** (no public port), with server-side persistence and an off-site **restic** backup on **BorgBase**. The price DB reuses that whole stack. *(Deployment facts verified against the live server 2026-10-30; stand-up steps in [SUBDOMAIN_SETUP_RUNBOOK.md](SUBDOMAIN_SETUP_RUNBOOK.md).)*

- **Hosted on your domain, not the laptop.** Same server as the CRM (`178.105.34.50`, Ubuntu 24.04, **Python 3.12.3**, **61 GB free**), as a **separate app** — its own subdomain **`prices.rivieraessentials.com`**, its own FastAPI process on its own port (e.g. `:8001`), and its **own SQLite file** under `/data/pricedb/`. The laptop is just a browser; if it dies, you sign in from any device and everything is there.
- **Login = the same Cloudflare Access.** One shared Access policy admits **two email addresses** — your main address and a **Gmail backup on a different provider** — for **both** apps, so losing one mailbox never locks you out. The same Gmail is added as a **second admin member** on the Cloudflare account.
- **Defence in depth (identity).** Today **neither app checks identity in code** — Cloudflare Access is the sole gate (tracked as **LED-1083**). The price DB optionally verifies the **`Cf-Access-Jwt-Assertion`** JWT on each `/api/*` request (signature against Access's public keys + expected `aud`) and checks the email against an **allow-list held in config with both addresses — never hardcoded**.
- **Process supervision.** The price DB ships **its own systemd unit** (`Restart=always`, enabled). The **CRM's** supervision is the roadmap's **Stage 1 Part D** (**LED-0048 / LED-0078**); the price-DB plan **references** that item, it does not perform it.
- **Uploads are validated and not stored.** Only `.xlsx/.xls/.csv`, size-capped, parsed defensively **in the browser** (SheetJS, §2.7); the server **never stores the original file** (O7) and **never executes** an upload. Every DB query is parameterized.
- **The Cloudflare 100-second cap applies here too.** Bulk import and full export must stay well under 100 s or stream/paginate.
- **Off-site backup: the same restic → BorgBase job** (append-only, EU, deletion-proven + restore-verified 2026-10-24). Adding **`/data/pricedb` (the SQLite file)** is one more path in the existing nightly command — restic dedups/encrypts client-side. *(No `/uploads/` path — originals are not kept, O7.)*
- **Offline copy you control (never lose the data).** An in-app **download button** produces a bundle — the SQLite file **plus** a plain Excel/CSV export of all offers and `current_best` — that you save to your own machine; a **weekly calendar reminder** keeps the habit. Live server copy + off-site restic copy + this local copy = **3-2-1**.
- **Proven, not assumed:** the monthly restic restore drill extends to the price-DB path.
- **No new secrets:** the FX source needs no API key.

Net: a **separate program** (separate code, separate data, no CRM data link), **hosted the same way, behind the same login, with the same off-site backup** — reboot-safe and with a local offline copy.

---

## 7. Test plan — prove the importer on synthetic fixtures (runs as build **Phase 2**)

The goal is to lock the data model and the importer against real-world mess. **This runs during Phase 2 of the build** (server-side ingestion, the first build phase), **before** the review screens and `current_best` depend on its output.

**Synthetic fixtures only (data minimisation — §2.8 and the git rule).** No real supplier or customer file is committed or used in the automated tests. The committed fixtures are **synthetic files that reproduce the shapes seen in the real files**: multi-tab (all sheets merged, §2.3), mixed-brand (brands resolved from existing EANs, §2.5), a per-box row needing division, multiple price columns, non-EUR currencies (GBP/USD/SGD), the header-name variety (`OFFER` / `PRICE (USD_` / `USD` / `Price Euros`), and the **data-minimisation case** (a contact block above the header, a sales-rep column, and a person's name in the filename — asserting none of it is stored). **Optional local acceptance:** you may run the importer against your real files on your own machine; those files are never committed.

1. **Use ~10 synthetic fixtures** spanning the range above, plus a legacy `.xls` shape and the data-minimisation case.
2. **Hand-label** a ground-truth extraction for a sample of rows per fixture (correct EAN / description / per-piece price). This is what correctness is measured against.
3. **Prototype the importer only** (not the whole app): run the §2 pipeline over all fixtures, output rows + the review queue.
4. **Measure, per file and per supplier:**
   - row-level correctness and how many rows landed in the review queue and why;
   - locale / multi-price handling (did "lowest price" pick correctly?);
   - **per-box handling** (per-box rows divided by pieces/box, then rounded once, to the right 2dp per-piece price?);
   - **rounding** (does `€24.99/12 = 2.0825 → 2.08`? do format-less cells still round to 2dp? do halves round up?);
   - brand resolution (single-brand applied from the form; EAN-under-another-brand → review; multi-brand resolved by existing EAN; unknown EAN → review);
   - EAN checksum catches;
   - **data minimisation** (contact block / sales-rep column / person-in-filename → none stored anywhere, incl. `source_file`).
5. **Decide from the measurements:** which suppliers need a stored column-mapping template.
6. **Load test** with synthetic data at projected volume (already done — §3 — re-run with real column shapes once the schema is final).
7. **Only then** build the full app, suppliers' templates first.

**Required scenario tests (Phase 2, synthetic fixtures):**
- a **Clinique** list does **not** replace the same supplier's **Clarins** list (different brands never replace);
- a **new Clarins price list replaces the old Clarins price list**, but **not** that supplier's **Clarins stock offer** (same brand, different type);
- a **multi-brand upload** matches known EANs to their existing brands and sends an **unknown EAN to review**;
- a **single-brand upload with an EAN already under another brand** goes to **review** (no overwrite);
- a **per-box price is divided then rounded once** (`24.99 / 12 → 2.08`).

Acceptance before build: review queue is small (target single-digit % of rows) and no silently-wrong prices got through.

---

## 8. UI / UX (Excel-like)

- **Left menu:** Products · Brands · Suppliers · Bestsellers · Import · Review queue · Customer requests (§9) · Settings (currency/FX).
- **Main view = a grid like Excel.** Rows = products (within a brand or a filter). Columns = offer batches ("Supplier — date — type"); a supplier appears in **multiple columns** when they sent multiple offers/stock lists over time.
- **Rankings read left-to-right, cheapest on the left.**
- **Bestsellers** — flagged by ticking a product or entering an EAN (≈10–15 per brand); highlighted, with a dedicated ranking view.
- **Currency toggle** (EUR default), with the original currency always shown alongside (§4.2). *(No per-piece/as-quoted toggle — only the per-piece price is stored.)*
- **"End offer" action** on a stock offer — marks it ended (`ended_at`) so it stops counting as live (§3.3 / O2). Price lists are replaced by uploading a newer one, not ended manually.
- **MOQ is shown** on each offer/column as **free text**, **display-only** — never parsed, never changes the ranking (O3). **Lead time** and **notes** (also free text) show here too, with the "business info only — no personal details" hint.
- **Two-offer %-diff tool:** pick two offers → side by side with a % difference column.
- Drill-down: click any product → its full price history across all suppliers/dates, cheapest first.

---

## 9. Customer-request sourcing (EAN + target price, adjustable margin)

Upload a customer file of **EAN + target price**; the tool tells you **which supplier(s) you can buy from while keeping your margin**.

- **Target price = your customer's desired buy price = YOUR selling price.** A supplier **qualifies** when your buying price sits at least the margin below it:
  `buy_price ≤ target_price × (1 − margin)`.
- **Margin is adjustable** (default **7%**, editable per run); achieved margin is `(target − buy) / target`.
- **Input:** an Excel file of EAN + target price (+ optional currency; default EUR).
- **Logic per line:** read the cheapest current per-piece EUR buying price per EAN from `current_best`.
- **Output per requested EAN:** best qualifying supplier, their price, the **achieved margin %**, qualifying suppliers ranked cheapest-first, and a clear flag for lines where **no** supplier meets the margin.
- Unknown EANs (not in your DB) land in a "need an offer for this" list.

*(The earlier "one click to a CRM offer" step is removed — O1 closed. You take the result to the CRM's Purchases tab manually, §5.)*

---

## 10. Suggested build order (after §7 passes)

1. Schema + **Excel import wizard** + validation + review queue + brand resolution.
2. `current_best` maintenance + the ranking/grid UI + currency toggle + %-diff tool + bestseller flag/highlight + "End offer".
3. Exchange-rate auto-fetch + manual override.
4. Customer-request sourcing (§9).

*(No CRM-link step — O1 closed.)*

---

## 11. Resolved decisions

### Your answers, 2026-10-18

1. **Price basis:** usually **per piece**; when a row is **per box**, divide by pieces/box. → §1.2 / §2.4. *(Updated 2026-11-02: pieces/box is used at import only and NOT stored; the result is rounded to 2dp.)*
2. **Box-only suppliers:** not applicable. (Dropped.)
3. **Bestsellers:** flagged by ticking in the UI or by entering an EAN, ≈**10–15 per brand**. → §8.
4. **Territory:** **distinction only, no VAT math**, set per upload. → §1.2.
5. **CRM link:** **closed — no link (2026-11-02).** See decision 17 below and §5.
6. **Sourcing margin:** **adjustable, default 7%**; target price is **your selling price**. → §9.
7. **Two suppliers, same brand, different EAN sets:** add both EAN sets and compare on overlap. → §2.5.
8. **Intake:** **manual file drop**, **Excel (.xlsx/.xls) + CSV — PDF dropped.** → §0, §2, §2.7.
9. **Deployment & safety:** hosted on your domain behind the CRM's Cloudflare Access login, FastAPI, server-side SQLite, same BorgBase backup — a separate program, same hosting/login/backup. → §3, §6.
10. **Look:** reuse the CRM's exact CSS so the two read as one suite. → §8. *(Updated 2026-11-02: no "Create CRM offer" hand-off — O1 closed.)*
11. **File types (2026-10-31):** accept **`.xlsx`, `.xls`, `.csv`**; readers verified in §2.7; recommended path parses in-browser with SheetJS. → §2.7, §6.
12. **Offline copy (2026-10-31):** a **download button** (SQLite file + a plain Excel/CSV export) + a **weekly calendar reminder**; auto-pull later, noted not built. → §6.
13. **Process supervision (2026-10-31):** the price DB runs under **its own systemd unit**; the CRM's supervision is the roadmap's Stage 1 Part D (**LED-0048 / LED-0078**), referenced not done. → §6.
14. **Prototype data source — SUPERSEDED (2026-11-01):** no seeding from the baked `prototype.html` dataset; real data enters via Phase 2 ingestion of originals; dev/tests use synthetic data only. → build plan.
15. **Cloudflare Access — two emails + second admin (2026-10-31):** one shared policy admits two emails (main + Gmail backup) for both apps; the Gmail is a second Cloudflare admin. → §6, [SUBDOMAIN_SETUP_RUNBOOK.md](SUBDOMAIN_SETUP_RUNBOOK.md).
16. **FX storage (2026-10-31):** keep **both** the import-date rate and the daily `fx_rates` table, so either basis is computable without migration. → §4.2.

### End-user decisions, 2026-11-02 (close O1–O4, O6, O7)

17. **No CRM link (O1 closed).** The price DB is a comparison tool only; it exchanges no data with the CRM. The owner uploads the winning Excel into the CRM's **Purchases tab** manually, outside this system. Removed: the CRM link options (a/b/c), the "Create CRM offer" hand-off, the CRM lookup link, the §9 one-click step, and the CRM-link build phase. The price DB still shares the server, Cloudflare login and backups with the CRM. → §5, §9, §10, build plan.
18. **Minimal stored data.** Per offer line: **EAN, product description, per-piece price**. Per upload (asked on the form): **supplier company, brand, T1/T2, currency, offer date, offer type (price list | stock offer), MOQ, lead time, notes** (the last three free text — items 26–27). Nothing else — no contact details, no available stock, no size, no raw rows, no original files. → §1.1, §2.8.
19. **Per-piece prices (O-pricing).** Per-box files are divided by pieces-per-box **during import**, then rounded. Pieces-per-box is **used at import only, not stored**. Rounding is **after dividing, never round-then-divide**. → §1.2, §2.4.
20. **Rounding (O6 closed).** Round **all** prices to **2 decimals** — per-piece prices and format-less cells included. **Round-half-up**, ties accepted; this matches Excel's display rounding (half-away-from-zero — verified 2026-11-02). Store the rounded value; the 6dp storage scale stays. FX conversion on read, displayed at 2dp. **Replaces the earlier "importer must not round" rule** and removes `raw_row` / the `.w`/`.z` capture. → §2.4, §1.1.
21. **Freshness (O2 closed).** No age-based expiry, no age warnings. Per supplier: a new **price list** replaces the previous price list; a new **stock offer** replaces the previous stock offer. Stock offers count until the owner **ends** them (an **"End offer"** action; `ended_at`). → §3.3, §8, §1.1.
22. **MOQ (O3).** Entered on the upload form, for **both** offer types; **display-only, never affects ranking**. **Type: free text** (e.g. "6240 pcs", "€25,000") — updated 2026-11-02 (see item 26); stored on the batch. → §1.1, §8.
23. **FX default view (O4 closed).** Default = **today's rate**; the **original currency is always shown alongside**. The manual rate override still wins when set. → §4.2.
24. **Originals (O7 closed).** **Don't keep uploaded files** — extract and discard. Removed: the uploads folder, the retention setting, and the "re-run history from originals" promise. **Duplicate detection by checksum stays** (a hash is not data). → §2.7, §2.8, §6.

### End-user answers on the four open points, 2026-11-02

25. **Replacement scope (O2 refined).** Replacement is **per supplier + per brand + per offer type**: a new price list replaces that supplier's previous price list for the **same brand** only; a new stock offer replaces the previous stock offer for the **same brand** only; **different brands never replace each other**. The "either rule as a setting" placeholder is **removed** — this rule is implemented directly. **Default (confirmed by the owner 2026-11-03 — item 29):** a multi-brand upload replaces, for each brand present in the file, that supplier's previous offer of the same type for that brand. → §3.3, §1.1.
26. **MOQ is free text (O3).** Supersedes the integer form (item 22): MOQ is **free text**, entered at upload, **display-only**, never parsed or used in ranking. Schema changed INTEGER → TEXT. → §1.1, §8.
27. **Lead time and notes: keep.** Both are **free-text fields on the upload (batch)**, with the UI hint **"business info only — no personal details"**. → §1.1, §2.2, §8.
28. **Brand always from the form, never from the file.** The prototype's "brand from a file column" path is **removed**. **Single-brand** upload: the picked brand applies to all rows; an EAN already under a **different** brand goes to **review** (no overwrite). **Multi-brand** upload (one tab): each row's brand comes from the **existing product by EAN**; unknown EANs go to review ("new product — assign a brand"), price kept but excluded from brand comparisons until assigned (**confirmed 2026-11-03 — item 29**). **Tab-per-brand workbooks are not supported**; multi-tab workbooks still merge into one batch. → §2.3, §2.5, §2.6.

29. **Offer types & replacement — FINAL (owner, 2026-11-03).** The two offer types are **"Regular offer (longer validity)"** (stored code `price_list`) and **"Stock offer (limited in time and/or quantity)"** (stored code `stock_offer`); the **stored codes are unchanged** (`app/offer_types.py` maps them to the display names). **One rule for both types, single- and multi-brand alike:** a new offer replaces, **for each brand present in the file**, that supplier's previous offer of the **same type** for that brand; **different brands never replace each other; regular and stock offers never replace each other**; a **stock offer** additionally drops out when the owner **ends** it (`ended_at`) — no date expiry, no quantity tracking. This finalises the O2 multi-brand default (items 25, 28) and **supersedes any earlier "multi-brand stock offer doesn't replace" reading**. Already implemented by `repo.live_offer_ids`; the four scenario tests are in `tests/test_replacement.py`. → §2.1, §2.5, §3.3, §8, §12-O2.

---

## 12. Decisions status — all decided; migration 0003 brought the schema to the decided model

All of O1–O7 are now **decided**. The Phase-1 schema (migrations 0001/0002) reserved columns for the then-open outcomes; migration **`0003_decided_schema`** (2026-11-02) then brought it to the decided model — dropping the reserved offer columns (`pieces_per_box`, `price_basis`, `raw_row`, `available_qty`, offer-level `moq`, `valid_until`) and `products.size`, adding the batch-level `brand`/`moq`(free text)/`lead_time`/`notes`/`ended_at`, and removing the freshness settings (default FX basis → `today`). So the resolutions are a mix of config/logic **and** this one reversible migration (with its `.down.sql`); any later schema change follows the same numbered-migration discipline (build plan §0).

Migration **`0004_frontend_support`** (2026-11-03, Step B) then added `suppliers.country` (a prototype Suppliers field) and the **data-minimised customer-request store** (`requests` + `request_lines`): only EAN, target price, currency and an optional company name/brand are kept — no contact details, the uploaded file is not stored, and the sourcing result is recomputed live (§9), never snapshotted. Step B also built the frontend on the API and the standard security measures (production Access gate, CSP, Origin/validation, XSS-safe rendering, SheetJS self-hosted + SRI) — see build plan §7.5/§7.6; an independent security review is the pre-cutover gate.

| # | Decision | Status (date) | Resolution |
|---|---|---|---|
| O1 | CRM link direction | **DECIDED 2026-11-02** | **No link.** Comparison tool only; manual handoff via the CRM Purchases tab (§5). All link options and the one-click step removed. |
| O2 | Offer freshness / replacement rule | **DECIDED 2026-11-02; FINAL 2026-11-03 (item 29)** | **No age expiry.** Replacement per **(supplier, brand, offer type)**: newest regular offer replaces the previous regular offer of the same brand; newest stock offer replaces the previous stock offer of the same brand; different brands never replace; regular and stock never replace each other. Stock offers count until **ended** (`ended_at`). Multi-brand uploads replace per brand present (**confirmed 2026-11-03**). Display names 2026-11-03: `price_list` = "Regular offer (longer validity)", `stock_offer` = "Stock offer (limited in time and/or quantity)". No setting — implemented directly (§3.3). |
| O3 | Quantities (available qty / MOQ) | **DECIDED 2026-11-02** | **available_qty removed.** **MOQ** is **free text** on the batch (updated 2026-11-02 from integer), both kinds, **display-only, never parsed or ranked** (§8). Lead time + notes kept as free-text batch fields (item 27). |
| O4 | FX default view | **DECIDED 2026-11-02** | **Today's rate** by default, **original currency always shown alongside**; manual override wins (§4.2). Both rates still stored. |
| O5 | Money & rate representation (developer) | **DECIDED 2026-10-31 / 11-01** | `price_original` = integer minor units at **6 decimals (×1,000,000)**; FX rates as **TEXT**, math in Python `Decimal`; derived EUR computed on read. The stored price is now a **2dp** value (O6) at that 6dp scale. See below. |
| O6 | Displayed vs stored price | **DECIDED 2026-11-02** | **Round everything to 2dp (half-up).** No displayed-vs-stored gap to keep; `raw_row` and `.w`/`.z` capture removed (§2.4). |
| O7 | Original uploaded files — retention | **DECIDED 2026-11-02** | **Don't keep.** Parse in browser, discard; keep only the SHA-256 for dedupe. Uploads folder + retention settings removed (§2.7, §2.8, §6). |

### O5 — Money & rate representation (DEVELOPER decision — Ted)

Direction decided 2026-10-31; **price scale finalised 2026-11-01** after the raw-file scan. **FX representation: TEXT. Price scale: 6 decimals (×1,000,000).** *(2026-11-02: with O6 deciding to round every price to 2 decimals at import, the stored `price_original` is always a 2dp value; the ×1,000,000 scale is retained as headroom and to avoid any future scale migration, not because >2dp values are now stored.)*

| Field | Decision | Representation | Note |
|---|---|---|---|
| `price_original` (per-piece price) | **integer minor units**, never float — **6 decimals (×1,000,000)**; value rounded to **2dp** (O6) | integer, e.g. `2.08` → `2080000` | per-box divided then rounded once (§1.2, §2.4) |
| `fx_rate_eur`, `fx_rates.rate` | **TEXT, exactly as published**; FX math in **Python `Decimal`** | exact string, e.g. `"0.84385"` | EUR computed in Python, not SQL |
| `price_eur`, `unit_price_eur` | **computed on read, not stored**; displayed at 2dp | — | `current_best` remains the only rebuildable cache |

**Why integer minor units for prices:** the roadmap rule is *money in integer cents / exact decimal, never float* (LED-1159 #5, RESTART_PLAN §SQLite). The scale is fixed at **6 decimals (×1,000,000)** for headroom; the stored value is a 2dp price (O6).

---

*Intake is Excel (.xlsx/.xls) + CSV; the review queue plus EAN check-digit validation (§2.6) are the safety net against mistyped data. There is no CRM link (§5). CRM roadmap single source of truth: [RESTART_PLAN_2026-10-13.md](RESTART_PLAN_2026-10-13.md) + [ROADMAP_LEDGER.md](ROADMAP_LEDGER.md) (both in the CRM repo).*
