"""
fetch.py — pulls the inputs the scoring engine needs.

Two very different reliability tiers, and the code is honest about it:
  • Benchmarks (S&P, VIX, yields, oil, FX, gold) → Yahoo Finance's public
    chart endpoint, clean & free, no API key. Also gives us history, not
    just the latest print — that's what engine/scoring.py's outlook
    pillar uses to compute trend.
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
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

UA = {"User-Agent": "Mozilla/5.0 (compatible; portfolio-bi/1.0)"}

FUND_URL = ("https://www.blackrock.com/ch/individual/en/products/229301/"
            "blackrock-new-energy-e2-eur-fund")
FUND_ISIN = "LU0171290074"

# Yahoo Finance symbols -> friendly keys used by the macro layer
YAHOO_SYMBOLS = {
    "sp500": "^GSPC",
    "vix": "^VIX",
    "us_10y": "^TNX",     # already expressed as a plain yield, e.g. 4.57 = 4.57%
    "oil_wti": "CL=F",
    "eurusd": "EURUSD=X",
    "gold": "GC=F",
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


def fetch_yahoo_chart(symbol, range_="6mo", interval="1d"):
    """Return (latest_close, history). history is a list of (iso_date,
    close) tuples, oldest first, gaps (None closes) dropped. On any
    failure, returns (None, []) — same "never fabricate" contract as
    the rest of this module."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}"
           f"?range={range_}&interval={interval}")
    try:
        text = _get(url)
        result = json.loads(text)["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except (URLError, HTTPError, TimeoutError, KeyError, IndexError,
            TypeError, ValueError):
        return None, []
    history = [
        (dt.datetime.utcfromtimestamp(t).date().isoformat(), c)
        for t, c in zip(timestamps, closes) if c is not None
    ]
    latest = history[-1][1] if history else None
    return latest, history


def fetch_all():
    print("Fetching benchmarks…")
    out, history = {}, {}
    for key, symbol in YAHOO_SYMBOLS.items():
        latest, hist = fetch_yahoo_chart(symbol)
        out[key], history[key] = latest, hist
        print(f"  {key}: {latest}  ({len(hist)} historical points)")
    print("Fetching fund NAV (BlackRock)…")
    nav, as_of = fetch_fund_nav()
    out["fund_nav"], out["fund_asof"] = nav, as_of
    print(f"  NAV: {nav} ({as_of})")
    return out, history


if __name__ == "__main__":
    market, history = fetch_all()
    print(json.dumps({"market": market, "history_points": {k: len(v) for k, v in history.items()}}, indent=2))
