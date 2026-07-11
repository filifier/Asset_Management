/*
 * ff.js — Fama-French-Carhart factor analysis, in the browser.
 *
 * Decomposes the user's (weighted) portfolio into the four academic
 * risk factors — Market, Size (SMB), Value (HML), Momentum (WML) —
 * exactly as an equity quant would, using Ken French's free daily
 * "Developed markets" factors (published by run.py to
 * docs/data/ff_factors.json). Reuses the OLS engine from ols.js
 * (_fitOLS): the regression is
 *
 *     (r_portfolio − RF)  =  α  +  β_mkt·(Mkt−RF) + β_smb·SMB
 *                              + β_hml·HML + β_wml·WML  +  ε
 *
 * The betas are the portfolio's factor *tilts*; α (intercept) is the
 * bit of return the factors DON'T explain. Everything is descriptive
 * and honest — α on a personal portfolio is almost never statistically
 * significant, and we say so.
 */

// Weighted portfolio daily returns (in %) from the held assets.
// holdings: [{weight, history:[[isoDate, price], ...]}]. Returns
// [[isoDate, portfolioReturnPct], ...] on the dates all holdings share.
function buildPortfolioReturns(holdings) {
  const retMaps = holdings.map(h => {
    const m = new Map();
    for (let i = 1; i < h.history.length; i++) {
      const p0 = h.history[i - 1][1], p1 = h.history[i][1];
      if (p0) m.set(h.history[i][0], (p1 - p0) / p0 * 100);
    }
    return { weight: h.weight, m };
  }).filter(r => r.m.size);
  if (!retMaps.length) return [];

  let dates = null;
  for (const r of retMaps) {
    const s = new Set(r.m.keys());
    dates = dates ? new Set([...dates].filter(x => s.has(x))) : s;
  }
  const out = [];
  for (const d of [...dates].sort()) {
    let r = 0;
    for (const rm of retMaps) r += rm.weight * rm.m.get(d);
    out.push([d, r]);
  }
  return out;
}

// Carhart 4-factor regression. portfolioReturns: [[date, ret%], ...];
// ffFactors: [[date, mktrf, smb, hml, wml, rf], ...] (percent). Returns
// {error} or {n, r2, adjR2, alphaDaily, alphaAnnual, alphaPvalue,
//  factors:[{name, beta, pvalue}]}.
function runCarhartRegression(portfolioReturns, ffFactors) {
  const ffMap = new Map(ffFactors.map(r => [r[0], r]));
  const y = [], X = [];
  for (const [d, ret] of portfolioReturns) {
    const ff = ffMap.get(d);
    if (!ff) continue;
    const mktrf = ff[1], smb = ff[2], hml = ff[3], wml = ff[4], rf = ff[5];
    // skip holiday-like all-zero factor rows
    if (mktrf === 0 && smb === 0 && hml === 0 && wml === 0) continue;
    y.push(ret - rf);
    X.push([mktrf, smb, hml, wml]);
  }
  if (y.length < 30) return { error: "storico insufficiente per l'analisi fattoriale (servono più dati sovrapposti)." };

  const fit = _fitOLS(y, X);  // coef[0] = alpha (intercept); coef[1..4] = betas
  if (!fit) return { error: "modello non risolvibile (dati degeneri)." };

  const names = ["Mkt-RF", "SMB", "HML", "WML"];
  return {
    n: fit.n, r2: fit.r2, adjR2: fit.adjR2,
    alphaDaily: fit.coef[0],
    alphaAnnual: fit.coef[0] * 252,   // daily % → annualized %
    alphaPvalue: fit.pvalues[0],
    factors: names.map((nm, i) => ({ name: nm, beta: fit.coef[i + 1], pvalue: fit.pvalues[i + 1] })),
  };
}

const FF_LABELS = {
  "Mkt-RF": "Mercato (Mkt-RF)", "SMB": "Dimensione (SMB)",
  "HML": "Stile Value/Growth (HML)", "WML": "Momentum (WML)",
};

// Plain-language readout of the tilts, for a non-expert. Only names a
// style tilt when it's statistically meaningful (p < 0.05); the market
// beta is always described.
function carhartNarrative(res, assetName) {
  const b = {};
  res.factors.forEach(f => { b[f.name] = f; });
  const parts = [];

  const mkt = b["Mkt-RF"].beta;
  const rel = mkt > 1.1 ? "amplifica i movimenti del" : mkt < 0.9 ? "si muove meno del" : "si muove come il";
  parts.push(`${rel} mercato azionario globale (beta ${mkt.toFixed(2)})`);

  const smb = b["SMB"];
  if (smb.pvalue < 0.05)
    parts.push(smb.beta > 0 ? "con un'inclinazione verso le piccole società (small cap)"
                            : "con un'inclinazione verso le grandi società (large cap)");
  const hml = b["HML"];
  if (hml.pvalue < 0.05)
    parts.push(hml.beta > 0 ? "e uno stile «value» (società sottovalutate)"
                            : "e uno stile «growth» (società in crescita)");
  const wml = b["WML"];
  if (wml.pvalue < 0.05)
    parts.push(wml.beta > 0 ? "con esposizione positiva al momentum (segue i trend)"
                            : "con esposizione contraria al momentum");

  return `${assetName || "Il tuo portafoglio"} ${parts.join(", ")}.`;
}
