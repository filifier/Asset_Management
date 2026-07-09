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

# Fixed start date for all benchmark/macro history (and the window the
# chart + regression use for the fund too). 2021-01-01 deliberately
# excludes the 2020 COVID crash/rebound, whose extreme moves would
# otherwise dominate a linear trend or regression fit. Every series is
# fetched from this same date so they're comparable on equal footing —
# not just "same number of days back", but the same actual calendar
# window.
HISTORY_START_DATE = "2021-01-01"

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

# Yahoo Finance symbols -> friendly keys used by the macro layer.
# Coverage of the 14-factor list requested for the regression: 11 of 14
# are free and available here; REAL_YIELD (10Y TIPS), INFLATION_BREAKEVEN
# and CESI are NOT — see README "Fattori macro" section for why.
YAHOO_SYMBOLS = {
    "sp500": "^GSPC",         # X10 SP500
    "vix": "^VIX",             # X1  VIX
    "us_10y": "^TNX",          # X3  US10Y — already a plain yield, e.g. 4.57 = 4.57%
    "oil_wti": "CL=F",         # X13 CRUDE_OIL
    "eurusd": "EURUSD=X",
    "gold": "GC=F",            # X12 GOLD
    "move": "^MOVE",           # X2  MOVE (bond market volatility)
    "us_2y": "2YY=F",          # X4  US2Y — 2-year yield futures, tracks spot yield closely
    "dxy": "DX-Y.NYB",         # X7  DXY
    "nasdaq100": "^NDX",       # X11 NASDAQ
    "hy_credit": "HYG",        # X14 HY_CREDIT (iShares iBoxx HY Corporate Bond ETF)
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


def fetch_yahoo_chart(symbol, start_date=HISTORY_START_DATE, interval="1d"):
    """Return (latest_close, history). history is a list of (iso_date,
    close) tuples, oldest first, gaps (None closes) dropped. On any
    failure, returns (None, []) — same "never fabricate" contract as
    the rest of this module.

    Fetches a FIXED calendar window (start_date -> now) rather than a
    rolling "last N years" range, so every symbol lines up on the same
    actual dates — required for both the chart's indexed-to-100 overlay
    and any regression across factors to be a fair comparison."""
    period1 = int(dt.datetime.strptime(start_date, "%Y-%m-%d")
                  .replace(tzinfo=dt.timezone.utc).timestamp())
    period2 = int(dt.datetime.now(dt.timezone.utc).timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}"
           f"?period1={period1}&period2={period2}&interval={interval}")
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
