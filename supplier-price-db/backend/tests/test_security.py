"""Security measures built into Step B (owner points 1-5): the production Access gate,
XSS-safe data handling, the Origin / JSON / size guard, server-side validation, and the CSP.
"""
import pytest

from app.config import AccessConfig, Config
from app.main import create_app

from .synth import make_ean

XSS = '<script>alert(1)</script><img src=x onerror="alert(2)">'


# ---- point 1: production refuses to start without the Access gate ----

def test_production_without_access_refuses_to_start(tmp_path):
    cfg = Config(db_path=str(tmp_path / "p.db"), environment="production",
                 allowed_emails=["a@example.com"], public_origin="https://prices.example.com",
                 access=AccessConfig(enabled=False))
    with pytest.raises(RuntimeError, match="security gate"):
        create_app(cfg)


def test_production_missing_aud_refuses_to_start(tmp_path):
    cfg = Config(db_path=str(tmp_path / "p.db"), environment="production",
                 allowed_emails=["a@example.com"], public_origin="https://prices.example.com",
                 access=AccessConfig(enabled=True, team_name="riviera", aud_tag=""))
    with pytest.raises(RuntimeError, match="aud_tag"):
        create_app(cfg)


def test_production_fully_configured_starts(tmp_path):
    cfg = Config(db_path=str(tmp_path / "p.db"), environment="production",
                 allowed_emails=["a@example.com"], public_origin="https://prices.example.com",
                 access=AccessConfig(enabled=True, team_name="riviera", aud_tag="aud123"))
    assert create_app(cfg) is not None            # all gate conditions satisfied


# ---- point 2: supplier-file data is never interpreted as HTML by the server ----

def test_xss_payload_round_trips_verbatim(client):
    """The server stores and returns dangerous text EXACTLY; the frontend escapes it on render."""
    sid = client.post("/suppliers", json={"name": "Acme"}).json()["supplier_id"]
    ean = make_ean("510000000001")
    b = client.post("/batches", json={"supplier_id": sid, "currency": "EUR", "kind": "price_list",
                                      "brand": "Wella", "fx_rate_eur": "1"}).json()
    client.post("/offers", json={"batch_id": b["batch_id"], "brand_mode": "single", "brand": "Wella",
                                 "offers": [{"ean": ean, "quoted_price": "5.00", "description": XSS}]})
    got = client.get(f"/products/{ean}/offers").json()
    assert got["description"] == XSS              # unchanged — not stripped, not escaped server-side
    # and it travels through the grid payload unchanged too
    row = client.get("/grid", params={"brand": "Wella"}).json()["rows"][0]
    assert row["description"] == XSS


# ---- point 4: Origin + JSON guard on state-changing requests ----

def test_cross_origin_post_refused(client):
    r = client.post("/suppliers", json={"name": "X"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_same_origin_post_allowed(client):
    r = client.post("/suppliers", json={"name": "Y"}, headers={"Origin": "http://127.0.0.1:8001"})
    assert r.status_code == 200


def test_get_is_not_origin_checked(client):
    assert client.get("/suppliers", headers={"Origin": "https://evil.example"}).status_code == 200


def test_non_json_body_refused(client):
    r = client.post("/suppliers", content="name=X", headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415


# ---- point 3: server-side validation ----

def test_bad_ean_rejected(client):
    assert client.post("/products", json={"ean": "not-a-number", "brand": "Wella"}).status_code == 422
    assert client.post("/products", json={"ean": "123", "brand": "Wella"}).status_code == 422


def test_bad_price_rejected(client):
    ean = make_ean("510000000018")
    r = client.post("/requests", json={"margin": "0.07",
                                       "lines": [{"ean": ean, "target_price": "-5", "currency": "EUR"}]})
    assert r.status_code == 422


def test_margin_out_of_range_rejected(client):
    ean = make_ean("510000000025")
    r = client.post("/requests", json={"margin": "1.5",
                                       "lines": [{"ean": ean, "target_price": "5", "currency": "EUR"}]})
    assert r.status_code == 422


def test_too_many_request_lines_rejected(client):
    ean = make_ean("510000000032")
    lines = [{"ean": ean, "target_price": "1", "currency": "EUR"} for _ in range(5001)]
    assert client.post("/requests", json={"margin": "0.07", "lines": lines}).status_code == 413


# ---- point 5: the CSP and hardening headers are present ----

def test_csp_and_security_headers(client):
    h = client.get("/healthz").headers
    csp = h.get("content-security-policy", "")
    assert "script-src 'self'" in csp and "default-src 'self'" in csp
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]   # not on scripts
    assert h.get("x-content-type-options") == "nosniff"
    assert h.get("x-frame-options") == "DENY"
