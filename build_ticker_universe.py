#!/usr/bin/env python3
"""
build_ticker_universe.py — pre-fetches price history for every ticker in
docs/data/ticker_list.json and saves it as docs/data/tickers/<SYMBOL>.json.

Why this exists as a separate script, not part of run.py: the browser
can't call Yahoo Finance directly (CORS blocks it — verified directly in
a real browser, not just guessed), so the "search a stock, see its
performance" flow in the public dashboard can only work for tickers
whose history WE'VE already published as a static file on our own
domain. This script is what publishes them. It fetches ~100+ tickers,
so it's slow (a few minutes) — run it occasionally when you want to
extend the curated search list, not on every `python run.py`.

Run:  python build_ticker_universe.py
"""

import os
import sys
import json
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from engine import fetch

TICKER_LIST = os.path.join(HERE, "docs", "data", "ticker_list.json")
OUT_DIR = os.path.join(HERE, "docs", "data", "tickers")


def main():
    with open(TICKER_LIST) as f:
        tickers = json.load(f)

    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"Fetching history for {len(tickers)} tickers…")
    ok, failed = 0, []
    for i, t in enumerate(tickers, 1):
        sym = t["symbol"]
        latest, hist = fetch.fetch_yahoo_chart(sym)
        if not hist:
            print(f"  [{i}/{len(tickers)}] ! {sym}: no data")
            failed.append(sym)
            continue
        out_path = os.path.join(OUT_DIR, f"{sym}.json")
        with open(out_path, "w") as f:
            json.dump(hist, f, separators=(",", ":"))
        print(f"  [{i}/{len(tickers)}] {sym}: {len(hist)} points -> {out_path}")
        ok += 1
        time.sleep(0.1)  # be polite to Yahoo's endpoint

    print(f"\nDone: {ok} saved, {len(failed)} failed.")
    if failed:
        print("Failed tickers (not published, will be skipped by search until retried):")
        print(" ", ", ".join(failed))


if __name__ == "__main__":
    main()
