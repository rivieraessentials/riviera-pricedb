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
class FxConfig:
    # The daily FX fetch (app.fx). No API key. Rates are base EUR (1 EUR = rate * currency).
    symbols: list[str] = field(default_factory=lambda: ["GBP", "USD", "SGD", "CAD", "HKD"])
    timeout: float = 10.0
    frankfurter_url: str = "https://api.frankfurter.dev/v1/latest"
    ecb_url: str = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 8001
    environment: str = "development"   # 'development' | 'production'
    db_path: str = str(BACKEND_DIR / "data" / "pricedb.db")
    allowed_emails: list[str] = field(default_factory=list)
    # Origins allowed to make state-changing calls (CSRF defence). In dev the localhost
    # origins are always accepted; in production the app's own public origin must be set.
    public_origin: str = ""
    allowed_origins: list[str] = field(default_factory=list)
    access: AccessConfig = field(default_factory=AccessConfig)
    fx: FxConfig = field(default_factory=FxConfig)

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"

    def email_allowed(self, email: str | None) -> bool:
        if not email:
            return False
        return email.strip().lower() in {e.strip().lower() for e in self.allowed_emails}

    def csrf_origins(self) -> set[str]:
        """The exact Origin header values a state-changing request may carry."""
        origins = {o.strip().rstrip("/") for o in self.allowed_origins if o.strip()}
        if self.public_origin.strip():
            origins.add(self.public_origin.strip().rstrip("/"))
        if not self.is_production:
            for host in ("127.0.0.1", "localhost"):
                origins.add(f"http://{host}:{self.port}")
                origins.add(f"http://{host}")
        return origins

    def check_production_security(self) -> None:
        """Fail fast at startup if production is missing its gate (owner security point 1).

        Production MUST front the app with Cloudflare Access: JWT verification on, a team
        name and AUD tag present, and a non-empty email allow-list. A misconfigured server
        refuses to start rather than serve price data unauthenticated."""
        if not self.is_production:
            return
        problems = []
        if not self.access.enabled:
            problems.append("[access] enabled = false")
        if not self.access.team_name.strip():
            problems.append("[access] team_name is empty")
        if not self.access.aud_tag.strip():
            problems.append("[access] aud_tag is empty")
        if not self.allowed_emails:
            problems.append("[identity] allowed_emails is empty")
        if not self.public_origin.strip():
            problems.append("[server] public_origin is empty (needed for the Origin/CSRF check)")
        if problems:
            raise RuntimeError(
                "refusing to start in production without the security gate: "
                + "; ".join(problems)
                + ". Set these in config.toml (see config.example.toml) or run with "
                  "environment = 'development' locally.")


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
    fx = raw.get("fx", {})

    db_path = database.get("path", "data/pricedb.db")
    if not os.path.isabs(db_path):
        db_path = str(BACKEND_DIR / db_path)

    cfg = Config(
        host=server.get("host", "127.0.0.1"),
        port=int(server.get("port", 8001)),
        environment=server.get("environment", "development"),
        db_path=db_path,
        allowed_emails=list(identity.get("allowed_emails", [])),
        public_origin=server.get("public_origin", ""),
        allowed_origins=list(server.get("allowed_origins", [])),
        access=AccessConfig(
            enabled=bool(access.get("enabled", False)),
            team_name=access.get("team_name", ""),
            aud_tag=access.get("aud_tag", ""),
            dev_identity=access.get("dev_identity", "dev@localhost"),
        ),
        fx=FxConfig(
            symbols=list(fx.get("symbols", FxConfig().symbols)),
            timeout=float(fx.get("timeout", 10.0)),
            frankfurter_url=fx.get("frankfurter_url", FxConfig().frankfurter_url),
            ecb_url=fx.get("ecb_url", FxConfig().ecb_url),
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
    if "PRICEDB_PUBLIC_ORIGIN" in os.environ:
        cfg.public_origin = os.environ["PRICEDB_PUBLIC_ORIGIN"]

    return cfg
