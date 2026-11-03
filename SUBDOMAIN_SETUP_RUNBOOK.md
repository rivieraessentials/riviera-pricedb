# prices.rivieraessentials.com — stand-up runbook (Cloudflare Access + tunnel route)

Puts the price DB online at **`prices.rivieraessentials.com`**, behind the **same Cloudflare Access login
as the CRM**, through the **same Cloudflare Tunnel** — with **two sign-in emails** (your main + a Gmail
backup on a different provider) and the Gmail also a **second admin** on the Cloudflare account.

**The order is deliberate: the Access application is created FIRST, before any DNS/route exists, so the
subdomain is never reachable without a login — not for a single second.**

> **Nothing in this runbook has been run.** It is written from a **read-only** probe of your server
> (2026‑10‑31). Server facts below are real (not placeholders); the only placeholders are your **two email
> addresses**. Each step is tagged **[you — Cloudflare dashboard]** or **[on the server]** (copy‑paste over
> `ssh tedj@178.105.34.50`; the root‑owned `/etc/cloudflared` steps use `sudo`, so they use `ssh -t`).

### Facts this runbook relies on (verified 2026‑10‑31, read‑only)
| Thing | Value |
|---|---|
| Tunnel (config‑file managed) | `tunnel: 75e6d371-cfea-4fa9-815d-947e541e1909`, config at `/etc/cloudflared/config.yml` |
| cloudflared | systemd `cloudflared.service`, enabled + active (survives reboot) |
| Current ingress | `crm.rivieraessentials.com → http://127.0.0.1:8000`, then a `http_status:404` catch‑all |
| Price DB will listen on | `http://127.0.0.1:8001` (its own app — Build Plan Phases 1–2) |
| Server | `178.105.34.50`, Ubuntu 24.04, Python 3.12.3, 61 GB free |

### Placeholders you fill
- `MAIN_EMAIL` = your main address (the one already on the CRM's Access policy).
- `BACKUP_EMAIL` = your Gmail backup address (different provider).

### Prerequisite
The price DB app should be **running on `127.0.0.1:8001`** (Build Plan Phases 1–2) for the subdomain to
return the app. You *can* run this runbook before the app exists — you'll then see the **Access login**,
and after it a `502` (nothing on :8001 yet). That still proves Access + routing; the app fills in later.

---

## Part A — Create the Access application FIRST (before any DNS/route)  **[you — Cloudflare dashboard]**

Cloudflare **Zero Trust** → **Access → Applications**.

**A1.** First, so both apps can share one rule: **Access → Policies → Add a policy** (a *reusable* policy).
- Name: `Riviera owners`.
- Action: **Allow**.
- Include → **Emails** → add **both**: `MAIN_EMAIL` **and** `BACKUP_EMAIL`.
- Save. *(If the CRM today uses an inline email rule instead of a reusable policy, this is the moment to
  switch it to this shared policy — see Part F's CRM check that it still works.)*

**A2.** **Add an application → Self-hosted.**
- Application name: `Riviera Price DB`.
- Session duration: match the CRM's (e.g. 24h).
- **Public hostname:** subdomain `prices`, domain `rivieraessentials.com` (path blank).
- **Policies:** attach the **`Riviera owners`** policy from A1.
- Save.

✅ The Access app now guards `prices.rivieraessentials.com`. **There is still no DNS record and no tunnel
route**, so the hostname resolves to nothing yet — exactly what we want until Access is in place.

**A3. (apply the same policy to the CRM, so it's one shared rule)** Open the existing **CRM** Access
application → **Policies** → ensure it uses the **`Riviera owners`** policy (so both emails work on the CRM
too). Don't remove your current access until Part F confirms the new policy works.

---

## Part B — Add the Gmail as a second admin on the Cloudflare account  **[you — Cloudflare dashboard]**

This is account-level recovery (separate from who can log into the apps): if your main login is ever lost,
the Gmail can still administer Cloudflare.

**B1.** **Manage Account → Members → Invite** → `BACKUP_EMAIL` → role **Administrator** (or
**Super Administrator** if you want full parity) → send invite.
**B2.** Accept the invite from the Gmail inbox, then **turn on 2FA** for that member and **save its backup
codes** (password manager + sealed offline copy — see the recovery checklist in
[BACKUP_SYSTEM.md](BACKUP_SYSTEM.md)).

✅ Two people/identities can now administer Cloudflare; a single mailbox loss no longer locks you out.

---

## Part C — Add the tunnel ingress rule for the subdomain  **[on the server]**

This points `prices.rivieraessentials.com` at the price DB on `:8001`, **before** the catch‑all `404`.
It writes a dated backup of `config.yml` first and rewrites it to the exact content below (tiny file).

```bash
ssh -t tedj@178.105.34.50 'sudo cp /etc/cloudflared/config.yml /etc/cloudflared/config.yml.bak.$(date +%Y%m%d-%H%M) && sudo tee /etc/cloudflared/config.yml >/dev/null <<"YML"
tunnel: 75e6d371-cfea-4fa9-815d-947e541e1909
credentials-file: /etc/cloudflared/75e6d371-cfea-4fa9-815d-947e541e1909.json

ingress:
  - hostname: crm.rivieraessentials.com
    service: http://127.0.0.1:8000
  - hostname: prices.rivieraessentials.com
    service: http://127.0.0.1:8001
  - service: http_status:404
YML
echo WRITTEN'
```
- ✅ You should see `WRITTEN`.

**C2. Validate the config before it's applied** (changes nothing live):
```bash
ssh tedj@178.105.34.50 'cloudflared tunnel ingress validate && echo "--- route check ---" && cloudflared tunnel ingress rule https://prices.rivieraessentials.com'
```
- ✅ You should see the config is **valid**, and the rule check resolves `prices.rivieraessentials.com` to
  `http://127.0.0.1:8001`. **If validation fails, do NOT restart cloudflared** — restore the backup
  (`sudo cp /etc/cloudflared/config.yml.bak.* /etc/cloudflared/config.yml`) and tell Claude Code.

---

## Part D — Add the DNS route for the subdomain  **[you — Cloudflare dashboard]**

**D1.** **DNS → Records → Add record:**
- Type **CNAME**, Name **`prices`**,
- Target **`75e6d371-cfea-4fa9-815d-947e541e1909.cfargotunnel.com`**,
- **Proxied** (orange cloud) — this is what makes Cloudflare (and therefore Access) sit in front.
- Save.

✅ `prices.rivieraessentials.com` now resolves through Cloudflare. Because Part A is already in place, the
very first request is gated by the Access login. *(The route won't carry traffic to the app until Part E
applies the new ingress — so order D then E, or E then D; both end in the same place. Access is enforced
either way.)*

---

## Part E — Apply the new ingress (the ONE step that can blip the CRM)  **[on the server — scheduled]**

⚠️ **This is the only step that can briefly interrupt the CRM.** Reloading/restarting cloudflared re-reads
the ingress. **Schedule it for a quiet evening** (you said today or tomorrow evening). Tell anyone using the
CRM it may blink for a few seconds.

**E1. Prefer a reload (no dropped tunnel if your cloudflared honours SIGHUP):**
```bash
ssh -t tedj@178.105.34.50 'sudo systemctl reload cloudflared && sleep 2 && echo RELOADED'
```
Immediately check the CRM stayed up:
```bash
ssh tedj@178.105.34.50 'curl -s http://127.0.0.1:8000/healthz'
```
- ✅ `RELOADED`, then `ok`. If the CRM is fine and the new route works (Part F), you're done — **no restart
  needed**.

**E2. Only if reload didn't pick up the new hostname** (Part F still 404s for prices), restart — a
few‑seconds CRM blip, so do it in the scheduled window:
```bash
ssh -t tedj@178.105.34.50 'sudo systemctl restart cloudflared && sleep 3 && systemctl is-active cloudflared && curl -s http://127.0.0.1:8000/healthz'
```
- ✅ `active` then `ok` (CRM back within a few seconds).

---

## Part F — Verify  **[you — browser + a couple of server checks]**

**F1. Access login appears (private window):** open a **private/incognito** window →
`https://prices.rivieraessentials.com`.
- ✅ You see the **Cloudflare Access login**, not the app and not an error. (If the app isn't built yet,
  after logging in you'll see a `502` — that's expected; Access + routing are proven.)

**F2. The second email works:** complete the Access login using **`BACKUP_EMAIL`** (the Gmail).
- ✅ It's accepted and you reach the app (or the expected `502`). Repeat once with `MAIN_EMAIL` to confirm
  both.

**F3. The CRM still works:** open `https://crm.rivieraessentials.com` (hard refresh), make a tiny edit,
confirm it still saves; and:
```bash
ssh tedj@178.105.34.50 'curl -s http://127.0.0.1:8000/healthz'
```
- ✅ CRM loads, saves, and `healthz` returns `ok`. The shared policy didn't lock you out of the CRM.

**F4. (once the price DB is running on :8001)** app health:
```bash
ssh tedj@178.105.34.50 'curl -s http://127.0.0.1:8001/healthz'
```
- ✅ `ok`, and `prices.rivieraessentials.com` shows the app after login.

---

## Rollback (undo cleanly) — ~1 minute

**R1. [on the server]** restore the previous tunnel config and reload:
```bash
ssh -t tedj@178.105.34.50 'sudo cp "$(ls -t /etc/cloudflared/config.yml.bak.* | head -1)" /etc/cloudflared/config.yml && sudo systemctl reload cloudflared && sleep 2 && curl -s http://127.0.0.1:8000/healthz'
```
- ✅ `ok` — the CRM is back on the original single‑hostname config.

**R2. [you — dashboard]** delete the `prices` **CNAME** (DNS → Records). Optionally delete the
`Riviera Price DB` **Access application** (harmless to leave; with no DNS it guards nothing). Leave the
`Riviera owners` policy and the second admin member in place — those are improvements worth keeping.

---

## Optional — identity defence‑in‑depth (not required to go live)

Today **neither app verifies identity in code** — Cloudflare Access is the sole gate (tracked as
**LED‑1083**; the CRM does **not** validate the `Cf-Access-Jwt-Assertion` header today). When the price DB
backend is built, it can verify that JWT on each `/api/*` request (signature against Access's public keys +
expected `aud`) and check the email against an **allow‑list in config holding `MAIN_EMAIL` and
`BACKUP_EMAIL` — never hardcoded**. That way a compromised/misconfigured tunnel still can't reach the data,
and changing the allowed emails stays a config edit. This is a build‑time add‑on, listed here so it isn't
forgotten.

---

---

## Server hardening checklist (security point 6, owner 2026-11-03)

Apply at server setup (step D), verify at the Security gate (step E):

1. **Identity is now enforced in code too** (no longer just the tunnel). Set in `config.toml` on the server:
   `[server] environment = "production"`, `public_origin = "https://prices.rivieraessentials.com"`;
   `[access] enabled = true`, `team_name`, `aud_tag`; `[identity] allowed_emails = [MAIN, BACKUP]`. The app
   **refuses to start** if any of these is missing in production (`config.check_production_security()`), so a
   misconfigured box fails closed instead of serving data unauthenticated.
2. **Run as a non-root user.** The systemd unit runs as a dedicated service user (e.g. `pricedb`), not root:
   `User=pricedb`, `Group=pricedb`, plus `NoNewPrivileges=true`, `ProtectSystem=strict`, `ProtectHome=true`,
   `PrivateTmp=true`, and `ReadWritePaths=/data/pricedb`.
3. **Data file permissions.** `/data/pricedb` owned by `pricedb:pricedb`, mode `0700` on the dir and `0600` on
   `pricedb.db*` — readable only by that user.
4. **Restart + logs.** `Restart=on-failure`. Access logs record method, path and status **only** — never row
   contents or request bodies (the app logs no offer data; keep uvicorn access logging at defaults).
5. **Backups.** Add `/data/pricedb` to the existing **restic** schedule and run a **restore drill** (restore to a
   scratch dir, open the DB, confirm `SELECT count(*) FROM offers`) before cutover, then periodically.
6. **The download bundle is the whole database** — keep it only on the encrypted drive (the UI says so by the
   button). Never commit it or any real supplier/customer file to the repo.

---

**In one line:** create the Access app + shared two‑email policy and the second Cloudflare admin **first**
(dashboard), add the tunnel ingress for `:8001` and validate (server), add the proxied DNS CNAME
(dashboard), then **reload cloudflared in a quiet window** (the only possible CRM blip), and verify the
Access login shows, the Gmail logs in, and the CRM still works — with a one‑minute rollback.

---

*🤖 Drafted with [Claude Code](https://claude.com/claude-code)*
