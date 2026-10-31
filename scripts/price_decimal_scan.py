#!/usr/bin/env python3
"""Read-only: max decimals in PRICE columns across supplier files (.xlsx/.xls/.csv).

Usage:
    python -I scripts/price_decimal_scan.py <folder> [--col "SUBSTRING"]

<folder> should contain ONLY supplier files. --col limits to columns whose header
contains SUBSTRING (case-insensitive); without it, price columns are auto-detected by
header keyword (any header containing a price word counts — "Price/box" and "Pack price"
included; packaging words never exclude a header that has a price word).

Each value is rounded to 8 decimals and trailing zeros stripped before counting, so
formula float-noise (11.253000000000002) does not overstate decimals. Prices stored as
TEXT (e.g. "12,50") are parsed too, for all three formats. Values like "1.234" / "1,234"
are AMBIGUOUS (thousands vs decimals) and are listed, not counted. When the max is 5+,
the top example values are shown and flagged as likely Excel-calculated. Writes nothing.

Needs: openpyxl (.xlsx), xlrd (.xls). CSV uses the stdlib. Install what you use:
    pip install openpyxl xlrd
"""
import os, glob, csv, argparse, re
from decimal import Decimal, InvalidOperation

PRICE_KW = ["price", "prix", "prezzo", "precio", "preis", "cena", "prijs", "eur", "usd",
            "gbp", "pln", "net", "wholesale", "pvp", "rrp", "cost", "tarif"]
AMBIG = re.compile(r"^-?\d{1,3}[.,]\d{3}$")   # one separator, exactly 3 trailing digits

def is_price_header(txt):
    # A column is a price column iff its header contains a price word. Packaging words
    # (box/pack/pcs) do NOT exclude it — "Price/box" and "Pack price" are prices.
    t = (txt or "").strip().lower()
    return bool(t) and any(k in t for k in PRICE_KW)

def clean(s):
    return "".join(ch for ch in str(s) if ch.isdigit() or ch in ".,-").strip()

def is_ambiguous(s):
    return bool(AMBIG.fullmatch(clean(s)))

def parse_text_num(s):
    t = clean(s)
    if not t or t in "-.,":
        return None
    if "," in t and "." in t:                      # rightmost separator is the decimal
        d = max(t.rfind(","), t.rfind("."))
        t = t[:d].replace(",", "").replace(".", "") + "." + t[d+1:]
    elif "," in t:                                  # lone comma -> decimal
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None

def dec_count(v):
    try:
        d = Decimal(str(round(float(v), 8))).normalize()
    except (InvalidOperation, ValueError, TypeError):
        return None
    exp = d.as_tuple().exponent
    return max(0, -exp) if isinstance(exp, int) else None

def value_of(cell):
    """Return (decimals, value, 'ambiguous'|None). Numbers and numeric TEXT both parsed."""
    if isinstance(cell, bool) or cell is None:
        return None, None, None
    if isinstance(cell, (int, float)):
        return dec_count(cell), cell, None
    if isinstance(cell, str):
        if cell.strip() == "":
            return None, None, None
        if is_ambiguous(cell):
            return None, cell, "ambiguous"
        n = parse_text_num(cell)
        return (dec_count(n), n, None) if n is not None else (None, None, None)
    return None, None, None

def pick_header(rows, col_sub):
    best_i, best_cols, best_score = None, [], 0
    for i, row in enumerate(rows[:15]):
        cells = [("" if c is None else str(c)) for c in row]
        cols = ([j for j, c in enumerate(cells) if col_sub.lower() in c.lower()] if col_sub
                else [j for j, c in enumerate(cells) if is_price_header(c)])
        if len(cols) > best_score:
            best_i, best_cols, best_score = i, cols, len(cols)
    return best_i, best_cols

def scan_rows(rows, col_sub):
    hi, cols = pick_header(rows, col_sub)
    if hi is None or not cols:
        return None
    headers = [str(rows[hi][c]) if c < len(rows[hi]) and rows[hi][c] is not None else "?" for c in cols]
    pairs, ambig = [], []
    for row in rows[hi+1:]:
        for c in cols:
            if c >= len(row):
                continue
            d, v, flag = value_of(row[c])
            if flag == "ambiguous":
                ambig.append(v)
            elif d is not None:
                pairs.append((d, v))
    if not pairs and not ambig:
        return {"max": -1, "headers": headers, "ambig": [], "top": []}
    mx = max((d for d, _ in pairs), default=-1)
    top = [v for _, v in sorted(set(pairs), key=lambda p: -p[0])[:3]]
    return {"max": mx, "headers": headers, "ambig": ambig, "top": top,
            "example": next((v for d, v in pairs if d == mx), None)}

def read_xlsx(path):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    return [(ws.title, [list(r) for r in ws.iter_rows(values_only=True)]) for ws in wb.worksheets]

def read_xls(path):
    import xlrd
    bk = xlrd.open_workbook(path)
    return [(sh.name, [sh.row_values(r) for r in range(sh.nrows)]) for sh in bk.sheets()]

def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        sample = f.read(4096); f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        return [("csv", [r for r in csv.reader(f, dialect)])]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--col", default=None, help="only columns whose header contains this substring")
    a = ap.parse_args()
    files = sorted(sum((glob.glob(os.path.join(a.folder, "*." + e))
                        for e in ("xlsx", "xls", "csv")), []))
    if not files:
        print("No .xlsx/.xls/.csv files in", a.folder); return
    overall = -1
    for path in files:
        name = os.path.basename(path)
        try:
            if path.lower().endswith(".xlsx"): sheets = read_xlsx(path)
            elif path.lower().endswith(".xls"): sheets = read_xls(path)
            else: sheets = read_csv(path)
        except ImportError as e:
            print(f"\n{name}: skipped — {e} (pip install the reader)"); continue
        except Exception as e:
            print(f"\n{name}: error — {e}"); continue
        best = None
        for sheet, rows in sheets:
            r = scan_rows(rows, a.col)
            if r and (best is None or r["max"] > best["max"]):
                r["sheet"] = sheet; best = r
        print(f"\n{name}")
        if best is None:
            print("  (no price column detected — pass --col \"<header>\")"); continue
        print(f"  price columns: {', '.join(best['headers'])}  [sheet: {best['sheet']}]")
        if best["max"] < 0:
            print("  max decimals: (no numeric price values found)")
        else:
            overall = max(overall, best["max"])
            print(f"  max decimals: {best['max']}   example: {best['example']}")
            if best["max"] >= 5:
                print(f"  !! 5+ decimals - likely calculated in Excel, CHECK BY EYE. top values: {best['top']}")
        if best["ambig"]:
            shown = best["ambig"][:10]
            print(f"  !! ambiguous (thousands vs decimals - NOT counted): {shown}"
                  + (f" +{len(best['ambig'])-10} more" if len(best['ambig']) > 10 else ""))
    if overall >= 0:
        print(f"\nGLOBAL max decimals in price columns: {overall}")
        print("=> 4dp (x10,000) is enough if this is <=2; choose 6dp (x1,000,000) if it is 3+.")
        print("   (resolve any ambiguous values by eye before trusting the max.)")

if __name__ == "__main__":
    main()
