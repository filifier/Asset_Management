#!/usr/bin/env python3
"""
refresh_public.py — regenerate ONLY the shared, non-personal market data
the public site serves as static files, so a scheduled GitHub Action can
keep it fresh automatically WITHOUT any personal position.

This deliberately does NOT read data/position.json and does NOT touch
docs/data/scorecard.json or nav_history.json — those depend on the
owner's personal position (and the front-end reads card.meta.nav from the
scorecard), so they stay owner-generated via `python run.py`. Everything
here is the same market data for every user.

Regenerates:
  docs/data/macro_history.json  — benchmark price history (feeds in-browser OLS)
  docs/data/ff_factors.json     — Fama-French-Carhart factors (feeds in-browser Carhart)
  docs/data/news.json           — RSS headlines, tagged with tickers + topics
  docs/data/ticker_sectors.json — sector themes per ticker (content-based news matching)

Per-ticker price histories (docs/data/tickers/*.json) are refreshed
separately by build_ticker_universe.py — slower, so it runs on a daily
schedule while this runs every few hours.

Run:  python refresh_public.py
"""

import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from engine import fetch, news

DATA = os.path.join(HERE, "docs", "data")


def main():
    print("Refreshing public market data (no personal position involved)…")

    # Macro benchmark history (S&P 500, VIX, yields, oil, gold, FX, …) —
    # the series the per-asset OLS regressions run against in the browser.
    _, history = fetch.fetch_all()
    with open(os.path.join(DATA, "macro_history.json"), "w") as f:
        json.dump({k: v for k, v in history.items()}, f, separators=(",", ":"))
    print(f"  macro_history.json: {len(history)} series")

    # Fama-French-Carhart factors (Ken French, Developed daily).
    ff = fetch.fetch_ff_factors()
    with open(os.path.join(DATA, "ff_factors.json"), "w") as f:
        json.dump(ff, f, separators=(",", ":"))
    print(f"  ff_factors.json: {len(ff)} daily rows")

    ticker_list = os.path.join(DATA, "ticker_list.json")

    # Financial news (RSS: Yahoo, Investing, MarketWatch, CNBC, Seeking
    # Alpha, Reuters) — headline + link + source, tagged.
    news_data = news.fetch_news(ticker_list)
    with open(os.path.join(DATA, "news.json"), "w") as f:
        json.dump(news_data, f, separators=(",", ":"), ensure_ascii=False)
    print(f"  news.json: {len(news_data['items'])} headlines")

    # Sector themes per ticker (lets the front-end match holdings to news
    # by topic, so news responds to the portfolio even for funds/ETFs).
    sectors = news.build_ticker_sectors(ticker_list)
    with open(os.path.join(DATA, "ticker_sectors.json"), "w") as f:
        json.dump(sectors, f, separators=(",", ":"), ensure_ascii=False)
    print(f"  ticker_sectors.json: {len(sectors)} tickers classified")

    print("Done — all files are shared market data, safe to commit & publish.")


if __name__ == "__main__":
    main()
