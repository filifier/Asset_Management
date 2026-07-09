#!/usr/bin/env python3
"""
run.py — the entry point. Ties everything together:

  1. Load your position (data/position.json)
  2. Fetch live data (engine/fetch.py) — with graceful fallback
  3. Translate raw market data into asset-relative macro signals
  4. Run the transparent scoring engine (engine/scoring.py)
  5. Print a clear BI scorecard to the console (and save JSON)

This is the skeleton. In Claude Code you can ask it to:
  • add an HTML front-end that renders the scorecard,
  • add more assets to position.json and profiles to scoring.py,
  • wire in a scheduler, etc.

Run:  python run.py
"""

import os
import json
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from engine import fetch, scoring


# Fallback only — used if the live NAV/benchmark history fetch fails.
# Normally low52/high52/ret_1y/ret_5y/bench_1y/bench_5y are all computed
# live in nav_range_and_returns() below, from BlackRock's own NAV history
# and Yahoo's S&P 500 history. Update "last_known_nav" by hand only if
# you want a specific fallback; it's otherwise unused while fetches work.
FUND_REFERENCE = {
    "low52": 12.86, "high52": 20.04,
    "ret_1y": 54.67, "bench_1y": 26.73,
    "ret_5y": 55.77, "bench_5y": 80.18,
    "last_known_nav": 19.44,
}

TRADING_DAYS_1Y = 252


def pct_change_over(history, days_back):
    """history: list of (iso_date, value), oldest first. % change from
    ~days_back trading days ago to the latest point. None if not enough
    history — never fabricated."""
    values = [v for _, v in history]
    if len(values) < 2:
        return None
    past = values[max(0, len(values) - 1 - days_back)]
    return (values[-1] - past) / past * 100 if past else None


def nav_range_and_returns(nav_history, bench_history):
    """Compute the fund's 52-week range and 1y/5y returns (fund + S&P
    500 benchmark) directly from historical series, so these numbers
    self-update instead of needing to be edited by hand."""
    window = [v for _, v in nav_history[-TRADING_DAYS_1Y:]]
    low52 = min(window) if window else None
    high52 = max(window) if window else None
    return {
        "low52": low52,
        "high52": high52,
        "ret_1y": pct_change_over(nav_history, TRADING_DAYS_1Y),
        "ret_5y": pct_change_over(nav_history, TRADING_DAYS_1Y * 5),
        "bench_1y": pct_change_over(bench_history, TRADING_DAYS_1Y),
        "bench_5y": pct_change_over(bench_history, TRADING_DAYS_1Y * 5),
    }


def macro_from_market(market: dict) -> dict:
    """
    Translate raw fetched levels into asset-POV bias signals.
    +1 = tailwind for a growth/clean-energy asset, -1 = headwind.

    NOTE: these thresholds are explicit and editable — that's the
    whole point. You can see and change every rule.
    """
    def bias_from(value, low_good, high_bad, invert=False):
        if value is None:
            return 0
        if invert:  # higher = worse
            return -1 if value >= high_bad else (1 if value <= low_good else 0)
        return 1 if value >= low_good else (-1 if value <= high_bad else 0)

    m = {}
    # US 10Y: higher yields = headwind for long-duration growth
    y = market.get("us_10y")
    m["us_10y"] = {
        "raw_bias": -1 if (y and y >= 4.3) else (1 if (y and y <= 3.5) else 0),
        "reading": f"{y:.2f}%" if y else "n/a",
        "note": "Rising long yields pressure growth valuations",
    }
    # VIX: low = calm = tailwind
    v = market.get("vix")
    m["vix"] = {
        "raw_bias": 1 if (v and v <= 18) else (-1 if (v and v >= 26) else 0),
        "reading": f"{v:.1f}" if v else "n/a",
        "note": "Low volatility supports risk assets",
    }
    # Gold: rising gold often = risk-off (mild headwind for equity risk)
    g = market.get("gold")
    m["gold"] = {"raw_bias": 0, "reading": f"${g:,.0f}" if g else "n/a",
                 "note": "Safe-haven demand proxy"}
    # Oil
    o = market.get("oil_wti")
    m["oil"] = {"raw_bias": 0, "reading": f"${o:.0f}" if o else "n/a",
                "note": "Energy-complex linkage, mixed effect"}
    # EUR/USD
    fx = market.get("eurusd")
    m["eurusd"] = {"raw_bias": 0, "reading": f"{fx:.3f}" if fx else "n/a",
                   "note": "FX effect on EUR-denominated holding"}
    # BTP-Bund proxy not fetched here; placeholder neutral
    m["btp_bund"] = {"raw_bias": 0, "reading": "n/a",
                     "note": "Euro-area stress proxy"}
    return m


def main():
    print("=" * 60)
    print("  PORTFOLIO BI — transparent scorecard")
    print("  (describes state & signals — does not give buy/sell advice)")
    print("=" * 60)

    with open(os.path.join(HERE, "data", "position.json")) as f:
        position = json.load(f)

    market, history = fetch.fetch_all()
    nav = market.get("fund_nav") or FUND_REFERENCE["last_known_nav"]
    if not market.get("fund_nav"):
        print(f"\n  (NAV fetch failed — using last known {nav})")

    holding = position["holdings"][0]
    macro = macro_from_market(market)

    # Live-computed from history where possible; fall back to the last
    # hand-maintained reference for any figure history can't produce yet
    # (e.g. first run, or fetch failure).
    live = nav_range_and_returns(history.get("fund_nav", []), history.get("sp500", []))
    asset_inputs = {k: (v if v is not None else FUND_REFERENCE[k]) for k, v in live.items()}
    asset_inputs["nav"] = nav
    nav_trend_1m = pct_change_over(history.get("fund_nav", []), 21)

    inputs = {
        "profile_key": holding["profile_key"],
        "asset": asset_inputs,
        "asset_nav_trend_1m": nav_trend_1m,
        "macro": macro,
        "macro_history": history,
        "position": {
            "units": holding["units"],
            "avg_cost": holding["avg_cost"],
            "nav": nav,
            "portfolio_value": position["portfolio_value_eur"],
        },
    }

    card = scoring.build_scorecard(inputs)
    card["meta"] = {"asset_name": holding["name"], "nav": nav}

    # ---- print ----
    print(f"\nAsset: {holding['name']}")
    print(f"NAV used: €{nav}\n")
    for p in card["pillars"]:
        print(f"■ {p['title']}: {p['label']} (score {p['score']:+.1f})")
        for s in p["signals"]:
            mark = {"supportive": "+", "caution": "!", "neutral": "·"}[s["bias"]]
            print(f"    [{mark}] {s['name']}: {s['reading']}")
            print(f"        {s['why']}")
    print("\n" + "-" * 60)
    print("SUMMARY:", card["summary"])
    print("-" * 60)

    out = os.path.join(HERE, "data", "scorecard.json")
    with open(out, "w") as f:
        json.dump(card, f, indent=2, ensure_ascii=False)
    print(f"\nSaved: {out}  (full — local only, contains your position)")

    # Public scorecard: asset + macro only, no position/portfolio data.
    # This is the one that's safe to commit and publish via GitHub Pages.
    public_card = scoring.build_public_scorecard(inputs)
    public_card["meta"] = {"asset_name": holding["name"], "nav": nav}
    public_out = os.path.join(HERE, "docs", "data", "scorecard.json")
    os.makedirs(os.path.dirname(public_out), exist_ok=True)
    with open(public_out, "w") as f:
        json.dump(public_card, f, indent=2, ensure_ascii=False)
    print(f"Saved: {public_out}  (public — no personal data, safe to git push)")


if __name__ == "__main__":
    main()
