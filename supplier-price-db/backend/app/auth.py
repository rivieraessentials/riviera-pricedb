"""Cloudflare Access JWT verification — DEFENCE IN DEPTH, OFF BY DEFAULT in local dev.

Cloudflare Access is the real gate in front of this app; verifying the signed Access
JWT at the origin is a second line so a direct hit (e.g. a tunnel misconfig) still can't
reach the data. Verified against Cloudflare's official docs (2026-11-01):

  - token arrives in the request header `Cf-Access-Jwt-Assertion` (RS256)
  - public keys (JWKS): https://<team>.cloudflareaccess.com/cdn-cgi/access/certs
  - issuer:            https://<team>.cloudflareaccess.com
  - audience:          the Access application's Application Audience (AUD) tag
  - claims checked:    aud == aud_tag, iss == issuer, exp (not expired); then the
                       identity's email must be in the config allow-list.

When config.access.enabled is false (local dev), no token is required and the configured
dev_identity is used. PyJWT is imported lazily so the app runs without it when Access is off.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request, status

ACCESS_HEADER = "Cf-Access-Jwt-Assertion"
_JWKS_CLIENTS: dict[str, object] = {}  # certs_url -> PyJWKClient (cached per process)


@dataclass
class Identity:
    email: str
    source: str           # 'cloudflare-access' | 'dev'
    claims: dict | None = None


def _jwks_client(certs_url: str):
    import jwt  # PyJWT, lazy
    client = _JWKS_CLIENTS.get(certs_url)
    if client is None:
        client = jwt.PyJWKClient(certs_url)
        _JWKS_CLIENTS[certs_url] = client
    return client


def verify_access_token(token: str, access_cfg) -> dict:
    """Verify a Cloudflare Access JWT and return its claims, or raise ValueError."""
    import jwt  # PyJWT, lazy
    if not access_cfg.team_name or not access_cfg.aud_tag:
        raise ValueError("Access verification needs team_name and aud_tag in config")
    try:
        signing_key = _jwks_client(access_cfg.certs_url).get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=access_cfg.aud_tag,
            issuer=access_cfg.issuer,
            options={"require": ["exp", "iss", "aud"]},
        )
    except Exception as exc:  # PyJWT raises several subclasses; treat all as auth failure
        raise ValueError(f"invalid Access token: {exc}") from exc


def require_identity(request: Request) -> Identity:
    """FastAPI dependency. Returns the caller's Identity or raises 401/403.

    OFF by default: when access.enabled is false this returns the dev identity without
    requiring a token (local development only)."""
    cfg = request.app.state.config
    access = cfg.access

    if not access.enabled:
        return Identity(email=access.dev_identity, source="dev")

    token = request.headers.get(ACCESS_HEADER)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=f"missing {ACCESS_HEADER}")
    try:
        claims = verify_access_token(token, access)
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    email = claims.get("email") or claims.get("identity") or ""
    if not cfg.email_allowed(email):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="email not in allow-list")
    return Identity(email=email, source="cloudflare-access", claims=claims)
