"""
fetch.py — pulls the inputs the scoring engine needs.

  • Benchmarks (S&P, VIX, yields, oil, FX, gold) → Yahoo Finance's public
    chart endpoint, clean & free, no API key.
  • Your fund NAV → BlackRock's own chart-data endpoint (the same one the
    fund page's performance chart loads from) — not a page scrape, this
    is the fund's actual daily NAV series going back to inception.

Both give us history, not just the latest print — that's what
engine/scoring.py's outlook pillar (and the asset pillar's 52-week
range / 1y / 5y returns, computed in run.py) use to compute trend.

No API keys. If a source is down, the pipeline degrades gracefully
rather than inventing numbers — every fetch here returns None / an
empty history on failure, never a fabricated value.
"""

import re
import json
import datetime as dt
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

UA = {"User-Agent": "Mozilla/5.0 (compatible; portfolio-bi/1.0)"}

# The fund page's performance-chart AJAX endpoint. The numeric id in the
# path is specific to this fund's page (BlackRock generates it per
# product) — if you point this at a different fund, view the fund's page
# source and look for a "<id>.ajax?tab=chart" request to find its id.
FUND_CHART_URL = ("https://www.blackrock.com/ch/individual/en/products/229301/"
                   "blackrock-new-energy-e2-eur-fund/1489751357104.ajax?tab=chart")

# Matches entries like:
#   {x:Date.UTC(2026,6,8),y:Number((18.66).toFixed(2)),formattedX: "08-Jul-2026"}
_NAV_ENTRY_RE = re.compile(
    r'x:Date\.UTC\(\d+,\d+,\d+\),y:Number\(\(([0-9.]+)\)\.toFixed\(2\)\),'
    r'formattedX:\s*"([^"]+)"'
)

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
    """Return (latest_nav, as_of_iso, history). history is a list of
    (iso_date, nav) tuples, oldest first, spanning the fund's full
    published NAV series. On any failure: (None, None, []) — never a
    fabricated value; the caller falls back to the last known NAV."""
    try:
        html = _get(FUND_CHART_URL)
    except (URLError, HTTPError, TimeoutError) as e:
        print(f"  ! fund fetch failed: {e}")
        return None, None, []
    entries = _NAV_ENTRY_RE.findall(html)
    if not entries:
        print("  ! navData not found — page layout may have changed")
        return None, None, []
    history = []
    for value, formatted in entries:
        try:
            iso = dt.datetime.strptime(formatted, "%d-%b-%Y").date().isoformat()
            history.append((iso, float(value)))
        except ValueError:
            continue
    if not history:
        return None, None, []
    as_of, latest = history[-1]
    return latest, as_of, history


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
    nav, as_of, nav_history = fetch_fund_nav()
    out["fund_nav"], out["fund_asof"] = nav, as_of
    history["fund_nav"] = nav_history
    print(f"  NAV: {nav} ({as_of})  ({len(nav_history)} historical points)")
    return out, history


if __name__ == "__main__":
    market, history = fetch_all()
    print(json.dumps({"market": market, "history_points": {k: len(v) for k, v in history.items()}}, indent=2))
