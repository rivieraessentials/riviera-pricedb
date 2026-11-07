#!/usr/bin/env bash
# INTERIM on-server backup: a consistent nightly SQLite snapshot kept LOCALLY on the box.
#
# This is the stop-gap while the off-site BorgBase repo is unavailable (see DEPLOY_RUNBOOK.md Step 5).
# It protects against MISTAKES (a bad import, an accidental delete) — it does NOT protect against
# losing the server itself. Off-site protection (BorgBase, or the CRM restic job) must still be added
# within the two-week deadline recorded in the build plan.
#
# Driven by env vars (set in the systemd unit): never copy the live DB file — use SQLite's backup API,
# which is safe under WAL.
set -euo pipefail

: "${PRICEDB_DB:?set PRICEDB_DB (e.g. /data/pricedb/pricedb.sqlite)}"
: "${PRICEDB_PY:?set PRICEDB_PY (path to the venv python)}"
KEEP_DAYS="${PRICEDB_SNAPSHOT_KEEP_DAYS:-14}"

SNAP_DIR="$(dirname "$PRICEDB_DB")/snapshots"
mkdir -p "$SNAP_DIR"
chmod 750 "$SNAP_DIR"                               # same reach as the data dir: owner + group only

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$SNAP_DIR/pricedb-$STAMP.sqlite"

# 1) Consistent snapshot via SQLite's own backup API (safe under WAL) — never copy the live file.
"$PRICEDB_PY" - "$PRICEDB_DB" "$OUT" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(src); d = sqlite3.connect(dst)
try:
    with d:
        s.backup(d)
finally:
    d.close(); s.close()
PY

chmod 640 "$OUT"                                   # same perms as the DB (not world-readable)

# 2) Prune snapshots older than KEEP_DAYS so the dir doesn't grow without bound.
find "$SNAP_DIR" -maxdepth 1 -name 'pricedb-*.sqlite' -type f -mtime +"$KEEP_DAYS" -delete

KEPT="$(find "$SNAP_DIR" -maxdepth 1 -name 'pricedb-*.sqlite' -type f | wc -l)"
echo "pricedb-snapshot: wrote $OUT; ${KEPT} snapshot(s) kept (<= ${KEEP_DAYS}d) at $(date -u +%FT%TZ)"
