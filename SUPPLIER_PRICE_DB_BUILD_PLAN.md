# Supplier Price DB — build plan

*Companion to [SUPPLIER_PRICE_DB_DESIGN.md](SUPPLIER_PRICE_DB_DESIGN.md). That file is the "what and why";
this is the "how we build it, safely, without ever losing data." **End-user decisions applied 2026-11-02**
(no CRM link, minimal stored data, per-piece 2dp rounding, freshness by replace/end, MOQ integer, today's-rate
FX default, originals not kept).*

---

## 0. First, your question: "Will I be able to make changes later?"

**Yes — by design, and more easily than the CRM.** Three things make that true:

1. **Code and data are kept apart** — the same rule your CRM runbook lives by. The program
   (`app.py` + the one HTML page) is one thing; your offers live in a separate SQLite file that the
   program never has baked into it. So I can redeploy a new version of the screen or the logic by
   copying **one file** (exactly like "Sitting A" in your Stage 1b runbook) **without touching a single
   offer**. If a deploy ever looks wrong, we swap the old file back in one command — your data never moved.

2. **The things you change most are data, not code.** Currencies, FX overrides, brands, suppliers,
   territories (T1/T2), the 7% margin, which columns show — all of that is edited **in the screen** and
   saved as data. No rebuild, no deploy, no me.

3. **Offers are append-only.** Nothing is ever overwritten — a new offer is a new row; a newer price list
   or stock offer from a supplier simply **replaces** the previous one of that kind for ranking (design §3.3),
   without deleting history. **We do not keep the original uploaded files** (decided 2026-11-02 — data
   minimisation): each file is parsed in the browser and discarded, and only its SHA-256 checksum is kept so
   a re-upload of the same bytes is caught. *(This drops the earlier "re-run the whole history from the
   originals" idea — the originals aren't retained.)*

Schema changes (a new field, a new table) are done with small, reversible migration scripts — rehearsed
on a copy first, the same discipline as the CRM's **Stage 1b** attachment migration.

---

## 1. Where it lives — a sibling to the CRM, not a separate world

It runs on **the same server as your CRM** (`178.105.34.50`), behind **the same Cloudflare Access
login**, and looks like part of the same system. But it is its **own app with its own data file**, and it
**exchanges no data with the CRM** (no link — design §5), so a problem in one can never touch the other.

```
                 you (one login, Cloudflare Access)
                              │
                 ┌────────────┴─────────────┐
   crm.rivieraessentials.com        prices.rivieraessentials.com
            │                                   │
     CRM FastAPI  :8000                 Price-DB FastAPI  :8001     ← same box, different port
            │                                   │
   /data/json/RivieraCRM_data.json     /data/pricedb/pricedb.sqlite ← separate data, separate backups
            │                                   │
            └──────────── NO data link (manual handoff via the CRM Purchases tab) ────────────┘
```

- **Same box, new port.** A second uvicorn process on `:8001`, bound to `127.0.0.1`, under its **own systemd
  unit** (`Restart=always`, enabled). The **CRM's** supervision is separate — the roadmap's **Stage 1 Part D**
  (**LED-0048 / LED-0078**); referenced, not done here. Server confirmed 2026-10-30: Ubuntu 24.04,
  **Python 3.12.3**, **61 GB free**.
- **Same login.** It sits behind the same Cloudflare Access policy; the app itself only listens on
  `127.0.0.1`, so Cloudflare is the only way in.
- **Own data, own backups.** `/data/pricedb/` is a new folder next to `/data/json`, added to the **same
  restic off-site backup** (one more path — just the SQLite file; no uploads folder, since originals aren't kept).

---

## 2. Data & growth — the honest numbers

You expect "exponential" growth; it's actually **steady linear**, well inside what this design was tested for.

| | |
|---|---|
| Offers you load | ~10 files/day, ~2,000 lines/day |
| Per year (append-only) | ~0.5–0.7 million rows |
| After 5 years | ~3 million rows |
| **Tested in the design** | **5 million rows — 368 MB file, queries still in milliseconds** |

Two things keep it instant no matter how big the history gets:

- A small maintained **`current_best`** table (one row per EAN = today's cheapest **live** offer). Every screen
  reads *that*, not the millions of historical rows.
- If the full history ever gets genuinely huge, old offers move to a **yearly archive table / file** — the
  live table stays small, nothing is deleted. Not needed for years.

**You will not outgrow SQLite at this volume.** A single file is exactly what makes backup and "download a
copy" trivial.

---

## 3. The build, in phases

Each phase is small, ends in something you can see working, and follows your runbook discipline:
**rehearse on a copy, verify before swap, keep a rollback.**

**Phase 1 — Backend skeleton (server-side SQLite + the API).**
Stand up the `:8001` app, create the SQLite schema from the design doc (products, offers append-only,
suppliers, FX, `current_best`), WAL mode on. No UI yet — just a health check and the data endpoints.
*Done when:* `curl .../healthz` returns `ok` and the empty DB accepts a test offer via the API.

> **Phase order (2026-11-01): ingestion first, then the screen port.** `prototype.html` is out of git (data
> minimisation) and its baked data is 2dp-rounded/part-synthetic, so seeding screens from it would be
> throwaway. Importing the real originals is the real path and it exercises the money / rounding / review core.

**Phase 2 — Server-side ingestion (data-minimised) + the synthetic-file test.**
A file (**`.xlsx`, `.xls` or `.csv`**) is parsed **in the browser** via SheetJS (reads all three, already tuned
on your files; all sheets merged into one batch — design §2.3). **Data minimisation (design §2.8):** the browser
reads **only rows below the detected header and only the mapped columns**, computes the **per-piece price**, and
POSTs **only** `EAN + description + per-piece price` per line plus the per-upload metadata — unmapped columns and
any contact block never leave the browser. **There is no `raw_row`.** Per upload the form captures **supplier
company, brand, territory T1/T2, currency, offer date, offer type (price list | stock offer), and MOQ (integer)**
(design §2.2). `offer_batches.source_file` is a **neutral reference** (`supplier + date + sha256-prefix`), never
the filename; the **original file is discarded** (O7) — only its SHA-256 is kept for the duplicate warning. Logs
record counts/reasons only.

**Pricing in the importer (decided 2026-11-02):** take the lowest price per row; if the row is **per box**,
**divide by pieces-per-box first**, then **round once to 2 decimals, half-up** (pieces-per-box is used here only,
never stored). Format-less cells are rounded too. A row that can't produce a per-piece price (per-box with no
pieces-per-box, no price, bad EAN) **routes to the review queue** — it does not fail the file. *(The earlier
">6 decimals → review" rule is gone: everything is rounded to 2dp now.)* The importer uses **explicit
per-supplier column mappings**, never keyword guessing.

This is where the design's **importer test (design §7)** runs, on **SYNTHETIC files only** (real supplier files
are never committed — §2.8). The set covers the header-naming variety (`OFFER`, `PRICE (USD_`, `USD`,
`Price Euros`), multi-brand (brand column), a per-box row, non-EUR currencies, and the **data-minimisation case**
(contact block above the header + sales-rep column + person's name in the filename → assert **none** of it
appears anywhere in the DB, incl. `source_file`).
*Done when:* synthetic files land correct offers (incl. an `OFFER`-headed file); `€24.99/12` stores `2.08`; a
format-less cell still rounds to 2dp; a per-box row with no pieces-per-box goes to review; the contact-block test
passes (zero personal data stored); and no original file is written to disk.

**Phase 3 — Port the prototype screens onto the backend.**
Switch the prototype's screens from browser `localStorage` to the `:8001` API — same look, server-side data.
**No baked-data migration:** real pricing enters via Phase 2 ingestion; dev/demos use **synthetic data only**.
Add the **"End offer"** action on stock offers and the **display-only MOQ** column (design §8). The currency
toggle shows EUR by default with the original currency alongside (design §4.2). *Done when:* you open
`prices.rivieraessentials.com` and every screen (Offers, Best sellers, Requests, Suppliers, Settings) works
against the server.

**Phase 4 — Backups, off-site, and offline downloads (the "never lose it" phase).** *(details in §5)*

**Phase 5 — Cutover (subdomain live + both apps under systemd).**
Stand up `prices.rivieraessentials.com` per [SUBDOMAIN_SETUP_RUNBOOK.md](SUBDOMAIN_SETUP_RUNBOOK.md)
(Access policy first, then tunnel route — the one brief cloudflared restart scheduled for a quiet evening),
put **both** apps under systemd, and confirm the live URL and the CRM still work. From here, changes are the
easy one-file redeploys of §0.

> **Removed: the old "Phase 5 — CRM link".** There is no CRM link (O1 closed — design §5). The handoff is
> manual: find the best price here, then upload the supplier's Excel into the CRM's **Purchases tab** as usual.

I'd suggest building **Phase 1–2 first and letting you drive it in the browser**, then 3–5. Each phase is its
own small, verifiable step — never a big-bang deploy.

---

## 4. The CRM handoff — manual, no link

**There is no connection between this tool and the CRM** (decided 2026-11-02 — design §5). The earlier
"export a draft file / additive endpoint / one-click CRM offer" options are all removed. Reading the CRM's
code had already shown its only JSON import is `Object.assign(S, data)` — a whole-collection replace that a
partial file would use to wipe a catalogue — so a data link was never worth the risk; now there simply isn't one.

**How you use the two together:** find the cheapest supplier for a product here, then upload **that supplier's
Excel into the CRM via its Purchases tab**, exactly as you do today. The price DB and the CRM share the server,
the Cloudflare login and the backups (§1, §5) — but no data.

---

## 5. Security, backups & "never lose the data"

**Security** — it inherits the CRM's posture, nothing weaker:
- **One login, Cloudflare Access** in front; the app binds only to `127.0.0.1:8001`. The shared Access policy
  admits **two emails** (your main + a Gmail backup on a different provider) for both apps, and that Gmail is a
  **second admin** on the Cloudflare account — so losing one mailbox never locks you out.
- **Defence in depth:** today neither app checks identity in code (Cloudflare is the only gate — LED-1083).
  The price DB can verify the Access JWT and check the email against a **config allow-list holding both
  addresses — never hardcoded**.
- **Own systemd unit** (`Restart=always`); the CRM's supervision is the roadmap's Stage 1 Part D
  (LED-0048/0078) — referenced, not done here.
- **Uploads are validated and not stored** — only `.xlsx/.xls/.csv`, size-capped, parsed **in the browser**
  with **self-hosted, pinned SheetJS + SRI** (Apache-2.0); the server **never stores the original file**
  (O7) and never executes an upload. Every SQL query is parameterized.
- **No secrets in the page.** FX needs no API key.

**Backups & never-losing-it — the 3-2-1 rule, with an offline copy you control:**

| Copy | Where | How |
|---|---|---|
| **1. Live** | `/data/pricedb/pricedb.sqlite` on the server | WAL mode; a nightly `.backup` snapshot so a copy is always consistent |
| **2. Off-site, encrypted** | your existing **restic** repo (BorgBase) | add `/data/pricedb` (the SQLite file) to the backup command — encrypted at rest, off the box |
| **3. Offline, yours** | **downloaded to your own computer** | a **download button** in the app (SQLite file **+** a plain Excel/CSV export of all offers and current-best) + a **weekly calendar reminder** to click it. *(Auto-pull later — noted, not built.)* |

That third copy is the direct answer to "offline downloads to never lose the data." Even if the server **and**
the off-site repo both vanished, you'd still hold a plain Excel export plus the database file on your own
machine, openable without any of this software. *(Originals are not retained, so "rebuild from source files"
is not part of the recovery story — the three copies above are.)*

**Proven, not assumed:** a **restore drill** runs on a schedule (same idea as the CRM's `restore_drill.sh`) —
it loads the newest backup read-only and confirms it's current and parseable.

---

## 6. Status of decisions

**Decided (2026-10-31):** address = **`prices.rivieraessentials.com`**; file types **.xlsx/.xls/.csv**
(SheetJS self-hosted + SRI, Apache-2.0); offline copy = **download button + weekly reminder**; **systemd for the
price DB** (CRM supervision referenced to Stage 1 Part D); two Access emails + Gmail as second Cloudflare admin;
money as **integer minor units at 6 decimals / ×1,000,000**, FX rates **as TEXT** (Python `Decimal`), derived
EUR/per-piece **computed on read** (design §12 O5); **data minimisation — no personal data, business data only**;
**phase order: ingestion first**.

**Decided (end user, 2026-11-02)** — design §11 items 17–24 / §12:
- **No CRM link** (O1): comparison tool only; manual handoff via the CRM Purchases tab. CRM-link phase removed.
- **Minimal stored data** (design §1.1): per line EAN + description + per-piece price; per upload supplier,
  brand, T1/T2, currency, offer date, offer type, MOQ. No size, no stock qty, no raw rows, no originals.
- **Per-piece prices**: per-box divided by pieces/box at import (not stored), then rounded.
- **Rounding** (O6): all prices to **2dp, half-up** (matches Excel's half-away-from-zero). No `raw_row`.
- **Freshness** (O2): no age expiry; newest price list / stock offer per supplier replaces the previous; stock
  offers end via an **"End offer"** action.
- **MOQ** (O3): **integer**, both kinds, **display-only**.
- **FX default** (O4): **today's rate**, original currency always shown; manual override wins.
- **Originals** (O7): **not kept** — parse and discard; checksum-only dedupe.

**Still open:** none. All of O1–O7 are decided (design §12). The schema reserved for every outcome, so each is
config/logic, not a migration; **Phases 1–5 can proceed.**

None of this is one-way: every phase is a small, reversible, rehearse-then-verify step, and after cutover each
change is a one-file redeploy that never touches your data.

---

*🤖 Drafted with [Claude Code](https://claude.com/claude-code)*
