/*
 * chart.js — shared "Andamento" section: a hand-rolled SVG line chart
 * comparing the asset against benchmark/macro series, plus an optional
 * linear-regression projection.
 *
 * No chart library: this stays consistent with the rest of the project
 * (nothing you can't read top to bottom and recompute by hand). Used by
 * both docs/index.html (public) and web/index.html (local dev) — they
 * just point SERIES_SOURCES at different relative paths.
 */

const SERIES_DEFS = [
  { key: "asset", label: "Asset (NAV)", color: "#2f7a4f", defaultOn: true, historyKey: null },
  { key: "sp500", label: "S&P 500", color: "#b5502a", defaultOn: true, historyKey: "sp500" },
  { key: "vix", label: "VIX", color: "#8a6d3b", defaultOn: false, historyKey: "vix" },
  { key: "us_10y", label: "US 10Y", color: "#5b6ee1", defaultOn: false, historyKey: "us_10y" },
  { key: "gold", label: "Gold", color: "#c9a227", defaultOn: false, historyKey: "gold" },
  { key: "oil_wti", label: "Oil (WTI)", color: "#6b4a3a", defaultOn: false, historyKey: "oil_wti" },
  { key: "eurusd", label: "EUR/USD", color: "#3b8a9e", defaultOn: false, historyKey: "eurusd" },
];

const RANGE_DAYS = { "3M": 63, "6M": 126, "1Y": 252, "5Y": 1260, "All": Infinity };

function sliceRange(series, tradingDays) {
  if (!series || !series.length) return [];
  if (tradingDays === Infinity) return series;
  return series.slice(Math.max(0, series.length - tradingDays));
}

function rebaseTo100(series) {
  if (!series.length) return [];
  const base = series[0][1];
  if (!base) return [];
  return series.map(([d, v]) => [d, (v / base) * 100]);
}

// Ordinary least squares on (index, value) pairs — y = slope*x + intercept.
// Returns null if fewer than 2 points. R^2 tells you how well a straight
// line actually explains the data (expect this to be low/mediocre for a
// noisy price series — that's honest, not a bug).
function linearRegression(series) {
  const n = series.length;
  if (n < 2) return null;
  const xs = series.map((_, i) => i);
  const ys = series.map(([, v]) => v);
  const xMean = xs.reduce((a, b) => a + b, 0) / n;
  const yMean = ys.reduce((a, b) => a + b, 0) / n;
  let num = 0, den = 0;
  for (let i = 0; i < n; i++) {
    num += (xs[i] - xMean) * (ys[i] - yMean);
    den += (xs[i] - xMean) ** 2;
  }
  const slope = den ? num / den : 0;
  const intercept = yMean - slope * xMean;
  let ssRes = 0, ssTot = 0;
  for (let i = 0; i < n; i++) {
    const pred = slope * xs[i] + intercept;
    ssRes += (ys[i] - pred) ** 2;
    ssTot += (ys[i] - yMean) ** 2;
  }
  const r2 = ssTot ? 1 - ssRes / ssTot : 0;
  return { slope, intercept, r2, n };
}

function addTradingDays(isoDate, days) {
  const d = new Date(isoDate + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + Math.round(days * 7 / 5)); // rough trading->calendar day conversion
  return d.toISOString().slice(0, 10);
}

function buildChartSVG(container, seriesData, opts) {
  const width = 760, height = 320, padL = 44, padR = 12, padT = 12, padB = 28;
  const plotW = width - padL - padR, plotH = height - padT - padB;

  const allPoints = seriesData.flatMap(s => s.points);
  const projPoints = opts.projection ? opts.projection.points : [];
  const allForScale = allPoints.concat(projPoints);
  if (!allForScale.length) {
    container.innerHTML = `<div id="private-placeholder">Nessun dato disponibile per il periodo selezionato.</div>`;
    return;
  }
  const values = allForScale.map(p => p[1]);
  let yMin = Math.min(...values), yMax = Math.max(...values);
  if (yMin === yMax) { yMin -= 1; yMax += 1; }
  const yPad = (yMax - yMin) * 0.08;
  yMin -= yPad; yMax += yPad;

  const dates = allPoints.map(p => p[0]).sort();
  const dateMin = dates[0], dateMax = (projPoints.length ? projPoints[projPoints.length - 1][0] : dates[dates.length - 1]);
  const t0 = new Date(dateMin + "T00:00:00Z").getTime();
  const t1 = new Date(dateMax + "T00:00:00Z").getTime();
  const xOf = (iso) => {
    const t = new Date(iso + "T00:00:00Z").getTime();
    return padL + (t1 > t0 ? (t - t0) / (t1 - t0) : 0) * plotW;
  };
  const yOf = (v) => padT + plotH - ((v - yMin) / (yMax - yMin)) * plotH;

  const pathFor = (points) => points.map((p, i) =>
    `${i === 0 ? "M" : "L"}${xOf(p[0]).toFixed(1)},${yOf(p[1]).toFixed(1)}`
  ).join(" ");

  let svg = `<svg viewBox="0 0 ${width} ${height}" width="100%" height="auto" role="img">`;

  // gridlines + y-axis labels (4 bands)
  for (let i = 0; i <= 4; i++) {
    const v = yMin + (yMax - yMin) * (i / 4);
    const y = yOf(v);
    svg += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${width - padR}" y2="${y.toFixed(1)}" stroke="#e4e0d6" stroke-width="1"/>`;
    svg += `<text x="4" y="${(y + 3).toFixed(1)}" font-size="10" fill="#6b7a70">${v.toFixed(0)}</text>`;
  }
  // x-axis start/end date labels
  svg += `<text x="${padL}" y="${height - 8}" font-size="10" fill="#6b7a70">${dateMin}</text>`;
  svg += `<text x="${width - padR}" y="${height - 8}" font-size="10" fill="#6b7a70" text-anchor="end">${dateMax}</text>`;

  for (const s of seriesData) {
    svg += `<path d="${pathFor(s.points)}" fill="none" stroke="${s.color}" stroke-width="2"/>`;
  }
  if (opts.projection) {
    svg += `<path d="${pathFor(opts.projection.points)}" fill="none" stroke="${opts.projection.color}" ` +
           `stroke-width="2" stroke-dasharray="5,4"/>`;
  }
  svg += `</svg>`;
  container.innerHTML = svg;
}

async function fetchJSON(url) {
  try {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) return null;
    return await r.json();
  } catch (e) {
    return null;
  }
}

async function initChartSection(container, sources) {
  container.innerHTML = `<div id="private-placeholder">Caricamento storico…</div>`;

  const [navHistory, macroHistory] = await Promise.all([
    fetchJSON(sources.navHistory),
    fetchJSON(sources.macroHistory),
  ]);
  if (!navHistory || !macroHistory) {
    container.innerHTML = `<div id="private-placeholder">Storico non disponibile in questo momento.</div>`;
    return;
  }

  const state = {
    range: "1Y",
    visible: new Set(SERIES_DEFS.filter(s => s.defaultOn).map(s => s.key)),
    showProjection: true,
  };

  const rangeRow = document.createElement("div");
  rangeRow.className = "chart-controls";
  rangeRow.innerHTML = Object.keys(RANGE_DAYS).map(r =>
    `<button type="button" class="chart-range-btn${r === state.range ? " active" : ""}" data-range="${r}">${r}</button>`
  ).join("");

  const legendRow = document.createElement("div");
  legendRow.className = "chart-legend";
  legendRow.innerHTML = SERIES_DEFS.map(s => `
    <label class="chart-legend-item">
      <input type="checkbox" data-series="${s.key}" ${state.visible.has(s.key) ? "checked" : ""}>
      <span class="chart-swatch" style="background:${s.color}"></span>${s.label}
    </label>
  `).join("");

  const projRow = document.createElement("label");
  projRow.className = "chart-legend-item";
  projRow.style.marginTop = "6px";
  projRow.innerHTML = `<input type="checkbox" id="chart-proj-toggle" checked>
    <span class="chart-swatch" style="background:#8a8478;border-style:dashed"></span>
    Proiezione lineare (asset)`;

  const svgHolder = document.createElement("div");
  svgHolder.className = "chart-svg-holder";

  const projNote = document.createElement("div");
  projNote.className = "private-note";
  projNote.style.marginTop = "8px";

  container.innerHTML = "";
  container.appendChild(rangeRow);
  container.appendChild(legendRow);
  container.appendChild(projRow);
  container.appendChild(svgHolder);
  container.appendChild(projNote);

  function render() {
    const days = RANGE_DAYS[state.range];
    const seriesData = [];

    if (state.visible.has("asset")) {
      const sliced = sliceRange(navHistory, days);
      const points = rebaseTo100(sliced);
      if (points.length) seriesData.push({ key: "asset", color: SERIES_DEFS[0].color, points });
    }
    for (const def of SERIES_DEFS.slice(1)) {
      if (!state.visible.has(def.key)) continue;
      const raw = macroHistory[def.historyKey];
      if (!raw) continue;
      const sliced = sliceRange(raw, days);
      const points = rebaseTo100(sliced);
      if (points.length) seriesData.push({ key: def.key, color: def.color, points });
    }

    let projection = null;
    if (state.showProjection) {
      const assetSliced = sliceRange(navHistory, days);
      if (assetSliced.length >= 10) {
        const reg = linearRegression(assetSliced);
        const lastDate = assetSliced[assetSliced.length - 1][0];
        const horizon = Math.max(10, Math.round(assetSliced.length / 4));
        const lastIdx = assetSliced.length - 1;
        const projPoints = [];
        for (let step = 0; step <= horizon; step += Math.max(1, Math.round(horizon / 12))) {
          const idx = lastIdx + step;
          const val = reg.slope * idx + reg.intercept;
          const date = step === 0 ? lastDate : addTradingDays(lastDate, step);
          projPoints.push([date, val]);
        }
        projection = { points: projPoints, color: "#8a8478", r2: reg.r2 };
        projNote.innerHTML =
          `📐 Proiezione lineare sul NAV, non indicizzato — estende matematicamente il trend ` +
          `degli ultimi ${state.range} in avanti di circa ${Math.round(horizon * 7 / 5)} giorni. ` +
          `R² = ${reg.r2.toFixed(2)} (quanto la retta spiega i dati reali: 1.0 = perfetto, valori bassi = ` +
          `il prezzo non segue affatto una linea retta). <strong>Non è una previsione affidabile</strong> — ` +
          `i mercati non si muovono in linea retta, è solo l'estensione geometrica del trend recente.`;
      } else {
        projection = null;
        projNote.textContent = "Non abbastanza punti nel periodo selezionato per una proiezione.";
      }
    } else {
      projNote.textContent = "";
    }

    // Projection is on raw NAV scale, not rebased-to-100 — keep it on its
    // own overlay only makes sense when asset is the only/primary series.
    // To keep the chart's y-axis coherent we only draw the projection
    // when the asset line itself is visible and rebase the projection
    // the same way (using the asset's own rebasing factor).
    if (projection && state.visible.has("asset")) {
      const assetSliced = sliceRange(navHistory, days);
      const base = assetSliced[0][1];
      projection.points = projection.points.map(([d, v]) => [d, base ? (v / base) * 100 : v]);
    } else {
      projection = null;
      if (state.showProjection) projNote.textContent = "Attiva la serie \"Asset (NAV)\" per vedere la proiezione.";
    }

    buildChartSVG(svgHolder, seriesData, { projection });
  }

  rangeRow.querySelectorAll(".chart-range-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      state.range = btn.dataset.range;
      rangeRow.querySelectorAll(".chart-range-btn").forEach(b => b.classList.toggle("active", b === btn));
      render();
    });
  });
  legendRow.querySelectorAll("input[type=checkbox]").forEach(cb => {
    cb.addEventListener("change", () => {
      if (cb.checked) state.visible.add(cb.dataset.series);
      else state.visible.delete(cb.dataset.series);
      render();
    });
  });
  projRow.querySelector("#chart-proj-toggle").addEventListener("change", (e) => {
    state.showProjection = e.target.checked;
    render();
  });

  render();
}
