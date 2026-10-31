# Supplier Price Database — Design (design only, nothing built yet)

**Date:** 2026-10-18
**Author:** Ted
**Status:** Design for review. No code, no deployment. The in-CRM attempt is discarded; this is a separate app linked to the CRM by EAN. **Intake is manual file drop — Excel (.xlsx/.xls) and CSV** (PDF dropped — see §11). Files are dropped in manually. **Deployment: hosted on your domain behind the CRM's existing login, exactly like the CRM — not a laptop-only program, so nothing critical lives only on the laptop (see §6).**

---

## 0. What this is, in one paragraph

A standalone price-intelligence tool that ingests supplier offers (**Excel files, dropped in manually**), stores every offered price as **append-only history** in its original currency, and lets you answer one question fast: *who is cheapest?* — per product (EAN), per brand, and across your bestsellers. Everything displays in **EUR** by default with a currency toggle. It links **one-way** into [patched_crm_v2.html](patched_crm_v2.html) to turn a selection into a CRM offer/proforma, without ever touching the CRM's own save path.

**Core principles that shaped every decision below:**

1. **EAN is the one source of truth for identity.** One EAN = one product, no duplicates. Descriptions are cosmetic and may collide.
2. **Offers are immutable history.** You never edit a price; a new offer is a new row (a new "column" in the Excel-like view). This is what makes "compare past and future offers" work.
3. **Store the original, derive the comparison.** Keep every figure exactly as the supplier quoted it (currency, pack, price). Compute EUR and per-piece values on top, reproducibly, never destroying the original.
4. **The CRM is read-only to us.** We never modify how it saves; any link is read-only toward the CRM (§5 — the draft-file idea was withdrawn after reading the CRM's import code).

---

## 1. Data model

### 1.1 Tables

```
brands
  brand_id        PK
  name            TEXT      -- canonical name, unique (case-insensitive)
  aliases_json    TEXT      -- JSON array of supplier spellings ("L'Oréal"/"Loreal"/"LOREAL PARIS").
                            -- SQLite has NO array type; stored as JSON text and parsed in memory (brands are few).
                            -- Alternative if aliases ever need SQL querying: a brand_aliases(brand_id, alias) table.

suppliers
  supplier_id     PK
  name            TEXT
  default_territory  TEXT   -- 'T1' (non-European) | 'T2' (European); overridable per batch
  notes           TEXT

products                    -- one row per EAN, forever
  product_id      PK
  ean             TEXT  UNIQUE NOT NULL   -- the identity. no duplicates, enforced by the DB
  description     TEXT      -- "SKU"/description as first seen; informational only
  brand_id        FK -> brands  (nullable until resolved)
  size            TEXT      -- intrinsic to the EAN: "100ml", "50ml". belongs to the product
  status          TEXT      -- 'active' | 'needs_review' (new EAN with no brand yet)
  is_bestseller   INTEGER   -- you flag these; drives visual highlight + a dedicated ranking
  created_at, first_seen_batch_id

offer_batches               -- one batch = one "column" in the grid = one supplier file/offer
  batch_id        PK
  supplier_id     FK
  received_date   DATE      -- the date you specify on import
  currency        TEXT      -- EUR/GBP/USD/SGD/CAD/HKD (batch default; can vary per line)
  territory       TEXT      -- T1 / T2 for this batch (distinction only, no VAT math)
  kind            TEXT      -- 'price list' | 'stock offer' | ... (a supplier can have many)
  source_file     TEXT      -- original filename, kept for audit
  source_sha256   TEXT      -- checksum of the uploaded file; a re-upload of the same bytes warns "already imported" (§2.1)
  fx_rate_eur     TEXT|INT  -- the import-date rate, stored EXACTLY as published, NEVER float (O5 §12: text vs high-scale integer — Ted picks). see §4
  fx_source       TEXT      -- 'frankfurter' | 'ecb' | 'manual'
  fx_locked       INTEGER   -- 1 if you overrode the rate manually (manual wins, and is saved here)
  label           TEXT      -- optional column header text, e.g. "Supplier X — 2026-10-12 stock"

offers                      -- APPEND-ONLY price history. one row per (batch, product)
  offer_id        PK
  batch_id        FK
  product_id      FK
  price_original  INTEGER   -- the LOWEST quoted price, as INTEGER MINOR UNITS at a fixed scale (O5 §12; NEVER float).
                            -- scan 2026-10-31: quoted prices are ≤2 decimals in all 16 batches; scale chosen with margin (≥4dp / ×10,000 proposed).
  currency        TEXT      -- usually = batch currency, but stored per-row for safety
  pieces_per_box  INTEGER   -- the case pack AS QUOTED in this offer (can differ per supplier)
  price_basis     TEXT      -- 'per_piece' | 'per_box' — read from the file's basis column (§1.2)
  -- NOTE (O5 §12): price_eur and unit_price_eur are NO LONGER stored here. EUR and per-piece values are
  -- COMPUTED ON READ from price_original + currency + the batch's fx_rate + pieces_per_box + price_basis.
  -- (Per-piece is a division result — inherently rounded — so it is never stored as a source of truth.)
  raw_row         TEXT      -- JSON of the original parsed row, for audit/review
  available_qty   INTEGER   -- nullable; stock quantity if the offer states it (OPEN decision §12 — reserved now, unused until decided)
  moq             INTEGER   -- nullable; minimum order quantity if stated (OPEN decision §12)
  valid_until     DATE      -- nullable; offer expiry if stated (feeds the freshness rule, OPEN decision §12)
  created_at
  -- offer "kind" (price list / stock offer) lives on offer_batches.kind (one file = one column = one kind)

fx_rates                    -- daily cache of fetched rates (base EUR)
  rate_date       DATE
  currency        TEXT
  rate            TEXT|INT  -- 1 EUR = rate * currency; stored EXACTLY as published, NEVER float (O5 §12). scan: up to 5 decimals (e.g. GBP 0.84385)
  source          TEXT
  PRIMARY KEY (rate_date, currency)

current_best                -- MAINTAINED cache: cheapest LIVE offer per product (see §3.3)
  product_id      PK
  best_offer_id   FK
  best_unit_price_eur  INTEGER  -- rounded per-piece EUR as minor units; a DERIVED, rebuildable cache value (the ONLY stored cache — O5 §12)
  best_supplier_id FK
  updated_at
  -- "LIVE" = which offers still count. The freshness rule that decides this is an OPEN decision (§12),
  -- held as a configurable setting (below), NOT hardcoded. current_best is rebuilt from offers using
  -- whatever rule the setting holds, so changing the rule later needs no schema migration.

settings                    -- small key/value config, editable in the UI. Examples:
  key             TEXT PK   --   'freshness_rule' (UNDECIDED §12), 'freshness_days', 'sourcing_margin' (0.07),
  value           TEXT      --   'fx_display_basis' ('import_date' | 'today', UNDECIDED §12), 'display_currency'
```

### 1.2 Store a unit **and** a pack size — yes, both. Here's why.

Your files carry two different size concepts and they do different jobs:

- **`size` = the consumer unit** (100ml, 50ml). This is **intrinsic to the EAN** — the same barcode is always the same bottle. It lives on `products`. It never changes between suppliers.
- **`pieces_per_box` = the case pack**, together with the **price basis**. This is **a property of the offer, not the product**: the same EAN may be quoted per piece by one supplier and per box by another.

**How your files actually quote it:** usually the price is **per piece**. Sometimes it's **per box**, and in that case the offer has a **column flagging the basis** — when that column says per box, we **divide by `pieces_per_box`** to get the per-piece price. So the importer reads the basis column per row (default `per_piece` when absent) and normalizes.

So the rule is: **store the price exactly as quoted (`price_original` + `price_basis` + `pieces_per_box`), and derive one normalized `unit_price_eur` = EUR price for a single piece.** All ranking and comparison use `unit_price_eur`; everything you show back to a supplier or put on a CRM offer uses the original as-quoted figures.

**Why both, not just one:**

- If you stored *only* a per-piece price, you'd lose the case pack and couldn't reproduce the supplier's actual quote on a CRM offer.
- If you stored *only* the as-quoted price, your rankings would compare a per-piece price against a per-box price and be meaningless.
- `size` (on the product) and `pieces_per_box` (on the offer) are genuinely different axes — a 100ml product might come 12/box from one supplier and 24/box from another. Keeping them separate is what lets you say "cheapest per bottle" correctly regardless of how each supplier packs it.

**EAN rule enforced in the schema:** `ean` is `UNIQUE NOT NULL`. Duplicate EAN is impossible at the database level. Two different descriptions pointing at two different EANs is fine and expected — they are two products. Two descriptions that look identical but share an EAN collapse to one product.

**Territory (T1/T2):** stored per batch (defaulting from the supplier), as a **label/filter only — no duty or VAT math**. It records the European vs non-European distinction and lets you facet columns by it.

---

## 2. Ingestion pipeline (Excel)

### 2.1 Shape of the flow

```
file you drop in (.xlsx / .xls / .csv)
        │
        ▼
 [1] parse the workbook/CSV (see §2.7 for which reader handles which format)
        │
        ▼
 [2] per-file import wizard:
       • you pick supplier, received_date, currency, territory, kind  (the "column" header)
       • multi-tab workbook: each tab handled separately (§2.3)
        │
        ▼
 [3] column mapping → EAN, description, size, pieces/box, price basis, price(s)
       • remember the mapping per supplier as a reusable template
        │
        ▼
 [4] per row: normalize → take LOWEST price (§2.4) → apply basis (§1.2) → validate (§2.6)
        │
        ├─ clean row, EAN exists        ─► append new offer to that product
        ├─ clean row, EAN is new        ─► create product (status=needs_review if no brand) + offer
        └─ uncertain / failed validation ─► REVIEW LIST (never silently imported)
        │
        ▼
 [5] recompute current_best for touched products (§3.3)
```

Nothing is destructive: an import only ever **appends** offers and **creates** products. Re-importing the same file creates a new batch (a new column); it never overwrites. **Duplicate guard:** the SHA-256 of each uploaded file is stored on its batch (`source_sha256`); dropping a file whose checksum was already imported shows a **"this exact file was already imported on &lt;date&gt; as &lt;column&gt;"** warning so you cancel or proceed deliberately.

### 2.2 Why an import wizard, not silent auto-import

Because brand names are often missing and files mix brands, a fully automatic importer would guess wrong and quietly corrupt your rankings. The wizard front-loads the five things only you know (supplier, date, currency, territory, kind) once per file, then does the row-level work automatically and sends only the genuinely ambiguous rows to a review list.

### 2.3 Multi-tab Excel

- Each worksheet (tab) is treated as a **potential separate batch/column**. The wizard lists all tabs and lets you include/exclude each, and set whether tabs share a header row or differ.
- Common real cases handled:
  - **One brand per tab** → each tab becomes its own column, brand taken from the tab (name or a cell) or confirmed by you.
  - **Different brands within one tab** (you called this out) → see §2.5; brands are resolved per row by EAN first, then by any brand column/section header, then by asking you.
  - **Junk tabs** (cover sheet, terms) → excluded in the wizard.
- Header detection: scan the first ~15 rows of each tab for the row that looks like headers (contains EAN/price-like labels). Supplier templates are remembered so next month's file from the same supplier maps itself.

### 2.4 "Always take the lowest price"

A file may show several price columns (retail, wholesale, other). Rule, applied per row:

1. Identify all numeric price columns (the wizard lets you tag which columns are prices; remembered per supplier).
2. Parse each with EU/US locale awareness (`12,50` → `12.50`).
3. Discard non-positive / blank / obviously-not-a-price values.
4. **Take the minimum of the remaining** as `price_original`, record in `raw_row` which column it came from, then apply the basis rule (§1.2) to get the per-piece figure.

### 2.5 Brand matching inside a mixed-brand file

Resolution order per row:

1. **EAN already in the DB** → use that product's existing brand. (Most reliable; needs nothing from the file.)
2. **EAN is new but the file/section gives a brand** (brand column, or a section header like "— CHANEL —") → create the product under that brand.
3. **EAN is new and no brand anywhere** → create the product with `status = needs_review`, `brand_id = NULL`, and put it on the **"new products — needs your input" list**. The offer is still recorded (so you don't lose the price), but it's excluded from brand rankings until you assign a brand.

This is exactly your requirement: match existing ones, create the new EANs, and ask you for input on the ones with no brand. And when two suppliers carry the same brand with **different EAN sets**, both sets are added to the master database (§11, your answer 7) and compared where they overlap — and across future offers.

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
- Size / pieces-per-box doesn't match expected patterns, or `price_basis = per_box` with no `pieces_per_box` to divide by.
- A near-duplicate description maps to a *different* EAN than a very similar existing product — flagged as "possible EAN typo, confirm".

Each review item carries the **reason** and the original row, so you're correcting "EAN checksum failed" or "no price found", not staring at a raw cell.

### 2.7 Accepted file types and which reader handles them

Accepted: **`.xlsx`, `.xls`, `.csv`** (manual drop; PDF remains dropped, §11).

**Recommended architecture — parse in the browser, store the original on the server.** The prototype already
parses all three formats client-side with **SheetJS** (xlsx 0.18.5), which reads `.xlsx`, `.xls` and `.csv`
alike, and its column-detection is already tuned on your real files. The production app keeps that: the
browser parses the file and POSTs (a) the **normalized rows** and (b) the **original file** to the server,
which stores the original under `/data/pricedb/uploads/` (audit + re-import) and appends the offers. This
reuses proven code and makes the server-side `.xls` library question moot.

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

Pin and re-verify these at install (xlrd is low-maintenance). Either way the **security rule is the same (§6):**
validate type and size, parse defensively, never execute an upload, keep the original bytes.

Official confirmation (not blog tutorials): openpyxl rejects `.xls` with `InvalidFileException` and accepts
only `.xlsx/.xlsm/.xltx/.xltm` — [openpyxl docs](https://openpyxl.readthedocs.io/); xlrd 2.0.0 (Dec 2020)
removed everything except `.xls` — [xlrd changelog](https://xlrd.readthedocs.io/en/latest/changes.html).

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

**Reading of this:** everything you do interactively — drill-downs, the %-diff tool, one brand's grid, the bestseller ranking, importing a file — is **milliseconds**. Only the two *whole-database* rankings (cheapest across all 250k products / all brands at once) take ~4–5 s, because they scan all 5M rows every time.

### 3.3 The fix for those two: a maintained `current_best` table

You don't need to re-scan 5M rows to know today's cheapest per product. Maintain a small `current_best` row per product, updated on each import for only the touched products (a 200-line import touches 200 rows). Then the global "cheapest everywhere", "best per brand", and the §9 sourcing feature read from `current_best` (250k rows, indexed) and return in **milliseconds** instead of ~5 s. The append-only `offers` table remains the full history behind it. **`current_best` is the only maintained cache** (rebuildable from `offers` at any time); EUR and per-piece figures everywhere else are **computed on read**, not stored (O5 §12).

**Conclusion:** SQLite handles many millions of rows here with ms-level interactivity. It runs **server-side on your domain host** (the same box as the CRM), as a single file read/written by the app's backend — so no separate database-server process is warranted, and the data lives on the server, not the laptop. (WAL mode on; single user, so no write-contention concerns.)

---

## 4. Exchange rates

### 4.1 Source (verified against live 2026 docs/endpoints)

- **Primary: Frankfurter** (`frankfurter.dev`) — ECB-based, free, **no API key**, EUR-native, EU-hosted, self-hostable via Docker.
- **Fallback: ECB `eurofxref-daily.xml`** directly — the canonical source, same data.

**All six of your currencies are confirmed present** (checked against the live currency list and today's values, not from memory): EUR base, plus GBP, USD, **SGD, CAD, HKD** — the three you'd worry about with an ECB source are all in ECB's ~30-currency list.

Example fetch (all six, base EUR):
```
https://api.frankfurter.dev/v1/latest?base=EUR&symbols=GBP,USD,SGD,CAD,HKD
→ {"base":"EUR","date":"2026-10-16","rates":{"CAD":1.5935,"GBP":0.84385,"SGD":1.4395,...}}
```

### 4.2 Behaviour

- **Scheduled server-side fetch**, once per working day, by a **cron/systemd timer on the server** (the same mechanism as the nightly backup — never "on first launch", which would depend on someone opening the app). It writes the day's rates into `fx_rates` (base EUR). No key, no quota to manage.
- **Manual override wins and is saved with the offer.** On import you can type a rate; if you do, `fx_locked = 1`, `fx_source = 'manual'`, and that exact rate is stored on the batch and used for every row in it — permanently and reproducibly, never silently re-converted later.
- **Weekend/holiday caveat:** neither ECB nor Frankfurter publishes rates on non-working days (Frankfurter returns the last working-day rate). The manual override is the escape hatch for those days.
- **Display-currency switch:** prices are stored in original currency + a derived EUR value. The UI toggles the *display* among EUR (default), GBP, USD, SGD, CAD, HKD by converting from stored EUR via the current rates — storage never changes, only display.
- **Two rates kept so either basis is computable (no migration needed):** the **import-date rate** is stored on the batch (`fx_rate_eur`) AND the **daily `fx_rates` table** keeps every day's rate. So a comparison can be shown at the *rate on the offer's import date* (stable, reproducible) or recomputed at *today's rate* (reflects current cost). **Which is the default view is an OPEN decision (§12, O4)** — both are supported by the data; only the default is unset. Rates are stored **exactly as published, never float** (O5 §12) — ECB/Frankfurter run to ~5 significant figures (e.g. GBP 0.84385 = 5dp).
- Attribution: if you ever publish the rates externally, cite "Source: European Central Bank". Internal single-user use needs nothing.

---

## 5. Linking to the CRM — without touching its save path

### 5.1 What the CRM's import actually does (verified by reading the code, 2026-10-30)

⚠️ **Correction to the original v1 plan.** The CRM's only JSON import is `loadFromFile()`
([patched_crm_v2.html:1697](patched_crm_v2.html)). Its merge is literally `Object.assign(S, data)` — a
**top-level key replace**: for every collection the imported file contains, it **overwrites that whole
array**, then in server mode immediately PUTs the result back. Confirmed behaviourally on synthetic data —
importing `{products:[one EAN]}` left the products table with **just that one row**, the rest gone.

**So a "partial draft file" would delete your catalogue.** There is no additive importer in the CRM today;
`loadFromFile` is a whole-file *restore*, not an *import*. (This matches tracked item **LED-1083** — the CRM
has no app-layer merge/validation; it trusts whatever JSON it's handed.) The original §5.2 "draft file"
approach is therefore **unsafe and withdrawn.**

### 5.2 The link is now an OPEN decision (§12) — three directions

Pending your choice (awaiting input, ~1–2 days). All keep the price DB **read-only toward the CRM** in
spirit; they differ in how data crosses:

- **(a) Read-only lookup only** *(simplest, safest)* — from the CRM you jump to the price DB for an EAN's
  current best offer; nothing is ever written to the CRM. Likely all you need for quoting.
- **(b) A new, genuinely-additive endpoint in the CRM** — a small, well-tested addition to `app.py` that
  merges products **by EAN** server-side and never touches other collections. More work; needs a CRM deploy
  and its own test. **CRM-roadmap constraint:** this cannot land **before Stage 1b**, belongs in the
  **structural/workflow phase**, ships in **its own sitting** (code and data never mixed), must pass the
  **protected accountant-export diff**, and follows the **SQLite-ready rules** (LED-1159).
- **(c) Whole-collection export/re-import** *(NOT recommended)* — safe only if you export the CRM's *entire
  current* product list, add to it, and re-import the whole thing. Fragile; one partial file wipes a
  collection. Recorded for completeness; avoid.

### 5.3 What a link would send (once a direction is picked)

Per line: EAN, description, size, pieces/box, supplier, chosen price (original currency + EUR), date — joined
on EAN, always ending with you reviewing/confirming inside the CRM.

### 5.4 If suppliers are ever linked across the two systems — by stable ID, never name

Today the link is **EAN-only** (products); suppliers are not cross-linked. The CRM has been bitten repeatedly
by **name-keyed** matching (spelling variants spawn phantom parties). So if a price-DB supplier is ever tied
to a CRM supplier, join on a **stable ID**, never the typed name — SQLite-ready rule #4 (LED-1159).

---

## 6. Security & backup

**Deployment model — same as the CRM, so laptop failure loses nothing.** The CRM is a **FastAPI backend served from your domain behind Cloudflare Access** (the login), reached only through a **Cloudflare Tunnel** (no public port), with server-side persistence and an off-site **restic** backup on **BorgBase**. The price DB reuses that whole stack. *(Deployment facts verified against the live server 2026-10-30; stand-up steps in [SUBDOMAIN_SETUP_RUNBOOK.md](SUBDOMAIN_SETUP_RUNBOOK.md).)*

- **Hosted on your domain, not the laptop.** Same server as the CRM (`178.105.34.50`, Ubuntu 24.04, **Python 3.12.3**, **61 GB free**), as a **separate app** — its own subdomain **`prices.rivieraessentials.com`**, its own FastAPI process on its own port (e.g. `:8001`), and its **own SQLite file** under `/data/pricedb/`. Decoupled from the CRM's save path and data. The laptop is just a browser; if it dies, you sign in from any device and everything is there.
- **Login = the same Cloudflare Access.** One shared Access policy admits **two email addresses** — your main address and a **Gmail backup on a different provider** — for **both** apps, so losing one mailbox never locks you out. The same Gmail is added as a **second admin member** on the Cloudflare account. (Dashboard steps + a private-window login test with the second email: the runbook.)
- **Defence in depth (identity).** Today **neither app checks identity in code** — Cloudflare Access is the sole gate (tracked as **LED-1083**). The price DB is the clean place to do it right: optionally verify the **`Cf-Access-Jwt-Assertion`** JWT on each `/api/*` request (signature against Access's public keys + expected `aud`) and check the email against an **allow-list held in config with both addresses — never hardcoded**. So adding/removing an email is a config/policy change, not a code change.
- **Process supervision.** The price DB ships **its own systemd unit** (`Restart=always`, enabled) so a crash or reboot auto-restarts it. The **CRM's** supervision is **not** done here — it is the roadmap's **Stage 1 Part D** (a watchdog without sudo, or a systemd unit if sudo), tracked as **LED-0048 / LED-0078**; today the CRM runs as a manual `nohup` process that would not survive a reboot (confirmed 2026-10-30). The price-DB plan **references** that item, it does not perform it.
- **Uploads are validated.** Only `.xlsx/.xls/.csv`, size-capped, parsed defensively (in the browser via SheetJS in the recommended design, §2.7); the server stores the original bytes under `/data/pricedb/uploads/` and **never executes** an upload. Every DB query is parameterized.
- **The Cloudflare 100-second cap applies here too.** The free plan drops any origin request over 100 s (the CRM already feels this on full saves). So bulk import and full export must stay well under 100 s or stream/paginate — a design constraint, not an afterthought.
- **Off-site backup: the same restic → BorgBase job** (append-only, EU, deletion-proven + restore-verified 2026-10-24). Adding `/data/pricedb` (the SQLite file **and** the `/uploads/` originals) is two more paths in the existing nightly command — restic dedups/encrypts client-side before anything leaves the server.
- **Offline copy you control (never lose the data).** An in-app **download button** produces a bundle — the SQLite file **plus** a plain Excel/CSV export of all offers and `current_best` — that you save to your own machine; a **weekly calendar reminder** keeps the habit. (An **automatic scheduled pull** to your computer is a possible later improvement, noted not built.) Live server copy + off-site restic copy + this local copy = **3-2-1**; and because every original upload is retained, the DB is rebuildable from source.
- **Proven, not assumed:** the monthly restic restore drill extends to the price-DB paths.
- **No new secrets:** the FX source needs no API key.

Net: a **separate program** (separate code, separate data, CRM save path untouched), **hosted the same way, behind the same login, with the same off-site backup** — now reboot-safe and with a local offline copy, not a laptop-only tool.

---

## 7. Test plan — prove the importer on 10 real files (runs as build **Phase 3**)

The goal is to lock the data model and the importer against real-world mess. **This runs during Phase 3 of the build** (server-side ingestion), **before** the review screens, `current_best`, and any CRM link depend on its output — not as a separate pre-build project.

**File count reconciled:** you supplied **8** real files (univi.net.pl = Wella + Elemis; DnA Trade = Phyto + Biotherm + Bioderma; Go Pro Now = Eucerin; Rexaline; Lierac; Caudalie). Run the test on those **8 now**, and **collect 2 more** (ideally a legacy `.xls` and a non-EUR price list) to reach the target **10** before Phase 3 is signed off.

1. **Use** the 8 supplied files + 2 to-be-collected, spanning the range: some multi-tab, some mixed-brand tabs, some with a per-box basis column, some with multiple price columns, and some in non-EUR currencies (GBP/USD/SGD/etc.).
2. **Hand-label** a ground-truth extraction for a sample of rows per file (correct EAN / description / size / pieces / basis / price). This is what correctness is measured against.
3. **Prototype the importer only** (not the whole app): run the §2 pipeline over all 10, output rows + the review queue.
4. **Measure, per file and per supplier:**
   - row-level correctness and how many rows landed in the review queue and why;
   - locale / multi-price handling (did "lowest price" pick correctly?);
   - basis handling (per-box rows correctly divided by pieces/box?);
   - brand resolution (existing matched, new created, no-brand flagged);
   - EAN checksum catches.
5. **Decide from the measurements:** which suppliers need a stored column-mapping template, and whether the data model needs any field it's currently missing.
6. **Load test** with synthetic data at your real projected volume (already done here — §3 — re-run with your real column shapes once the schema is final).
7. **Only then** build the full app, suppliers' templates first.

Acceptance before build: review queue is small (target single-digit % of rows) and no silently-wrong prices got through.

---

## 8. UI / UX (Excel-like)

- **Left menu:** Products · Brands · Suppliers · Bestsellers · Import · Review queue · Customer requests (§9) · Settings (currency/FX).
- **Main view = a grid like Excel.** Rows = products (within a brand or a filter). Columns = offer batches ("Supplier — date — kind"); a supplier appears in **multiple columns** when they sent multiple offers/stock lists.
- **Rankings read left-to-right, cheapest on the left.** Columns order by price so your eye lands on the best price first.
- **Bestsellers** — you flag them **either by ticking a product in the UI or by entering an EAN** (≈10–15 per brand). Flagged products are **highlighted** (coloured row marker / star) and have a dedicated "Bestsellers" ranking view.
- **Per-piece vs as-quoted toggle**, and the **currency toggle** (EUR default).
- **Two-offer %-diff tool:** pick two offers → shows them side by side with a third column = percentage difference. (Benchmark: ~2 ms, instant.)
- Drill-down: click any product → its full price history across all suppliers/dates, cheapest first.

---

## 9. Customer-request sourcing (EAN + target price, adjustable margin)

Upload a customer file of **EAN + target price**; the tool tells you **which supplier(s) you can buy from while keeping your margin**.

- **Target price = your customer's desired buy price = YOUR selling price.** A supplier **qualifies** when your buying price sits at least the margin below it:
  `buy_price ≤ target_price × (1 − margin)`.
- **Margin is adjustable** (default **7%**, editable per run), so the achieved margin is `(target − buy) / target`.
- **Input:** an Excel file of EAN + target price (+ optional currency; default EUR).
- **Logic per line:** read the cheapest current buying price per EAN from `current_best`, normalized to per-piece and to EUR.
- **Output per requested EAN:** best qualifying supplier, their price, the **achieved margin %**, qualifying suppliers ranked cheapest-first, and a clear flag for lines where **no** supplier meets the margin (go renegotiate or decline).
- **One click to turn the qualifying lines into a CRM offer/proforma** via §5.
- Unknown EANs (not in your DB) land in a "need an offer for this" list.

This reuses the whole stack: same EAN identity, same `current_best`, same currency handling, same CRM link.

---

## 10. Suggested build order (after §7 passes)

1. Schema + **Excel import wizard** + validation + review queue + brand resolution.
2. `current_best` maintenance + the ranking/grid UI + currency toggle + %-diff tool + bestseller flag/highlight.
3. Exchange-rate auto-fetch + manual override.
4. CRM link — per the direction you pick (§5.2 / §12): (a) read-only lookup [likely], (b) additive endpoint, (c) not recommended.
5. Customer-request sourcing (§9).

---

## 11. Resolved decisions (your answers, 2026-10-18)

1. **Price basis:** usually **per piece**; when a row is **per box**, a **basis column in the offer** says so and we divide by pieces/box. → modelled as `price_basis` + normalization in §1.2 / §2.4.
2. **Box-only suppliers:** not applicable. (Dropped.)
3. **Bestsellers:** flagged **by ticking in the UI or by entering an EAN**, ≈**10–15 per brand**. → §8.
4. **Territory:** **distinction only, no VAT math.** → §1.2.
5. **CRM link:** ~~both options~~ **superseded** — the CRM's JSON import replaces whole collections, so a draft file is unsafe (§5.1). Direction is now an OPEN decision (§12): (a) read-only lookup, (b) additive endpoint, (c) whole-collection re-import [not recommended].
6. **Sourcing margin:** **adjustable, default 7%**; target price is **your selling price** (customer's desired buy price). → §9.
7. **Two suppliers, same brand, different EAN sets:** **add both EAN sets to the master DB** and compare on overlap and across future offers. → §2.5.
8. **Intake:** **manual file drop**, **Excel (.xlsx/.xls) + CSV — PDF dropped.** → §0, §2, §2.7. (The previously-researched PDF/OCR pipeline is set aside; revisit only if a supplier ever sends PDF-only.)
9. **Deployment & safety:** **hosted on your domain behind the CRM's Cloudflare Access login**, FastAPI backend, **server-side SQLite**, same **BorgBase** off-site backup — a separate program from the CRM, but the same hosting/login/backup model so laptop loss costs nothing. → §3, §6.
10. **Look:** the prototype reuses the **CRM's exact CSS** (navy sidebar, same cards/tables/badges/fonts) so the two read as one suite, with a "Create CRM offer" hand-off. → §8, `supplier-price-db/prototype.html`.
11. **File types (2026-10-31):** accept **`.xlsx`, `.xls`, `.csv`**. Readers verified in §2.7 (openpyxl does **not** read `.xls`; xlrd does) — recommended path parses in-browser with SheetJS, which reads all three. → §2.7, §6.
12. **Offline copy (2026-10-31):** a **download button in the app** (SQLite file + a plain Excel/CSV export) + a **weekly calendar reminder**. An **automatic scheduled pull to your computer is a possible later improvement**, noted not built. → §6.
13. **Process supervision (2026-10-31):** the price DB runs under **its own systemd unit** (`Restart=always`, enabled). The **CRM's** supervision is the roadmap's **Stage 1 Part D** (watchdog-without-sudo, or systemd if sudo) — **LED-0048** (defect) / **LED-0078** (fix, P5); the 2026-10-30 probe confirms it's **not yet done** (unit exists but inactive; manual `nohup`). The price-DB build **references** that item; it does not do the CRM's. → §6.
14. **Prototype data source (2026-10-31):** seed data is the dataset **baked into `supplier-price-db/prototype.html`** (917 products / 1,443 offers); you confirmed you have not edited it in your browser, so no browser export is needed. → Build Plan Phase 2.
15. **Cloudflare Access — two emails + second admin (2026-10-31):** one shared Access policy admits **two emails** (your main + a Gmail backup on a different provider) for both apps, and the Gmail is also added as a **second admin member** on the Cloudflare account. Steps in [SUBDOMAIN_SETUP_RUNBOOK.md](SUBDOMAIN_SETUP_RUNBOOK.md) + the recovery checklist in [BACKUP_SYSTEM.md](BACKUP_SYSTEM.md). → §6.
16. **FX storage (2026-10-31):** keep **both** the import-date rate (on the batch) **and** the daily `fx_rates` table, so either comparison basis is computable without migration. → §4.2, §12.

---

## 12. Open decisions — schema already built so none needs a later migration

**O1–O4 are the END USER's** (the business owner) — awaiting their input, ~1–2 days. **O5 is the DEVELOPER's** (Ted) — direction decided below, exact scale pending. All are **deliberately unset in code**; the schema reserves for every outcome, so choosing later is config, not migration.

| # | Decision | Reserved so no migration is needed | Trade-off (for when you decide) |
|---|---|---|---|
| O1 | **CRM link direction** (a/b/c) | §5.2; nothing built toward the CRM yet | (a) read-only lookup = safest/least work; (b) additive endpoint = one-click but needs a CRM deploy + test; (c) whole-collection re-import = **not recommended** (fragile). **(b) roadmap constraint:** not before Stage 1b; structural/workflow phase; own sitting; protected-export diff; SQLite-ready rules (LED-1159). |
| O2 | **Offer freshness rule** (when an offer stops counting for "best price") | `settings.freshness_rule` (+ `freshness_days`), `offers.valid_until`; `current_best` rebuilt from the rule | age cutoff (simple; can hide a valid old list) · newest-per-supplier (good for price lists; a promo can mask a standing price) · type-aware (most correct; needs the offer kind tagged). **`current_best` cannot be finalised until this is set.** |
| O3 | **Quantities** (available qty / MOQ) | `offers.available_qty`, `offers.moq` (nullable, present now, unused) | including them lets "best price" respect stock/MOQ (more decision-useful) vs price-only (simpler; can rank an unbuyable price first). |
| O4 | **FX default view** (import-date rate vs today's) | both rates stored (§4.2) | import-date = stable/reproducible; today = reflects current cost. Only the *default* is unset; both views work. |

### O5 — Money & rate representation (DEVELOPER decision — Ted, not the end user)

Direction decided 2026-10-31 (exact scale pending Ted's confirmation). Grounded in a read-only decimal scan of the 16 baked batches:

- **Quoted prices: the baked-data scan is UNRELIABLE for this.** The prototype's parser **rounds the chosen price to 2 decimals** before storing it (`price:Math.round(Math.min(...ps)*100)/100`, [prototype.html:252](supplier-price-db/prototype.html)), and EUR to 4dp (:606). So the "≤2dp across all 16 batches" the scan reported only reflects **that rounding**, not what suppliers actually quote. The true decimal count **must** come from the **raw files** (corrected scan script provided). Until that runs, the price scale is provisional.
- **FX rates: up to 5 decimals** (EUR 1.0, USD 1.1253, GBP **0.84385**, SGD 1.4395, CAD 1.5935, HKD 8.8316 — ECB's ~5-significant-figure convention).

| Field | Decision | Representation | Note |
|---|---|---|---|
| `price_original` (quoted price) | **integer minor units**, never float — **DECIDED: 4 decimals (×10,000), pending raw scan** | **→ 6 decimals (×1,000,000) if the corrected raw-file scan shows any supplier quoting 3+ decimals** | the baked scan can't confirm this (parser rounds to 2dp, above), so the raw scan is the gate |
| `fx_rate_eur`, `fx_rates.rate` | **stored exactly as published**, never float | **text** (exact string, e.g. `"0.84385"`) *or* **high-scale integer** (e.g. ×10⁸) | Ted picks text vs integer (trade-off below) |
| `price_eur`, `unit_price_eur` | **computed on read, not stored** per offer row | — | `current_best` remains the **only** rebuildable cache |

**FX storage trade-off:** *text* stores exactly what ECB published at any precision and never drifts, but each arithmetic step must parse to `Decimal` (no direct SQL math); *high-scale integer* (e.g. rate ×10⁸) is exact to that scale and allows integer SQL math, but fixes precision at the chosen scale up front. Either satisfies "never float."
**Where EUR is computed (drives the choice):** in this design EUR is computed **in Python at import** (inserting offers and maintaining `current_best`), and the cross-currency comparison grid **sorts on the precomputed `current_best` integer** — so there is **no bulk rate-multiplication inside ad-hoc SQL**. The only thing that might do SQL-side rate math is a future "recompute at today's rate" across many rows (O4). **Recommendation (not a decision):** if all EUR math stays in Python/JS as designed, **text** is exact and simplest; pick **scaled integer** only to keep the option of multiplying rates **in SQL** without CAST-to-float.
**Why integer minor units for prices:** the roadmap rule is *money in integer cents / exact decimal, never float* (LED-1159 #5, RESTART_PLAN §SQLite). Plain 2-dp cents is too tight for sub-cent per-piece prices; a fixed higher scale keeps the quoted value exact and defers the inevitable rounding to the computed per-piece value.

---

*The CRM-link code detail from the Part A review is recorded in §5.1. Intake is Excel (.xlsx/.xls) + CSV; the review queue plus EAN check-digit validation (§2.6) are the safety net against mistyped data. CRM roadmap single source of truth: [RESTART_PLAN_2026-10-13.md](RESTART_PLAN_2026-10-13.md) + [ROADMAP_LEDGER.md](ROADMAP_LEDGER.md).*
