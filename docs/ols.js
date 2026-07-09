/*
 * ols.js — browser-side Ordinary Least Squares, so the "Analisi &
 * Previsione" tab can run a regression PER SELECTED ASSET, live, from
 * whatever portfolio the user builds in the browser — without a Python
 * round-trip. Yahoo/backend can't be reached from the browser (CORS),
 * but every input we need is already published as a static file:
 * docs/data/macro_history.json + docs/data/tickers/<SYM>.json.
 *
 * Replicates engine/regression.py's methodology exactly, so numbers
 * match the Python engine:
 *   - term_spread derived as us_10y - us_2y
 *   - rate-like series (yields, spread, VIX, MOVE) use first differences;
 *     price-like series use daily % returns
 *   - inner join on dates so every row has every factor
 *   - us_2y excluded from the model itself (collinear with us_10y +
 *     term_spread); us_10y (level) + term_spread (slope) kept
 *   - coefficients, R², VIF identical to statsmodels
 *
 * The ONE deliberate approximation vs Python: p-values use the normal
 * CDF instead of the Student-t. With n ~1000 observations the t
 * distribution is indistinguishable from normal (df > 900), so this is
 * accurate to well past the 3rd decimal — documented, not hidden.
 */

const OLS_MACRO_KEYS = ["sp500", "vix", "us_10y", "oil_wti", "eurusd", "gold",
                        "move", "us_2y", "dxy", "nasdaq100", "hy_credit"];
const OLS_RATE_LIKE = new Set(["us_10y", "us_2y", "vix", "move", "term_spread"]);
const OLS_FACTORS = ["sp500", "vix", "us_10y", "term_spread", "oil_wti", "eurusd",
                     "gold", "move", "dxy", "nasdaq100", "hy_credit"];
const OLS_FACTOR_LABELS = {
  sp500: "S&P 500", vix: "VIX", us_10y: "US 10Y",
  term_spread: "Term Spread (10Y-2Y)", oil_wti: "Oil (WTI)", eurusd: "EUR/USD",
  gold: "Gold", move: "MOVE", dxy: "DXY", nasdaq100: "NASDAQ 100", hy_credit: "HY Credit",
};

// Φ(x): standard normal CDF via erf (Abramowitz-Stegun 7.1.26, ~1e-7).
function _normalCdf(x) {
  const sign = x < 0 ? -1 : 1;
  const ax = Math.abs(x) / Math.SQRT2;
  const t = 1 / (1 + 0.3275911 * ax);
  const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
                  - 0.284496736) * t + 0.254829592) * t * Math.exp(-ax * ax);
  return 0.5 * (1 + sign * y);
}

// Solve A x = b for a small square A via Gauss-Jordan with partial
// pivoting. Returns null if singular.
function _solve(A, b) {
  const n = A.length;
  const M = A.map((row, i) => row.concat(b[i]));
  for (let col = 0; col < n; col++) {
    let piv = col;
    for (let r = col + 1; r < n; r++) if (Math.abs(M[r][col]) > Math.abs(M[piv][col])) piv = r;
    if (Math.abs(M[piv][col]) < 1e-12) return null;
    [M[col], M[piv]] = [M[piv], M[col]];
    const d = M[col][col];
    for (let j = col; j <= n; j++) M[col][j] /= d;
    for (let r = 0; r < n; r++) {
      if (r === col) continue;
      const f = M[r][col];
      for (let j = col; j <= n; j++) M[r][j] -= f * M[col][j];
    }
  }
  return M.map(row => row[n]);
}

// Invert a small square matrix (needed for coefficient standard errors).
function _invert(A) {
  const n = A.length;
  const cols = [];
  for (let i = 0; i < n; i++) {
    const e = new Array(n).fill(0); e[i] = 1;
    const c = _solve(A, e);
    if (!c) return null;
    cols.push(c);
  }
  // cols[i] is the i-th column of the inverse; transpose into rows
  const inv = Array.from({ length: n }, () => new Array(n));
  for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) inv[i][j] = cols[j][i];
  return inv;
}

// Core OLS: y (n), X (n×k, WITHOUT intercept — added here). Returns
// {coef[], pvalues[], r2, adjR2, n} or null.
function _fitOLS(y, X) {
  const n = y.length;
  if (n < X[0].length + 2) return null;
  const k = X[0].length + 1; // + intercept
  const Xc = X.map(row => [1, ...row]);
  // XtX (k×k) and Xty (k)
  const XtX = Array.from({ length: k }, () => new Array(k).fill(0));
  const Xty = new Array(k).fill(0);
  for (let i = 0; i < n; i++) {
    const xi = Xc[i];
    for (let a = 0; a < k; a++) {
      Xty[a] += xi[a] * y[i];
      for (let b = a; b < k; b++) XtX[a][b] += xi[a] * xi[b];
    }
  }
  for (let a = 0; a < k; a++) for (let b = 0; b < a; b++) XtX[a][b] = XtX[b][a];

  const beta = _solve(XtX, Xty);
  if (!beta) return null;
  const XtXinv = _invert(XtX);
  if (!XtXinv) return null;

  let ssRes = 0, ssTot = 0;
  const yMean = y.reduce((s, v) => s + v, 0) / n;
  for (let i = 0; i < n; i++) {
    let pred = 0;
    for (let a = 0; a < k; a++) pred += beta[a] * Xc[i][a];
    ssRes += (y[i] - pred) ** 2;
    ssTot += (y[i] - yMean) ** 2;
  }
  const df = n - k;
  const sigma2 = ssRes / df;
  const pvalues = beta.map((b, a) => {
    const se = Math.sqrt(sigma2 * XtXinv[a][a]);
    if (!(se > 0)) return 1;
    const t = b / se;
    return 2 * (1 - _normalCdf(Math.abs(t)));
  });
  const r2 = ssTot ? 1 - ssRes / ssTot : 0;
  const adjR2 = ssTot ? 1 - (1 - r2) * (n - 1) / df : 0;
  return { coef: beta, pvalues, r2, adjR2, n };
}

// Align asset + macro histories into a daily-change design matrix,
// exactly as engine/regression.py does.
function _buildChanges(assetHist, macroHist) {
  const assetMap = new Map(assetHist);
  const maps = {};
  for (const k of OLS_MACRO_KEYS) maps[k] = new Map(macroHist[k] || []);

  // dates present in asset AND every macro key
  let dates = [...assetMap.keys()];
  for (const k of OLS_MACRO_KEYS) {
    const m = maps[k];
    dates = dates.filter(d => m.has(d));
  }
  dates.sort();
  if (dates.length < 30) return null;

  // levels per column (asset + factors incl. derived term_spread)
  const cols = ["asset", ...OLS_FACTORS];
  const levels = dates.map(d => {
    const row = { asset: assetMap.get(d) };
    for (const k of OLS_MACRO_KEYS) row[k] = maps[k].get(d);
    row.term_spread = row.us_10y - row.us_2y;
    return row;
  });

  // changes: diff for rate-like, pct*100 otherwise; drop first row
  const changes = [];
  for (let i = 1; i < levels.length; i++) {
    const row = {};
    let ok = true;
    for (const c of cols) {
      const prev = levels[i - 1][c], cur = levels[i][c];
      if (prev == null || cur == null) { ok = false; break; }
      row[c] = OLS_RATE_LIKE.has(c) ? (cur - prev) : (prev ? (cur - prev) / prev * 100 : 0);
    }
    if (ok) changes.push(row);
  }
  return changes.length >= OLS_FACTORS.length * 10 ? changes : null;
}

// Public: regress one asset's returns on the macro factors, with VIF.
// Returns {error} or {n, r2, adjR2, factors:{key:{coef,pvalue,vif}}}.
function runAssetRegression(assetHist, macroHist) {
  const changes = _buildChanges(assetHist, macroHist);
  if (!changes) return { error: "storico insufficiente per stimare il modello" };

  const y = changes.map(r => r.asset);
  const X = changes.map(r => OLS_FACTORS.map(f => r[f]));
  const fit = _fitOLS(y, X);
  if (!fit) return { error: "modello non risolvibile (dati degeneri)" };

  // VIF_j = 1 / (1 - R²_j), R²_j from regressing factor j on the others.
  const vifs = {};
  for (let j = 0; j < OLS_FACTORS.length; j++) {
    const yj = changes.map(r => r[OLS_FACTORS[j]]);
    const Xj = changes.map(r => OLS_FACTORS.filter((_, i) => i !== j).map(f => r[f]));
    const fj = _fitOLS(yj, Xj);
    vifs[OLS_FACTORS[j]] = fj && fj.r2 < 1 ? 1 / (1 - fj.r2) : null;
  }

  const factors = {};
  OLS_FACTORS.forEach((f, i) => {
    factors[f] = { coef: fit.coef[i + 1], pvalue: fit.pvalues[i + 1], vif: vifs[f] };
  });
  return { n: fit.n, r2: fit.r2, adjR2: fit.adjR2, factors };
}

// Plain-language readout for a non-expert. Only mentions factors that
// are BOTH statistically significant (p<0.05) AND not badly collinear
// (VIF < 10) — a significant coefficient on a high-VIF factor has an
// unreliable sign (e.g. NVIDIA "suffering when the S&P 500 rises" is a
// collinearity artifact, not a real relationship), so putting it in
// plain language would mislead. Those factors still appear in the
// table, flagged with their VIF.
function regressionNarrative(result, assetName) {
  if (result.error) return result.error;
  const pos = [], neg = [];
  for (const [k, v] of Object.entries(result.factors)) {
    const reliable = v.pvalue < 0.05 && (v.vif == null || v.vif < 10);
    if (reliable && v.coef > 0) pos.push(OLS_FACTOR_LABELS[k] || k);
    if (reliable && v.coef < 0) neg.push(OLS_FACTOR_LABELS[k] || k);
  }
  if (!pos.length && !neg.length) {
    return `Su questo storico, nessun fattore ha una relazione statisticamente affidabile con ${assetName} — i movimenti sembrano dominati da cause specifiche dell'asset, non dal contesto macro tracciato qui.`;
  }
  const parts = [];
  if (pos.length) parts.push(`si muove storicamente insieme a ${pos.join(", ")}`);
  if (neg.length) parts.push(`tende a soffrire quando sale ${neg.join(", ")}`);
  return `${assetName} ${parts.join("; ")}. Relazione storica, non una garanzia futura.`;
}
