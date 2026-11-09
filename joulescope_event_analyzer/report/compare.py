# SPDX-FileCopyrightText: Copyright 2026 Jetperch LLC
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Two-capture comparison HTML report.

All conclusions in the page are derived from the comparison verdicts and
measured values.  Text describes what was found; it never asserts an outcome
that the data did not show.
"""

from __future__ import annotations
from dataclasses import asdict
from .analysis import report_data
from .common import write_page, render_page


def comparison_data(cmp) -> dict:
    """Return the JSON-ready data for a :class:`~joulescope_event_analyzer.compare.Comparison`."""
    def slim(result):
        if result is None:
            return None
        d = report_data(result)
        d['sleep'].pop('histogram', None)
        d.pop('overview', None)
        return d

    return {
        'labels': list(cmp.labels),
        'a': slim(cmp.a), 'b': slim(cmp.b), 'model': slim(cmp.model),
        'model_ratio': cmp.model_ratio, 'model_bandwidth': cmp.model_bandwidth,
        'filter_examples': {k: {'a': v['a'].to_dict(), 'model': v['model'].to_dict()} for k, v in cmp.filter_examples.items()},
        'filter_examples_source': cmp.filter_examples_source,
        'rel_tolerance': cmp.rel_tolerance, 'sigmas': cmp.sigmas,
        'types': [{'name': t.name, 'count_a': t.count_a, 'count_b': t.count_b, 'count_model': t.count_model,
                   'metrics': {m: asdict(mc) for m, mc in t.metrics.items()}} for t in cmp.types],
    }


def _title(cmp):
    return f'Comparison: {cmp.labels[0]} vs {cmp.labels[1]}'


def render_comparison(cmp, plotly='embed') -> str:
    return render_page(_title(cmp), BODY, SCRIPT, comparison_data(cmp), plotly)


def write_comparison(cmp, path, plotly='embed'):
    return write_page(path, _title(cmp), BODY, SCRIPT, comparison_data(cmp), plotly)


BODY = r'''
<h1 id="headline"></h1>
<p class="lede" id="lede"></p>
<div class="meta" id="meta"></div>
<div class="tiles" id="tiles"></div>

<section>
  <h2>Every event, both captures</h2>
  <p>Each dot is one event, shown as its difference from the mean of its type in the first capture.
     The vertical spread within one capture shows how much events of the same type vary in that capture.</p>
  <div class="toolbar">
    <div class="legend" id="strip-legend"></div>
    <div class="seg" id="metric-seg"><button data-m="excess_charge" class="on">Charge</button><button data-m="excess_energy">Energy</button><button data-m="duration">Duration</button><button data-m="i_peak">Peak</button></div>
  </div>
  <div id="strip" class="plot"></div>
</section>

<section>
  <div class="toolbar">
    <div><h2>Look inside one event type</h2><p style="margin:0">Choose an event type to compare its waveforms.</p></div>
    <div class="seg" id="type-seg"></div>
  </div>
  <div class="grid2" style="margin-top:12px">
    <div id="filter-block">
      <h3 id="filter-title"></h3>
      <div class="legend" id="filter-legend"></div>
      <div id="p-filter" class="plot"></div>
      <div class="cap" id="cap-filter"></div>
    </div>
    <div>
      <h3>Charge above sleep during each event</h3>
      <div class="legend" id="cum-legend"></div>
      <div id="p-cum" class="plot"></div>
      <div class="cap" id="cap-cum"></div>
    </div>
  </div>
  <div style="margin-top:20px">
    <h3 id="match-title"></h3>
    <div class="legend" id="match-legend"></div>
    <div id="p-match" class="plot tall"></div>
    <div class="cap" id="match-cap"></div>
  </div>
  <h3 style="margin-top:20px">Measurements for this event type</h3>
  <div class="scroll"><table id="type-table"></table></div>
</section>

<section>
  <h2>All event types</h2>
  <div class="scroll"><table id="all-table"></table></div>
  <div class="cap" id="all-note"></div>
</section>

<section id="findings"></section>

<section>
  <h2>Sleep current and settings</h2>
  <div class="scroll"><table id="sleep-table"></table></div>
  <div class="cap" id="sleep-cap"></div>
</section>

<section>
  <h2>How this comparison was made</h2>
  <p id="method1"></p>
  <p id="method2"></p>
  <p id="method3"></p>
</section>
'''


SCRIPT = r'''
const A = D.a, Bc = D.b, M = D.model;
const L = D.labels;
const hasModel = !!M;
const hiIsB = D.filter_examples_source === 'b';
const HI = hasModel ? (hiIsB ? Bc : A) : null, LO = hasModel ? (hiIsB ? A : Bc) : null;
const hiLabel = hasModel ? L[hiIsB ? 1 : 0] : '', loLabel = hasModel ? L[hiIsB ? 0 : 1] : '';
const modelLabel = hasModel ? `${hiLabel} → ${eng(LO.capture.fs, 'Hz', 3)}` : '';
const cA = () => css('--s1'), cB = () => css('--s2'), cM = () => css('--s3');
const METRIC_LABEL = { excess_charge: 'Charge', charge: 'Total charge', excess_energy: 'Energy', energy: 'Total energy', duration: 'Duration', i_peak: 'Peak current' };
const METRIC_LOWER = { excess_charge: 'charge', excess_energy: 'energy', duration: 'duration', i_peak: 'peak current' };
const METRIC_UNIT = { excess_charge: 'C', charge: 'C', excess_energy: 'J', energy: 'J', duration: 's', i_peak: 'A' };
const KEY_METRICS = ['excess_charge', 'excess_energy', 'duration', 'i_peak'];
const VERDICT = { same: 'Same', differs: 'Differs', bandwidth: 'Sample rate', 'n/a': '–' };
const badge = v => `<span class="badge ${v === 'n/a' ? 'na' : v}">${v === 'same' ? '✓ ' : ''}${VERDICT[v] || v}</span>`;
const types = D.types;
const solid = types.filter(t => t.count_a >= 3 && t.count_b >= 3);
const listNames = names => names.length === 1 ? `type ${names[0]}` : `types ${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
const pctAbs = v => (100 * Math.abs(v)).toFixed(1) + ' %';
const range = vals => {
  let lo = Infinity, hi = -Infinity;
  for (const v of vals) { if (v < lo) lo = v; if (v > hi) hi = v; }
  return [lo, hi];
};
const rangeStr = (vals, f) => { const [lo, hi] = range(vals); return lo === hi ? f(lo) : `${f(lo)} to ${f(hi)}`; };

// ---------- findings: computed once, used by every section ----------
const F = {};
for (const m of KEY_METRICS) {
  const rows = solid.map(t => ({ name: t.name, mc: t.metrics[m] })).filter(r => r.mc.verdict !== 'n/a');
  F[m] = {
    rows,
    same: rows.filter(r => r.mc.verdict === 'same'),
    bandwidth: rows.filter(r => r.mc.verdict === 'bandwidth'),
    differs: rows.filter(r => r.mc.verdict === 'differs'),
    worst: rows.reduce((a, r) => Math.max(a, Math.abs(r.mc.diff)), 0),
  };
  F[m].allSame = rows.length > 0 && F[m].same.length === rows.length;
}
// lower-rate value as a fraction of the higher-rate value (only meaningful with a rate model)
const loOverHi = mc => hiIsB ? mc.a.mean / mc.b.mean : mc.b.mean / mc.a.mean;
const countDiff = types.filter(t => t.count_a !== t.count_b);
const sleepDiff = Bc.sleep.level - A.sleep.level;

function metricSentence(m) {
  const f = F[m], name = METRIC_LOWER[m];
  if (!f.rows.length) return '';
  if (f.allSame) return `${METRIC_LABEL[m]} agrees for every type (largest difference ${pctAbs(f.worst)}).`;
  const parts = [];
  if (f.same.length) parts.push(`agrees for ${listNames(f.same.map(r => r.name))}`);
  if (f.bandwidth.length) parts.push(`differs for ${listNames(f.bandwidth.map(r => r.name))} (${loLabel} reads ` +
    `${rangeStr(f.bandwidth.map(r => loOverHi(r.mc)), v => Math.round(100 * v) + ' %')} of ${hiLabel}), which the sample-rate model reproduces`);
  if (f.differs.length) parts.push(`differs for ${listNames(f.differs.map(r => r.name))} by ` +
    `${rangeStr(f.differs.map(r => r.mc.diff), v => pctStr(v))}, more than the expected variation` +
    (hasModel ? ' and not explained by the sample-rate model' : ''));
  return `${METRIC_LABEL[m]} ${parts.join('; ')}.`;
}

// ---------- headline and summary ----------
const energyOk = F.excess_energy.allSame || !F.excess_energy.rows.length;
if (!solid.length) {
  document.getElementById('headline').textContent = 'Event type comparison';
} else if (F.excess_charge.allSame && energyOk) {
  document.getElementById('headline').textContent = F.excess_energy.rows.length ? 'Charge and energy per event agree' : 'Charge per event agrees';
} else {
  const bad = [...new Set([...F.excess_charge.differs, ...F.excess_charge.bandwidth, ...F.excess_energy.differs, ...F.excess_energy.bandwidth].map(r => r.name))];
  document.getElementById('headline').textContent = `Charge or energy per event differs for ${listNames(bad)}`;
}
const rateTxt = A.capture.fs !== Bc.capture.fs
  ? `<b>${esc(L[0])}</b> samples at ${eng(A.capture.fs, 'Hz', 3)} and <b>${esc(L[1])}</b> at ${eng(Bc.capture.fs, 'Hz', 3)}.`
  : `Both captures sample at ${eng(A.capture.fs, 'Hz', 3)}.`;
let gapTxt = '';
if (A.capture.utc_start && Bc.capture.utc_start) {
  const g = (Date.parse(Bc.capture.utc_start) - Date.parse(A.capture.utc_start)) / 1000;
  gapTxt = ` Their start times are ${eng(Math.abs(g), 's', 3)} apart.`;
}
const instA = A.capture.instrument, instB = Bc.capture.instrument;
const instTxt = !instA && !instB ? ''
  : (instA === instB ? `Both recorded with ${esc(instA)}.`
    : `Recorded with ${esc(instA || 'an unknown instrument')} and ${esc(instB || 'an unknown instrument')}.`);
let lede = `${instTxt ? instTxt + ' ' : ''}${rateTxt}${gapTxt} `;
if (solid.length) {
  lede += `Compared ${listNames(solid.map(t => t.name))}, which occur at least 3 times in both captures. ` +
    KEY_METRICS.map(metricSentence).filter(Boolean).join(' ');
} else {
  lede += 'No event type occurs at least 3 times in both captures, so differences cannot be separated from normal variation between events.';
}
document.getElementById('lede').innerHTML = lede;
const metaRow = (c, label, color) => `<span><span class="sw" style="background:${color}"></span>${esc(label)}: ` +
  [esc(c.capture.name || ''), utcStr(c.capture.utc_start), eng(c.capture.duration, 's'), `${c.events.length} events`].filter(Boolean).join(' · ') + '</span>';
document.getElementById('meta').innerHTML = metaRow(A, L[0], cA()) + metaRow(Bc, L[1], cB());

const tiles = [];
if (F.excess_charge.rows.length) tiles.push(['Charge per event', `${pctAbs(F.excess_charge.worst)} max difference`, `${F.excess_charge.same.length} of ${F.excess_charge.rows.length} types ${badge('same')}`]);
if (F.excess_energy.rows.length) tiles.push(['Energy per event', `${pctAbs(F.excess_energy.worst)} max difference`, `${F.excess_energy.same.length} of ${F.excess_energy.rows.length} types ${badge('same')}`]);
if (solid.length) {
  const spread = solid.reduce((a, t) => { const s = t.metrics.excess_charge.a; return Math.max(a, s.std / Math.abs(s.mean)); }, 0);
  tiles.push(['Event-to-event spread', `up to ${(100 * spread).toFixed(1)} %`, `charge std / mean within ${esc(L[0])}`]);
}
tiles.push(['Sleep current', `${eng(A.sleep.level, 'A', 3)} vs ${eng(Bc.sleep.level, 'A', 3)}`, `differ by ${eng(Math.abs(sleepDiff), 'A', 2)} (${pctStr(sleepDiff / A.sleep.level)})`]);
const onlyA = types.filter(t => t.count_a && !t.count_b).length, onlyB = types.filter(t => t.count_b && !t.count_a).length;
tiles.push(['Event types', `${types.filter(t => t.count_a && t.count_b).length} in both`, `${onlyA} only in ${esc(L[0])}, ${onlyB} only in ${esc(L[1])}`]);
document.getElementById('tiles').innerHTML = tiles.map(([k, v, s]) => `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`).join('');

// ---------- strip plot ----------
let metric = 'excess_charge';
document.getElementById('strip-legend').innerHTML = `<span><i class="dot" style="background:${cA()}"></i>${esc(L[0])}</span><span><i class="dot" style="background:${cB()}"></i>${esc(L[1])}</span>`;
function drawStrip() {
  const unit = METRIC_UNIT[metric];
  const shown = types.filter(t => t.count_a && t.count_b);
  const traces = [];
  const jitter = k => ((k * 0.618034) % 1 - 0.5) * 0.22;
  for (const [res, color, off, label] of [[A, cA(), -0.18, L[0]], [Bc, cB(), 0.18, L[1]]]) {
    const x = [], y = [], text = [];
    shown.forEach((t, ti) => {
      const ref = t.metrics[metric].a ? t.metrics[metric].a.mean : null;
      if (!ref) return;
      const rt = res.types.find(z => z.name === t.name);
      if (!rt) return;
      rt.members.forEach((k, j) => {
        const e = res.events[k], v = e[metric];
        if (v === null || !isFinite(v)) return;
        x.push(ti + off + jitter(j)); y.push(100 * (v / ref - 1));
        text.push(`Type ${t.name} · ${esc(label)}<br>t = ${e.t.toFixed(3)} s<br>${eng(v, unit)} (${pctStr(v / ref - 1, 2)})`);
      });
    });
    traces.push({ x, y, text, type: 'scatter', mode: 'markers', hovertemplate: '%{text}<extra></extra>',
      marker: { size: 9, color, line: { color: css('--surface'), width: 2 } } });
  }
  const lay = layout('', `Difference from ${L[0]} mean (%)`);
  lay.xaxis = Object.assign(lay.xaxis, { tickvals: shown.map((_, k) => k), ticktext: shown.map(t => 'Type ' + t.name), range: [-0.6, shown.length - 0.4], showgrid: false });
  lay.yaxis.zeroline = true; lay.yaxis.zerolinecolor = css('--muted'); lay.yaxis.ticksuffix = ' %';
  let m = 4; for (const t of traces) m = Math.max(m, maxAbs(t.y));
  lay.yaxis.range = [-m * 1.15, m * 1.15];
  plot('strip', traces, lay);
}
document.querySelectorAll('#metric-seg button').forEach(b => b.addEventListener('click', () => {
  metric = b.dataset.m;
  document.querySelectorAll('#metric-seg button').forEach(x => x.classList.toggle('on', x === b));
  drawStrip();
}));

// ---------- per type ----------
const typeSeg = document.getElementById('type-seg');
const viewTypes = types.filter(t => t.count_a || t.count_b);
typeSeg.innerHTML = viewTypes.map((t, k) => `<button data-k="${k}"${k === 0 ? ' class="on"' : ''}>Type ${esc(t.name)}</button>`).join('');
let cur = 0;
typeSeg.querySelectorAll('button').forEach(b => b.addEventListener('click', () => {
  cur = +b.dataset.k;
  typeSeg.querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
  drawType();
}));
const lineTrace = (x, y, color, width, opacity, hover) => ({ x, y, type: 'scattergl', mode: 'lines', line: { color, width }, opacity, hovertemplate: hover + '<extra></extra>' });
const legend = items => items.map(([c, l]) => `<span><i style="background:${c}"></i>${esc(l)}</span>`).join('');

function waves(res, name) {
  if (!res) return [];
  const t = res.types.find(z => z.name === name);
  if (!t) return [];
  return t.members.filter(k => res.waveforms[k]).map(k => ({ w: res.waveforms[k], e: res.events[k], sleep: res.sleep.level }));
}
function cumulative(w, sleep) {
  const q = new Array(w.i.length); let acc = 0;
  for (let j = 0; j < w.i.length; j++) { const v = w.i[j]; if (v !== null) acc += (v - sleep) * w.dt; q[j] = acc; }
  return q;
}
function sumCharge(w) { let s = 0; for (const v of w.i) if (v !== null) s += v; return s * w.dt; }

function metricNote(mc) {
  const allowed = isFinite(mc.allowed) && mc.allowed !== null ? `± ${(100 * mc.allowed).toFixed(1)} %` : '';
  switch (mc.verdict) {
    case 'same': return `The difference is within the expected variation (${allowed}).`;
    case 'bandwidth': return `Outside the expected variation (${allowed}), but ${esc(loLabel)} is within ${pctAbs(mc.model_diff)} of ${esc(modelLabel)}, so the sample rate accounts for it.`;
    case 'differs': return `Outside the expected variation (${allowed}).` +
      (hasModel && isFinite(mc.model_diff) && mc.model_diff !== null ? ` ${esc(loLabel)} also differs from ${esc(modelLabel)} by ${pctStr(mc.model_diff)}, so the sample rate does not account for it.` : '');
    default: return 'Not measured in both captures.';
  }
}

function drawType() {
  const t = viewTypes[cur];
  const wa = waves(A, t.name), wb = waves(Bc, t.name), wm = waves(M, t.name);
  const xs = unitFor([...wa, ...wb].map(s => s.e.duration), 's');
  const ys = unitFor([...wa, ...wb, ...wm].map(s => maxAbs(s.w.i)), 'A');
  const op = n => Math.max(0.3, Math.min(0.9, 2.2 / Math.sqrt(Math.max(1, n))));

  // 1. the same event before and after the sample-rate model
  const fx = D.filter_examples[t.name];
  const fb = document.getElementById('filter-block');
  if (fx) {
    fb.style.display = '';
    document.getElementById('filter-title').textContent = `One ${hiLabel} event, before and after the ${eng(LO.capture.fs, 'Hz', 3)} model`;
    document.getElementById('filter-legend').innerHTML = legend([[cA(), hiLabel], [cM(), modelLabel]]);
    plot('p-filter', [
      Object.assign(lineTrace(waveX(fx.model, xs.scale), scaled(fx.model.i, ys.scale), cM(), 3, 0.9, `${esc(modelLabel)}<br>%{x:.4g} ${xs.label} · %{y:.4g} ${ys.label}`),
        { mode: 'lines+markers', marker: { size: 5, color: cM() } }),
      lineTrace(waveX(fx.a, xs.scale), scaled(fx.a.i, ys.scale), cA(), 1, 1, `${esc(hiLabel)}<br>%{x:.4g} ${xs.label} · %{y:.4g} ${ys.label}`),
    ], layout(`Time from event start (${xs.label})`, `Current (${ys.label})`));
    const pa = maxAbs(fx.a.i), pmod = maxAbs(fx.model.i), qa = sumCharge(fx.a), qm = sumCharge(fx.model);
    document.getElementById('cap-filter').innerHTML = `${esc(hiLabel)} event at t = ${HI.events[fx.a.event].t.toFixed(4)} s. ` +
      `Peak: <b>${eng(pa, 'A', 3)}</b> → <b>${eng(pmod, 'A', 3)}</b> (${pctStr(pmod / pa - 1)}). ` +
      `Total charge in this window: <b>${eng(qa, 'C')}</b> → <b>${eng(qm, 'C')}</b> (${pctStr(qm / qa - 1, 2)}).`;
  } else {
    fb.style.display = 'none';
  }

  // 2. cumulative charge
  document.getElementById('cum-legend').innerHTML = legend([[cA(), L[0]], [cB(), L[1]]]);
  const qmc = t.metrics.excess_charge;
  const uq = unitFor([qmc.a ? qmc.a.mean : 0, qmc.b ? qmc.b.mean : 0], 'C');
  const cum = [];
  for (const [set, color, label] of [[wa, cA(), L[0]], [wb, cB(), L[1]]]) {
    for (const s of set) cum.push(lineTrace(waveX(s.w, xs.scale, s.e.lag), scaled(cumulative(s.w, s.sleep), uq.scale), color, 1.6, op(set.length),
      `${esc(label)} · t = ${s.e.t.toFixed(3)} s<br>%{x:.4g} ${xs.label} · %{y:.4g} ${uq.label}`));
  }
  plot('p-cum', cum, layout(`Time from event start (${xs.label})`, `Charge above sleep (${uq.label})`));
  document.getElementById('cap-cum').innerHTML = 'Running total of charge above sleep; each curve ends at that event\'s charge. ' +
    (qmc.a && qmc.b ? `Mean: ${eng(qmc.a.mean, 'C')} (${esc(L[0])}) vs ${eng(qmc.b.mean, 'C')} (${esc(L[1])}), ${pctStr(qmc.diff)} ${badge(qmc.verdict)}` : '');

  // 3. waveform overlay at a common bandwidth when a rate model exists
  const loSet = hasModel ? (hiIsB ? wa : wb) : null;
  const setsMatch = hasModel ? [[wm, cM(), modelLabel], [loSet, cB(), loLabel]] : [[wa, cA(), L[0]], [wb, cB(), L[1]]];
  document.getElementById('match-title').textContent = hasModel
    ? `${loLabel} and ${modelLabel}: both at ${eng(LO.capture.fs, 'Hz', 3)}` : `Waveforms from ${L[0]} and ${L[1]}`;
  document.getElementById('match-legend').innerHTML = legend(setsMatch.map(s => [s[1], s[2]]));
  document.getElementById('match-cap').textContent = 'Each line is one event, aligned on its best match to the type reference.' +
    (hasModel ? ` The ${hiLabel} events are shown after the sample-rate model, so both sets have the same bandwidth.` : '');
  const tr = [];
  for (const [set, color, label] of setsMatch) for (const s of set)
    tr.push(lineTrace(waveX(s.w, xs.scale, s.e.lag), scaled(s.w.i, ys.scale), color, 1.4, op(set.length), `${esc(label)} · t = ${s.e.t.toFixed(3)} s<br>%{x:.4g} ${xs.label} · %{y:.4g} ${ys.label}`));
  plot('p-match', tr, layout(`Time from event start (${xs.label})`, `Current (${ys.label})`));

  // table
  let h = `<thead><tr><th>Metric</th><th>${esc(L[0])} (${t.count_a})</th>${hasModel ? `<th>${esc(modelLabel)}</th>` : ''}<th>${esc(L[1])} (${t.count_b})</th><th>Difference</th><th>Expected variation</th><th>Result</th><th>Why</th></tr></thead><tbody>`;
  for (const m of KEY_METRICS) {
    const mc = t.metrics[m], u = METRIC_UNIT[m];
    h += `<tr><td>${METRIC_LABEL[m]}</td><td>${pm(mc.a, u)}</td>${hasModel ? `<td>${mc.model ? eng(mc.model.mean, u) : '–'}</td>` : ''}<td>${pm(mc.b, u)}</td>
      <td>${pctStr(mc.diff)}</td><td>${isFinite(mc.allowed) && mc.allowed !== null ? '± ' + (100 * mc.allowed).toFixed(1) + ' %' : '–'}</td><td>${badge(mc.verdict)}</td><td class="note">${metricNote(mc)}</td></tr>`;
  }
  document.getElementById('type-table').innerHTML = h + '</tbody>';
}

// ---------- all types ----------
(function () {
  let h = `<thead><tr><th>Type</th><th>Events</th><th>Charge ${esc(L[0])}</th><th>Charge ${esc(L[1])}</th><th>Δ charge</th><th>Charge</th>
    <th>Energy</th><th>Duration</th><th>Peak</th></tr></thead><tbody>`;
  for (const t of types) {
    const q = t.metrics.excess_charge;
    const few = (t.count_a && t.count_b && (t.count_a < 3 || t.count_b < 3)) ? '*' : '';
    h += `<tr><td>Type ${esc(t.name)}</td><td>${t.count_a} / ${t.count_b}${few}</td><td>${q.a ? eng(q.a.mean, 'C') : '–'}</td><td>${q.b ? eng(q.b.mean, 'C') : '–'}</td>
      <td>${pctStr(q.diff)}</td><td>${badge(q.verdict)}</td><td>${badge(t.metrics.excess_energy.verdict)}</td>
      <td>${badge(t.metrics.duration.verdict)}</td><td>${badge(t.metrics.i_peak.verdict)}</td></tr>`;
  }
  document.getElementById('all-table').innerHTML = h + '</tbody>';
  const fewTypes = types.filter(t => t.count_a && t.count_b && (t.count_a < 3 || t.count_b < 3)).map(t => t.name);
  document.getElementById('all-note').innerHTML =
    `${badge('same')} within the expected variation: the larger of ${(100 * D.rel_tolerance).toFixed(0)} % and ${D.sigmas} standard errors of the difference of means. ` +
    (hasModel ? `${badge('bandwidth')} outside it, but ${esc(loLabel)} matches ${esc(modelLabel)}. ` : '') +
    `${badge('differs')} outside the expected variation${hasModel ? ' and not matched by the sample-rate model' : ''}.` +
    (fewTypes.length ? ` * Fewer than 3 events of ${listNames(fewTypes)} in one capture: the expected variation is wide, so the result is weak evidence either way.` : '');
})();

// ---------- findings section (only statements backed by the verdicts) ----------
(function () {
  const el = document.getElementById('findings');
  if (!solid.length) { el.style.display = 'none'; return; }
  const agreed = KEY_METRICS.filter(m => F[m].allSame);
  const rate = KEY_METRICS.filter(m => F[m].bandwidth.length);
  const diff = KEY_METRICS.filter(m => F[m].differs.length);
  const li = items => items.length ? `<ul>${items.map(x => `<li>${x}</li>`).join('')}</ul>` : '<p class="muted">None.</p>';
  let h = `<h2>Findings</h2><div class="cards">`;
  h += `<div class="card"><h3>${badge('same')} Agree for every compared type</h3>` +
    li(agreed.map(m => `<b>${METRIC_LABEL[m]}</b>: largest difference ${pctAbs(F[m].worst)}.`)) + '</div>';
  if (hasModel) {
    h += `<div class="card"><h3>${badge('bandwidth')} Explained by sample rate</h3>` +
      li(rate.map(m => `<b>${METRIC_LABEL[m]}</b> (${listNames(F[m].bandwidth.map(r => r.name))}): ${esc(loLabel)} reads ` +
        `${rangeStr(F[m].bandwidth.map(r => loOverHi(r.mc)), v => Math.round(100 * v) + ' %')} of ${esc(hiLabel)}.`)) + '</div>';
  }
  h += `<div class="card"><h3>${badge('differs')} Not explained</h3>` +
    li(diff.map(m => `<b>${METRIC_LABEL[m]}</b> (${listNames(F[m].differs.map(r => r.name))}): ${rangeStr(F[m].differs.map(r => r.mc.diff), v => pctStr(v))}.`)) + '</div>';
  const other = [];
  other.push(`<b>Sleep current</b>: ${eng(A.sleep.level, 'A', 4)} vs ${eng(Bc.sleep.level, 'A', 4)} (${pctStr(sleepDiff / A.sleep.level, 2)}).` +
    (hasModel ? ` ${esc(modelLabel)}: ${eng(M.sleep.level, 'A', 4)}.` : ''));
  if (hasModel) other.push(`<b>Sample noise</b>: ${eng(HI.sleep.noise_sample, 'A', 3)} rms at ${eng(HI.capture.fs, 'Hz', 3)}, ` +
    `${eng(LO.sleep.noise_sample, 'A', 3)} at ${eng(LO.capture.fs, 'Hz', 3)}, and ${eng(M.sleep.noise_sample, 'A', 3)} for ${esc(modelLabel)}.`);
  if (countDiff.length) other.push(`<b>Event counts</b> differ for ${listNames(countDiff.map(t => t.name))} ` +
    `(${countDiff.map(t => `${t.name}: ${t.count_a} vs ${t.count_b}`).join(', ')}). Per-event values are compared, so totals and averages can still differ.`);
  h += `<div class="card"><h3>Other measurements</h3>${li(other)}</div></div>`;
  el.innerHTML = h;
})();

// ---------- sleep table and method ----------
(function () {
  const cols = [[A, L[0]], ...(hasModel ? [[M, modelLabel]] : []), [Bc, L[1]]];
  const row = (label, f) => `<tr><td>${label}</td>${cols.map(([r]) => `<td>${f(r)}</td>`).join('')}</tr>`;
  document.getElementById('sleep-table').innerHTML = `<thead><tr><th></th>${cols.map(([, l]) => `<th>${esc(l)}</th>`).join('')}</tr></thead><tbody>` +
    row('Sample rate', r => eng(r.capture.fs, 'Hz', 3)) +
    row('Sleep current', r => eng(r.sleep.level, 'A')) +
    row('Sample noise (rms)', r => eng(r.sleep.noise_sample, 'A', 3)) +
    row('Average current', r => eng(r.budget.avg_current, 'A')) +
    row('Mean voltage', r => eng(r.totals.v_mean, 'V')) +
    row('Event start threshold', r => eng(r.thresholds.start, 'A', 3) + ' above sleep') +
    row('Event end threshold', r => eng(r.thresholds.end, 'A', 3) + ' above sleep') +
    row('Events found', r => r.events.length) + '</tbody>';
  const sameTh = A.thresholds.start === Bc.thresholds.start && A.thresholds.end === Bc.thresholds.end;
  document.getElementById('sleep-cap').textContent = sameTh
    ? `Both captures used the same detection thresholds: events start ${eng(A.thresholds.start, 'A', 3)} and end ${eng(A.thresholds.end, 'A', 3)} above each capture's sleep current.`
    : 'The captures used different detection thresholds.';
  document.getElementById('method1').textContent = `Both captures were analyzed with the same event detector. It finds the sleep current, marks where the current rises above it, ` +
    `and groups events with matching waveforms into types. The types found in ${L[0]} were used as templates for ${L[1]}, so the same letter means the same waveform shape in both. ` +
    `For each type, the difference in mean values is compared with the expected variation: the larger of ${(100 * D.rel_tolerance).toFixed(0)} % and ${D.sigmas} standard errors.`;
  document.getElementById('method2').textContent = hasModel
    ? `The sample rates differ by ${D.model_ratio}×, so ${hiLabel} was also filtered to ${eng(LO.capture.fs, 'Hz', 3)} with a Sinc3 model: three ${D.model_ratio}-sample moving averages, ` +
      `keeping every ${D.model_ratio}th sample (about ${eng(D.model_bandwidth, 'Hz', 2)} bandwidth). This is an assumption about how the lower-rate capture was downsampled. ` +
      `How closely it matches ${loLabel} is shown above: sample noise ${eng(M.sleep.noise_sample, 'A', 3)} (model) vs ${eng(LO.sleep.noise_sample, 'A', 3)} (${loLabel}).`
    : (A.capture.fs !== Bc.capture.fs ? 'The sample rates are not an integer ratio apart, so no sample-rate model was made.' : '');
  document.getElementById('method3').textContent = countDiff.length
    ? `Event counts differ between the captures (${listNames(countDiff.map(t => t.name))}), so totals such as average current reflect which events each capture contains. Per-event values do not depend on counts.`
    : '';
})();

function drawAll() { drawStrip(); if (viewTypes.length) drawType(); }
drawAll();
onThemeChange(drawAll);
'''
