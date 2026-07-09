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
from sklearn.ensemble import RandomForestRegressor
from sklearn.inspection import permutation_importance

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


def fit_random_forest_model(nav_history, macro_history, test_frac=0.2,
                            n_estimators=300, max_depth=5, min_samples_leaf=10):
    """
    EXPERIMENTAL — local-only comparison, not wired into the scorecard yet.

    Random Forest on the same factors/transforms as fit_factor_model(), but
    evaluated honestly: chronological train/test split (no shuffling — this
    is a time series, shuffling would leak future info into training), and
    the OLS model is re-fit on the SAME train split and scored on the SAME
    test split so the R² comparison is apples-to-apples (fit_factor_model's
    R² above is in-sample, which flatters any model).

    Feature ranking uses PERMUTATION importance on the test set, not the
    default impurity-based importance — impurity importance is biased
    toward correlated/high-cardinality features (exactly the SP500/NASDAQ
    collinearity problem flagged in fit_factor_model).

    Returns {"error": "..."} if there's not enough data for a meaningful
    split, else a dict with n_train/n_test, rf_train_r2/rf_test_r2,
    ols_test_r2 (for comparison), and importances per factor.
    """
    levels = _build_levels(nav_history, macro_history)
    changes = _to_changes(levels)
    factors = [f for f in REGRESSION_FACTORS if f in changes.columns]
    n = len(changes)
    split = int(n * (1 - test_frac))
    if n < 100 or split < 50 or (n - split) < 20:
        return {"error": f"not enough aligned data for a train/test split ({n} rows)"}

    y = changes["asset"]
    X = changes[factors]
    X_train, X_test = X.iloc[:split], X.iloc[split:]
    y_train, y_test = y.iloc[:split], y.iloc[split:]

    rf = RandomForestRegressor(n_estimators=n_estimators, max_depth=max_depth,
                               min_samples_leaf=min_samples_leaf, random_state=42, n_jobs=-1)
    rf.fit(X_train, y_train)
    rf_train_r2 = float(rf.score(X_train, y_train))
    rf_test_r2 = float(rf.score(X_test, y_test))

    # Same train/test split, OLS this time, for a fair comparison.
    ols = sm.OLS(y_train, sm.add_constant(X_train)).fit()
    X_test_c = sm.add_constant(X_test, has_constant="add")
    y_pred = ols.predict(X_test_c)
    ss_res = ((y_test - y_pred) ** 2).sum()
    ss_tot = ((y_test - y_test.mean()) ** 2).sum()
    ols_test_r2 = float(1 - ss_res / ss_tot) if ss_tot else None

    perm = permutation_importance(rf, X_test, y_test, n_repeats=30,
                                  random_state=42, n_jobs=-1)

    return {
        "n_train": split,
        "n_test": n - split,
        "test_start": str(changes.index[split]),
        "test_end": str(changes.index[-1]),
        "rf_train_r2": rf_train_r2,
        "rf_test_r2": rf_test_r2,
        "ols_test_r2": ols_test_r2,
        "importances": {
            f: {"importance": float(perm.importances_mean[i]), "std": float(perm.importances_std[i])}
            for i, f in enumerate(factors)
        },
    }
