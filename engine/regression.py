"""
regression.py — multi-factor OLS: how sensitive has the fund's daily
return historically been to each macro factor, with real statistical
diagnostics (p-values, R², VIF for multicollinearity).

This is DESCRIPTIVE — historical co-movement with each factor — NOT
predictive. It does not forecast future returns, and it does not say
whether a factor will rise or fall (that's what the "Macro outlook"
pillar's trend signals are for). See README "Analisi fattoriale" for
the full methodology writeup, including why some requested factors
(REAL_YIELD, INFLATION_BREAKEVEN, CESI) aren't included: no free data
source was found for them.

Methodology, spelled out so it's checkable:
  - All series aligned on 2021-01-01+ dates (engine/fetch.py's
    HISTORY_START_DATE), inner-joined so every row has every factor —
    deliberately excludes the 2020 COVID crash/rebound.
  - Price-like series (equities, commodities, FX, credit ETF, the fund
    itself) use daily % returns. Rate-like series (yields, the 10Y-2Y
    term spread, VIX, MOVE) use daily first differences (level change),
    not % returns — a % return on a value that can cross zero (like a
    yield spread) is not meaningful.
  - US2Y is dropped from the regression itself: US10Y and US2Y and
    TERM_SPREAD (=US10Y-US2Y) are exactly collinear (one is a linear
    combination of the other two) — including all three would make the
    design matrix singular. We keep US10Y (level) + TERM_SPREAD (slope)
    — a standard "level + slope" decomposition of the yield curve — and
    drop the redundant US2Y from the model (it's still shown elsewhere,
    e.g. in the chart).
  - No selection/regularization: every candidate factor is included
    and reported, including ones with high VIF (e.g. SP500 and NASDAQ
    100 are highly correlated) — flagged, not hidden or dropped.
"""

import pandas as pd
import statsmodels.api as sm
from statsmodels.stats.outliers_influence import variance_inflation_factor

# Rate/spread/volatility-index series: use first differences (level
# change), not % returns (their level can be near/cross zero).
RATE_LIKE_FACTORS = {"us_10y", "us_2y", "vix", "move", "term_spread"}

# Factors offered to the model — us_2y intentionally excluded here (see
# module docstring); it's still fetched/charted, just not regressed
# alongside its own derived term_spread.
REGRESSION_FACTORS = [
    "sp500", "vix", "us_10y", "term_spread", "oil_wti", "eurusd",
    "gold", "move", "dxy", "nasdaq100", "hy_credit",
]

FACTOR_LABELS = {
    "sp500": "S&P 500", "vix": "VIX", "us_10y": "US 10Y",
    "term_spread": "Term Spread (10Y-2Y)", "oil_wti": "Oil (WTI)",
    "eurusd": "EUR/USD", "gold": "Gold", "move": "MOVE", "dxy": "DXY",
    "nasdaq100": "NASDAQ 100", "hy_credit": "HY Credit",
}


def _to_frame(history, col):
    if not history:
        return pd.DataFrame(columns=[col])
    df = pd.DataFrame(history, columns=["date", col])
    return df.set_index("date")


def _build_levels(nav_history, macro_history):
    """Align fund NAV + macro factors on shared trading dates (inner
    join — only dates where every series has a value survive), add the
    derived term_spread column."""
    frames = [_to_frame(nav_history, "asset")]
    for key in ("sp500", "vix", "us_10y", "oil_wti", "eurusd", "gold",
                "move", "us_2y", "dxy", "nasdaq100", "hy_credit"):
        frames.append(_to_frame(macro_history.get(key, []), key))
    df = pd.concat(frames, axis=1, join="inner").sort_index()
    if "us_10y" in df.columns and "us_2y" in df.columns:
        df["term_spread"] = df["us_10y"] - df["us_2y"]
    return df


def _to_changes(levels):
    out = pd.DataFrame(index=levels.index)
    for col in levels.columns:
        out[col] = levels[col].diff() if col in RATE_LIKE_FACTORS else levels[col].pct_change() * 100
    return out.dropna()


def build_portfolio_nav(holdings_histories: dict, weights: dict) -> list:
    """
    holdings_histories: {holding_name: [(iso_date, price), ...]}
    weights: {holding_name: float}, current (today's) portfolio weights.

    Returns a synthetic portfolio price series (list of (iso_date, value)
    tuples, indexed to 100 at the first date every holding has a price),
    built by applying TODAY'S weights retroactively across the whole
    history.

    This is a deliberate simplification: it answers "how would a
    portfolio with today's mix have behaved historically", not "how did
    my actual portfolio evolve" (which would require simulating cash
    flows from each holding's own purchase date — real, but a lot more
    machinery for a personal tool). Documented here and in the pillar's
    own signal text so it's never presented as more than it is.
    """
    frames = {name: dict(hist) for name, hist in holdings_histories.items() if hist}
    if not frames:
        return []
    common_dates = set.intersection(*(set(d.keys()) for d in frames.values()))
    if not common_dates:
        return []
    common_dates = sorted(common_dates)
    bases = {name: frames[name][common_dates[0]] for name in frames}
    portfolio = []
    for d in common_dates:
        val = sum(weights.get(name, 0) * (frames[name][d] / bases[name])
                  for name in frames if bases[name])
        portfolio.append((d, val * 100))
    return portfolio


def narrative_summary(result: dict) -> str:
    """Plain-language readout for a non-expert. Only mentions factors
    that are BOTH statistically significant (p<0.05) AND not badly
    collinear (VIF < 10) — a significant coefficient on a high-VIF
    factor has an unreliable sign, so putting it in plain language would
    mislead. Those factors still appear in the coefficient table,
    flagged with their VIF."""
    if "error" in result:
        return result["error"]

    def reliable(v):
        return v["pvalue"] < 0.05 and (v.get("vif") is None or v["vif"] < 10)

    positive = [FACTOR_LABELS.get(k, k) for k, v in result["factors"].items()
               if reliable(v) and v["coef"] > 0]
    negative = [FACTOR_LABELS.get(k, k) for k, v in result["factors"].items()
               if reliable(v) and v["coef"] < 0]
    if not positive and not negative:
        return ("Su questo storico, nessun fattore ha una relazione statisticamente "
                "affidabile con il tuo portafoglio — i movimenti sembrano dominati da "
                "cause specifiche dell'asset, non dal contesto macro tracciato qui.")
    parts = []
    if positive:
        parts.append(f"si muove storicamente insieme a {', '.join(positive)}")
    if negative:
        parts.append(f"tende a soffrire quando sale {', '.join(negative)}")
    return "Il tuo portafoglio " + "; ".join(parts) + ". Relazione storica, non una garanzia futura."


def fit_factor_model(nav_history, macro_history):
    """Returns a dict:
      {"n_obs": int, "r2": float, "adj_r2": float, "start": iso, "end": iso,
       "factors": {key: {"coef", "pvalue", "vif"}}}
    or {"error": "..."} if there's not enough aligned data (e.g. a
    fetch failed and left a factor's history empty)."""
    levels = _build_levels(nav_history, macro_history)
    changes = _to_changes(levels)
    factors = [f for f in REGRESSION_FACTORS if f in changes.columns]
    min_obs = len(factors) * 10  # rough floor so the fit isn't a coin flip
    if changes.empty or len(factors) < 3 or len(changes) < min_obs:
        return {"error": f"not enough aligned historical data to fit a model "
                          f"({len(changes)} rows, {len(factors)} factors available)"}

    y = changes["asset"]
    X = sm.add_constant(changes[factors])
    model = sm.OLS(y, X).fit()

    vifs = {}
    for i, col in enumerate(X.columns):
        if col == "const":
            continue
        try:
            vifs[col] = float(variance_inflation_factor(X.values, i))
        except Exception:
            vifs[col] = None

    return {
        "n_obs": int(model.nobs),
        "r2": float(model.rsquared),
        "adj_r2": float(model.rsquared_adj),
        "start": str(changes.index.min()),
        "end": str(changes.index.max()),
        "factors": {
            f: {
                "coef": float(model.params[f]),
                "pvalue": float(model.pvalues[f]),
                "vif": vifs.get(f),
            } for f in factors
        },
    }
