"""Configuration loader. The app hardcodes NOTHING operational — identity allow-list,
Cloudflare Access settings, DB path and bind address all come from config.toml (next to
the backend dir) or PRICEDB_* environment variables. See config.example.toml.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = BACKEND_DIR / "config.toml"


@dataclass
class AccessConfig:
    enabled: bool = False
    team_name: str = ""
    aud_tag: str = ""
    dev_identity: str = "dev@localhost"

    @property
    def issuer(self) -> str:
        return f"https://{self.team_name}.cloudflareaccess.com"

    @property
    def certs_url(self) -> str:
        return f"https://{self.team_name}.cloudflareaccess.com/cdn-cgi/access/certs"


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 8001
    environment: str = "development"   # 'development' | 'production'
    db_path: str = str(BACKEND_DIR / "data" / "pricedb.db")
    allowed_emails: list[str] = field(default_factory=list)
    access: AccessConfig = field(default_factory=AccessConfig)

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"

    def email_allowed(self, email: str | None) -> bool:
        if not email:
            return False
        return email.strip().lower() in {e.strip().lower() for e in self.allowed_emails}


def _load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load_config(path: str | os.PathLike | None = None) -> Config:
    """Load config from TOML, then let PRICEDB_* env vars override. All optional — a
    missing file yields safe local-dev defaults (Access OFF, empty allow-list)."""
    cfg_path = Path(path) if path else Path(os.environ.get("PRICEDB_CONFIG", DEFAULT_CONFIG_PATH))
    raw = _load_toml(cfg_path)

    server = raw.get("server", {})
    database = raw.get("database", {})
    identity = raw.get("identity", {})
    access = raw.get("access", {})

    db_path = database.get("path", "data/pricedb.db")
    if not os.path.isabs(db_path):
        db_path = str(BACKEND_DIR / db_path)

    cfg = Config(
        host=server.get("host", "127.0.0.1"),
        port=int(server.get("port", 8001)),
        environment=server.get("environment", "development"),
        db_path=db_path,
        allowed_emails=list(identity.get("allowed_emails", [])),
        access=AccessConfig(
            enabled=bool(access.get("enabled", False)),
            team_name=access.get("team_name", ""),
            aud_tag=access.get("aud_tag", ""),
            dev_identity=access.get("dev_identity", "dev@localhost"),
        ),
    )

    # Env overrides (useful for the server unit and for tests).
    if "PRICEDB_DB_PATH" in os.environ:
        cfg.db_path = os.environ["PRICEDB_DB_PATH"]
    if "PRICEDB_HOST" in os.environ:
        cfg.host = os.environ["PRICEDB_HOST"]
    if "PRICEDB_PORT" in os.environ:
        cfg.port = int(os.environ["PRICEDB_PORT"])
    if "PRICEDB_ENV" in os.environ:
        cfg.environment = os.environ["PRICEDB_ENV"]
    if "PRICEDB_ALLOWED_EMAILS" in os.environ:
        cfg.allowed_emails = [e for e in os.environ["PRICEDB_ALLOWED_EMAILS"].split(",") if e.strip()]
    if "PRICEDB_ACCESS_ENABLED" in os.environ:
        cfg.access.enabled = os.environ["PRICEDB_ACCESS_ENABLED"].lower() in ("1", "true", "yes", "on")
    if "PRICEDB_ACCESS_TEAM" in os.environ:
        cfg.access.team_name = os.environ["PRICEDB_ACCESS_TEAM"]
    if "PRICEDB_ACCESS_AUD" in os.environ:
        cfg.access.aud_tag = os.environ["PRICEDB_ACCESS_AUD"]

    return cfg
