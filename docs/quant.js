/*
 * quant.js — the institutional-style quant models, computed IN THE
 * BROWSER on the same daily close-price data everything else uses.
 * No paid API, no server: pure computation.
 *
 *   - GARCH(1,1): volatility clustering & conditional-vol forecast
 *     (variance targeting + maximum likelihood via grid search — a
 *     deliberate, reproducible fit rather than a black-box optimizer)
 *   - Value at Risk: historical, variance-covariance (normal), and
 *     Monte Carlo (bootstrap of real daily returns, seeded so results
 *     are reproducible), plus Expected Shortfall
 *   - Time-Series Momentum (TSMOM): sign of past returns over multiple
 *     horizons + EMA(20)/EMA(100) crossover
 *   - Efficiency metrics: Sharpe, Sortino, Calmar, max drawdown
 *   - Diversification stats: HHI concentration, effective N, average
 *     pairwise correlation
 *
 * Every model returns a 0–100 score with an honest label. Scores are
 * DESCRIPTIVE readings of the data, not signals; the diversification
 * section produces observations ("what an analyst would notice"), never
 * buy/sell recommendations — same philosophy as the rest of the app.
 *
 * Conventions (mirrored 1:1 by tools/quant_check.py, the Excel
 * double-check): daily returns in percent; population standard
 * deviation (divide by n); 252 trading days per year; linear-
 * interpolation quantiles; EMA seeded with the SMA of the first N
 * values; Monte Carlo seeded with mulberry32(42).
 */

// ── small numeric helpers ──
function _qmean(a) { return a.reduce((s, x) => s + x, 0) / a.length; }
function _qstd(a) { const m = _qmean(a); return Math.sqrt(a.reduce((s, x) => s + (x - m) ** 2, 0) / a.length); }

// Linear-interpolation quantile (numpy default). a must be sorted asc.
function _quantileSorted(a, p) {
  const pos = (a.length - 1) * p, lo = Math.floor(pos), frac = pos - lo;
  return lo + 1 < a.length ? a[lo] + frac * (a[lo + 1] - a[lo]) : a[lo];
}

// Deterministic RNG so Monte Carlo numbers are reproducible (and match
// the Python double-check exactly).
function _mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ── efficiency / performance metrics ──
// rets: daily % returns. rfDaily: average daily risk-free in % (from the
// Ken French RF series over the same sample; 0 if unavailable).
function perfMetrics(rets, rfDaily) {
  const n = rets.length;
  if (n < 40) return null;
  rfDaily = rfDaily || 0;
  const mean = _qmean(rets), sd = _qstd(rets);
  const excess = rets.map(r => r - rfDaily);
  const annRet = (Math.pow(rets.reduce((p, r) => p * (1 + r / 100), 1), 252 / n) - 1) * 100; // CAGR
  const annVol = sd * Math.sqrt(252);
  const sharpe = sd ? _qmean(excess) / sd * Math.sqrt(252) : 0;
  const downside = excess.filter(r => r < 0);
  const dsd = downside.length ? Math.sqrt(downside.reduce((s, x) => s + x * x, 0) / excess.length) : 0;
  const sortino = dsd ? _qmean(excess) / dsd * Math.sqrt(252) : null;
  // max drawdown on the cumulative wealth path
  let wealth = 1, peak = 1, maxDD = 0;
  for (const r of rets) {
    wealth *= 1 + r / 100;
    if (wealth > peak) peak = wealth;
    const dd = (wealth - peak) / peak;
    if (dd < maxDD) maxDD = dd;
  }
  maxDD *= 100; // negative %
  const calmar = maxDD < 0 ? annRet / Math.abs(maxDD) : null;
  return { n, annRet, annVol, sharpe, sortino, maxDD, calmar, meanDaily: mean, sdDaily: sd };
}

// ── GARCH(1,1) with variance targeting ──
// sigma2_t = omega + alpha*eps_{t-1}^2 + beta*sigma2_{t-1},
// omega = uncondVar*(1-alpha-beta). Fit by maximizing the Gaussian
// log-likelihood over an (alpha, beta) grid, then a finer local grid.
// Grid MLE is transparent and 100% reproducible in Excel/Python.
function garchFit(rets) {
  const n = rets.length;
  if (n < 150) return null;
  const mu = _qmean(rets);
  const eps = rets.map(r => r - mu);
  const uncondVar = eps.reduce((s, e) => s + e * e, 0) / n;
  if (!uncondVar) return null;

  function negLL(alpha, beta) {
    const omega = uncondVar * (1 - alpha - beta);
    let sigma2 = uncondVar, ll = 0;
    for (let t = 1; t < n; t++) {
      sigma2 = omega + alpha * eps[t - 1] * eps[t - 1] + beta * sigma2;
      if (sigma2 <= 0) return Infinity;
      ll += Math.log(sigma2) + (eps[t] * eps[t]) / sigma2;
    }
    return ll; // ∝ -2·loglik, constant dropped
  }

  let best = { a: 0.05, b: 0.90, v: Infinity };
  for (let a = 0.02; a <= 0.301; a += 0.02) {
    for (let b = 0.55; b <= 0.981; b += 0.01) {
      if (a + b >= 0.999) continue;
      const v = negLL(a, b);
      if (v < best.v) best = { a, b, v };
    }
  }
  // Refinement window spans ±0.02 in BOTH parameters — i.e. a full
  // coarse-grid cell on each side. With ±0.01 on beta the true optimum
  // can sit just outside the window of the winning coarse cell when the
  // likelihood ridge is flat (verified on real data), making the result
  // fragile to which near-tied coarse point wins.
  for (let a = Math.max(0.005, best.a - 0.02); a <= best.a + 0.0201; a += 0.005) {
    for (let b = Math.max(0.4, best.b - 0.02); b <= Math.min(0.995, best.b + 0.0201); b += 0.0025) {
      if (a + b >= 0.999) continue;
      const v = negLL(a, b);
      if (v < best.v) best = { a, b, v };
    }
  }

  const alpha = best.a, beta = best.b;
  const omega = uncondVar * (1 - alpha - beta);
  // filter to get sigma2 at the end of the sample + 1-step forecast
  let sigma2 = uncondVar;
  for (let t = 1; t < n; t++) sigma2 = omega + alpha * eps[t - 1] * eps[t - 1] + beta * sigma2;
  const sigma2Next = omega + alpha * eps[n - 1] * eps[n - 1] + beta * sigma2;
  // k-step forecasts revert to uncondVar at rate (alpha+beta)
  const pers = alpha + beta;
  let f = 0, s2k = sigma2Next;
  for (let k = 0; k < 10; k++) { f += s2k; s2k = uncondVar + pers * (s2k - uncondVar); }
  const avg10 = f / 10;

  return {
    n, alpha, beta, omega, persistence: pers,
    uncondVolAnn: Math.sqrt(uncondVar * 252),
    condVolAnn: Math.sqrt(sigma2Next * 252),
    condVolDaily: Math.sqrt(sigma2Next),
    forecast10dVolAnn: Math.sqrt(avg10 * 252),
    regimeRatio: Math.sqrt(sigma2Next / uncondVar),
  };
}

function garchNarrative(g) {
  const ratio = g.regimeRatio;
  const regime = ratio > 1.25 ? "un regime di volatilità <strong>alta</strong> (cluster turbolento in corso)"
    : ratio < 0.8 ? "un regime di volatilità <strong>bassa</strong> (fase calma)"
    : "un regime di volatilità <strong>nella norma</strong>";
  const dir = g.forecast10dVolAnn > g.condVolAnn * 1.02 ? "in aumento"
    : g.forecast10dVolAnn < g.condVolAnn * 0.98 ? "in rientro verso la media" : "stabile";
  return `Il portafoglio è ora in ${regime}: volatilità condizionata ≈ <strong>${g.condVolAnn.toFixed(0)}%</strong> annua contro una media di lungo periodo del ${g.uncondVolAnn.toFixed(0)}%. La persistenza (α+β = ${g.persistence.toFixed(2)}) indica che i periodi turbolenti/calmi tendono a durare; la previsione a 10 giorni è ${dir}.`;
}

// ── Value at Risk ──
// rets: daily % returns; condVolDaily: GARCH 1-step vol (optional);
// value: current market value in € (optional, for € amounts).
// All VaR figures are POSITIVE loss percentages.
function varAnalysis(rets, condVolDaily, value) {
  if (rets.length < 100) return null;
  const sorted = rets.slice().sort((a, b) => a - b);
  const var95h = -_quantileSorted(sorted, 0.05);
  const var99h = -_quantileSorted(sorted, 0.01);
  const tail95 = sorted.filter(r => r <= -var95h);
  const es95 = tail95.length ? -_qmean(tail95) : var95h;

  const sd = _qstd(rets);
  const var95p = 1.645 * sd, var99p = 2.326 * sd;
  const var95g = condVolDaily ? 1.645 * condVolDaily : null;
  const var99g = condVolDaily ? 2.326 * condVolDaily : null;

  // Monte Carlo: 10,000 bootstrapped 10-day paths from REAL daily
  // returns (no normality assumption), compounded. Seeded → reproducible.
  const rng = _mulberry32(42), N = 10000, H = 10, mc = new Array(N);
  for (let i = 0; i < N; i++) {
    let w = 1;
    for (let k = 0; k < H; k++) w *= 1 + rets[Math.floor(rng() * rets.length)] / 100;
    mc[i] = (w - 1) * 100;
  }
  mc.sort((a, b) => a - b);
  const var95mc = -_quantileSorted(mc, 0.05);
  const var99mc = -_quantileSorted(mc, 0.01);

  const eur = (pct) => (value && pct != null) ? value * pct / 100 : null;
  return {
    n: rets.length, value: value || null,
    hist: { var95: var95h, var99: var99h, es95 },
    param: { var95: var95p, var99: var99p, sd },
    paramGarch: (var95g != null) ? { var95: var95g, var99: var99g } : null,
    mc10d: { var95: var95mc, var99: var99mc, nSims: N, horizon: H },
    eur95: eur(var95h), eur99: eur(var99h), eurEs95: eur(es95),
    eur99mc10d: eur(var99mc),
  };
}

function varNarrative(v) {
  const fmtEur = (x) => x != null ? ` (≈ €${Math.round(x).toLocaleString("it-IT")})` : "";
  return `In una giornata davvero negativa (1 su 100), storicamente il portafoglio ha perso fino a <strong>${v.hist.var99.toFixed(1)}%</strong>${fmtEur(v.eur99)}. Nel 5% peggiore delle giornate la perdita media è stata del ${v.hist.es95.toFixed(1)}%${fmtEur(v.eurEs95)} (Expected Shortfall). Su 10 giorni, lo scenario Monte Carlo 1-su-100 arriva a <strong>${v.mc10d.var99.toFixed(1)}%</strong>${fmtEur(v.eur99mc10d)}. Fotografia del passato e simulazione, non un limite garantito.`;
}

// ── Time-Series Momentum ──
// closes: array of prices (oldest first). Multi-horizon sign of past
// total return + EMA(20)/EMA(100) crossover, averaged into [-1, +1].
function _ema(closes, N) {
  if (closes.length < N + 1) return null;
  const k = 2 / (N + 1);
  let e = _qmean(closes.slice(0, N)); // seed: SMA of first N
  for (let i = N; i < closes.length; i++) e = closes[i] * k + e * (1 - k);
  return e;
}

function tsmom(closes) {
  if (closes.length < 130) return null;
  const last = closes[closes.length - 1];
  const horizons = [21, 63, 126, 252].filter(h => closes.length > h);
  const signals = horizons.map(h => {
    const past = closes[closes.length - 1 - h];
    const ret = (last / past - 1) * 100;
    return { h, ret, sign: ret > 0 ? 1 : ret < 0 ? -1 : 0 };
  });
  const ema20 = _ema(closes, 20), ema100 = _ema(closes, 100);
  const emaSign = (ema20 != null && ema100 != null) ? (ema20 > ema100 ? 1 : -1) : 0;
  const parts = signals.map(s => s.sign).concat(emaSign !== 0 ? [emaSign] : []);
  const score = parts.length ? _qmean(parts) : 0;
  const label = score >= 0.6 ? "rialzista netto" : score >= 0.2 ? "rialzista moderato"
    : score <= -0.6 ? "ribassista netto" : score <= -0.2 ? "ribassista moderato" : "laterale / misto";
  return { signals, ema20, ema100, emaSign, score, label };
}

function tsmomNarrative(t, name) {
  const emaTxt = t.emaSign > 0 ? "la media mobile veloce (EMA 20) è sopra quella lenta (EMA 100)"
    : t.emaSign < 0 ? "la media mobile veloce (EMA 20) è sotto quella lenta (EMA 100)" : "";
  const horizTxt = `${t.signals.filter(s => s.sign > 0).length} orizzonti su ${t.signals.length} in positivo`;
  return `<strong>${name}</strong>: trend <strong>${t.label}</strong> — ${horizTxt}${emaTxt ? ", " + emaTxt : ""}.`;
}

// ── diversification stats ──
// assets: [{name, weight, history: [[date, close], ...]}]
function diversificationStats(assets) {
  const weights = assets.map(a => ({ name: a.name, weight: a.weight }));
  const hhi = assets.reduce((s, a) => s + a.weight * a.weight, 0);
  const effN = hhi > 0 ? 1 / hhi : null;

  const retMaps = assets.map(a => {
    const m = new Map();
    for (let i = 1; i < a.history.length; i++) {
      const p0 = a.history[i - 1][1];
      if (p0) m.set(a.history[i][0], (a.history[i][1] - p0) / p0 * 100);
    }
    return m;
  });
  const pairs = [];
  for (let i = 0; i < assets.length; i++) {
    for (let j = i + 1; j < assets.length; j++) {
      const x = [], y = [];
      for (const [d, r] of retMaps[i]) {
        if (retMaps[j].has(d)) { x.push(r); y.push(retMaps[j].get(d)); }
      }
      if (x.length < 60) continue;
      const mx = _qmean(x), my = _qmean(y);
      let sxy = 0, sxx = 0, syy = 0;
      for (let k = 0; k < x.length; k++) {
        sxy += (x[k] - mx) * (y[k] - my); sxx += (x[k] - mx) ** 2; syy += (y[k] - my) ** 2;
      }
      const corr = (sxx && syy) ? sxy / Math.sqrt(sxx * syy) : null;
      if (corr != null) pairs.push({ a: assets[i].name, b: assets[j].name, corr, n: x.length });
    }
  }
  const avgCorr = pairs.length ? _qmean(pairs.map(p => p.corr)) : null;
  return { weights, hhi, effN, pairs, avgCorr };
}

// ── model scores, 0–100 ──
// Efficiency: higher = better risk-adjusted performance.
// Risk scores: higher = MORE risk (honest direction, labelled as such).
const clamp100 = (x) => Math.max(0, Math.min(100, Math.round(x)));
function scoreEfficiency(met) { return met ? clamp100(50 + 25 * met.sharpe) : null; }
function scoreGarchRisk(g) { return g ? clamp100(g.condVolAnn * 2.2) : null; }
function scoreVarRisk(v) { return v ? clamp100(v.hist.var99 * 18) : null; }
function scoreTrend(t) { return t ? clamp100((t.score + 1) / 2 * 100) : null; }
function scoreDiversification(d) {
  if (!d) return null;
  const spread = 1 - d.hhi;                       // 0 = one asset, →1 = many
  const deco = d.avgCorr == null ? 1 : (1 - Math.max(0, d.avgCorr)); // decorrelation
  return clamp100(100 * spread * (0.4 + 0.6 * deco));
}
function scoreOverallRisk(gs, vs, ds) {
  const parts = [];
  if (gs != null) parts.push(gs);
  if (vs != null) parts.push(vs);
  if (ds != null) parts.push(100 - ds); // concentration adds risk
  return parts.length ? clamp100(_qmean(parts)) : null;
}

// ── diversification insights (descriptive, never prescriptive) ──
// Turns the numbers above + the factor/macro context into the
// observations an analyst would write in the margin. Each bullet cites
// the number it comes from, so nothing here is opinion.
function buildDiversificationInsights(ctx) {
  const { div, met, garch, varres, carhart, olsNeg, tsmomByAsset, sectorTopics } = ctx;
  const out = [];

  if (div && div.weights.length) {
    const top = div.weights.slice().sort((a, b) => b.weight - a.weight)[0];
    if (div.hhi >= 0.5) {
      out.push({ icon: "⚖️", text: `<strong>Concentrazione alta</strong> (HHI ${div.hhi.toFixed(2)}, ≈ ${div.effN.toFixed(1)} titoli "effettivi"): ${top.name} pesa il ${(top.weight * 100).toFixed(0)}% — il destino del portafoglio dipende in gran parte da un solo titolo. I portafogli istituzionali tengono tipicamente il singolo emittente sotto il 10–20%.` });
    } else if (div.hhi >= 0.3) {
      out.push({ icon: "⚖️", text: `<strong>Concentrazione moderata</strong> (≈ ${div.effN.toFixed(1)} titoli "effettivi"): la diversificazione c'è ma è parziale — ${top.name} resta la posizione dominante (${(top.weight * 100).toFixed(0)}%).` });
    }
    if (div.avgCorr != null && div.avgCorr >= 0.6) {
      out.push({ icon: "🔗", text: `<strong>I tuoi titoli si muovono insieme</strong> (correlazione media ${div.avgCorr.toFixed(2)}): nei giorni difficili scendono in gruppo, quindi il beneficio di diversificazione reale è basso. Asset con correlazione bassa o negativa tra loro (obbligazionario di qualità, oro, settori difensivi, aree geografiche diverse) storicamente attenuano le cadute comuni.` });
    } else if (div.avgCorr != null && div.avgCorr <= 0.3) {
      out.push({ icon: "🔗", text: `<strong>Buona de-correlazione</strong> (correlazione media ${div.avgCorr.toFixed(2)}): i titoli non si muovono all'unisono, il che ammortizza i movimenti del portafoglio.` });
    }
  }

  if (carhart && !carhart.error) {
    const b = {}; carhart.factors.forEach(f => { b[f.name] = f; });
    const mkt = b["Mkt-RF"];
    if (mkt && mkt.beta > 1.3) {
      out.push({ icon: "📈", text: `<strong>Beta di mercato elevato</strong> (${mkt.beta.toFixed(2)}): il portafoglio amplifica i movimenti dell'azionario globale, in entrambe le direzioni. Una quota di asset a beta basso ridurrebbe l'ampiezza delle oscillazioni complessive.` });
    }
    const growth = b["HML"] && b["HML"].pvalue < 0.05 && b["HML"].beta < 0;
    const momo = b["WML"] && b["WML"].pvalue < 0.05 && b["WML"].beta > 0;
    if (growth && momo) {
      out.push({ icon: "🧭", text: `<strong>Stile concentrato growth + momentum</strong>: entrambe le inclinazioni sono statisticamente significative. Sono stili che storicamente soffrono insieme nelle rotazioni verso il value (es. fasi di rialzo tassi) — segmenti value/dividendi si comportano in modo complementare.` });
    }
  }

  if (olsNeg && olsNeg.length) {
    out.push({ icon: "🏦", text: `<strong>Esposizione macro comune</strong>: dalle regressioni OLS il portafoglio tende a soffrire quando salgono <strong>${olsNeg.join(", ")}</strong>. Se più titoli condividono la stessa sensibilità, quella è un'unica scommessa macro implicita, non ${olsNeg.length > 1 ? "più scommesse" : "una diversificazione"}.` });
  }

  if (garch && varres && garch.regimeRatio > 1.15) {
    out.push({ icon: "🌪️", text: `<strong>Regime di volatilità alto</strong> (GARCH: vol condizionata ${garch.condVolAnn.toFixed(0)}% vs media ${garch.uncondVolAnn.toFixed(0)}%): in cluster turbolenti il VaR di domani somiglia a quello di oggi. È il contesto in cui la dimensione delle posizioni conta più della loro direzione.` });
  }

  if (sectorTopics && sectorTopics.length >= 1 && sectorTopics.length <= 2) {
    const labels = sectorTopics.map(t => (typeof NEWS_TOPIC_LABELS !== "undefined" && NEWS_TOPIC_LABELS[t]) || t);
    out.push({ icon: "📰", text: `<strong>Settore dominante nelle notizie</strong>: le news che riguardano i tuoi titoli ruotano quasi tutte intorno a <strong>${labels.join(" e ")}</strong> — un altro segnale che il rischio è concentrato su un solo tema.` });
  }

  if (tsmomByAsset && tsmomByAsset.length) {
    const allUp = tsmomByAsset.every(e => e.t && e.t.score > 0.2);
    if (allUp && tsmomByAsset.length > 1) {
      out.push({ icon: "➡️", text: `<strong>Tutti i titoli sono nello stesso trend</strong> (TSMOM positivo ovunque): finché dura è un vento a favore, ma significa anche che un'inversione li colpirebbe tutti insieme.` });
    }
  }

  if (!out.length && met) {
    out.push({ icon: "✅", text: `Dai modelli non emergono squilibri evidenti: concentrazione, correlazioni ed esposizioni fattoriali sono in zona ragionevole per i dati disponibili.` });
  }
  return out;
}
