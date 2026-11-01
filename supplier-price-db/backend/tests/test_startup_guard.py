"""Production refuses to start while freshness_rule is the placeholder 'all' (O2)."""
import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import AccessConfig, Config
from app.main import ConfigError, create_app


def _cfg(tmp_path, name, env):
    return Config(db_path=str(tmp_path / name), environment=env,
                  access=AccessConfig(enabled=False))


def test_production_refuses_placeholder_freshness(tmp_path):
    cfg = _cfg(tmp_path, "prod.db", "production")
    with pytest.raises(ConfigError) as err:
        with TestClient(create_app(cfg)):
            pass
    assert "O2" in str(err.value) and "freshness_rule" in str(err.value)


def test_development_allows_placeholder(tmp_path):
    cfg = _cfg(tmp_path, "dev.db", "development")
    with TestClient(create_app(cfg)) as client:
        assert client.get("/healthz").json()["environment"] == "development"


def test_production_starts_once_freshness_decided(tmp_path):
    path = str(tmp_path / "prod_ok.db")
    conn = db.connect(path)
    db.migrate(conn)
    conn.execute("UPDATE settings SET value='newest_per_supplier' WHERE key='freshness_rule'")
    conn.close()
    cfg = Config(db_path=path, environment="production", access=AccessConfig(enabled=False))
    with TestClient(create_app(cfg)) as client:          # guard passes
        assert client.get("/healthz").status_code == 200
