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

    # Short-term relative strength vs benchmark
    if ret_1y - bench_1y > 5:
        p.add(Signal("1y vs benchmark", f"+{ret_1y-bench_1y:.0f}pp ahead", +1,
                     "supportive", "Beating its benchmark over the last year — momentum is with it."))
    elif ret_1y - bench_1y < -5:
        p.add(Signal("1y vs benchmark", f"{ret_1y-bench_1y:.0f}pp behind", -1,
                     "caution", "Lagging its benchmark over the last year."))
    else:
        p.add(Signal("1y vs benchmark", "roughly in line", 0, "neutral",
                     "Tracking its benchmark over the last year."))

    # Long-term relative strength (structural)
    if ret_5y - bench_5y < -10:
        p.add(Signal("5y vs benchmark", f"{ret_5y-bench_5y:.0f}pp behind", -1,
                     "caution", "Structurally behind its benchmark over 5 years — recent strength may be cyclical."))
    elif ret_5y - bench_5y > 10:
        p.add(Signal("5y vs benchmark", f"+{ret_5y-bench_5y:.0f}pp ahead", +1,
                     "supportive", "Structurally ahead over 5 years."))
    else:
        p.add(Signal("5y vs benchmark", "roughly in line", 0, "neutral",
                     "In line with its benchmark over 5 years."))
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
        if not series or len(series) < 2:
            p.add(Signal(factor.replace("_", " ").upper(), "n/a", 0, "neutral",
                         "No historical data available yet for this factor."))
            continue
        closes = [c for _, c in series]
        latest = closes[-1]
        past = closes[max(0, len(closes) - 1 - lookback_days)]
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


# ── PILLAR 3: YOUR POSITION ─────────────────────────────────────────
def pillar_position(units: float, avg_cost: float, nav: float,
                    portfolio_value: float) -> Pillar:
    p = Pillar("position", "Your position")
    if units <= 0 or avg_cost <= 0:
        p.add(Signal("Position", "not entered", 0, "neutral",
                     "Enter your units and average cost to activate this pillar."))
        return p

    cost_basis = units * avg_cost
    market_value = units * nav
    pnl_pct = (market_value - cost_basis) / cost_basis * 100

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


def build_scorecard(inputs: dict) -> dict:
    """Full scorecard: asset + macro + outlook + your position. Contains
    personal portfolio figures (P&L, concentration) — keep this one
    local, never publish it."""
    a = pillar_asset(**inputs["asset"], nav_trend_1m=inputs.get("asset_nav_trend_1m"))
    m = pillar_macro(inputs["macro"], inputs["profile_key"])
    o = pillar_outlook(inputs["macro_history"], inputs["profile_key"])
    pos = pillar_position(**inputs["position"])
    return _assemble([a, m, o, pos])


def build_public_scorecard(inputs: dict) -> dict:
    """Asset + macro + outlook — no position/portfolio data. Safe to
    publish: describes the asset and market against themselves, not
    your personal holding."""
    a = pillar_asset(**inputs["asset"], nav_trend_1m=inputs.get("asset_nav_trend_1m"))
    m = pillar_macro(inputs["macro"], inputs["profile_key"])
    o = pillar_outlook(inputs["macro_history"], inputs["profile_key"])
    return _assemble([a, m, o])
