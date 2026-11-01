"""The migration mechanism is rehearsable and reversible."""
from app import db


def test_migrate_is_idempotent(tmp_path):
    conn = db.connect(str(tmp_path / "m.db"))
    applied = db.migrate(conn)
    assert 1 in applied
    assert db.migrate(conn) == []              # nothing left to apply
    assert db.applied_versions(conn) == [1]
    # all nine tables exist
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("brands", "suppliers", "products", "offer_batches", "offers",
              "fx_rates", "current_best", "settings", "schema_migrations"):
        assert t in names
    conn.close()


def test_rollback_then_reapply(tmp_path):
    conn = db.connect(str(tmp_path / "m.db"))
    db.migrate(conn)
    db.rollback(conn, steps=1)                 # reversible
    assert db.applied_versions(conn) == []
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "offers" not in names               # tables dropped
    db.migrate(conn)                           # re-apply cleanly
    assert db.applied_versions(conn) == [1]
    conn.close()


def test_settings_seeded_with_placeholder_freshness(tmp_path):
    conn = db.connect(str(tmp_path / "m.db"))
    db.migrate(conn)
    val = conn.execute("SELECT value FROM settings WHERE key='freshness_rule'").fetchone()[0]
    assert val == "all"                        # PLACEHOLDER until O2 is decided
    conn.close()
