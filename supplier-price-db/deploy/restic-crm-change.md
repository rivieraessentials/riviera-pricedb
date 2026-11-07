# What changes in the existing CRM nightly restic job (and nothing else)

> **DECIDED 2026-11-07: Option 1.** The price DB gets its **own BorgBase repository and its own
> backup timer**. The CRM's nightly restic job is **not changed at all** — same repo, keys,
> schedule, retention, and paths. Option 2 below is kept only for reference.

The price DB reuses the **same restic → BorgBase** mechanism the CRM already runs. Two ways to wire
it in — pick ONE. Either way, **the only change to the CRM side is adding one more source path to
the nightly backup**: a *consistent SQLite snapshot* of the price DB, **not** the live DB file.

> Why a snapshot, not the live file: the live `pricedb.sqlite` is in WAL mode and may be mid-write at
> 02:30. Backing up the raw file can capture a torn state. The snapshot (SQLite's `.backup` API) is
> always internally consistent. The CRM's own JSON doesn't have this issue, which is why its job
> copies a file directly — so for the price DB we add a *snapshot step first*, then back up the result.

Current CRM nightly job (per the operating-rules note), unchanged in spirit:
- backs up `/data/json/RivieraCRM_data.json` + `/data/attachments`
- restic repo on BorgBase, EU, **append-only** key on the box, full key off-box

---

## Option 1 — CHOSEN (2026-11-07): keep the price DB on its OWN timer, leave the CRM job untouched

Nothing in the CRM job changes at all. The price DB gets its **own** `pricedb-backup.timer`
(provided in `deploy/systemd/`), its **own** BorgBase repo, and its **own** healthcheck. This is the
cleanest separation — a change or outage on one never touches the other. **Use this unless you have a
reason to share one repo.** If you choose this, there is **no CRM-side change to clear** — you only
add the new price-DB units from the runbook.

## Option 2: fold the price DB into the CRM's existing nightly script

If you'd rather have one nightly job, add exactly these lines to the CRM backup script, **before** its
existing `restic backup ...` line, and add the snapshot path to that line's arguments:

```bash
# --- added for the price DB: consistent snapshot BEFORE backing up ---
/opt/pricedb/supplier-price-db/backend/.venv/bin/python - \
    /data/pricedb/pricedb.sqlite /data/pricedb/pricedb-snap.sqlite <<'PY'
import sqlite3, sys
s = sqlite3.connect(sys.argv[1]); d = sqlite3.connect(sys.argv[2])
try:
    with d: s.backup(d)
finally:
    d.close(); s.close()
PY
# --- end added ---
```

Then change the existing backup line from:

```bash
restic backup /data/json/RivieraCRM_data.json /data/attachments
```

to (adds ONE path — the snapshot):

```bash
restic backup /data/json/RivieraCRM_data.json /data/attachments /data/pricedb/pricedb-snap.sqlite
```

That is the **entire** diff. No change to the repo, keys, schedule, retention, or the attachments/JSON
paths. The CRM's restore drill can optionally assert the snapshot restores too, but that isn't required.

---

### To clear with the CRM side first
- Confirm the CRM's restic repo has room / the plan allows one more small file (~3 MB today, grows slowly).
- Decide Option 1 (separate repo + timer — nothing to change on the CRM) vs Option 2 (one line added).
- If Option 2: confirm who owns the CRM backup script and that adding the snapshot step is acceptable.
- Either way: the price DB's **healthcheck alert email** should go to the **same inbox** as the CRM
  backup emails, so a missed/failed price-DB backup also reaches you (today you only get CRM emails).
