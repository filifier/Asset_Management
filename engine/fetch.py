"""
fetch.py — pulls the inputs the scoring engine needs.

Two very different reliability tiers, and the code is honest about it:
  • Benchmarks (S&P, VIX, yields, oil, FX) → Stooq, clean & free.
  • Your fund NAV → scraped from BlackRock's page by ISIN. FRAGILE by
    nature (it's a UCITS mutual fund; no free structured feed exists).
    If the scrape fails, we DON'T fabricate — we return None and the
    caller falls back to the last known NAV in position.json.

No API keys. If a source is down, the pipeline degrades gracefully
rather than inventing numbers.
"""

import re
import json
import datetime as dt
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

UA = {"User-Agent": "Mozilla/5.0 (compatible; portfolio-bi/1.0)"}

FUND_URL = ("https://www.blackrock.com/ch/individual/en/products/229301/"
            "blackrock-new-energy-e2-eur-fund")
FUND_ISIN = "LU0171290074"

# Stooq symbols -> friendly keys used by the macro layer
BENCHMARKS = {
    "^spx": "sp500",
    "^vix": "vix",
    "10usy.b": "us_10y",
    "cl.f": "oil_wti",
    "eurusd": "eurusd",
    "xauusd": "gold",
}


def _get(url, timeout=30):
    with urlopen(Request(url, headers=UA), timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def fetch_fund_nav():
    """Return (nav, as_of) or (None, None) — never a fabricated value."""
    try:
        html = _get(FUND_URL)
    except (URLError, HTTPError, TimeoutError) as e:
        print(f"  ! fund fetch failed: {e}")
        return None, None
    idx = html.find(FUND_ISIN)
    if idx == -1:
        print("  ! ISIN not found — page layout may have changed")
        return None, None
    window = html[max(0, idx - 1500):idx]
    dates = re.findall(r"\d{1,2}-[A-Za-z]{3}-\d{4}", window)
    as_of = dates[-1] if dates else dt.date.today().isoformat()
    nums = re.findall(r">\s*([0-9][0-9'.,]*\.\d{1,4})\s*<", window)
    for n in nums:
        v = float(n.replace("'", "").replace(",", ""))
        if 0.01 < v < 100000:
            return v, as_of
    return None, None


def fetch_stooq(symbol):
    url = f"https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=csv"
    try:
        text = _get(url)
    except (URLError, HTTPError, TimeoutError):
        return None
    lines = text.strip().splitlines()
    if len(lines) < 2:
        return None
    rec = dict(zip(lines[0].split(","), lines[1].split(",")))
    try:
        return float(rec.get("Close", ""))
    except (ValueError, TypeError):
        return None


def fetch_all():
    print("Fetching benchmarks…")
    out = {}
    for sym, key in BENCHMARKS.items():
        out[key] = fetch_stooq(sym)
        print(f"  {key}: {out[key]}")
    print("Fetching fund NAV (BlackRock)…")
    nav, as_of = fetch_fund_nav()
    out["fund_nav"], out["fund_asof"] = nav, as_of
    print(f"  NAV: {nav} ({as_of})")
    return out


if __name__ == "__main__":
    print(json.dumps(fetch_all(), indent=2))
