"""SQLite connection + a small numbered-migration runner (rehearsable, reversible).

Migrations live in app/migrations as NNNN_name.sql (forward) with an optional paired
NNNN_name.down.sql (rollback). Applied versions are tracked in schema_migrations, so a
later schema change is a new numbered file you can apply and roll back, not a hand-edit.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_UP_RE = re.compile(r"^(\d+)_([A-Za-z0-9_\-]+)\.sql$")
_DOWN_RE = re.compile(r"^(\d+)_([A-Za-z0-9_\-]+)\.down\.sql$")


def connect(db_path: str) -> sqlite3.Connection:
    """Open a connection with the pragmas the whole app relies on."""
    conn = sqlite3.connect(db_path, isolation_level=None)  # autocommit; we manage txns explicitly
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")      # concurrent reads + durable writes
    conn.execute("PRAGMA foreign_keys=ON")       # enforce the FKs declared in the schema
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _ensure_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  version INTEGER PRIMARY KEY,"
        "  name TEXT NOT NULL,"
        "  applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )


def applied_versions(conn: sqlite3.Connection) -> list[int]:
    _ensure_version_table(conn)
    return [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]


def _discover_up() -> list[tuple[int, str, Path]]:
    out = []
    for p in MIGRATIONS_DIR.glob("*.sql"):
        if _DOWN_RE.match(p.name):
            continue
        m = _UP_RE.match(p.name)
        if m:
            out.append((int(m.group(1)), m.group(2), p))
    return sorted(out)


def _down_path(version: int) -> Path | None:
    for p in MIGRATIONS_DIR.glob(f"{version:04d}_*.down.sql"):
        return p
    # also tolerate non-zero-padded authoring
    for p in MIGRATIONS_DIR.glob("*.down.sql"):
        m = _DOWN_RE.match(p.name)
        if m and int(m.group(1)) == version:
            return p
    return None


def migrate(conn: sqlite3.Connection, to: int | None = None) -> list[int]:
    """Apply pending forward migrations (optionally only up to version `to`).

    Each migration runs in its own transaction and is recorded on success. Returns the
    versions applied in this call.
    """
    _ensure_version_table(conn)
    done = set(applied_versions(conn))
    applied_now: list[int] = []
    for version, name, path in _discover_up():
        if version in done:
            continue
        if to is not None and version > to:
            break
        sql = path.read_text(encoding="utf-8")
        # Wrap the DDL + the version record in one transaction INSIDE the script, so the
        # whole migration is atomic. (BEGIN is embedded rather than issued via a separate
        # execute() because executescript() would otherwise commit it first.) version is an
        # int and name matches [A-Za-z0-9_-]+, so inlining them is safe.
        script = (f"BEGIN;\n{sql}\n"
                  f"INSERT INTO schema_migrations (version, name) VALUES ({version}, '{name}');\nCOMMIT;")
        try:
            conn.executescript(script)
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        applied_now.append(version)
    return applied_now


def rollback(conn: sqlite3.Connection, steps: int = 1) -> list[int]:
    """Roll back the last `steps` applied migrations using their .down.sql files."""
    _ensure_version_table(conn)
    rolled: list[int] = []
    for version in reversed(applied_versions(conn)):
        if len(rolled) >= steps:
            break
        down = _down_path(version)
        if down is None:
            raise FileNotFoundError(f"no .down.sql for migration {version}; cannot roll back")
        script = (f"BEGIN;\n{down.read_text(encoding='utf-8')}\n"
                  f"DELETE FROM schema_migrations WHERE version = {version};\nCOMMIT;")
        try:
            conn.executescript(script)
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        rolled.append(version)
    return rolled
