#!/usr/bin/env python3
"""
model_validation.py — INTERNAL statistical validation of every model the
platform runs. Backend-only: this is our double check that the models are
trained/tested properly and that there is enough data — nothing here is
shown to users.

What "training and testing" means for each model, and what we test:

  GARCH(1,1)   "Training" = maximum-likelihood fit of (α, β) on a window
               of returns. "Testing" = OUT-OF-SAMPLE: refit on an
               expanding window, forecast tomorrow's variance, score the
               forecast against tomorrow's realized squared return with
               QLIKE (the standard robust loss for variance forecasts),
               and compare against the naive benchmark (constant
               variance). If GARCH doesn't beat "volatility is always
               the same", it earns nothing.

  VaR          "Testing" = coverage backtest. Each day, compute VaR from
               the PAST W days only (no look-ahead), then check whether
               the NEXT day's loss exceeded it. A correct 99% VaR gets
               exceeded ~1% of the time. Kupiec's POF test tells whether
               the observed violation rate is statistically compatible
               with the promised coverage; Christoffersen's test checks
               violations don't cluster.

  OLS macro    "Training" = fit on the first 70% of the sample.
               "Testing" = R² on the LAST 30% the model never saw, vs
               in-sample R² (gap = overfitting measure). Plus beta
               stability: fit on 2 disjoint halves, compare signs of the
               significant coefficients.

  Carhart      Same walk-forward out-of-sample R² + subsample beta
               stability on the 4 factors.

  TSMOM        "Testing" = signal efficacy: at each month-end, take the
               sign of the past 12-month return; measure the NEXT
               month's return. Hit rate and long-minus-flat spread over
               the whole history, per asset.

  Data check   Points available per source vs a stated minimum for each
               model. Honest verdict per model: OK / LIMITE / NO.

Run:  python3 tools/model_validation.py
Writes model_validation_report.md next to the repo root (gitignored —
internal artifact).
"""

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "docs", "data")
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

from quant_check import (qmean, qstd, quantile_sorted, garch_fit, fit_ols,
                         build_portfolio_returns, nav_on_or_before,
                         run_asset_regression, PORTFOLIO,
                         OLS_FACTORS)

REPORT = []
def say(line=""):
    print(line)
    REPORT.append(line)


# ── GARCH out-of-sample: expanding-window 1-step variance forecasts ──
def validate_garch(rets, train_min=750, refit_every=21):
    """QLIKE loss of GARCH 1-step-ahead variance forecasts vs the
    constant-variance benchmark, out of sample. Lower is better.
    QLIKE(h, r²) = log h + r²/h — robust to the noise in using r² as the
    realized-variance proxy (Patton 2011)."""
    n = len(rets)
    if n < train_min + 100:
        return None
    qlike_g, qlike_c = [], []
    params = None
    viol = 0
    for t in range(train_min, n - 1):
        window = rets[:t]
        if params is None or (t - train_min) % refit_every == 0:
            g = garch_fit(window)
            if g is None:
                return None
            params = g
            # filter sigma2 up to t with current params
        mu = qmean(window)
        eps = [r - mu for r in window]
        uncond = sum(e * e for e in eps) / len(eps)
        omega = uncond * (1 - params["alpha"] - params["beta"])
        s2 = uncond
        for k in range(1, len(eps)):
            s2 = omega + params["alpha"] * eps[k - 1] ** 2 + params["beta"] * s2
        h_next = omega + params["alpha"] * eps[-1] ** 2 + params["beta"] * s2
        r2_next = (rets[t] - mu) ** 2
        qlike_g.append(math.log(h_next) + r2_next / h_next)
        qlike_c.append(math.log(uncond) + r2_next / uncond)
    return {"n_oos": len(qlike_g), "qlike_garch": qmean(qlike_g), "qlike_const": qmean(qlike_c),
            "improvement_pct": (qmean(qlike_c) - qmean(qlike_g)) / abs(qmean(qlike_c)) * 100}


# ── VaR coverage backtest: Kupiec POF + Christoffersen independence ──
def _chi2_sf1(x):
    """Survival function of chi-square with 1 dof: P(X > x) = erfc(sqrt(x/2))."""
    return math.erfc(math.sqrt(x / 2)) if x > 0 else 1.0

def validate_var(rets, level=0.99, window=500):
    n = len(rets)
    if n < window + 250:
        return None
    hits = []  # 1 = violation (loss worse than VaR)
    for t in range(window, n):
        past = sorted(rets[t - window:t])
        var_t = -quantile_sorted(past, 1 - level)
        hits.append(1 if rets[t] < -var_t else 0)
    T, x = len(hits), sum(hits)
    p_hat = x / T
    p0 = 1 - level
    # Kupiec proportion-of-failures LR test
    def _ll(p): return (T - x) * math.log(1 - p) + x * math.log(p) if 0 < p < 1 else float("-inf")
    lr_pof = -2 * (_ll(p0) - _ll(p_hat)) if x > 0 else -2 * (T * math.log(1 - p0))
    p_pof = _chi2_sf1(lr_pof)
    # Christoffersen independence: violations shouldn't cluster
    n00 = n01 = n10 = n11 = 0
    for i in range(1, T):
        a, b = hits[i - 1], hits[i]
        if a == 0 and b == 0: n00 += 1
        elif a == 0 and b == 1: n01 += 1
        elif a == 1 and b == 0: n10 += 1
        else: n11 += 1
    p_ind = None
    if n01 + n11 > 0 and n00 + n10 > 0:
        pi0 = n01 / (n00 + n01) if n00 + n01 else 0
        pi1 = n11 / (n10 + n11) if n10 + n11 else 0
        pi = (n01 + n11) / (n00 + n01 + n10 + n11)
        def _l(p, a, b): return (a * math.log(1 - p) if p < 1 and a else 0) + (b * math.log(p) if p > 0 and b else 0)
        lr_ind = -2 * ((_l(pi, n00 + n10, n01 + n11)) - (_l(pi0, n00, n01) + _l(pi1, n10, n11)))
        p_ind = _chi2_sf1(lr_ind)
    return {"level": level, "window": window, "n_oos": T, "violations": x,
            "expected": T * p0, "rate_pct": p_hat * 100, "expected_pct": p0 * 100,
            "kupiec_p": p_pof, "christoffersen_p": p_ind}


# ── regressions: walk-forward out-of-sample R² + subsample beta stability ──
def _oos_r2(y, X, split=0.7):
    n = len(y)
    k = int(n * split)
    fit = fit_ols(y[:k], X[:k])
    coef = fit["coef"]
    resid_sq, tot_sq = 0.0, 0.0
    y_test = y[k:]
    y_mean = qmean(y_test)
    for i in range(k, n):
        pred = coef[0] + sum(c * v for c, v in zip(coef[1:], X[i]))
        resid_sq += (y[i] - pred) ** 2
        tot_sq += (y[i] - y_mean) ** 2
    return {"r2_in": fit["r2"], "r2_oos": 1 - resid_sq / tot_sq if tot_sq else None,
            "n_train": k, "n_test": n - k}

def _beta_stability(y, X, names, pmax=0.05):
    n = len(y); h = n // 2
    f1 = fit_ols(y[:h], X[:h]); f2 = fit_ols(y[h:], X[h:])
    flips, sig_any = [], 0
    for i, name in enumerate(names):
        s1 = f1["pvalues"][i + 1] < pmax
        s2 = f2["pvalues"][i + 1] < pmax
        if s1 or s2:
            sig_any += 1
            if (f1["coef"][i + 1] > 0) != (f2["coef"][i + 1] > 0) and (s1 and s2):
                flips.append(name)
    return {"significant": sig_any, "sign_flips": flips}


# ── TSMOM: monthly signal efficacy over the full history ──
def validate_tsmom(closes, lookback=252, step=21):
    n = len(closes)
    if n < lookback + step * 6:
        return None
    long_rets, flat_rets = [], []
    hits = 0; total = 0
    t = lookback
    while t + step < n:
        sig = 1 if closes[t] > closes[t - lookback] else -1
        fwd = (closes[t + step] / closes[t] - 1) * 100
        if sig > 0: long_rets.append(fwd)
        else: flat_rets.append(fwd)
        if (sig > 0 and fwd > 0) or (sig < 0 and fwd < 0): hits += 1
        total += 1
        t += step
    return {"n_signals": total, "hit_rate_pct": hits / total * 100,
            "avg_after_long_pct": qmean(long_rets) if long_rets else None,
            "avg_after_neg_signal_pct": qmean(flat_rets) if flat_rets else None}


def main():
    say("=" * 72)
    say("  VALIDAZIONE STATISTICA DEI MODELLI — double check interno")
    say("  (out-of-sample dove possibile: il modello è valutato su dati mai visti)")
    say("=" * 72)

    with open(os.path.join(DATA, "macro_history.json")) as f: macro = json.load(f)
    with open(os.path.join(DATA, "ff_factors.json")) as f: ff = json.load(f)

    holdings = []
    for p in PORTFOLIO:
        with open(p["file"]) as f: hist = json.load(f)
        pp = nav_on_or_before(hist, p["purchase"]); cur = hist[-1][1]
        holdings.append({**p, "history": hist, "mv": p["amount"] * cur / pp})
    tot = sum(h["mv"] for h in holdings)
    for h in holdings: h["weight"] = h["mv"] / tot
    port_ret = build_portfolio_returns(holdings)
    rets = [r for _, r in port_ret]

    # ── data sufficiency ──
    say("\n── 1. SUFFICIENZA DATI " + "─" * 46)
    checks = [
        ("Rendimenti portafoglio (GARCH/VaR/metriche)", len(rets), 750, 1500),
        ("Fattori Fama-French (Carhart)", len(ff), 750, 1500),
        ("Macro S&P 500 (OLS)", len(macro.get("sp500", [])), 750, 1500),
        ("Macro US 2Y (vincola l'inner-join OLS)", len(macro.get("us_2y", [])), 750, 1500),
    ]
    for name, n, minimum, good in checks:
        verdict = "OK" if n >= good else ("LIMITE (usabile ma statistica debole)" if n >= minimum else "NO (insufficiente)")
        say(f"  {name}: {n} punti → {verdict}")

    # Series to stress: the actual portfolio (depth bound by the fund's
    # NAV history) plus two 10-year series, to show what the extra depth
    # buys statistically.
    def _rets_of(hist):
        out = []
        for i in range(1, len(hist)):
            p0 = hist[i - 1][1]
            if p0 and hist[i][1]: out.append((hist[i][1] - p0) / p0 * 100)
        return out
    series = [("Portafoglio (NVDA+BGF-SE)", rets),
              ("NVDA (10 anni)", _rets_of(holdings[0]["history"])),
              ("S&P 500 (10 anni)", _rets_of(macro["sp500"]))]

    # ── GARCH ──
    say("\n── 2. GARCH(1,1) — forecast di varianza out-of-sample " + "─" * 15)
    for sname, srets in series:
        g = validate_garch(srets, refit_every=63)
        if g:
            better = g["qlike_garch"] < g["qlike_const"]
            say(f"  {sname}: {g['n_oos']} forecast oos (refit trimestrale)")
            say(f"    QLIKE GARCH {g['qlike_garch']:.5f} vs costante {g['qlike_const']:.5f} → "
                f"{'BATTE' if better else 'NON batte'} il benchmark ({g['improvement_pct']:+.1f}%)")
        else:
            say(f"  {sname}: dati insufficienti per il backtest GARCH.")

    # ── VaR ──
    say("\n── 3. VaR STORICO — backtest di copertura (no look-ahead) " + "─" * 11)
    for sname, srets in series:
        say(f"  {sname}:")
        for level in (0.95, 0.99):
            v = validate_var(srets, level=level)
            if not v:
                say(f"    VaR {int(level*100)}%: dati insufficienti."); continue
            ok_cov = v["kupiec_p"] > 0.05
            say(f"    VaR {int(level*100)}% ({v['n_oos']}g oos): violazioni {v['violations']} vs attese {v['expected']:.1f} "
                f"— Kupiec p={v['kupiec_p']:.3f} → {'COMPATIBILE' if ok_cov else 'NON compatibile'}"
                + (f"; Christoffersen p={v['christoffersen_p']:.3f}"
                   + (" (violazioni clusterizzate)" if v['christoffersen_p'] is not None and v['christoffersen_p'] <= 0.05 else "")
                   if v["christoffersen_p"] is not None else ""))

    # ── Carhart ──
    say("\n── 4. CARHART — R² out-of-sample e stabilità dei beta " + "─" * 15)
    ff_map = {r[0]: r for r in ff}
    yc, Xc = [], []
    for d, r in port_ret:
        f = ff_map.get(d)
        if not f or (f[1] == 0 and f[2] == 0 and f[3] == 0 and f[4] == 0): continue
        yc.append(r - f[5]); Xc.append([f[1], f[2], f[3], f[4]])
    oos = _oos_r2(yc, Xc)
    stab = _beta_stability(yc, Xc, ["Mkt-RF", "SMB", "HML", "WML"])
    say(f"  Train {oos['n_train']} giorni / test {oos['n_test']} giorni (mai visti)")
    say(f"  R² in-sample:  {oos['r2_in']:.3f}")
    say(f"  R² out-of-sample: {oos['r2_oos']:.3f}  → "
        f"{'regge fuori campione' if oos['r2_oos'] > 0.7 * oos['r2_in'] else 'cala fuori campione (in parte fisiologico)'}")
    say(f"  Stabilità beta (2 metà): {stab['significant']} fattori significativi, "
        f"cambi di segno: {', '.join(stab['sign_flips']) if stab['sign_flips'] else 'NESSUNO'}")

    # ── OLS per asset ──
    say("\n── 5. OLS MACRO PER TITOLO — out-of-sample e stabilità " + "─" * 14)
    for h in holdings:
        res = run_asset_regression(h["history"], macro)
        # rebuild the design to run oos/stability with the same recipe
        from quant_check import OLS_MACRO_KEYS, OLS_RATE_LIKE
        asset_map = dict(h["history"])
        maps = {k: dict(macro.get(k, [])) for k in OLS_MACRO_KEYS}
        dates = sorted(d for d in asset_map if all(d in maps[k] for k in OLS_MACRO_KEYS))
        levels = []
        for d in dates:
            row = {"asset": asset_map[d]}
            for k in OLS_MACRO_KEYS: row[k] = maps[k][d]
            row["term_spread"] = row["us_10y"] - row["us_2y"]
            levels.append(row)
        cols = ["asset"] + OLS_FACTORS
        ch = []
        for i in range(1, len(levels)):
            row = {}
            for c in cols:
                prev, cur = levels[i - 1][c], levels[i][c]
                row[c] = (cur - prev) if c in OLS_RATE_LIKE else ((cur - prev) / prev * 100 if prev else 0)
            ch.append(row)
        y = [r["asset"] for r in ch]; X = [[r[f] for f in OLS_FACTORS] for r in ch]
        o = _oos_r2(y, X); s = _beta_stability(y, X, OLS_FACTORS)
        say(f"  {h['name'][:44]}: R² in {o['r2_in']:.3f} / oos {o['r2_oos']:.3f} — "
            f"beta significativi {s['significant']}, flip: {', '.join(s['sign_flips']) or 'nessuno'}")

    # ── TSMOM ──
    say("\n── 6. TSMOM — efficacia storica del segnale (mensile) " + "─" * 15)
    for h in holdings + [{"name": "S&P 500 (benchmark)", "history": macro["sp500"]}]:
        closes = [v for _, v in h["history"] if v and v > 0]
        t = validate_tsmom(closes)
        if not t:
            say(f"  {h['name'][:40]}: storico insufficiente."); continue
        say(f"  {h['name'][:44]}: {t['n_signals']} segnali, hit-rate {t['hit_rate_pct']:.0f}%, "
            f"media mese dopo segnale + : {t['avg_after_long_pct']:+.2f}%, dopo segnale − : "
            f"{(t['avg_after_neg_signal_pct'] if t['avg_after_neg_signal_pct'] is not None else float('nan')):+.2f}%")

    say("\n" + "=" * 72)
    say("  Nota metodologica: out-of-sample = il modello NON ha mai visto i dati")
    say("  su cui viene valutato; è il test onesto. In-sample da solo sovrastima")
    say("  sempre. Questo report è interno (gitignored) — non mostrato agli utenti.")
    say("=" * 72)

    out = os.path.join(ROOT, "model_validation_report.md")
    with open(out, "w") as f:
        f.write("```\n" + "\n".join(REPORT) + "\n```\n")
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
