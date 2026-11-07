#!/usr/bin/env bash
# Nightly off-site backup of the price DB to BorgBase via restic.
# Driven entirely by env vars (set in the backup.env EnvironmentFile; see DEPLOY_RUNBOOK.md
# and the build plan 5.1). The server holds an APPEND-ONLY restic key, so this script only
# *adds* snapshots — it never prunes. Retention runs off-box with the full key.
set -euo pipefail

: "${PRICEDB_DB:?set PRICEDB_DB (e.g. /data/pricedb/pricedb.sqlite)}"
: "${PRICEDB_PY:?set PRICEDB_PY (path to the venv python)}"
: "${RESTIC_REPOSITORY:?set RESTIC_REPOSITORY}"
: "${RESTIC_PASSWORD:?set RESTIC_PASSWORD}"
export RESTIC_REPOSITORY RESTIC_PASSWORD

SNAP="$(dirname "$PRICEDB_DB")/pricedb-snap.sqlite"

# 1) Consistent snapshot (safe under WAL) via SQLite's own backup API — never copy the live file.
"$PRICEDB_PY" - "$PRICEDB_DB" "$SNAP" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(src); d = sqlite3.connect(dst)
try:
    with d:
        s.backup(d)
finally:
    d.close(); s.close()
PY

# 2) Push ONLY the snapshot off-site (append-only key allows backup, not forget/prune).
restic backup "$SNAP" --tag pricedb --host prices

# 3) Success ping so a missed/failed night alerts you by email (healthchecks.io). Optional.
if [ -n "${HEALTHCHECK_URL:-}" ]; then curl -fsS --retry 3 "$HEALTHCHECK_URL" >/dev/null || true; fi

echo "pricedb-backup: done ($(date -u +%FT%TZ))"
