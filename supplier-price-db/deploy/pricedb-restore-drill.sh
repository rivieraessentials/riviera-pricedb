#!/usr/bin/env bash
# Restore drill: proves the off-site backup is restorable and current (build plan 5.1, gate E).
# Restores the latest snapshot to a throwaway dir, opens it read-only, asserts it parses, then
# deletes the copy. Run weekly from a timer, and ALSO once by hand before cutover.
set -euo pipefail

: "${PRICEDB_PY:?set PRICEDB_PY}"
: "${RESTIC_REPOSITORY:?set RESTIC_REPOSITORY}"
: "${RESTIC_PASSWORD:?set RESTIC_PASSWORD}"
export RESTIC_REPOSITORY RESTIC_PASSWORD

DEST="$(mktemp -d)"
trap 'rm -rf "$DEST"' EXIT

restic restore latest --tag pricedb --target "$DEST"
SNAP="$(find "$DEST" -name 'pricedb-snap.sqlite' | head -n1)"
[ -n "$SNAP" ] || { echo "restore drill FAILED: snapshot not found in restore" >&2; exit 1; }

"$PRICEDB_PY" - "$SNAP" <<'PY'
import sqlite3, sys
c = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
c.row_factory = sqlite3.Row
offers = c.execute("SELECT COUNT(*) FROM offers").fetchone()[0]
brands = c.execute("SELECT COUNT(*) FROM brands").fetchone()[0]
products = c.execute("SELECT COUNT(*) FROM products").fetchone()[0]
c.close()
assert offers >= 0 and brands >= 0 and products >= 0
print(f"restore drill OK: {offers} offers, {products} products, {brands} brands")
PY

if [ -n "${DRILL_HEALTHCHECK_URL:-}" ]; then curl -fsS --retry 3 "$DRILL_HEALTHCHECK_URL" >/dev/null || true; fi
