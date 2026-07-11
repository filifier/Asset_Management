/*
 * technical.js — classic technical / statistical indicators, computed
 * in the browser from the same daily CLOSE-price history we already
 * load (docs/data/tickers/*.json). No new data, no cost. Gives the
 * "trend & risk" context an analyst reads off a chart, in plain
 * language for a non-expert.
 *
 * Indicators (all close-only, so they work on every published ticker):
 *   - RSI(14, Wilder): overbought / oversold
 *   - Bollinger Band Width(20): volatility contraction vs expansion
 *   - Annualized volatility (last ~60 daily returns)
 *   - Skewness & excess Kurtosis of returns: asymmetry / tail risk
 *   - Momentum: 3-month and 12-month total return
 *
 * NOTE: ADX / Directional Movement need intraday High/Low, which our
 * published files don't carry (close only) — deliberately left out
 * rather than faked from close.
 */

function _sma(a) { return a.reduce((s, x) => s + x, 0) / a.length; }
function _std(a) { const m = _sma(a); return Math.sqrt(a.reduce((s, x) => s + (x - m) ** 2, 0) / a.length); }

// history: [[isoDate, close], ...]. Returns null if too short, else an
// object of current indicator values.
function computeTechnicals(history) {
  const closes = history.map(h => h[1]).filter(v => v > 0);
  if (closes.length < 40) return null;

  const rets = [];
  for (let i = 1; i < closes.length; i++) rets.push((closes[i] - closes[i - 1]) / closes[i - 1]);

  // RSI(14) with Wilder's smoothing
  const P = 14;
  let gain = 0, loss = 0;
  for (let i = 1; i <= P; i++) { const d = closes[i] - closes[i - 1]; if (d >= 0) gain += d; else loss -= d; }
  let ag = gain / P, al = loss / P;
  for (let i = P + 1; i < closes.length; i++) {
    const d = closes[i] - closes[i - 1];
    ag = (ag * (P - 1) + Math.max(d, 0)) / P;
    al = (al * (P - 1) + Math.max(-d, 0)) / P;
  }
  const rsi = al === 0 ? 100 : 100 - 100 / (1 + ag / al);

  // Bollinger Band Width(20) = (upper-lower)/SMA = 4*std/SMA, as %.
  const W = 20;
  const widthAt = (win) => { const m = _sma(win), s = _std(win); return m ? 4 * s / m * 100 : null; };
  const bbWidth = widthAt(closes.slice(-W));
  const widthSeries = [];
  for (let i = W; i <= closes.length; i++) { const w = widthAt(closes.slice(i - W, i)); if (w != null) widthSeries.push(w); }
  const bbAvg = widthSeries.length ? _sma(widthSeries) : bbWidth;
  const bbState = bbWidth == null ? null
    : bbWidth > bbAvg * 1.15 ? "espansione"
    : bbWidth < bbAvg * 0.85 ? "contrazione" : "normale";

  // Annualized volatility from last 60 daily returns
  const rWin = rets.slice(-60);
  const volAnnual = _std(rWin) * Math.sqrt(252) * 100;

  // Skewness & excess kurtosis of last 60 returns
  const m = _sma(rWin), sd = _std(rWin);
  const skew = sd ? rWin.reduce((s, x) => s + ((x - m) / sd) ** 3, 0) / rWin.length : 0;
  const kurt = sd ? rWin.reduce((s, x) => s + ((x - m) / sd) ** 4, 0) / rWin.length - 3 : 0;

  // Momentum (total return over N trading days)
  const mom = (n) => closes.length > n ? (closes[closes.length - 1] / closes[closes.length - 1 - n] - 1) * 100 : null;

  return { rsi, bbWidth, bbState, volAnnual, skew, kurt, mom3m: mom(63), mom12m: mom(252) };
}

// Plain-language, one line per asset.
function technicalNarrative(t, name) {
  const parts = [];
  if (t.rsi >= 70) parts.push(`in <strong>ipercomprato</strong> (RSI ${t.rsi.toFixed(0)}) — è corso molto, possibile pausa`);
  else if (t.rsi <= 30) parts.push(`in <strong>ipervenduto</strong> (RSI ${t.rsi.toFixed(0)}) — molto venduto di recente`);
  else parts.push(`RSI neutro (${t.rsi.toFixed(0)})`);

  if (t.bbState === "espansione") parts.push("volatilità in espansione (movimenti più ampi del solito)");
  else if (t.bbState === "contrazione") parts.push("volatilità in contrazione (fase più calma)");

  if (t.mom12m != null) parts.push(`momentum 12 mesi ${t.mom12m >= 0 ? "+" : ""}${t.mom12m.toFixed(0)}%`);
  else if (t.mom3m != null) parts.push(`momentum 3 mesi ${t.mom3m >= 0 ? "+" : ""}${t.mom3m.toFixed(0)}%`);

  return `<strong>${name}</strong>: ${parts.join(", ")}.`;
}
