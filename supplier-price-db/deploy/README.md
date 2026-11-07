# deploy/ — production setup for the price DB (prepare-only)

Everything here is **instructions and templates** for standing the price DB up on the server. Claude
prepared it without connecting anywhere; **you run the commands.** Start with the runbook.

| File | What it is |
|---|---|
| [`DEPLOY_RUNBOOK.md`](DEPLOY_RUNBOOK.md) | **Start here.** Every command in order, expected output, rollback per step, and ⟵ CHECKPOINTs where you paste output back. Two paths: **A** (sudo / system service) and **B** (no sudo / user service). |
| [`config.toml.example`](config.toml.example) | Production config with `<PLACEHOLDER>`s you fill (team name, AUD tag, emails, DB path). |
| `systemd/system/` | Unit + timer files for **Path A** (dedicated `pricedb` user). |
| `systemd/user/` | Unit + timer files for **Path B** (`systemctl --user`). |
| [`pricedb-backup.sh`](pricedb-backup.sh) | Nightly consistent SQLite snapshot → restic push to BorgBase. |
| [`pricedb-restore-drill.sh`](pricedb-restore-drill.sh) | Restores the latest snapshot read-only and asserts it parses. |
| [`restic-crm-change.md`](restic-crm-change.md) | Exactly what (if anything) changes in the CRM's nightly restic job — clear with the CRM side first. |
| [`SECURITY_GATE.md`](SECURITY_GATE.md) | Checklist for an **independent** reviewer to run after setup, before real use. |

Related, already in the repo:
- [`../../SUBDOMAIN_SETUP_RUNBOOK.md`](../../SUBDOMAIN_SETUP_RUNBOOK.md) — the Cloudflare Access + tunnel steps (runbook Step 6 points here).
- [`../../SUPPLIER_PRICE_DB_BUILD_PLAN.md`](../../SUPPLIER_PRICE_DB_BUILD_PLAN.md) §5.1 — creating the BorgBase repo (runbook Step 5 points here).

**Never commit real secrets:** the live `config.toml` and `backup.env` hold the allow-list / repo
password and are gitignored — keep them on the server only.
