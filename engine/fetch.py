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
import io
import json
import zipfile
import datetime as dt
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

UA = {"User-Agent": "Mozilla/5.0 (compatible; portfolio-bi/1.0)"}

# Fama-French-Carhart factors, Developed markets, DAILY — from Ken
# French's Data Library (free, academic gold standard). Developed (not
# US-only) because a typical portfolio here is global (FTSE All-World,
# global funds). 3-factor file gives Mkt-RF/SMB/HML/RF; momentum (WML)
# is a separate file. Values are in PERCENT.
FF_3F_URL = ("https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
             "Developed_3_Factors_daily_CSV.zip")
FF_MOM_URL = ("https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
              "Developed_MOM_Factor_daily_CSV.zip")

# Fixed start date for all benchmark/macro history (and the window the
# chart + regression use for the fund too). 2021-01-01 deliberately
# excludes the 2020 COVID crash/rebound, whose extreme moves would
# otherwise dominate a linear trend or regression fit. Every series is
# fetched from this same date so they're comparable on equal footing —
# not just "same number of days back", but the same actual calendar
# window.
HISTORY_START_DATE = "2021-01-01"

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


def fetch_fund_nav(chart_url):
    """Return (latest_nav, as_of_iso, history) for a BlackRock-style fund
    chart-data endpoint (chart_url — the fund page's own "<id>.ajax?tab=chart"
    URL; to find it for a different fund, view that fund's page source and
    look for that request). history is a list of (iso_date, nav) tuples,
    oldest first, spanning the fund's full published NAV series. On any
    failure: (None, None, []) — never a fabricated value; the caller falls
    back to the last known NAV."""
    try:
        html = _get(chart_url)
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


def fetch_holding_price(holding):
    """Dispatch on holding["source"]: "blackrock" scrapes the fund's own
    chart endpoint (holding["fund_chart_url"]); "yahoo" pulls a ticker
    (holding["ticker"]) from Yahoo Finance — same reliable source as the
    benchmarks, works for any exchange-traded ETF/stock. Returns
    (latest_price, as_of_iso, history) — (None, None, []) on failure or an
    unrecognized source, never a fabricated value."""
    source = holding.get("source", "yahoo")
    if source == "blackrock":
        return fetch_fund_nav(holding["fund_chart_url"])
    if source == "yahoo":
        latest, history = fetch_yahoo_chart(holding["ticker"])
        as_of = history[-1][0] if history else None
        return latest, as_of, history
    print(f"  ! unknown holding source {source!r} for {holding.get('name')!r}")
    return None, None, []


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


def _get_bytes(url, timeout=30):
    with urlopen(Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def _parse_ff_zip(raw, ncols):
    """Parse a Ken French CSV-in-zip: skip the metadata header lines, keep
    rows whose first field is an 8-digit date. Returns {iso_date: [vals]}."""
    z = zipfile.ZipFile(io.BytesIO(raw))
    text = z.read(z.namelist()[0]).decode("latin-1")
    rows = {}
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= ncols + 1 and re.fullmatch(r"\d{8}", parts[0]):
            try:
                vals = [float(x) for x in parts[1:ncols + 1]]
            except ValueError:
                continue
            d = f"{parts[0][:4]}-{parts[0][4:6]}-{parts[0][6:8]}"
            rows[d] = vals
    return rows


def fetch_ff_factors(start_date=HISTORY_START_DATE):
    """Fama-French-Carhart daily factors (Developed markets), from
    start_date onward. Returns a list of (iso_date, mkt_rf, smb, hml,
    wml, rf) tuples — all in PERCENT — or [] on failure (never
    fabricated). Note: Ken French updates monthly with a lag, so the
    series typically ends a few weeks before today; that's expected and
    fine for a historical factor regression."""
    try:
        f3 = _parse_ff_zip(_get_bytes(FF_3F_URL), 4)    # Mkt-RF, SMB, HML, RF
        mom = _parse_ff_zip(_get_bytes(FF_MOM_URL), 1)  # WML (momentum)
    except (URLError, HTTPError, TimeoutError, zipfile.BadZipFile, OSError) as e:
        print(f"  ! Fama-French factors fetch failed: {e}")
        return []
    out = []
    for d in sorted(f3):
        if d < start_date or d not in mom:
            continue
        mkt_rf, smb, hml, rf = f3[d]
        out.append((d, mkt_rf, smb, hml, mom[d][0], rf))
    return out


def fetch_all():
    """Benchmarks/macro factors only — no specific holding. See
    fetch_holdings() for per-position price history."""
    print("Fetching benchmarks…")
    out, history = {}, {}
    for key, symbol in YAHOO_SYMBOLS.items():
        latest, hist = fetch_yahoo_chart(symbol)
        out[key], history[key] = latest, hist
        print(f"  {key}: {latest}  ({len(hist)} historical points)")
    return out, history


def fetch_holdings(holdings):
    """Fetch latest price + history for every holding in position.json.
    Returns {holding_name: {"price": float|None, "as_of": iso|None,
    "history": [...]}}, keyed by each holding's "name"."""
    out = {}
    for h in holdings:
        name = h["name"]
        print(f"Fetching {name} ({h.get('source', 'yahoo')})…")
        price, as_of, hist = fetch_holding_price(h)
        out[name] = {"price": price, "as_of": as_of, "history": hist}
        print(f"  {name}: {price} ({as_of})  ({len(hist)} historical points)")
    return out


if __name__ == "__main__":
    market, history = fetch_all()
    print(json.dumps({"market": market, "history_points": {k: len(v) for k, v in history.items()}}, indent=2))
