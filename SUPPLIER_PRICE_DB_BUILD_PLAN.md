# Supplier Price DB — build plan & how it connects to the CRM

*Companion to [SUPPLIER_PRICE_DB_DESIGN.md](SUPPLIER_PRICE_DB_DESIGN.md). That file is the "what and why";
this is the "how we build it, safely, without ever losing data."*

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
   saved as data. No rebuild, no deploy, no me. That is already how the prototype works today.

3. **Offers are append-only and we keep every original file.** Nothing is ever overwritten — a new
   offer is a new row. And we archive the **raw Excel file** you uploaded alongside it. That means if we
   ever improve how prices or descriptions are detected, we can **re-run the whole history** from the
   originals and get a better database, without you re-uploading anything. It's the strongest possible
   answer to "can I change it later": even past decisions aren't locked in.

Schema changes (a new field, a new table) are done with small, reversible migration scripts — rehearsed
on a copy first, the same discipline as the CRM's **Stage 1b** attachment migration, which has been
**built and rehearsed on a copy but is not yet deployed** (Stage 1d is the part that is live).

---

## 1. Where it lives — a sibling to the CRM, not a separate world

It runs on **the same server as your CRM** (`178.105.34.50`), behind **the same Cloudflare Access
login**, and looks like part of the same system. But it is its **own app with its own data file**, so a
problem in one can never corrupt the other.

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
            └──────── EAN link (read-only, through the front door) ─┘
```

- **Same box, new port.** A second uvicorn process on `:8001`, owned by `tedj`, bound to `127.0.0.1`. It
  runs under its **own systemd unit** (`Restart=always`, enabled) so it survives crashes and reboots. The
  **CRM's** supervision is separate — the roadmap's **Stage 1 Part D** (a watchdog without sudo, or systemd
  if sudo; **LED-0048 / LED-0078**); today the CRM runs as a manual `nohup` process that would *not* survive
  a reboot. The price-DB plan **references** that item, it doesn't do it. Server confirmed 2026-10-30:
  Ubuntu 24.04, **Python 3.12.3**, **61 GB free**.
- **Same login.** It sits behind the same Cloudflare Access policy, so there is **one** login and nothing
  is reachable from the open internet without it. The app itself only listens on `127.0.0.1` — same as
  the CRM — so Cloudflare is the only way in.
- **Own data, own backups.** `/data/pricedb/` is a new folder next to `/data/json`. It gets added to the
  **same restic off-site backup** you already run (just two more paths in the backup command).

---

## 2. Data & growth — the honest numbers

You said you expect "exponential" growth. The good news: it's actually **steady linear** growth, and the
volume is well inside what this design was already tested for.

| | |
|---|---|
| Offers you load | ~10 files/day, ~2,000 lines/day |
| Per year (append-only) | ~0.5–0.7 million rows |
| After 5 years | ~3 million rows |
| **Tested in the design** | **5 million rows — 368 MB file, queries still in milliseconds** |

Two things keep it instant no matter how big the history gets:

- A small maintained **`current_best`** table (one row per EAN = today's cheapest offer). Every screen you
  look at reads *that*, not the millions of historical rows — so it's instant even at 10M+ rows.
- If the full history ever gets genuinely huge (many years out), old offers move to a **yearly archive
  table / file** — the live table stays small, nothing is deleted. We don't need this for years; it's the
  release valve so the answer is always "it stays fast."

**You will not outgrow SQLite at this volume.** It's a single file, which is exactly what makes backup
and "download a copy" trivial (next sections). If one day you genuinely needed many people writing at
once, the move to Postgres is a known, boring migration — but nothing about your usage points there.

---

## 3. The build, in phases

Each phase is small, ends in something you can see working, and follows your runbook discipline:
**rehearse on a copy, verify before swap, keep a rollback.** Nothing touches the CRM's live data until
the very last, read-only link step.

**Phase 1 — Backend skeleton (server-side SQLite + the API).**
Stand up the `:8001` app, create the SQLite schema from the design doc (products, offers append-only,
suppliers, FX, `current_best`), WAL mode on. No UI yet — just a health check and the data endpoints.
*Done when:* `curl .../healthz` returns `ok` and the empty DB accepts a test offer via the API.

**Phase 2 — Port the prototype onto the backend (+ one-off data migration).**
Switch the prototype's storage from the browser (localStorage) to the `:8001` API — same screens, same
look, but the data now lives on the server, shared and backed-up instead of trapped in one browser.
**Data migration (explicit step):** the seed data is the dataset **baked into
`supplier-price-db/prototype.html`** (917 products / 1,443 offers; you confirmed you haven't edited it in
your browser, so no browser export is needed). A one-off script extracts it (the existing `split.py`) and
loads it into the SQLite schema — run once, verified by row counts. **Caveat:** the baked seed is already
2dp-rounded (a prototype limitation, §2.4), so it seeds history at 2dp while Phase-3 imports carry full
precision — re-import an original file if exact historical precision ever matters. *Done when:* you open
`prices.rivieraessentials.com`, see your 1,443 offers, and every screen (Offers, Best sellers, Requests,
Suppliers, Settings) works against the server.

**Phase 3 — Server-side ingestion + keep every original + the 10-file test.**
Uploading a file (**`.xlsx`, `.xls` or `.csv`**) POSTs it to the server. Parsing stays in the browser via
SheetJS (which reads all three and is already tuned on your files); the normalized rows are inserted as new
offers, **and the original file is stored** under `/data/pricedb/uploads/` (audit + re-processing), with
its SHA-256 so a duplicate upload warns you. The brand-approval / review-queue flow stays as is. **The
importer MUST NOT round** — unlike the prototype (2dp price / 4dp EUR), production stores `price_original`
at **full quoted precision** (integer minor units, §12 O5) and computes per-piece/EUR on read. This is
where the design's **10-file importer test (§7)** runs — on your **8 supplied files now**, plus 2 more to
collect — *before* the review screens and CRM link depend on it. The test files cover the header-naming
variety already seen: price columns are **not** always called "price" — real headers include **`OFFER`**
(clarins, clinique), **`PRICE (USD_`** (La Roche Posay), **`USD`** (HongKong) and **`Price Euros`**
(it's-a-10). **Note:** the scan script's keyword auto-detection ([scripts/price_decimal_scan.py](scripts/price_decimal_scan.py))
is a throwaway investigation aid, **not** the importer — it missed `OFFER` and latched onto title cells. The
**importer uses explicit per-supplier column mappings** (set once in the wizard, §2.3, remembered per
supplier), never keyword guessing. *Done when:* each real file lands correct offers (incl. an `OFFER`-headed
file), the raw file is retrievable, the review queue stays small, **a 3-decimal price round-trips through
import unchanged**, and the full stored value plus the supplier's on-screen text are both captured (§2.4 pt 6).

**Phase 4 — Backups, off-site, and offline downloads (the "never lose it" phase).** *(details in §5)*

**Phase 5 — The CRM link (direction still open — §4).** Built only once you pick (a) read-only lookup,
(b) an additive CRM endpoint, or (c) whole-collection re-import [not recommended]. *(details in §4)*

**Phase 6 — Cutover (subdomain live + both apps under systemd).**
Stand up `prices.rivieraessentials.com` per [SUBDOMAIN_SETUP_RUNBOOK.md](SUBDOMAIN_SETUP_RUNBOOK.md)
(Access policy first, then tunnel route — the one brief cloudflared restart is scheduled for a quiet
evening), put **both** apps under systemd, and confirm the live URL and the CRM still work. From here,
changes are the easy one-file redeploys of §0. *(The importer correctness test already happened in Phase 3.)*

I'd suggest building **Phase 1–2 first and letting you drive it in the browser**, then 3–6. Each phase is
its own small, verifiable step — never a big-bang deploy.

---

## 4. How it connects to the CRM — corrected after reading the CRM's import code

**Important correction (Part A, 2026-10-30).** The original plan was "export a draft file and import it into
the CRM." Reading the code killed that: the CRM's only JSON import is `Object.assign(S, data)` — it
**replaces whole collections**. A partial draft file would **wipe your product catalogue**. So the
draft-file idea is **withdrawn**, and the link direction is now an **open decision** (design §5 / §12):

- **(a) Read-only lookup** *(simplest, safest, likely enough)* — from the CRM you jump to the price DB for
  an EAN's current best offer, so you see the buying price next to your selling price when quoting. Nothing
  is ever written to the CRM.
- **(b) A new additive endpoint in the CRM** — a small, well-tested addition to `app.py` that merges
  products **by EAN** and never touches other collections. One-click, but needs a CRM deploy + its own test.
- **(c) Whole-collection re-import** — **not recommended** (fragile; one partial file wipes a collection).

Whichever you pick, the price DB stays **read-only toward the CRM** and the CRM's save path is untouched.
I'll build this in Phase 5 once you choose.

---

## 5. Security, backups & "never lose the data"

**Security** — it inherits the CRM's posture, nothing weaker:
- **One login, Cloudflare Access** in front; the app binds only to `127.0.0.1:8001`, so there is no way to
  reach it without logging in. The shared Access policy admits **two emails** (your main + a Gmail backup on
  a different provider) for both apps, and that Gmail is a **second admin** on the Cloudflare account — so
  losing one mailbox never locks you out.
- **Defence in depth:** today neither app checks identity in code (Cloudflare is the only gate — LED-1083).
  The price DB can optionally verify the Access JWT and check the email against a **config allow-list holding
  both addresses — never hardcoded**, so a second email stays a config change.
- **The price DB runs under its own systemd unit** (`Restart=always`, enabled) so a crash or reboot
  auto-restarts it; the **CRM's** supervision is the roadmap's Stage 1 Part D (watchdog-or-systemd;
  LED-0048/0078) — referenced, not done here.
- **Uploads are validated** — only `.xlsx/.xls/.csv`, size-capped, parsed in the browser with
  **self-hosted, pinned SheetJS + SRI** (Apache-2.0); the server stores the original bytes and never
  executes an upload. Every SQL query is parameterized.
- **No secrets in the page.** FX needs no API key; anything sensitive lives server-side only.

**Backups & never-losing-it — the 3-2-1 rule, with an offline copy you control:**

| Copy | Where | How |
|---|---|---|
| **1. Live** | `/data/pricedb/pricedb.sqlite` on the server | WAL mode; a nightly `.backup` snapshot so a copy is always consistent |
| **2. Off-site, encrypted** | your existing **restic** repo (BorgBase) | add `/data/pricedb` to the backup command you already run — encrypted at rest, off the box |
| **3. Offline, yours** | **downloaded to your own computer** | a **download button** in the app (SQLite file **+** a plain Excel/CSV export of all offers and current-best) + a **weekly calendar reminder** to click it. *(An automatic scheduled pull to your computer is a possible later improvement — noted, not built.)* |

That third copy is the direct answer to "offline downloads to never lose the data." Even if the server
**and** the off-site repo both vanished, you'd still hold a plain Excel export plus the database file on
your own machine, openable without any of this software. And because we keep **every original uploaded
file** too, the database can be rebuilt from source at any time.

**Proven, not assumed:** a **restore drill** runs on a schedule (same idea as your CRM's
`restore_drill.sh`) — it loads the newest backup read-only and confirms it's current and parseable, so
you find out backups are good *before* you ever need them, not after.

---

## 6. Status of decisions

**Decided (2026-10-31):** address = **`prices.rivieraessentials.com`**; file types **.xlsx/.xls/.csv**
(SheetJS self-hosted + SRI, Apache-2.0); offline copy = **download button + weekly reminder** (auto-pull
later); **systemd for the price DB** (the CRM's supervision is referenced to its Stage 1 Part D, not done
here); two Access emails (main + Gmail) + Gmail as second Cloudflare admin; seed data from the baked-in
prototype; money as **integer minor units at 6 decimals / ×1,000,000** (importer must NOT round — full
precision stored; raw-file scan 2026-11-01 found suppliers quoting up to 4dp, so 6dp for headroom), FX rates
stored **as TEXT** (Python `Decimal` math), derived EUR/per-piece **computed on read** (design §12 O5).

**Still open (awaiting input, ~1–2 days)** — design §12: CRM link direction (a/b/c), offer freshness rule,
quantities, FX default view. The schema reserves for all of these, so **Phases 1–4 can start now** and none
needs a later migration. Phase 5 (CRM link) waits on the direction; `current_best`'s freshness rule is a
configurable setting until you decide.

None of this is one-way: every phase is a small, reversible, rehearse-then-verify step, and after cutover
each change is a one-file redeploy that never touches your data.

---

*🤖 Drafted with [Claude Code](https://claude.com/claude-code)*
