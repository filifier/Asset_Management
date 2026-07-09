"""
scoring.py — the transparent BI scoring engine.

Philosophy: this DESCRIBES state, it does not PRESCRIBE trades.
Every signal is an explicit, inspectable rule. No black box, no
"buy/sell" output — only "here is where things stand, and here are
the signals pointing each way, so YOU can decide."

Three pillars:
  1. Asset momentum & valuation  — where the asset sits in its own history
  2. Macro context (asset-weighted) — how the environment reads FOR THIS asset
  3. Your position                 — P&L, concentration, size vs risk budget

Each pillar returns a score in [-2, +2] plus a list of the signals that
produced it. Positive = supportive of holding/adding; negative = reasons
a profit-taker would note. The engine NEVER concludes for you.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Literal

from engine import regression


Bias = Literal["supportive", "caution", "neutral"]


@dataclass
class Signal:
    name: str
    reading: str            # human-readable current value
    points: int             # contribution to pillar score (-2..+2 per signal)
    bias: Bias
    why: str                # plain-language rationale


@dataclass
class Pillar:
    key: str
    title: str
    score: float = 0.0
    signals: List[Signal] = field(default_factory=list)

    def add(self, s: Signal):
        self.signals.append(s)
        self.score += s.points

    @property
    def label(self) -> str:
        if self.score >= 1.5: return "Strongly supportive"
        if self.score >= 0.5: return "Supportive"
        if self.score > -0.5: return "Balanced"
        if self.score > -1.5: return "Caution"
        return "Strong caution"


# ── ASSET CONFIG ────────────────────────────────────────────────────
# How much each macro factor matters FOR A GIVEN ASSET TYPE.
# This is what makes the score personalized to the asset chosen.
# Weights are multipliers on the raw macro signal (0 = irrelevant).
ASSET_PROFILES = {
    "clean_energy_equity": {
        "label": "Clean-energy equity fund",
        "macro_weights": {
            "us_10y": 1.0,     # long-duration growth: rates matter a lot
            "vix": 0.7,
            "gold": 0.2,
            "oil": 0.5,        # energy-complex linked
            "eurusd": 0.3,
            "btp_bund": 0.4,
        },
    },
    # add more profiles here as you add asset types
    "broad_equity": {
        "label": "Broad equity",
        "macro_weights": {"us_10y": 0.6, "vix": 1.0, "gold": 0.3,
                          "oil": 0.3, "eurusd": 0.4, "btp_bund": 0.5},
    },
}


# ── PILLAR 1: ASSET MOMENTUM & VALUATION ────────────────────────────
def pillar_asset(nav: float, low52: float, high52: float,
                 ret_1y: float, bench_1y: float,
                 ret_5y: float, bench_5y: float,
                 nav_trend_1m: float = None) -> Pillar:
    p = Pillar("asset", "Asset momentum & valuation")

    # Short-term NAV trend — same "plain % change" outlook logic as the
    # macro outlook pillar, applied to the fund's own price this time.
    if nav_trend_1m is not None:
        direction = "rising" if nav_trend_1m > 0.5 else ("falling" if nav_trend_1m < -0.5 else "flat")
        if direction == "falling":
            pts, bias = -1, "caution"
        elif direction == "rising":
            pts, bias = 1, "supportive"
        else:
            pts, bias = 0, "neutral"
        p.add(Signal("NAV trend (~1 month)", f"{nav_trend_1m:+.1f}% ({direction})", pts, bias,
                     "Trend, not a prediction — plain % change in NAV over the lookback window."))

    # Where in the 52-week range? Near the top = "richer" (a profit-taker's note).
    rng = (nav - low52) / (high52 - low52) if high52 > low52 else 0.5
    if rng >= 0.9:
        p.add(Signal("52-week range", f"{rng*100:.0f}% of range — near highs", -1,
                     "caution", "Near the top of its yearly range; less room before prior resistance."))
    elif rng <= 0.25:
        p.add(Signal("52-week range", f"{rng*100:.0f}% of range — near lows", +1,
                     "supportive", "Near the bottom of its yearly range; historically cheaper entry."))
    else:
        p.add(Signal("52-week range", f"{rng*100:.0f}% of range — mid", 0,
                     "neutral", "Sits in the middle of its yearly range."))

    # Short-term relative strength vs benchmark. NOTE: the benchmark here
    # is the S&P 500 (run.py computes bench_1y/bench_5y from Yahoo's
    # ^GSPC history) — a broad-market proxy, not this fund's official
    # benchmark index. Labelled explicitly so it's never ambiguous what
    # you're actually looking at.
    if ret_1y - bench_1y > 5:
        p.add(Signal("1y vs S&P 500", f"+{ret_1y-bench_1y:.0f}pp ahead", +1,
                     "supportive", "Beating the S&P 500 over the last year — momentum is with it."))
    elif ret_1y - bench_1y < -5:
        p.add(Signal("1y vs S&P 500", f"{ret_1y-bench_1y:.0f}pp behind", -1,
                     "caution", "Lagging the S&P 500 over the last year."))
    else:
        p.add(Signal("1y vs S&P 500", "roughly in line", 0, "neutral",
                     "Tracking the S&P 500 over the last year."))

    # Long-term relative strength (structural)
    if ret_5y - bench_5y < -10:
        p.add(Signal("5y vs S&P 500", f"{ret_5y-bench_5y:.0f}pp behind", -1,
                     "caution", "Structurally behind the S&P 500 over 5 years — recent strength may be cyclical."))
    elif ret_5y - bench_5y > 10:
        p.add(Signal("5y vs S&P 500", f"+{ret_5y-bench_5y:.0f}pp ahead", +1,
                     "supportive", "Structurally ahead of the S&P 500 over 5 years."))
    else:
        p.add(Signal("5y vs S&P 500", "roughly in line", 0, "neutral",
                     "In line with the S&P 500 over 5 years."))
    return p


# ── PILLAR 2: MACRO CONTEXT (asset-weighted) ────────────────────────
def pillar_macro(macro: Dict[str, dict], profile_key: str) -> Pillar:
    """
    macro: dict of {factor: {"raw_bias": -1|0|+1, "reading": str, "note": str}}
      raw_bias is from the asset's POV: +1 tailwind, -1 headwind, 0 neutral.
    profile_key: selects the weighting for the chosen asset type.
    """
    p = Pillar("macro", "Macro context (weighted for this asset)")
    weights = ASSET_PROFILES[profile_key]["macro_weights"]
    for factor, w in weights.items():
        if factor not in macro:
            continue
        m = macro[factor]
        contrib = m["raw_bias"] * w
        # collapse weighted contribution into signal points (-1..+1 band)
        pts = 1 if contrib >= 0.5 else (-1 if contrib <= -0.5 else 0)
        bias = "supportive" if pts > 0 else ("caution" if pts < 0 else "neutral")
        p.add(Signal(factor.replace("_", " ").upper(), m["reading"], pts, bias,
                     f"{m['note']} (weight {w:.1f} for this asset type)."))
    # normalize: macro pillar can accumulate; clamp to [-2,2]
    p.score = max(-2, min(2, p.score))
    return p


# ── PILLAR: MACRO OUTLOOK (trend) ───────────────────────────────────
# Companion to "Macro context": same factors, but reads DIRECTION over
# the recent past instead of the current LEVEL. This is the "forecast"
# layer — deliberately a plain % change over a lookback window, not a
# model. No black box: every number here you could recompute by hand
# from the same history the engine used.
#
# Polarity: does a RISING reading help or hurt this asset type?
#   +1 = rising is supportive, -1 = rising is a headwind, 0 = mixed/
#   asset-dependent (shown for context, doesn't move the score).
TREND_POLARITY = {
    "us_10y": -1,    # rising yields = headwind for growth valuations
    "vix": -1,       # rising volatility = risk-off = headwind
    "gold": -1,      # rising gold = risk-off signal = mild headwind
    "oil": 0,        # mixed / energy-complex linked, no clean direction
    "eurusd": 0,      # mixed / depends on currency exposure
    "btp_bund": 0,   # not tracked yet
}


def pillar_outlook(history: Dict[str, list], profile_key: str,
                   lookback_days: int = 21) -> Pillar:
    """
    history: dict of {factor: [(iso_date, close), ...]}, oldest first —
      same keys as the macro dict (sp500, vix, us_10y, oil_wti, eurusd, gold).
    lookback_days: how many data points back to compare against (~21
      trading days ≈ 1 calendar month).
    """
    p = Pillar("outlook", "Macro outlook (trend, last ~1 month)")
    weights = ASSET_PROFILES[profile_key]["macro_weights"]
    key_map = {"oil": "oil_wti"}  # weight keys vs history/fetch keys differ for oil
    for factor, w in weights.items():
        series = history.get(key_map.get(factor, factor))
        if not series or len(series) <= lookback_days:
            p.add(Signal(factor.replace("_", " ").upper(), "n/a", 0, "neutral",
                         "No historical data available yet for this factor."))
            continue
        closes = [c for _, c in series]
        latest = closes[-1]
        past = closes[len(closes) - 1 - lookback_days]
        if not past:
            continue
        change_pct = (latest - past) / past * 100
        direction = "rising" if change_pct > 0.5 else ("falling" if change_pct < -0.5 else "flat")

        polarity = TREND_POLARITY.get(factor, 0)
        if direction == "flat" or polarity == 0:
            pts, bias = 0, "neutral"
        else:
            signed = 1 if direction == "rising" else -1
            pts = polarity * signed
            bias = "supportive" if pts > 0 else "caution"

        p.add(Signal(factor.replace("_", " ").upper(),
                     f"{change_pct:+.1f}% over ~{lookback_days}d ({direction})",
                     pts, bias,
                     f"Trend, not a prediction — plain % change over the lookback window "
                     f"(weight {w:.1f} for this asset type)."))
    p.score = max(-2, min(2, p.score))
    return p


# ── PILLAR: FACTOR REGRESSION (statistical, descriptive) ────────────
# How sensitive has the fund's daily return historically been to each
# macro factor? Real OLS coefficients with p-values and VIF — see
# engine/regression.py for the full methodology. This is DESCRIPTIVE
# (historical co-movement), not a forecast: it doesn't say whether a
# factor will rise or fall, only how the asset has moved when it did.
# A factor only counts toward the score if statistically significant
# (p < 0.05) — everything else is shown but scored neutral.
def pillar_factor_regression(nav_history: list, macro_history: Dict[str, list]) -> Pillar:
    p = Pillar("factor_regression", "Analisi fattoriale (regressione OLS)")
    result = regression.fit_factor_model(nav_history, macro_history)

    if "error" in result:
        p.add(Signal("Modello", "non disponibile", 0, "neutral", result["error"]))
        return p

    p.add(Signal("In sintesi", regression.narrative_summary(result), 0, "neutral",
                 "Lettura in linguaggio semplice dei soli fattori statisticamente significativi (p<0.05) qui sotto — non un consiglio, solo un riassunto della tabella."))

    p.add(Signal("Adattamento del modello",
                 f"R²={result['r2']:.2f} (adj. {result['adj_r2']:.2f}), n={result['n_obs']}",
                 0, "neutral",
                 f"Regressione OLS sui rendimenti giornalieri, {result['start']} → {result['end']}. "
                 f"R² basso è normale e onesto: i mercati non sono spiegati bene da un modello lineare."))

    for key, stats in result["factors"].items():
        label = regression.FACTOR_LABELS.get(key, key.upper())
        coef, pval, vif = stats["coef"], stats["pvalue"], stats["vif"]
        significant = pval < 0.05
        if significant and coef > 0:
            pts, bias = 1, "supportive"
        elif significant and coef < 0:
            pts, bias = -1, "caution"
        else:
            pts, bias = 0, "neutral"

        vif_note = ""
        if vif is not None:
            if vif >= 10:
                vif_note = f" — VIF={vif:.1f}, alta multicollinearità: coefficiente poco affidabile."
            elif vif >= 5:
                vif_note = f" — VIF={vif:.1f}, multicollinearità moderata."
            else:
                vif_note = f" — VIF={vif:.1f}."

        sig_note = "statisticamente significativo (p<0.05)" if significant else f"non significativo (p={pval:.2f})"
        p.add(Signal(label, f"coef={coef:+.3f}, p={pval:.3f}", pts, bias,
                     f"Sensibilità storica del rendimento dell'asset a variazioni di questo fattore — "
                     f"{sig_note}{vif_note} Non è una previsione: descrive solo la relazione osservata."))

    p.score = max(-2, min(2, p.score))
    return p


# ── PILLAR 3: YOUR POSITION ─────────────────────────────────────────
def pillar_position(invested_amount: float, purchase_nav: float, nav: float,
                    portfolio_value: float) -> Pillar:
    """
    invested_amount: how much you put in, in your reporting currency.
    purchase_nav: the fund's NAV on (or nearest trading day before) your
      purchase date — looked up from history by the caller (run.py's
      nav_on_or_before, or the browser's equivalent for the public
      dashboard's private calculator). Units are never asked for
      directly: they're implied by invested_amount / purchase_nav.
    """
    p = Pillar("position", "Your position")
    if invested_amount <= 0 or not purchase_nav or purchase_nav <= 0:
        p.add(Signal("Position", "not entered", 0, "neutral",
                     "Enter your invested amount and purchase date to activate this pillar."))
        return p

    market_value = invested_amount * nav / purchase_nav
    pnl_pct = (market_value - invested_amount) / invested_amount * 100

    # Unrealised P&L state (a big gain is a profit-taker's note, not a verdict)
    if pnl_pct >= 25:
        p.add(Signal("Unrealised P&L", f"+{pnl_pct:.0f}%", -1, "caution",
                     "Sitting on a sizeable gain — a profit-taker would note the cushion."))
    elif pnl_pct <= -15:
        p.add(Signal("Unrealised P&L", f"{pnl_pct:.0f}%", 0, "neutral",
                     "Underwater — decision hinges on whether your original thesis still holds."))
    else:
        p.add(Signal("Unrealised P&L", f"{pnl_pct:+.0f}%", 0, "neutral",
                     "P&L is in a moderate range."))

    # Concentration
    if portfolio_value > 0:
        conc = market_value / portfolio_value * 100
        if conc >= 30:
            p.add(Signal("Concentration", f"{conc:.0f}% of portfolio", -1, "caution",
                         "This single holding is a large share of your portfolio — concentration risk."))
        elif conc <= 10:
            p.add(Signal("Concentration", f"{conc:.0f}% of portfolio", +1, "supportive",
                         "A modest share of your portfolio — room to size up if conviction is high."))
        else:
            p.add(Signal("Concentration", f"{conc:.0f}% of portfolio", 0, "neutral",
                         "A moderate share of your portfolio."))
    return p


# ── PILLAR 3b: YOUR POSITION, MULTI-HOLDING ─────────────────────────
def pillar_position_multi(holdings: List[dict], portfolio_value: float) -> Pillar:
    """
    Same idea as pillar_position, generalized to N holdings.

    holdings: list of dicts, each: {"name", "invested_amount",
      "market_value"} — market_value is None for holdings we couldn't
      price (fetch failed / not enough history), which are excluded
      from every computation below rather than guessed at.

    Only the AGGREGATE P&L is scored (using the same thresholds as the
    single-holding version) — per-holding P&L is shown for context but
    doesn't add its own points, so a portfolio with 4 holdings doesn't
    just accumulate score from repeating substantially the same signal.
    Concentration IS scored per holding, since each position's size is
    its own, independent risk.
    """
    p = Pillar("position", "Your position")
    priced = [h for h in holdings if h.get("market_value") is not None]
    if not priced:
        p.add(Signal("Position", "not entered", 0, "neutral",
                     "Enter invested amount and purchase date for at least one holding to activate this pillar."))
        return p

    total_invested = sum(h["invested_amount"] for h in priced)
    total_value = sum(h["market_value"] for h in priced)
    total_pnl_pct = (total_value - total_invested) / total_invested * 100 if total_invested else 0

    if total_pnl_pct >= 25:
        p.add(Signal("Portfolio P&L (tracked holdings)", f"+{total_pnl_pct:.0f}%", -1, "caution",
                     "Sitting on a sizeable gain across your tracked holdings — a profit-taker would note the cushion."))
    elif total_pnl_pct <= -15:
        p.add(Signal("Portfolio P&L (tracked holdings)", f"{total_pnl_pct:.0f}%", 0, "neutral",
                     "Underwater overall — decision hinges on whether your original thesis still holds."))
    else:
        p.add(Signal("Portfolio P&L (tracked holdings)", f"{total_pnl_pct:+.0f}%", 0, "neutral",
                     "P&L is in a moderate range across your tracked holdings."))

    if len(holdings) > len(priced):
        skipped = [h["name"] for h in holdings if h.get("market_value") is None]
        p.add(Signal("Not tracked", ", ".join(skipped), 0, "neutral",
                     "No price history available for these — excluded from every number above, not guessed at."))

    for h in priced:
        pnl_pct = (h["market_value"] - h["invested_amount"]) / h["invested_amount"] * 100
        p.add(Signal(f"{h['name']} — P&L", f"{pnl_pct:+.0f}%", 0, "neutral",
                     "Per-holding unrealised P&L — informational, doesn't add to the pillar score (the portfolio total above already does)."))
        if portfolio_value > 0:
            conc = h["market_value"] / portfolio_value * 100
            if conc >= 30:
                p.add(Signal(f"{h['name']} — Concentration", f"{conc:.0f}% of portfolio", -1, "caution",
                             "A large share of your declared portfolio value — concentration risk."))
            elif conc <= 10:
                p.add(Signal(f"{h['name']} — Concentration", f"{conc:.0f}% of portfolio", +1, "supportive",
                             "A modest share of your declared portfolio value — room to size up if conviction is high."))
            else:
                p.add(Signal(f"{h['name']} — Concentration", f"{conc:.0f}% of portfolio", 0, "neutral",
                             "A moderate share of your declared portfolio value."))
    p.score = max(-2, min(2, p.score))
    return p


# ── AGGREGATE ───────────────────────────────────────────────────────
def _assemble(pillars: List[Pillar]) -> dict:
    """Shared aggregation: total score + descriptive summary for a set of pillars."""
    total = sum(p.score for p in pillars)

    # Synthesis is DESCRIPTIVE. It counts signals, it does not command.
    caution_signals = [s for p in pillars for s in p.signals if s.bias == "caution"]
    support_signals = [s for p in pillars for s in p.signals if s.bias == "supportive"]

    summary = (
        f"{len(support_signals)} supportive signal(s) vs {len(caution_signals)} caution "
        f"signal(s). The board leans "
        + ("supportive" if total >= 1 else "toward caution" if total <= -1 else "balanced")
        + ". This is a description of the current state — the decision to hold, add or "
          "trim is yours."
    )
    return {
        "pillars": [
            {"key": p.key, "title": p.title, "score": p.score, "label": p.label,
             "signals": [s.__dict__ for s in p.signals]}
            for p in pillars
        ],
        "total": total,
        "summary": summary,
    }


def build_scorecard_from_pillars(pillars: List[Pillar]) -> dict:
    """Generic assembler: run.py builds whichever pillars apply (one
    pillar_asset per holding now that a portfolio can have several,
    plus macro/outlook/factor_regression/position) and hands the list
    here. Replaces the old single-holding build_scorecard/
    build_public_scorecard, which assumed exactly one asset."""
    return _assemble(pillars)
