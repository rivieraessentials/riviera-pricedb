import pytest

from app import db
from app.config import AccessConfig, Config


@pytest.fixture
def conn(tmp_path):
    """A migrated, isolated SQLite DB per test."""
    c = db.connect(str(tmp_path / "test.db"))
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def client(tmp_path):
    """FastAPI TestClient on a temp DB, Cloudflare Access OFF (local-dev default)."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    cfg = Config(
        db_path=str(tmp_path / "api.db"),
        allowed_emails=[],
        access=AccessConfig(enabled=False, dev_identity="dev@localhost"),
    )
    with TestClient(create_app(cfg)) as c:
        yield c
