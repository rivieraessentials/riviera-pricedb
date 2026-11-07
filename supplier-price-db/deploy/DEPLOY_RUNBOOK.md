# Price DB — production deploy runbook (owner runs every command)

**You run all of this on the server yourself** — Claude has no SSH access. Nothing here touches the
CRM except the one optional restic line in [`restic-crm-change.md`](restic-crm-change.md), which you
clear with the CRM side first. The one step that can briefly affect the CRM is applying the Cloudflare
tunnel ingress — that is covered by the existing [`../../SUBDOMAIN_SETUP_RUNBOOK.md`](../../SUBDOMAIN_SETUP_RUNBOOK.md)
and scheduled for a quiet evening.

### How to use this
- Do the steps **in order**. Each has **what to run**, **expected output**, and **rollback**.
- Wherever you see **⟵ CHECKPOINT**, paste that command's output back to me before continuing — that's
  where a wrong result is cheap to catch and expensive to pass.
- Replace every `<PLACEHOLDER>`. Key ones: `<REPO_URL>` (the git remote you push to),
  `<YOUR_USER>` (your Linux login), and the Cloudflare `<CF_TEAM_NAME>` / `<CF_AUD_TAG>` + the two emails.
- Two paths through the runbook, decided in Step 0:
  - **Path A — system service** (you have sudo): dedicated `pricedb` user, `/opt`, `/etc`, `/data`.
  - **Path B — user service** (no sudo): everything under your home, `systemctl --user`.
  Do the **A** or **B** variant of each step, not both.

---

## Step 0 — Do you have sudo? (read-only)

Run (prompts for nothing, changes nothing):
```bash
id -nG | tr ' ' '\n' | grep -qE '^(sudo|wheel|admin)$' && echo "IN a sudo group -> Path A" || echo "NOT in a sudo group -> Path B"
```
Expected: one line telling you Path A or Path B. If it says Path A and you want to be sure sudo actually
works, run `sudo -v` (it will ask for your password; Ctrl-C to cancel — running it changes nothing).

**⟵ CHECKPOINT:** paste the line. We lock in Path A or Path B here.

*Rollback:* none (read-only).

---

## Step 1 — Get the code + Python environment

### Path A (system)
```bash
sudo useradd --system --home /opt/pricedb --shell /usr/sbin/nologin pricedb   # ok if "already exists"
sudo mkdir -p /opt/pricedb && sudo chown pricedb:pricedb /opt/pricedb
sudo -u pricedb git clone <REPO_URL> /opt/pricedb/supplier-price-db-repo
# the backend lives under supplier-price-db/ inside the repo:
cd /opt/pricedb/supplier-price-db-repo/supplier-price-db/backend
sudo -u pricedb python3.12 -m venv .venv
sudo -u pricedb .venv/bin/pip install --upgrade pip
sudo -u pricedb .venv/bin/pip install -r requirements.lock
```
> Note: the app serves the frontend from the repo tree, so keep the **whole** `supplier-price-db/`
> directory in place — don't copy out just the backend. Paths in the systemd units assume the backend is
> at `/opt/pricedb/supplier-price-db/backend`. Either clone to that exact path, or adjust the units'
> `WorkingDirectory`/`ExecStart` to wherever you cloned (`.../supplier-price-db-repo/supplier-price-db/backend`).

### Path B (user)
```bash
mkdir -p ~/pricedb && git clone <REPO_URL> ~/pricedb/supplier-price-db-repo
cd ~/pricedb/supplier-price-db-repo/supplier-price-db/backend
python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.lock
```

**Expected (both):** pip ends with `Successfully installed fastapi-... uvicorn-... openpyxl-... pyjwt-...`
(the set in `requirements.lock`). No build errors.

**⟵ CHECKPOINT:** paste the last ~10 lines of the pip install and the output of `.venv/bin/python -c "import fastapi, uvicorn, openpyxl, jwt; print('deps ok')"`.

*Rollback:* `rm -rf` the clone dir (Path A: `sudo rm -rf /opt/pricedb/supplier-price-db-repo`; Path B:
`rm -rf ~/pricedb/supplier-price-db-repo`). Path A also `sudo userdel pricedb` if you want to undo the user.

---

## Step 2 — Config + data directory

Fill in [`config.toml.example`](config.toml.example) and place it + create the data dir.

### Path A
```bash
sudo mkdir -p /etc/pricedb /data/pricedb
sudo chown pricedb:pricedb /data/pricedb
sudo cp /opt/pricedb/supplier-price-db-repo/supplier-price-db/deploy/config.toml.example /etc/pricedb/config.toml
sudo nano /etc/pricedb/config.toml          # fill every <PLACEHOLDER>; set path = "/data/pricedb/pricedb.sqlite"
sudo chown root:pricedb /etc/pricedb/config.toml && sudo chmod 640 /etc/pricedb/config.toml
```

### Path B
```bash
mkdir -p ~/pricedb/data
cp ~/pricedb/supplier-price-db-repo/supplier-price-db/deploy/config.toml.example ~/pricedb/config.toml
nano ~/pricedb/config.toml                  # fill placeholders; set path = "/home/<YOUR_USER>/pricedb/data/pricedb.sqlite"
chmod 600 ~/pricedb/config.toml
```

In the config set: `environment = "production"`, `public_origin = "https://prices.rivieraessentials.com"`,
`[access] enabled = true` with `team_name` + `aud_tag`, and both `allowed_emails`.
You get `team_name` + `aud_tag` from Cloudflare in Step 6 — you can fill them now if you already created
the Access app, or do Step 6 first and come back. The app **won't start** in production until they're set.

**⟵ CHECKPOINT:** paste `ls -l` of the config file (to confirm perms) — **not** its contents (it holds the
allow-list; no secrets, but keep the habit).

*Rollback:* delete the config and data dir you just created.

---

## Step 3 — First run by hand (creates + migrates the DB), then stop

Run the app in the foreground once to confirm it starts, migrates, and answers — then Ctrl-C.

### Path A
```bash
cd /opt/pricedb/supplier-price-db-repo/supplier-price-db/backend
sudo -u pricedb PRICEDB_CONFIG=/etc/pricedb/config.toml .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```
### Path B
```bash
cd ~/pricedb/supplier-price-db-repo/supplier-price-db/backend
PRICEDB_CONFIG=~/pricedb/config.toml .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```
In a second shell:
```bash
curl -s http://127.0.0.1:8001/healthz
```
**Expected:** `{"status":"ok","db":"ok","migrations":[1,2,3,4,5,6],"environment":"production","access_enabled":true}`
and the DB file now exists at the configured path. Then Ctrl-C the uvicorn.
> If instead uvicorn **exits immediately** with "refusing to start in production without the security
> gate: ..." — that's expected and good: it lists which of access/team/aud/emails/public_origin are still
> blank. Fill them in the config and re-run.

**⟵ CHECKPOINT:** paste the `/healthz` JSON and `ls -l` of the created `pricedb.sqlite`.

*Rollback:* Ctrl-C; delete the created `pricedb.sqlite` if you want a clean slate.

---

## Step 4 — Install the service + the daily FX timer

The unit files are in `deploy/systemd/system/` (Path A) and `deploy/systemd/user/` (Path B). If you cloned
to a path other than `/opt/pricedb/supplier-price-db`, edit the `WorkingDirectory`/`ExecStart` lines first.

### Path A (system)
```bash
R=/opt/pricedb/supplier-price-db-repo/supplier-price-db
sudo cp $R/deploy/systemd/system/pricedb*.service $R/deploy/systemd/system/pricedb*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pricedb.service
sudo systemctl enable --now pricedb-fx.timer
systemctl status pricedb.service --no-pager
```
### Path B (user)
```bash
R=~/pricedb/supplier-price-db-repo/supplier-price-db
mkdir -p ~/.config/systemd/user
cp $R/deploy/systemd/user/pricedb*.service $R/deploy/systemd/user/pricedb*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now pricedb.service
systemctl --user enable --now pricedb-fx.timer
loginctl enable-linger "$USER"      # so your service runs when you're not logged in
systemctl --user status pricedb.service --no-pager
```
> **Path B + `enable-linger` denied?** It can need admin. Either ask whoever has sudo to run
> `sudo loginctl enable-linger <YOUR_USER>` once, or add a fallback so it survives reboots:
> `crontab -e` → add `@reboot XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user start pricedb.service`.

**Expected (both):** status shows `active (running)`; `curl -s http://127.0.0.1:8001/healthz` still ok.

**⟵ CHECKPOINT:** paste the `status` output and the `/healthz` line.

*Rollback:*
- Path A: `sudo systemctl disable --now pricedb.service pricedb-fx.timer && sudo rm /etc/systemd/system/pricedb*.{service,timer} && sudo systemctl daemon-reload`
- Path B: `systemctl --user disable --now pricedb.service pricedb-fx.timer && rm ~/.config/systemd/user/pricedb*.{service,timer} && systemctl --user daemon-reload`

---

## Step 5 — Off-site backup (BorgBase/restic) + restore drill

Follow **build plan §5.1** ([`../../SUPPLIER_PRICE_DB_BUILD_PLAN.md`](../../SUPPLIER_PRICE_DB_BUILD_PLAN.md))
for creating the BorgBase repo + the append-only/full key split. Then wire the scripts:

1. **restic available?** `restic version`. If missing: Path A `sudo apt-get install -y restic`; Path B
   download the binary to `~/bin` (from restic's GitHub releases) and `export PATH=$HOME/bin:$PATH`.
2. **Install the scripts.**
   - Path A: `sudo cp $R/deploy/pricedb-backup.sh $R/deploy/pricedb-restore-drill.sh /usr/local/bin/ && sudo chmod 755 /usr/local/bin/pricedb-*.sh`
   - Path B: `mkdir -p ~/pricedb/bin && cp $R/deploy/pricedb-backup.sh $R/deploy/pricedb-restore-drill.sh ~/pricedb/bin/ && chmod 755 ~/pricedb/bin/pricedb-*.sh`
3. **Backup env file** (holds the repo URL + password; `600`):
   - Path A `/etc/pricedb/backup.env`, Path B `~/pricedb/backup.env`:
   ```
   RESTIC_REPOSITORY=rest:https://<REPO_ID>.repo.borgbase.com
   RESTIC_PASSWORD=<repo password>
   PRICEDB_DB=<ABSOLUTE_DB_PATH>
   PRICEDB_PY=<ABSOLUTE path to .venv/bin/python>
   HEALTHCHECK_URL=https://hc-ping.com/<PRICEDB_CHECK_UUID>
   DRILL_HEALTHCHECK_URL=https://hc-ping.com/<DRILL_CHECK_UUID>
   ```
   Perms: Path A `sudo chown root:pricedb /etc/pricedb/backup.env && sudo chmod 640` (readable by the
   service user, not world); Path B `chmod 600 ~/pricedb/backup.env`.
4. **Initialise the repo once with the FULL key** (append-only cannot init), then leave the server on the
   append-only key. See §5.1.
5. **First backup by hand**, then enable the timers:
   - Path A: `sudo -u pricedb bash -lc 'set -a; . /etc/pricedb/backup.env; set +a; /usr/local/bin/pricedb-backup.sh'`
     then `sudo systemctl enable --now pricedb-backup.timer pricedb-restore-drill.timer`
   - Path B: `set -a; . ~/pricedb/backup.env; set +a; ~/pricedb/bin/pricedb-backup.sh`
     then `systemctl --user enable --now pricedb-backup.timer pricedb-restore-drill.timer`
6. **Prove the restore** (do this before cutover): run `pricedb-restore-drill.sh` the same way. It must
   print `restore drill OK: N offers, ...`.
7. **CRM side:** clear [`restic-crm-change.md`](restic-crm-change.md) with the CRM — recommended Option 1
   keeps the price DB on its own repo/timer, so **nothing in the CRM job changes**.

**Expected:** `restic snapshots --tag pricedb` lists your first snapshot; the restore drill prints OK;
`systemctl [--user] list-timers | grep pricedb` shows the backup + drill timers scheduled.

**⟵ CHECKPOINT:** paste `restic snapshots --tag pricedb`, the restore-drill output, and the `list-timers` lines.

*Rollback:* disable the two timers (as in Step 4 rollback, with the backup/drill names); the BorgBase repo
can stay (it only holds backups). Remove the scripts/env file if abandoning.

---

## Step 6 — Cloudflare: subdomain, Access, tunnel

Do the existing [`../../SUBDOMAIN_SETUP_RUNBOOK.md`](../../SUBDOMAIN_SETUP_RUNBOOK.md) in its stated order:
**Part A** (create the Access application + shared policy) → **Part B** (Gmail as 2nd admin) →
**Part C** (tunnel ingress rule → `http://127.0.0.1:8001` on the server) → **Part D** (DNS record) →
**Part E** (apply the ingress — the one step that can blip the CRM, so a **quiet evening**) →
**Part F** (verify). From Part A you get the `aud_tag`; the `team_name` is your Zero Trust team slug —
put both into `config.toml` (Step 2) and `sudo systemctl restart pricedb.service` (Path A) /
`systemctl --user restart pricedb.service` (Path B) so the app's own JWT check matches.

**⟵ CHECKPOINT:** after Part F, paste the result of its F4 health check and confirm the CRM still loads.

*Rollback:* the subdomain runbook's **Rollback (R1/R2)** — restore the previous tunnel config + reload,
delete the `prices` DNS record. ~1 minute; the CRM is unaffected by removing the new hostname.

---

## Step 7 — Smoke test on the live URL

In a private window at `https://prices.rivieraessentials.com`, after the Access login:
- [ ] The Offers screen loads; brands/suppliers counts look right.
- [ ] Import one real supplier file (regular + a non-EUR one if you have it); review queue behaves.
- [ ] Best price, currency toggle, End offer, sourcing, two-offer compare all work.
- [ ] **Download database (Excel)** downloads `pricedb-<date>.xlsx` (one sheet per brand).
- [ ] The **backup archive** downloads too.
- [ ] `https://crm.rivieraessentials.com` still works (hard refresh + a tiny edit).

**⟵ CHECKPOINT:** tell me which boxes passed; paste anything that didn't.

---

## Step 8 — Security gate (independent)

Hand [`SECURITY_GATE.md`](SECURITY_GATE.md) to a reviewer **who did not write the code** (a separate agent
is fine). They verify the measures on the running server and report PASS/FAIL with evidence. **Fix any
FAIL before real use.** Section 1 (authentication) has no exceptions.

---

## Placeholder quick-reference
| Placeholder | Where to get it |
|---|---|
| `<REPO_URL>` | the git remote you push this repo to |
| `<YOUR_USER>` | your Linux login (Path B) |
| `<ABSOLUTE_DB_PATH>` | A: `/data/pricedb/pricedb.sqlite` · B: `/home/<YOUR_USER>/pricedb/data/pricedb.sqlite` |
| `<CF_TEAM_NAME>` | Cloudflare Zero Trust team slug (not a URL) |
| `<CF_AUD_TAG>` | Access > Applications > your app > Application Audience (AUD) Tag |
| `<MAIN_EMAIL>` / `<BACKUP_EMAIL>` | your main address + the Gmail backup |
| `<REPO_ID>` / repo password | BorgBase repo (build plan §5.1) |
| `<PRICEDB_CHECK_UUID>` / `<DRILL_CHECK_UUID>` | healthchecks.io checks you create |
