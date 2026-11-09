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

"""Single-capture HTML report."""

from __future__ import annotations
from .common import write_page, render_page


def report_data(result) -> dict:
    """Return the JSON-ready subset of an AnalysisResult used by the reports."""
    d = result.to_dict(waveforms=True)
    for t in d['types']:
        t.pop('reference', None)
    d['sleep']['baseline'] = None
    return d


def render_analysis(result, plotly='embed') -> str:
    """Return the analysis report HTML as a string."""
    title = f"Event report: {result.capture.get('name', 'capture')}"
    return render_page(title, BODY, SCRIPT, report_data(result), plotly)


def write_analysis(result, path, plotly='embed'):
    """Write the analysis report to path."""
    title = f"Event report: {result.capture.get('name', 'capture')}"
    return write_page(path, title, BODY, SCRIPT, report_data(result), plotly)


BODY = r'''
<h1 id="title"></h1>
<div class="meta" id="meta"></div>
<div class="tiles" id="tiles"></div>

<section>
  <h2>Capture overview</h2>
  <div id="overview" class="plot short"></div>
  <div class="cap">Maximum current per display interval. Markers show detected events, colored by type; click one to open it below.</div>
</section>

<section>
  <h2>Where the charge goes</h2>
  <div class="scroll"><table id="budget"></table></div>
  <div class="cap" id="budget-note"></div>
</section>

<section>
  <h2>Event types</h2>
  <div class="scroll"><table id="types"></table></div>
  <div class="cap">Mean ± standard deviation. Excess charge is charge above the sleep current. Click a row to view its waveforms.</div>
</section>

<section>
  <div class="toolbar">
    <div class="controls">
      <label>Event type <select id="type-select"></select></label>
      <label>Signal <select id="signal-select"></select></label>
      <label>Align <select id="align-select"><option value="start">Event start</option><option value="match">Best match</option></select></label>
      <label><input type="checkbox" id="show-mean"> Mean trace</label>
    </div>
    <button class="btn" id="csv">Download CSV</button>
  </div>
  <div id="wave" class="plot tall"></div>
  <div class="cap" id="wave-note"></div>
  <h3 style="margin-top:16px" id="inst-title">Instances</h3>
  <div class="table-wrap"><table id="instances"></table></div>
  <div class="cap" id="inst-note">Click a row to highlight its waveform. Click a column header to sort.</div>
</section>

<section>
  <h2>Sleep current</h2>
  <div class="grid2">
    <div>
      <div id="hist" class="plot short"></div>
      <div class="cap" id="hist-cap"></div>
    </div>
    <div>
      <div class="scroll"><table id="levels"></table></div>
      <div class="cap" id="levels-note"></div>
    </div>
  </div>
</section>

<section>
  <h2>Method and settings</h2>
  <p id="method"></p>
  <details><summary>Configuration used</summary><pre id="config"></pre></details>
</section>
'''


SCRIPT = r'''
const C = D.capture, S = D.sleep, TH = D.thresholds, B = D.budget;
const typeColor = {};
D.types.forEach((t, k) => { typeColor[t.name] = seriesColor(k); });
const capDur = C.duration;

// ---------- header ----------
document.getElementById('title').textContent = 'Event report: ' + (C.name || 'capture');
const meta = [];
if (C.instrument) meta.push('Instrument: ' + esc(C.instrument));
if (C.utc_start) meta.push('Start: ' + utcStr(C.utc_start));
meta.push('Sample rate: ' + eng(C.fs, 'Hz', 3));
meta.push('Duration: ' + eng(capDur, 's'));
if (D.version) meta.push('joulescope_event_analyzer ' + esc(D.version));
document.getElementById('meta').innerHTML = meta.map(m => `<span>${m}</span>`).join('');
const nTypes = D.types.filter(t => t.count).length;
const tiles = [
  ['Sleep current', eng(S.level, 'A'), S.manual ? 'set manually' : 'detected automatically'],
  ['Average current', eng(B.avg_current, 'A'), 'whole capture'],
  ['Total charge', eng(B.total_charge, 'C'), eng(B.total_energy, 'J')],
  ['Events', `${D.events.length}`, `${nTypes} type${nTypes === 1 ? '' : 's'}`],
  ['Unattributed charge', pctStr(B.unattributed_fraction), 'not sleep and not an event'],
];
if (B.battery_life_hours) tiles.push(['Battery life', B.battery_life_hours >= 48 ? sig(B.battery_life_hours / 24, 3) + ' days' : sig(B.battery_life_hours, 3) + ' h',
  `${B.battery_capacity_mAh} mAh at the capture average`]);
document.getElementById('tiles').innerHTML = tiles.map(([k, v, s]) =>
  `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`).join('');

// ---------- charge budget ----------
(function () {
  const rows = B.rows;
  const maxQ = rows.reduce((a, r) => Math.max(a, Math.abs(r.charge)), 0) || 1;
  let h = '<thead><tr><th>Where</th><th>Charge</th><th>Energy</th><th>Average current</th><th>Share of charge</th><th></th></tr></thead><tbody>';
  for (const r of rows) {
    const color = r.kind === 'type' ? typeColor[r.type] : (r.kind === 'sleep' ? css('--muted') : css('--border'));
    const sw = r.kind === 'type' ? `<span class="sw" style="background:${color}"></span>` : '';
    h += `<tr${r.kind === 'type' ? ` class="click" data-type="${esc(r.type)}"` : ''}><td>${sw}${esc(r.name)}</td><td>${eng(r.charge, 'C')}</td><td>${eng(r.energy, 'J')}</td>
      <td>${eng(r.avg_current, 'A')}</td><td>${pctStr(r.charge_fraction).replace('+', '')}</td>
      <td style="width:30%"><span class="bar" style="width:${Math.max(0, 100 * r.charge / maxQ)}%;background:${color}"></span></td></tr>`;
  }
  document.getElementById('budget').innerHTML = h + '</tbody>';
  document.querySelectorAll('#budget tr.click').forEach(tr => tr.addEventListener('click', () => selectType(tr.dataset.type, true)));
  let note = 'Sleep is the sleep current over the whole capture. Each event type is its charge above sleep. ';
  const u = B.unattributed_fraction;
  const uq = B.rows[B.rows.length - 1].charge;
  // charge that time at other (non-event) current levels would add relative to sleep
  const levelCharge = levels => levels.reduce((a, l) => a + (l.current - S.level) * l.fraction * capDur, 0);
  const below = S.levels.filter(l => !l.is_sleep && l.current < S.level);
  const between = S.levels.filter(l => !l.is_sleep && l.current > S.level && l.current < S.level + TH.start);
  if (isFinite(u) && Math.abs(u) > 0.02) {
    note += `Unattributed is ${pctStr(u)} (${eng(uq, 'C')}). `;
    const cand = u < 0 ? below : between;
    if (cand.length) {
      const est = levelCharge(cand);
      note += `Time at ${cand.map(l => eng(l.current, 'A', 3)).join(', ')} (${u < 0 ? 'below sleep' : 'above sleep but below the event threshold'}) ` +
        `accounts for about ${eng(est, 'C', 3)} of it (${sig(100 * est / uq, 2)} %).`;
    } else {
      note += u < 0
        ? 'No current level below sleep was found, so the sleep level may be set too high, or the sleep current drifts. Check the Sleep current section, or try a rolling baseline.'
        : 'No current level between sleep and the event threshold was found, so this may be activity too small or slow to detect as events, or a drifting sleep current. Lower the thresholds or try a rolling baseline.';
    }
  } else if (isFinite(u)) {
    note += `Unattributed is ${pctStr(u, 2)}, so sleep and events account for ${sig(100 * (1 - Math.abs(u)), 4)} % of the charge.`;
  }
  if (D.rejected) note += ` ${D.rejected} candidate(s) were rejected as noise (total ${eng(D.rejected_charge, 'C')}).`;
  document.getElementById('budget-note').textContent = note;
})();

// ---------- type table ----------
function drawTypes() {
  let h = `<thead><tr><th>Type</th><th>Count</th><th>Duration</th><th>Excess charge</th><th>Energy</th>
    <th>Peak current</th><th>Period</th><th>Average current</th><th>Share</th></tr></thead><tbody>`;
  for (const t of D.types) {
    if (!t.count) continue;
    h += `<tr class="click" data-type="${esc(t.name)}"><td><span class="sw" style="background:${typeColor[t.name]}"></span>${esc(t.name)}</td>
      <td>${t.count}</td><td>${pm(t.stats.duration, 's')}</td><td>${pm(t.stats.excess_charge, 'C')}</td><td>${pm(t.stats.energy, 'J')}</td>
      <td>${pm(t.stats.i_peak, 'A')}</td><td>${pm(t.stats.period, 's')}</td><td>${eng(t.avg_current, 'A')}</td>
      <td>${pctStr(t.charge_fraction).replace('+', '')}</td></tr>`;
  }
  const tbl = document.getElementById('types');
  tbl.innerHTML = h + '</tbody>';
  tbl.querySelectorAll('tbody tr').forEach(tr => tr.addEventListener('click', () => selectType(tr.dataset.type, true)));
}

// ---------- overview ----------
function drawOverview() {
  const ov = D.overview;
  if (!ov || !ov.max) return;
  const n = ov.max.length;
  const x = new Array(n); for (let j = 0; j < n; j++) x[j] = (j + 0.5) * ov.dt;
  const us = unitFor(ov.max, 'A');
  const traces = [{ x, y: scaled(ov.max, us.scale), type: 'scattergl', mode: 'lines', line: { color: css('--muted'), width: 1 }, hoverinfo: 'skip' }];
  for (const t of D.types) {
    if (!t.count) continue;
    const evs = t.members.map(k => D.events[k]);
    traces.push({ x: evs.map(e => e.t), y: evs.map(e => e.i_peak * us.scale), type: 'scattergl', mode: 'markers', customdata: t.members,
      marker: { color: typeColor[t.name], size: 8, line: { color: css('--surface'), width: 1 }, opacity: (!curType || curType === t.name) ? 1 : 0.25 },
      text: evs.map(e => `Type ${e.type} #${e.index}<br>t = ${e.t.toFixed(6)} s<br>${eng(e.duration, 's')}, ${eng(e.excess_charge, 'C')}`),
      hovertemplate: '%{text}<extra></extra>' });
  }
  plot('overview', traces, layout('Time (s)', `Current (${us.label})`));
}

// ---------- waveform explorer ----------
const typeSel = document.getElementById('type-select');
typeSel.innerHTML = D.types.filter(t => t.count).map(t =>
  `<option value="${esc(t.name)}">${esc(t.name)} (${t.count}×, ${eng(t.stats.duration.mean, 's', 3)}, ${eng(t.stats.excess_charge.mean, 'C', 3)})</option>`).join('');
const SIGNALS = [['i', 'Current', 'A']];
if (C.has_voltage) SIGNALS.push(['v', 'Voltage', 'V']);
if (C.has_power) SIGNALS.push(['p', 'Power', 'W']);
const sigSel = document.getElementById('signal-select');
sigSel.innerHTML = SIGNALS.map(s => `<option value="${s[0]}">${s[1]}</option>`).join('');
const alignSel = document.getElementById('align-select');
const showMean = document.getElementById('show-mean');
let curType = (D.types.find(t => t.count) || {}).name;
let highlight = null;
let sortKey = 'index', sortDir = 1;

function selectType(name, scroll) {
  curType = name; highlight = null; typeSel.value = name;
  document.querySelectorAll('#types tbody tr').forEach(tr => tr.classList.toggle('sel', tr.dataset.type === name));
  drawOverview(); drawWave(); drawInstances();
  if (scroll) document.getElementById('type-select').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function drawWave() {
  const t = D.types.find(x => x.name === curType);
  if (!t) return;
  const sig = SIGNALS.find(s => s[0] === sigSel.value);
  const shown = t.members.filter(k => D.waveforms[k] && D.waveforms[k][sig[0]]);
  const ys = unitFor(shown.map(k => maxAbs(D.waveforms[k][sig[0]])), sig[2]);
  const xs = unitFor([t.stats.duration.max], 's');
  const base = typeColor[curType];
  const series = shown.map(k => {
    const w = D.waveforms[k];
    const shift = alignSel.value === 'match' ? D.events[k].lag : 0;
    return { k, x: waveX(w, xs.scale, shift), y: scaled(w[sig[0]], ys.scale) };
  });
  const dim = highlight !== null;
  const op = Math.max(0.2, Math.min(0.85, 3 / Math.sqrt(Math.max(1, series.length))));
  const traces = series.map(s => {
    const e = D.events[s.k], hi = highlight === s.k;
    return { x: s.x, y: s.y, type: 'scattergl', mode: 'lines', line: { color: hi ? css('--text') : base, width: hi ? 2 : 1 },
      opacity: dim ? (hi ? 1 : 0.15) : op,
      hovertemplate: `#${e.index} @ ${e.t.toFixed(6)} s<br>%{x:.4g} ${xs.label}, %{y:.4g} ${ys.label}<extra></extra>` };
  });
  if (highlight !== null) {
    const j = series.findIndex(s => s.k === highlight);
    if (j >= 0) traces.push(traces.splice(j, 1)[0]);
  }
  if (showMean.checked && series.length > 1) {
    const step = (D.waveforms[series[0].k].dt) * xs.scale;
    const x0 = series.reduce((a, s) => Math.max(a, s.x[0]), -Infinity);
    const x1 = series.reduce((a, s) => Math.min(a, s.x[s.x.length - 1]), Infinity);
    const n = Math.max(0, Math.floor((x1 - x0) / step));
    const mx = new Array(n), my = new Array(n);
    for (let j = 0; j < n; j++) {
      const x = x0 + j * step; let acc = 0, cnt = 0;
      for (const s of series) {
        const f = (x - s.x[0]) / step, i0 = Math.floor(f), a = f - i0;
        const y0 = s.y[i0], y1 = s.y[i0 + 1] ?? y0;
        if (y0 !== null && y1 !== null) { acc += y0 * (1 - a) + y1 * a; cnt++; }
      }
      mx[j] = x; my[j] = cnt ? acc / cnt : null;
    }
    traces.push({ x: mx, y: my, type: 'scattergl', mode: 'lines', line: { color: css('--text'), width: 2, dash: 'dot' },
      hovertemplate: `mean<br>%{x:.4g} ${xs.label}, %{y:.4g} ${ys.label}<extra></extra>` });
  }
  plot('wave', traces, layout(`Time from event start (${xs.label})`, `${sig[1]} (${ys.label})`));
  let note = `Showing ${series.length} of ${t.count} instance(s) of type ${curType}.`;
  if (D.waveform_decimate > 1) note += ` Waveforms are averaged ×${D.waveform_decimate} for display; measurements use every sample.`;
  document.getElementById('wave-note').textContent = note;
}

const COLS = [
  ['index', '#', null], ['t', 'Start (s)', 'time'], ['duration', 'Duration', 's'], ['excess_charge', 'Excess charge', 'C'],
  ['charge', 'Charge', 'C'], ['energy', 'Energy', 'J'], ['i_peak', 'Peak current', 'A'], ['i_mean', 'Mean current', 'A'],
  ['v_min', 'Min voltage', 'V'], ['interval', 'Since previous', 's'], ['match_distance', 'Match', 'dist'],
];
function drawInstances() {
  const t = D.types.find(x => x.name === curType);
  if (!t) return;
  let prev = null;
  let rows = t.members.map(k => {
    const e = D.events[k];
    const r = Object.assign({ k, interval: prev === null ? null : e.t - prev }, e);
    prev = e.t;
    return r;
  });
  const key = sortKey;
  rows.sort((a, b) => { const x = a[key] ?? -Infinity, y = b[key] ?? -Infinity; return sortDir * ((x > y) - (x < y)); });
  const limit = (D.config.report || {}).max_table_rows || 5000;
  const total = rows.length;
  rows = rows.slice(0, limit);
  const units = {};
  for (const [k, , u] of COLS) if (u && u !== 'time' && u !== 'dist') units[k] = unitFor(rows.map(r => r[k]).filter(v => v !== null && isFinite(v)), u);
  if (!C.has_voltage) delete units.v_min;
  const cols = COLS.filter(c => c[0] !== 'v_min' || C.has_voltage);
  const head = '<thead><tr>' + cols.map(([k, label]) => {
    const lab = units[k] ? `${label} (${units[k].label})` : label;
    return `<th class="sort" data-key="${k}">${lab}${sortKey === k ? (sortDir > 0 ? ' ▲' : ' ▼') : ''}</th>`;
  }).join('') + '</tr></thead>';
  const body = rows.map(r => `<tr class="click${highlight === r.k ? ' sel' : ''}" data-k="${r.k}">` + cols.map(([k, , u]) => {
    const v = r[k];
    if (v === null || v === undefined || (typeof v === 'number' && !isFinite(v))) return '<td>–</td>';
    if (k === 'index') return `<td>${v}${r.gap ? ' <span class="muted" title="contains missing samples">gap</span>' : ''}</td>`;
    if (u === 'time') return `<td>${v.toFixed(6)}</td>`;
    if (u === 'dist') return `<td>${v.toFixed(3)}</td>`;
    return `<td>${sig(v * units[k].scale, 4)}</td>`;
  }).join('') + '</tr>').join('');
  const tbl = document.getElementById('instances');
  tbl.innerHTML = head + '<tbody>' + body + '</tbody>';
  document.getElementById('inst-title').textContent = `Instances of type ${curType}`;
  document.getElementById('inst-note').textContent = (total > limit ? `Showing the first ${limit} of ${total} rows; the CSV has every event. ` : '') +
    'Click a row to highlight its waveform (if stored). Click a column header to sort.';
  tbl.querySelectorAll('th.sort').forEach(th => th.addEventListener('click', () => {
    sortDir = sortKey === th.dataset.key ? -sortDir : 1; sortKey = th.dataset.key; drawInstances();
  }));
  tbl.querySelectorAll('tbody tr').forEach(tr => tr.addEventListener('click', () => {
    const k = +tr.dataset.k; highlight = highlight === k ? null : k; drawWave(); drawInstances();
  }));
}

// ---------- sleep histogram and levels ----------
function drawSleep() {
  const h = S.histogram;
  if (h && h.counts && h.counts.length) {
    const x = [], y = [];
    const total = h.counts.reduce((a, b) => a + b, 0) || 1;
    for (let j = 0; j < h.counts.length; j++) {
      const c = 0.5 * (h.edges[j] + h.edges[j + 1]);
      if (c > 0) { x.push(c); y.push(100 * h.counts[j] / total); }   // log axis: positive currents only
    }
    const L = layout('Current (A)', '% of time');
    L.xaxis.type = 'log'; L.xaxis.exponentformat = 'SI';
    const mark = (v, color, dash) => ({ type: 'line', xref: 'x', yref: 'paper', x0: v, x1: v, y0: 0, y1: 1, line: { color, width: 1.5, dash } });
    L.shapes = [mark(S.level, css('--s1'), 'solid')];
    if (S.level + TH.start > 0) L.shapes.push(mark(S.level + TH.start, css('--s2'), 'dash'));
    if (S.level + TH.end > 0) L.shapes.push(mark(S.level + TH.end, css('--s3'), 'dot'));
    plot('hist', [{ x, y, type: 'scatter', mode: 'lines', fill: 'tozeroy', line: { color: css('--muted'), width: 1 },
      hovertemplate: '%{x:.3s}A: %{y:.3f} %<extra></extra>' }], L);
  }
  let t = '<thead><tr><th>Level</th><th>Current</th><th>Time</th><th>Visits</th><th>Typical stay</th></tr></thead><tbody>';
  S.levels.forEach((l, k) => {
    t += `<tr${l.is_sleep ? ' class="sel"' : ''}><td>${l.is_sleep ? '<b>Sleep</b>' : 'Level ' + (k + 1)}</td><td>${eng(l.current, 'A')}</td>
      <td>${(100 * l.fraction).toFixed(1)} %</td><td>${l.visits}</td><td>${eng(l.median_dwell, 's', 3)}</td></tr>`;
  });
  document.getElementById('levels').innerHTML = t + '</tbody>';
  const minDwell = ((D.config.sleep || {}).min_dwell);
  const SELECT = {
    dwell: `Sleep is the lowest level where the device stayed for stretches of at least ${eng(minDwell, 's', 2)}.`,
    most_common: `No level had stretches of at least ${eng(minDwell, 's', 2)}, so sleep is the level where the device spent the most time.`,
    manual: 'The sleep level was set manually.',
  };
  let note = `${SELECT[S.selection] || ''} Detection noise ${eng(S.noise_detect, 'A', 3)} rms; ` +
    `events start ${eng(TH.start, 'A', 3)} above sleep (dashed) and end within ${eng(TH.end, 'A', 3)} (dotted).`;
  const others = S.levels.filter(l => !l.is_sleep);
  if (others.length) note += ' Other levels: ' + others.map(l =>
    `${eng(l.current, 'A', 3)} (${(100 * l.fraction).toFixed(1)} % of the time, ${l.visits} visits, typically ${eng(l.median_dwell, 's', 2)})`).join('; ') +
    '. Levels below sleep are never counted as events.';
  document.getElementById('levels-note').textContent = note;
  document.getElementById('hist-cap').textContent = `Time spent at each current level (${eng(S.block_duration, 's', 3)} averages). ` +
    'Solid line: sleep. Dashed: event start threshold. Dotted: event end threshold.';
}

// ---------- method ----------
document.getElementById('method').textContent =
  `Sleep current is found from the time spent at each current level. Events are found where the current, averaged over ${eng(TH.smooth, 's', 3)}, ` +
  `rises more than ${eng(TH.start, 'A', 3)} above sleep. Their boundaries extend until the current returns within ${eng(TH.end, 'A', 3)} of sleep, ` +
  `and events less than ${eng(TH.merge_gap, 's', 3)} apart are merged. Candidates whose charge above sleep is under ${TH.significance} times the ` +
  `sleep noise are rejected. Events are grouped into types by comparing their current waveforms.`;
document.getElementById('config').textContent = JSON.stringify(D.config, null, 2);

// ---------- CSV ----------
document.getElementById('csv').addEventListener('click', () => {
  const keys = ['type', 'index', 't', 'duration', 'charge', 'excess_charge', 'energy', 'excess_energy', 'i_peak', 'i_mean', 'v_mean', 'v_min', 'significance', 'partial', 'gap'];
  const lines = [keys.join(',')];
  for (const e of D.events) lines.push(keys.map(k => e[k] === null || e[k] === undefined ? '' : e[k]).join(','));
  downloadText((C.name || 'capture').replace(/\.[^.]+$/, '') + '_events.csv', lines.join('\n'), 'text/csv');
});

typeSel.addEventListener('change', () => selectType(typeSel.value));
[sigSel, alignSel, showMean].forEach(el => el.addEventListener('change', drawWave));
function drawAll() { drawTypes(); drawSleep(); if (curType) selectType(curType); else drawOverview(); }
drawAll();
onThemeChange(drawAll);
const ovEl = document.getElementById('overview');
if (ovEl.on) ovEl.on('plotly_click', ev => {
  const pt = ev.points && ev.points[0];
  if (!pt || pt.customdata === undefined) return;
  const k = pt.customdata;
  selectType(D.events[k].type);
  highlight = k; drawWave(); drawInstances();
});
'''
