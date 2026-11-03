"""FX fetch: parse Frankfurter JSON + ECB XML (exact text, never float), the ECB fallback,
and the manual override via the API. No network in tests — http_get is stubbed."""
from app import fx, repo
from app.config import FxConfig

FRANKFURTER = (b'{"amount":1,"base":"EUR","date":"2026-11-03",'
               b'"rates":{"USD":1.0825,"GBP":0.84385,"SGD":1.4395}}')

ECB = (b'<?xml version="1.0" encoding="UTF-8"?>'
       b'<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"'
       b' xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">'
       b'<Cube><Cube time="2026-11-03">'
       b'<Cube currency="USD" rate="1.0825"/><Cube currency="GBP" rate="0.84385"/>'
       b'<Cube currency="JPY" rate="163.5"/></Cube></Cube></gesmes:Envelope>')


def test_parse_frankfurter_keeps_exact_text_and_filters_symbols():
    date, rates = fx.parse_frankfurter(FRANKFURTER, {"USD", "GBP"})
    assert date == "2026-11-03"
    assert rates == {"USD": "1.0825", "GBP": "0.84385"}   # exact strings; SGD filtered out


def test_parse_ecb_filters_symbols():
    date, rates = fx.parse_ecb(ECB, {"USD", "GBP"})
    assert date == "2026-11-03"
    assert rates == {"USD": "1.0825", "GBP": "0.84385"}   # JPY filtered out


def test_fetch_falls_back_to_ecb_when_frankfurter_fails():
    def http_get(url, timeout):
        if "frankfurter" in url:
            raise OSError("network down")
        return ECB
    cfg = FxConfig(symbols=["USD", "GBP"],
                   frankfurter_url="https://frankfurter.example/latest",
                   ecb_url="https://ecb.example/daily.xml")
    date, rates, source = fx.fetch_latest(cfg, http_get=http_get)
    assert source == "ecb" and date == "2026-11-03" and rates["USD"] == "1.0825"


def test_update_fx_rates_writes_rows(conn):
    cfg = FxConfig(symbols=["USD", "GBP"])
    date, n, source = fx.update_fx_rates(conn, cfg, http_get=lambda url, timeout: FRANKFURTER)
    assert (date, n, source) == ("2026-11-03", 2, "frankfurter")
    assert repo.get_fx_rate(conn, "2026-11-03", "USD") == "1.0825"   # stored exactly as published


def test_manual_override_via_api(client):
    r = client.post("/fx/rates", json={"rate_date": "2026-11-03", "currency": "usd", "rate": "1.10"})
    assert r.status_code == 200, r.text
    assert r.json()["rates"]["USD"] == "1.10"                        # upper-cased, stored exactly
    got = client.get("/fx/rates", params={"rate_date": "2026-11-03"}).json()
    assert got["rates"]["USD"] == "1.10"


def test_manual_override_rejects_bad_rate(client):
    r = client.post("/fx/rates", json={"rate_date": "2026-11-03", "currency": "USD", "rate": "0"})
    assert r.status_code == 400
