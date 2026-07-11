#!/usr/bin/env python3
"""
run.py — the entry point. Ties everything together:

  1. Load your position (data/position.json) — one or more holdings
  2. Fetch live data for each holding + macro benchmarks (engine/fetch.py)
  3. Build one "Asset momentum" pillar per holding, plus portfolio-level
     macro/outlook/factor-regression/position pillars
  4. Run the transparent scoring engine (engine/scoring.py)
  5. Print a clear BI scorecard to the console (and save JSON)

Run:  python run.py
"""

import os
import json
import sys
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from engine import fetch, scoring, regression

TRADING_DAYS_1Y = 252


def pct_change_over(history, days_back):
    """history: list of (iso_date, value), oldest first. % change from
    ~days_back trading days ago to the latest point. None if there isn't
    actually days_back worth of history — we do NOT silently fall back
    to the oldest available point, since that would quietly compare
    against a shorter, misleading window instead of a real failure."""
    values = [v for _, v in history]
    if len(values) <= days_back:
        return None
    past = values[len(values) - 1 - days_back]
    return (values[-1] - past) / past * 100 if past else None


def nav_on_or_before(history, date_iso):
    """Find the closest price on or before date_iso (handles weekends/
    holidays by snapping back to the last trading day). history: list
    of (iso_date, value), oldest first. Returns (matched_date, value),
    or (None, None) if date_iso is malformed or predates all history."""
    try:
        dt.date.fromisoformat(date_iso)
    except (ValueError, TypeError):
        return None, None
    match = None
    for d, v in history:
        if d <= date_iso:
            match = (d, v)
        else:
            break
    return match if match else (None, None)


def nav_range_and_returns(price_history, bench_history):
    """Compute a holding's 52-week range and 1y/5y returns (holding +
    S&P 500 benchmark) directly from historical series. Returns a dict
    with None values for anything that can't be computed yet (e.g. a
    holding held for under a year) — never a fabricated number."""
    window = [v for _, v in price_history[-TRADING_DAYS_1Y:]]
    low52 = min(window) if window else None
    high52 = max(window) if window else None
    return {
        "low52": low52,
        "high52": high52,
        "ret_1y": pct_change_over(price_history, TRADING_DAYS_1Y),
        "ret_5y": pct_change_over(price_history, TRADING_DAYS_1Y * 5),
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
    holdings = position["holdings"]
    portfolio_value = position["portfolio_value_eur"]

    market, history = fetch.fetch_all()
    holdings_data = fetch.fetch_holdings(holdings)
    macro = macro_from_market(market)

    priced_holdings = []       # for the position pillar
    asset_pillars = []         # one "Asset momentum" pillar per holding
    holdings_histories = {}    # for the portfolio-level regression

    for h in holdings:
        name = h["name"]
        hd = holdings_data[name]
        price, hist = hd["price"], hd["history"]

        if not price or not hist:
            print(f"\n  ! no price for {name} — excluded from position & regression")
            priced_holdings.append({"name": name, "invested_amount": h["invested_amount_eur"],
                                    "market_value": None})
            continue

        holdings_histories[name] = hist
        purchase_date, purchase_price = nav_on_or_before(hist, h["purchase_date"])
        market_value = h["invested_amount_eur"] * price / purchase_price if purchase_price else None
        if market_value is None:
            print(f"\n  ! no price history at/before {h['purchase_date']} for {name} — "
                  f"position pillar will exclude it")
        priced_holdings.append({"name": name, "invested_amount": h["invested_amount_eur"],
                                "market_value": market_value})

        live = nav_range_and_returns(hist, history.get("sp500", []))
        if all(v is not None for v in live.values()):
            nav_trend_1m = pct_change_over(hist, 21)
            pillar = scoring.pillar_asset(**live, nav=price, nav_trend_1m=nav_trend_1m)
            pillar.title = f"{pillar.title} — {name}"
            asset_pillars.append(pillar)
        else:
            print(f"  (not enough history yet for {name}'s momentum pillar — needs ~5y for full stats)")

    total_value = sum(h["market_value"] for h in priced_holdings if h["market_value"])
    weights = {h["name"]: (h["market_value"] / total_value if h["market_value"] and total_value else 0)
              for h in priced_holdings}

    # Macro/outlook weighting uses the FIRST holding's asset profile.
    # Simplification: with multiple holdings of different profile_keys,
    # a proper blended view would weight each profile's macro_weights by
    # portfolio share — not built yet, flagged here rather than silently
    # picking one and calling it done.
    primary_profile = holdings[0]["profile_key"]
    macro_pillar = scoring.pillar_macro(macro, primary_profile)
    outlook_pillar = scoring.pillar_outlook(history, primary_profile)

    # Portfolio-level regression: Y = today's-weights portfolio return,
    # not any single holding's. See regression.build_portfolio_nav's
    # docstring for the "current weights applied retroactively" caveat.
    # If nothing is priced yet (e.g. purchase_date still a placeholder),
    # every weight is 0 and a portfolio series is meaningless — skip it
    # explicitly rather than feeding in a degenerate all-zero series.
    if total_value:
        portfolio_nav = regression.build_portfolio_nav(holdings_histories, weights)
    else:
        portfolio_nav = []
        print("\n  ! no holding has a valid purchase price yet — factor regression "
              "pillar will show 'not available' until at least one does")
    factor_pillar = scoring.pillar_factor_regression(portfolio_nav, history)

    position_pillar = scoring.pillar_position_multi(priced_holdings, portfolio_value)

    pillars_full = asset_pillars + [macro_pillar, outlook_pillar, factor_pillar, position_pillar]
    pillars_public = asset_pillars + [macro_pillar, outlook_pillar, factor_pillar]

    card = scoring.build_scorecard_from_pillars(pillars_full)
    card["meta"] = {
        "asset_name": " + ".join(h["name"] for h in holdings) if len(holdings) > 1 else holdings[0]["name"],
        "nav": holdings_data[holdings[0]["name"]]["price"],
        "holdings_count": len(holdings),
    }

    # ---- print ----
    print(f"\nPortafoglio: {card['meta']['asset_name']} ({len(holdings)} posizion{'e' if len(holdings)==1 else 'i'})\n")
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

    # Public scorecard: no position/portfolio data. Safe to publish.
    public_card = scoring.build_scorecard_from_pillars(pillars_public)
    public_card["meta"] = card["meta"]
    public_out = os.path.join(HERE, "docs", "data", "scorecard.json")
    os.makedirs(os.path.dirname(public_out), exist_ok=True)
    with open(public_out, "w") as f:
        json.dump(public_card, f, indent=2, ensure_ascii=False)
    print(f"Saved: {public_out}  (public — no personal data, safe to git push)")

    # Primary holding's price history: still published for the public
    # dashboard's private calculator (single-holding UI, unchanged for
    # now — multi-holding front-end is a follow-up).
    primary_name = holdings[0]["name"]
    nav_history_out = os.path.join(HERE, "docs", "data", "nav_history.json")
    with open(nav_history_out, "w") as f:
        json.dump(holdings_data[primary_name]["history"], f, separators=(",", ":"))
    print(f"Saved: {nav_history_out}  (public — primary holding price history, safe to git push)")

    macro_history_out = os.path.join(HERE, "docs", "data", "macro_history.json")
    with open(macro_history_out, "w") as f:
        json.dump({k: v for k, v in history.items()}, f, separators=(",", ":"))
    print(f"Saved: {macro_history_out}  (public — benchmark price history, safe to git push)")

    # Fama-French-Carhart factors (Developed, daily) for the academic
    # factor decomposition — market data, published like the rest.
    print("Fetching Fama-French-Carhart factors (Ken French, Developed daily)…")
    ff = fetch.fetch_ff_factors()
    ff_out = os.path.join(HERE, "docs", "data", "ff_factors.json")
    with open(ff_out, "w") as f:
        json.dump(ff, f, separators=(",", ":"))
    print(f"Saved: {ff_out}  ({len(ff)} daily rows: Mkt-RF, SMB, HML, WML, RF)")


if __name__ == "__main__":
    main()
