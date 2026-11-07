# Supplier Price DB — build plan

*Companion to [SUPPLIER_PRICE_DB_DESIGN.md](SUPPLIER_PRICE_DB_DESIGN.md). That file is the "what and why";
this is the "how we build it, safely, without ever losing data." **End-user decisions applied 2026-11-02**
(no CRM link, minimal stored data, per-piece 2dp rounding, replacement per supplier+brand+type, MOQ free text, today's-rate
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
   crm.rivieraessentials.com        db.rivieraessentials.com
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
company, brand, territory T1/T2, currency, offer date, offer type (price list | stock offer), and MOQ, lead time
and notes (all free text, display-only)** (design §2.2). **Brand always comes from the form, never from the file**
(design §2.5): a single-brand upload applies the picked brand to every row, but an EAN already under a *different*
brand goes to **review** (no overwrite); a multi-brand upload resolves each row's brand from the **existing product
by EAN**, and an unknown EAN goes to review ("new product — assign a brand"). `offer_batches.source_file` is a
**neutral reference** (`supplier + date + sha256-prefix`), never
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
`Price Euros`), multi-brand (brands resolved from existing EANs), a per-box row, non-EUR currencies, the
**replacement rule** (per supplier + brand + type — design §7's five scenario tests), and the **data-minimisation case**
(contact block above the header + sales-rep column + person's name in the filename → assert **none** of it
appears anywhere in the DB, incl. `source_file`).
*Done when:* synthetic files land correct offers (incl. an `OFFER`-headed file); `€24.99/12` stores `2.08`; a
format-less cell still rounds to 2dp; a per-box row with no pieces-per-box goes to review; the contact-block test
passes (zero personal data stored); no original file is written to disk; and the **five replacement/brand scenario
tests pass** (Clinique≠Clarins; new Clarins list replaces old Clarins list but not the Clarins stock offer;
multi-brand matches known EANs and sends an unknown EAN to review; single-brand upload with an EAN under another
brand → review).

**Phase 3 — Port the prototype screens onto the backend.**
Switch the prototype's screens from browser `localStorage` to the `:8001` API — same look, server-side data.
**No baked-data migration:** real pricing enters via Phase 2 ingestion; dev/demos use **synthetic data only**.
Add the **"End offer"** action on stock offers and the **display-only MOQ** column (design §8). The currency
toggle shows EUR by default with the original currency alongside (design §4.2). *Done when:* you open
`db.rivieraessentials.com` and every screen (Offers, Best sellers, Requests, Suppliers, Settings) works
against the server.

**Phase 4 — Backups, off-site, and offline downloads (the "never lose it" phase).** *(details in §5)*

**Phase 5 — Cutover (subdomain live + both apps under systemd).**
Stand up `db.rivieraessentials.com` per [SUBDOMAIN_SETUP_RUNBOOK.md](SUBDOMAIN_SETUP_RUNBOOK.md)
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

### 5.1 Creating the off-site backup on BorgBase (restic) — exact steps

> **DEFERRED (2026-11-07) — BorgBase access pending.** The owner is locked out of BorgBase (2FA recovery
> codes not found), so the separate `pricedb` repo can't be created yet. The price DB **goes live without
> off-site backup**, with interim protection in place (below). **Hard deadline: off-site backups must be in
> place within TWO WEEKS of go-live** — either via this BorgBase repo once access is recovered, **or** via
> the fallback of adding the price DB snapshot to the **CRM's existing restic job** (this needs **CRM-side
> approval first** — do not change the CRM job without it). The BorgBase steps below apply unchanged once
> access is back.
>
> **Interim backups in force until off-site lands:**
> 1. **Nightly on-box snapshot** — `deploy/pricedb-snapshot.sh` + `pricedb-snapshot.timer` write a
>    consistent SQLite `.backup` snapshot to `/data/pricedb/snapshots/` (kept 14 days, same perms as the DB).
>    Protects against mistakes (bad import, accidental delete), **not** against losing the server.
> 2. **Downloaded export bundle** — the owner downloads the backup archive after each import session (at
>    least weekly) to the encrypted drive; that is the only interim copy that survives losing the server.
> 3. **Hetzner's own server backup** — considered, **not enabled** (owner decision; see runbook Step 5 note).
> 4. **Security gate** marks off-site as *DEFERRED — BorgBase access pending* (not PASS) and instead tests a
>    restore from the local snapshot and from a downloaded bundle (`deploy/SECURITY_GATE.md` §5).

This is copy **2** above. It mirrors the CRM's existing **restic → BorgBase** job (EU, append-only key on the
box, full key off-box); the price DB is just one more path. *(This is the content the README's missing
`BACKUP_SYSTEM.md` was meant to hold — it lives here until that file exists.)* Placeholders in `<…>`.

**One-time — create the repo (owner, BorgBase web UI):**
1. Log in to BorgBase (**same account as the CRM**). **Repositories → New Repository**: name `pricedb`,
   region **EU**, **restic** compatibility, quota as needed. Note the repo URL BorgBase shows for restic
   (e.g. `rest:https://<REPO_ID>.repo.borgbase.com`) and set a strong **repo password**.
2. **Two keys, split on purpose** (same as the CRM): give the **server** an **append-only** key — it can *add*
   snapshots but never delete or prune them, so a compromised box can't wipe history. Keep a **separate
   full-access key off the server** (password manager) for pruning and restores.

**One-time — on the server (as the `pricedb` user):**
3. Install restic (match the CRM's pinned version): `sudo apt-get install -y restic`.
4. Secrets in a **root-owned** env file, never in the repo — `/etc/pricedb/backup.env` (`chmod 600`):
   ```
   RESTIC_REPOSITORY=rest:https://<REPO_ID>.repo.borgbase.com
   RESTIC_PASSWORD=<repo password>
   ```
5. **Initialise once with the FULL key** (append-only cannot `init`): `restic init`. Then leave the **server
   configured with the append-only key** for the nightly job.

**Nightly backup (systemd timer, same shape as the FX timer):**
6. `/usr/local/bin/pricedb-backup.sh`:
   ```bash
   #!/usr/bin/env bash
   set -euo pipefail
   set -a; source /etc/pricedb/backup.env; set +a
   # WAL-safe consistent snapshot first, then back that up
   sqlite3 /data/pricedb/pricedb.sqlite ".backup '/data/pricedb/pricedb-snap.sqlite'"
   restic backup /data/pricedb/pricedb-snap.sqlite --tag pricedb --host db
   curl -fsS "https://hc-ping.com/<PRICEDB_CHECK_UUID>"   # ping healthchecks.io ONLY on success
   ```
   The server job is **backup + success-ping only** — with an append-only key it cannot prune.
7. `pricedb-backup.service` + `pricedb-backup.timer` (`OnCalendar=*-*-* 02:30`, `User=pricedb`,
   `Restart=no`); `systemctl enable --now pricedb-backup.timer`.
8. **Retention** runs **off the box** with the full key (or BorgBase server-side pruning), e.g. monthly from
   your machine: `restic forget --tag pricedb --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --prune`.

**Monitoring — the email you asked for:**
9. Create a **healthchecks.io** check "pricedb nightly backup" (period 1 day + grace) and send its alerts to
   the **same inbox as the CRM backup emails**. A missed or failed night then emails you too — today you only
   get CRM mails because the price DB isn't wired in yet.

**Restore drill — proven, not assumed (gate E / §7.3):**
10. `pricedb-restore-drill.sh` (weekly timer): `restic restore latest --tag pricedb --target /tmp/pricedb-drill`,
    then open the restored `.sqlite` **read-only** and assert the expected tables + a sane row count; ping a
    **second** healthcheck on success.
11. Before cutover, do **one manual end-to-end restore**: restore `latest` to a scratch path, point a throwaway
    app instance (port 8002, `PRICEDB_DB_PATH`) at it, and confirm the grid loads. Only then is copy 2 "proven".

---

## 6. Status of decisions

**Decided (2026-11-07):** go **live without off-site backup** (BorgBase account locked out). Interim: a
nightly **on-box SQLite snapshot** (`pricedb-snapshot.timer`, kept 14 days) + the owner's **downloaded
export bundle** after each import session. **Off-site backup must be in place within two weeks of go-live** —
BorgBase repo once access is recovered, or the fallback of adding the price DB to the **CRM restic job**
(needs CRM approval first). Security gate marks off-site **DEFERRED — BorgBase access pending** and tests
restore from the local snapshot + a downloaded bundle instead (§5.1, `deploy/SECURITY_GATE.md` §5).

**Decided (2026-10-31):** address = **`db.rivieraessentials.com`**; file types **.xlsx/.xls/.csv**
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
- **Freshness / replacement** (O2, **final 2026-11-03 — design item 29**): no age expiry; one rule for both
  types, single- and multi-brand alike — a new **regular** / **stock** offer replaces the previous offer of the
  **same type** for **each brand present in the file**; different brands never replace; regular and stock never
  replace each other; stock offers additionally drop out via **"End offer"** (`ended_at`). The two types are now
  named **"Regular offer (longer validity)"** (`price_list`) and **"Stock offer (limited in time and/or
  quantity)"** (`stock_offer`) — stored codes unchanged.
- **MOQ** (O3): **free text**, both kinds, **display-only, never parsed/ranked**.
- **Lead time & notes:** kept as **free-text** batch fields ("business info only — no personal details").
- **Brand:** always from the upload form, **never from the file**; single-brand conflict (EAN under another brand)
  → review; multi-brand resolves by existing EAN, unknown EAN → review.
- **FX default** (O4): **today's rate**, original currency always shown; manual override wins.
- **Originals** (O7): **not kept** — parse and discard; checksum-only dedupe.

**Decided (go-live, 2026-11-07):**
- **Backups:** the price DB gets its **own BorgBase repository + its own timer**; the CRM nightly restic
  job is **unchanged** (deploy `restic-crm-change.md`, Option 1).
- **Only CRM-touching step at cutover:** adding `db.rivieraessentials.com` to
  `/etc/cloudflared/config.yml`, applied by **`systemctl restart cloudflared`**. Cloudflare's official
  docs document a **restart** (not a reload) for config changes, and the installed unit has no
  `ExecReload`, so plan on **~a few seconds of CRM downtime** while the shared tunnel bounces — hence the
  quiet evening. The CRM's own uvicorn and data are untouched. (Verified against Cloudflare docs 2026-11-07.)

**Still open:** none. All of O1–O7 are decided (design §12). Migration **`0003_decided_schema`** brought the
Phase-1 schema to the decided model (reversible, with its `.down.sql`); any later schema change is another
numbered migration (§0). **Phases 1–5 can proceed.**

None of this is one-way: every phase is a small, reversible, rehearse-then-verify step, and after cutover each
change is a one-file redeploy that never touches your data.

---

## 7. MVP scope & go-live plan (owner-confirmed 2026-11-03)

**Approach (owner):** ship a minimum first version the owner can use for real, go live, then add the rest.

### 7.1 In the first version (MVP)

- **Import offers** — the upload wizard (supplier, brand, territory, currency, offer date, **offer type** [Regular
  offer | Stock offer], MOQ / lead time / notes), per-box divide-then-round, and the **review queue** (resolve
  EAN-under-another-brand, unknown-EAN, checksum fails).
- **Best price per product** — the grid + drill-down, cheapest-first, on the live/replacement rule (§3.3).
- **Currency** — EUR default with the original shown alongside; manual override; a daily FX fetch.
- **End offer** on a stock offer.
- **Brands / Suppliers / Products** basic management + **bestseller flag + highlight** (not a separate ranking page).
- **Customer-request sourcing (design §9)** — upload a customer's EAN + target price list, check against current
  best prices at the adjustable margin (default 7%); show qualifying suppliers, achieved margin, lines no supplier
  can meet, and unknown EANs. **No CRM step** (O1 — the "→ Create CRM offer" button is removed).
- **Two-offer comparison** — pick two offers, show them side by side with a % difference column.
- **Offline export/download** bundle (3-2-1) + **login/hosting/backup** (Cloudflare Access, tunnel, systemd, restic + restore drill).

**Sourcing data minimisation (owner, 2026-11-03):** a saved request stores **only EAN, target price, currency and
(optional) customer COMPANY name** — plus optional brand/date. **No contact details.** The uploaded request file
is **not kept**. The sourcing result (which supplier wins, at what buy price, achieved margin) is **recomputed live**
against `current_best` on each view — **not** snapshotted into the request — so it is always current and nothing
extra is stored. *(This slims the prototype's `requests`, which persisted the full result incl. supplier names and
buy prices; see the conflict note handed to the owner 2026-11-03.)*

### 7.2 Deferred to after go-live

Separate **bestseller ranking page** (the flag + highlight stay); **per-supplier column-mapping templates** (MVP
maps columns per import, pre-filled by a guess the owner confirms; templates are added once the real-file test
shows the patterns — §7 of the design); **brand-alias management UI**.

### 7.3 Every remaining step to go-live, and who's needed

| Step | What | Who |
|---|---|---|
| **A. Backend finish** | FX daily fetch (Frankfurter → ECB fallback) + manual override; export/download bundle | me |
| **B. Frontend** | port the screens onto the API — wizard, grid/drill-down, review queue, currency toggle, end-offer, bestseller flag, settings, export, **sourcing**, **two-offer comparison**; self-host **SheetJS 0.20.3 + SRI**; sourcing/comparison backend endpoints | me |
| **C. Real-file acceptance** | run ~10 real supplier files locally (mix of regular/stock, multi-brand, non-EUR); measure review rate, correctness, per-supplier mapping; files stay **outside the repo, never committed** | **owner** provides files; me to analyse/adjust |
| **D. Server setup** | subdomain DNS; **Cloudflare Access app** (AUD tag) + shared policy; **tunnel** ingress → `:8001`; **systemd** unit (non-root user); add `/data/pricedb` to restic + **restore drill** | **owner** (Cloudflare/DNS) + a **quiet evening** (shared box — a blip can touch the CRM) |
| **E. Security gate** | independent security review of the finished code **by a separate agent that did not write it**; verify the §7.5 measures on the running server (Access ON, CSP, Origin/validation, non-root + file perms, restic restore); report findings to the owner and **fix before cutover** | separate agent + me to fix; **owner** reviews findings |
| **F. Cutover** | deploy, smoke test (import a real file, best price, currency, end-offer, sourcing, export), hand over | me + owner verify |

### 7.4 Honest estimate (working sessions) + uncertainty

- **A** — ~1–2 sessions (**built**: FX fetch + manual override + export bundle).
- **B** — **4–6 sessions. Built this turn** (2026-11-03): all MVP screens on the API, sourcing, two-offer
  comparison, SheetJS self-host + SRI, and the §7.5 security measures. Real-file test (C) still ahead.
- **C** — 1–2 sessions, **high uncertainty**: depends on how messy the real files are; can loop back into B.
- **D** — 1–2 sessions of work, **calendar-gated** by owner/Cloudflare access and the quiet-evening rule; the
  Access-app/tunnel wiring is the main operational unknown (the subdomain runbook de-risks it).
- **E (Security gate)** — ~1 session (independent agent review + fixes).
- **F (Cutover)** — ~1 session.

**Total ≈ 9–14 working sessions** (adds the security gate). Biggest risks:
**(1)** the frontend breadth + real-file messiness (B/C), **(2)** the Cloudflare Access/tunnel stand-up (D).

### 7.5 Security measures built into Step B (owner, 2026-11-03)

Standard measures, built in from the start — not extra screens or settings. Reported per point:

1. **Production Access gate** — `config.check_production_security()` runs at startup; in `production` the app
   **refuses to boot** unless Cloudflare Access JWT verification is ON with a team name + AUD tag and a non-empty
   email allow-list (and a public origin for the Origin check). OFF only in local development. *(test_security)*
2. **XSS** — the frontend holds **no inline event handlers and no inline `<script>`**; every value from a supplier
   file or the user is written with `esc()` / text, never raw HTML. Verified in the browser: a description of
   `<img onerror>…<script>` renders as literal text and does not execute. *(test: XSS payload round-trips verbatim)*
3. **Server-side validation** (`app/validate.py`) on every endpoint — EAN digits/length (and check digit on import),
   prices as positive numbers under a sane cap, required fields, **row-count** (≤20k offers, ≤5k request lines) and
   **16 MB body** limits; clear 4xx on bad input; **parameterised SQL only**. *(test_security, test_frontend_api)*
4. **CSRF** — state-changing requests must be **JSON** and carry an **Origin** matching the app's own origin
   (`SecurityMiddleware`); a cross-site form can't do either. *(test_security)*
5. **No runtime external resources** — the page loads only this origin: `styles.css`, `app.js`, and the
   **self-hosted SheetJS 0.20.3 with SRI**. A strict **Content-Security-Policy** (`default-src 'self'`,
   `script-src 'self'`, `connect-src 'self'`, `object-src 'none'`, `frame-ancestors 'none'`) is set on every
   response, plus `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`. *(test_security)*
6. **Server hardening** (step D, runbook) — run as a **non-root** user; `/data/pricedb` readable only by that user;
   **systemd** unit with `Restart=on-failure`; access logs carry URLs/status only, **never row contents**; `/data`
   in **restic** with a **restore drill**. *(documented in the subdomain/ops runbook; applied at server setup)*
7. **Export bundle** — the Settings screen warns, by the download button, that the bundle is the **full database**
   and belongs on the **encrypted drive**; the bundle README repeats "business data only".
8. **Independent security review** — **step E**: a separate agent that did not write the code reviews it, findings
   go to the owner, fixes land **before cutover**.

### 7.6 Prototype → MVP deviations (Step B), each forced by a decision

- Offer type: prototype's free-text `kind` (3 values) → the two structured types (Regular / Stock).
- Brand from the upload **form only** (single, or multi matched by EAN); the prototype's brand-column +
  brand-approval step is gone.
- **Best price = cheapest *live* offer** (replacement/End-offer rule); the prototype showed every batch and picked
  the cheapest shown. Superseded/ended offers are not grid columns (they stay in a product's history).
- **Customer requests** store inputs only and recompute live; the prototype persisted the full result and had a
  **"→ Create CRM offer"** button — **removed** (O1, no CRM link).
- **Size** is not stored (dropped 2026-11-02), so the product editor has description + brand only.
- **Delete offer** is a hard removal of a mis-imported batch (prototype behaviour kept); append-only still governs
  recorded **prices** — a correction is a new offer, never an edit.
- Data now lives on the **server**; `localStorage` holds only the grid zoom level. Column reordering is an
  in-session convenience (not persisted). Supplier **country** added as `suppliers.country` (migration 0004) to
  keep the prototype's Suppliers field.
- SheetJS **0.18.5 (CDN)** → **0.20.3 self-hosted + SRI**.

---

*🤖 Drafted with [Claude Code](https://claude.com/claude-code)*
