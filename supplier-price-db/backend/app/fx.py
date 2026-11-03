"""Daily FX fetch (base EUR) — Frankfurter primary, ECB XML fallback. No API key.

Rate convention (design §1.1 / §4.2): 1 EUR = rate * currency, so price_eur =
price_in_currency / rate. Rates are stored as TEXT exactly as published (O5 §12): the
JSON/XML number tokens are parsed as strings and never round-tripped through float.

Scheduled server-side job (design §4.2 — a systemd timer / cron, like the nightly backup):

    python -m app.fx

It writes one fx_rates row per currency for the published date. A manual override for a day
(the weekend/holiday escape hatch) is written via the API (POST /fx/rates) or
repo.set_fx_rate(..., source="manual"). The per-batch override (offer_batches.fx_rate_eur,
fx_locked) is separate and still wins for that batch's rows.
"""
from __future__ import annotations

import json
import sys
import urllib.request
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation

from . import db, repo
from .config import FxConfig, load_config

_UA = {"User-Agent": "riviera-pricedb/0.2 (+fx daily fetch)"}


def _http_get(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # fixed ECB/Frankfurter hosts
        return resp.read()


def _valid_rate(text: str) -> bool:
    try:
        return Decimal(text) > 0
    except (InvalidOperation, ValueError):
        return False


def parse_frankfurter(raw: bytes, symbols) -> tuple[str, dict[str, str]]:
    # parse_float=str keeps the published token exactly ("1.5935"), never a float.
    data = json.loads(raw.decode("utf-8"), parse_float=str, parse_int=str)
    date = data["date"]
    out = {cur: str(rate) for cur, rate in (data.get("rates") or {}).items()
           if cur in symbols and _valid_rate(str(rate))}
    return date, out


def parse_ecb(raw: bytes, symbols) -> tuple[str | None, dict[str, str]]:
    root = ET.fromstring(raw)
    date: str | None = None
    out: dict[str, str] = {}
    for cube in root.iter():
        if cube.tag.rsplit("}", 1)[-1] != "Cube":
            continue
        if "time" in cube.attrib:
            date = cube.attrib["time"]
        cur, rate = cube.attrib.get("currency"), cube.attrib.get("rate")
        if cur and rate and cur in symbols and _valid_rate(rate):
            out[cur] = rate
    return date, out


def fetch_latest(cfg: FxConfig, *, http_get=_http_get) -> tuple[str | None, dict[str, str], str]:
    """Return (date, {currency: rate_text}, source). Frankfurter first; ECB on any failure."""
    symbols = set(cfg.symbols)
    try:
        url = f"{cfg.frankfurter_url}?base=EUR&symbols={','.join(cfg.symbols)}"
        date, rates = parse_frankfurter(http_get(url, cfg.timeout), symbols)
        if rates:
            return date, rates, "frankfurter"
    except Exception:
        pass
    date, rates = parse_ecb(http_get(cfg.ecb_url, cfg.timeout), symbols)
    return date, rates, "ecb"


def write_rates(conn, date: str, rates: dict[str, str], source: str) -> int:
    conn.execute("BEGIN")
    try:
        for cur, rate in rates.items():
            repo.set_fx_rate(conn, date, cur, rate, source)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return len(rates)


def update_fx_rates(conn, cfg: FxConfig, *, http_get=_http_get) -> tuple[str | None, int, str]:
    date, rates, source = fetch_latest(cfg, http_get=http_get)
    if not date:
        raise RuntimeError("FX fetch returned no date")
    return date, write_rates(conn, date, rates, source), source


def main(argv=None) -> int:
    cfg = load_config()
    conn = db.connect(cfg.db_path)
    try:
        date, n, source = update_fx_rates(conn, cfg.fx)
    finally:
        conn.close()
    print(f"fx: wrote {n} rate(s) for {date} from {source}")
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main())
