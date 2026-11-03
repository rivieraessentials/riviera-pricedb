"""The migration mechanism is rehearsable and reversible, including 0003_decided_schema."""
from app import db


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_migrate_is_idempotent(tmp_path):
    conn = db.connect(str(tmp_path / "m.db"))
    applied = db.migrate(conn)
    assert applied == [1, 2, 3]
    assert db.migrate(conn) == []              # nothing left to apply
    assert db.applied_versions(conn) == [1, 2, 3]
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("brands", "suppliers", "products", "offer_batches", "offers", "fx_rates",
              "current_best", "settings", "review_queue", "schema_migrations"):
        assert t in names
    conn.close()


def test_rollback_then_reapply(tmp_path):
    conn = db.connect(str(tmp_path / "m.db"))
    db.migrate(conn)
    # 0003 shaped the schema to the decided columns
    assert "brand" in _cols(conn, "offer_batches")
    assert "raw_row" not in _cols(conn, "offers")
    assert "size" not in _cols(conn, "products")

    db.rollback(conn, steps=1)                 # reversible: drops only 0003
    assert db.applied_versions(conn) == [1, 2]
    assert "brand" not in _cols(conn, "offer_batches")
    assert "raw_row" in _cols(conn, "offers")  # 0001 structure restored

    db.migrate(conn)                           # re-apply cleanly
    assert db.applied_versions(conn) == [1, 2, 3]
    assert "brand" in _cols(conn, "offer_batches")
    conn.close()


def test_settings_after_decided_schema(tmp_path):
    conn = db.connect(str(tmp_path / "m.db"))
    db.migrate(conn)
    keys = {r[0] for r in conn.execute("SELECT key FROM settings")}
    assert "freshness_rule" not in keys and "freshness_days" not in keys   # O2 decided, removed
    basis = conn.execute("SELECT value FROM settings WHERE key='fx_display_basis'").fetchone()[0]
    assert basis == "today"                    # O4 decided: default today's rate
    conn.close()
