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

"""Shared HTML report building blocks.

Reports are single self-contained HTML files.  Data is embedded as JSON and
rendered in the browser with Plotly, which is embedded by default so reports
work offline and can be shared as one file.
"""

from __future__ import annotations
import base64
import hashlib
import html
import json
import logging
import os
import sys
import urllib.request
from ..analyze import json_clean


_log = logging.getLogger(__name__)


PLOTLY_VERSION = '2.35.2'
PLOTLY_URL = f'https://cdn.jsdelivr.net/npm/plotly.js-dist-min@{PLOTLY_VERSION}/plotly.min.js'
# SHA-256 of plotly.min.js; identical on jsDelivr and in the npm tarball.
PLOTLY_SHA256 = '6d21266ce1bd7d9e5ab4e115989c70c20de0382fd973a8f26ab58619eba4d603'
PLOTLY_ENV = 'JOULESCOPE_EVENT_ANALYZER_PLOTLY'


def cache_dir() -> str:
    """Return the per-user cache directory."""
    if sys.platform.startswith('win'):
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    elif sys.platform == 'darwin':
        base = os.path.expanduser('~/Library/Caches')
    else:
        base = os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache')
    return os.path.join(base, 'joulescope_event_analyzer')


def plotly_path() -> str:
    """Return the local Plotly path (environment override or cache)."""
    return os.environ.get(PLOTLY_ENV) or os.path.join(cache_dir(), f'plotly-{PLOTLY_VERSION}.min.js')


def _sha256_ok(data: bytes) -> bool:
    return hashlib.sha256(data).hexdigest() == PLOTLY_SHA256


def _plotly_cached() -> bytes | None:
    """Return the verified cached Plotly, downloading it if needed, or None."""
    path = os.path.join(cache_dir(), f'plotly-{PLOTLY_VERSION}.min.js')
    if os.path.isfile(path):
        with open(path, 'rb') as f:
            js = f.read()
        if _sha256_ok(js):
            return js
        _log.warning('Cached Plotly %s does not match its SHA-256; downloading again', path)
    try:
        with urllib.request.urlopen(PLOTLY_URL, timeout=30) as f:
            js = f.read()
    except Exception as ex:
        _log.warning('Could not download Plotly (%s); the report will load it from the CDN', ex)
        return None
    if not _sha256_ok(js):
        _log.warning('Downloaded Plotly does not match its SHA-256; the report will load it from the CDN')
        return None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(js)
        os.replace(tmp, path)
    except OSError as ex:
        _log.warning('Could not cache Plotly at %s (%s)', path, ex)
    return js


def plotly_tag(mode: str = 'embed') -> str:
    """Return the <script> tag that loads Plotly.

    :param mode: One of:
        * 'embed': include Plotly in the page.  Uses the file named by the
          JOULESCOPE_EVENT_ANALYZER_PLOTLY environment variable if set, else a
          per-user cached copy that is downloaded once and checked against
          PLOTLY_SHA256.  Falls back to the CDN if unavailable.
        * 'cdn': reference the CDN (smaller file, needs internet to view).
        * Any other value is the path to a plotly.min.js to embed, for
          applications that ship their own copy.  It must exist.
    """
    cdn = f'<script src="{PLOTLY_URL}" integrity="sha256-{_sri(PLOTLY_SHA256)}" crossorigin="anonymous"></script>'
    if mode == 'cdn':
        return cdn
    if mode == 'embed':
        env = os.environ.get(PLOTLY_ENV)
        if env:
            js = _read_file(env)
        else:
            js = _plotly_cached()
            if js is None:
                return cdn
    else:
        js = _read_file(mode)
    return '<script>' + js.decode('utf-8').replace('</script', '<\\/script') + '</script>'


def _read_file(path) -> bytes:
    with open(os.fspath(path), 'rb') as f:
        return f.read()


def _sri(hexdigest: str) -> str:
    """Convert a hex SHA-256 digest to the base64 form used by subresource integrity."""
    return base64.b64encode(bytes.fromhex(hexdigest)).decode('ascii')


def data_json(obj) -> str:
    """Serialize data for embedding inside <script type="application/json">."""
    # escape every '<' so no sequence in the data can end or open an HTML element
    return json.dumps(json_clean(obj), separators=(',', ':'), allow_nan=False).replace('<', '\\u003c')


def render_page(title: str, body: str, script: str, data, plotly: str = 'embed') -> str:
    """Assemble a complete report page."""
    return PAGE.replace('{{TITLE}}', html.escape(title)) \
        .replace('{{CSS}}', CSS) \
        .replace('{{BODY}}', body) \
        .replace('{{DATA}}', data_json(data)) \
        .replace('{{COMMON_JS}}', COMMON_JS) \
        .replace('{{SCRIPT}}', script) \
        .replace('{{PLOTLY}}', plotly_tag(plotly))


def write_page(path, title, body, script, data, plotly='embed'):
    page = render_page(title, body, script, data, plotly)
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(page)
    return path


PAGE = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}}</title>
{{PLOTLY}}
<style>{{CSS}}</style>
</head>
<body>
<main>
{{BODY}}
</main>
<script id="report-data" type="application/json">{{DATA}}</script>
<script>
(function () {
{{COMMON_JS}}
{{SCRIPT}}
})();
</script>
</body>
</html>
'''


CSS = r'''
:root {
  color-scheme: light;
  --bg: #f4f4f2; --surface: #fcfcfb; --text: #0b0b0b; --text-2: #52514e; --muted: #77756f;
  --border: #e3e2de; --grid: #ebeae6; --row-hover: #f0f3f8; --row-sel: #e2ebf8;
  --s1: #2a78d6; --s2: #eb6834; --s3: #1baf7a; --s4: #eda100; --s5: #e87ba4; --s6: #008300; --s7: #6250d6; --s8: #e34948;
  --good-bg: #e7f5ee; --good-ink: #12643f; --warn-bg: #fdf1dc; --warn-ink: #7a4a00; --info-bg: #e8f0fb; --info-ink: #1c4f8f;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --bg: #121211; --surface: #1a1a19; --text: #f0efec; --text-2: #c3c2b7; --muted: #8f8e86;
    --border: #2f2f2c; --grid: #2a2a28; --row-hover: #22252b; --row-sel: #1f2c3f;
    --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500; --s5: #d55181; --s6: #008300; --s7: #9085e9; --s8: #e66767;
    --good-bg: #16291f; --good-ink: #7fd3a8; --warn-bg: #2d2311; --warn-ink: #f0c36b; --info-bg: #17243a; --info-ink: #9cc2f5;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #121211; --surface: #1a1a19; --text: #f0efec; --text-2: #c3c2b7; --muted: #8f8e86;
  --border: #2f2f2c; --grid: #2a2a28; --row-hover: #22252b; --row-sel: #1f2c3f;
  --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500; --s5: #d55181; --s6: #008300; --s7: #9085e9; --s8: #e66767;
  --good-bg: #16291f; --good-ink: #7fd3a8; --warn-bg: #2d2311; --warn-ink: #f0c36b; --info-bg: #17243a; --info-ink: #9cc2f5;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 14.5px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 1280px; margin: 0 auto; padding: 28px 16px 64px; }
h1 { font-size: 26px; line-height: 1.2; margin: 0 0 6px; letter-spacing: -0.01em; }
h2 { font-size: 18px; margin: 0 0 6px; }
h3 { font-size: 14.5px; margin: 0 0 4px; }
p { margin: 0 0 10px; color: var(--text-2); max-width: 82ch; }
.lede { font-size: 16.5px; color: var(--text-2); max-width: 86ch; }
.meta { color: var(--muted); font-size: 13px; margin-top: 8px; }
.meta span { margin-right: 18px; display: inline-block; }
section { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 18px 20px; margin-top: 18px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; margin-top: 18px; }
.tile { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; }
.tile .k { color: var(--text-2); font-size: 12.5px; }
.tile .v { font-size: 21px; font-weight: 600; font-variant-numeric: tabular-nums; margin: 2px 0; }
.tile .s { color: var(--muted); font-size: 12px; }
.badge { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; font-weight: 600; padding: 1px 8px;
  border-radius: 999px; white-space: nowrap; }
.badge.same { background: var(--good-bg); color: var(--good-ink); }
.badge.differs { background: var(--warn-bg); color: var(--warn-ink); }
.badge.bandwidth { background: var(--info-bg); color: var(--info-ink); }
.badge.na { color: var(--muted); }
.callout { border-radius: 8px; padding: 10px 14px; margin: 10px 0; font-size: 13.5px; }
.callout.warn { background: var(--warn-bg); color: var(--warn-ink); }
.callout.info { background: var(--info-bg); color: var(--info-ink); }
.legend { display: flex; flex-wrap: wrap; gap: 16px; font-size: 13px; color: var(--text-2); margin: 6px 0 4px; }
.legend i { display: inline-block; width: 14px; height: 3px; border-radius: 2px; vertical-align: middle; margin-right: 6px; }
.legend i.dot { width: 9px; height: 9px; border-radius: 50%; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
@media (max-width: 860px) { .grid2 { grid-template-columns: 1fr; } }
.plot { width: 100%; height: 340px; }
.plot.short { height: 230px; }
.plot.tall { height: 420px; }
.seg { display: inline-flex; flex-wrap: wrap; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.seg button { font: inherit; font-size: 13px; padding: 5px 12px; border: 0; background: transparent; color: var(--text-2); cursor: pointer; }
.seg button + button { border-left: 1px solid var(--border); }
.seg button.on { background: var(--text); color: var(--surface); }
.toolbar { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.controls { display: flex; flex-wrap: wrap; gap: 14px; align-items: center; }
.controls label { display: inline-flex; gap: 6px; align-items: center; color: var(--text-2); font-size: 13.5px; }
select, button.btn { font: inherit; font-size: 13.5px; color: var(--text); background: var(--surface); border: 1px solid var(--border);
  border-radius: 6px; padding: 4px 8px; cursor: pointer; }
.scroll { overflow-x: auto; }
.table-wrap { max-height: 440px; overflow: auto; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; font-size: 13.5px; }
th, td { padding: 6px 10px; text-align: right; border-bottom: 1px solid var(--border); white-space: nowrap; }
th:first-child, td:first-child { text-align: left; }
th { color: var(--text-2); font-weight: 600; font-size: 12px; background: var(--surface); position: sticky; top: 0; }
th.sort { cursor: pointer; user-select: none; }
tbody tr.click { cursor: pointer; }
tbody tr.click:hover { background: var(--row-hover); }
tbody tr.sel { background: var(--row-sel); }
td.note { text-align: left; white-space: normal; color: var(--text-2); min-width: 220px; }
.sw { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 6px; vertical-align: -1px; }
.bar { display: inline-block; height: 8px; border-radius: 2px; background: var(--s1); vertical-align: middle; }
.cap { color: var(--muted); font-size: 12.5px; margin-top: 4px; }
.cards { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 760px) { .cards { grid-template-columns: 1fr; } }
.card { border: 1px solid var(--border); border-radius: 10px; padding: 12px 16px; }
.card ul { margin: 6px 0 0; padding-left: 18px; color: var(--text-2); }
.card li { margin: 3px 0; }
details summary { cursor: pointer; color: var(--text-2); }
pre { font-size: 12px; background: var(--bg); padding: 10px; border-radius: 6px; overflow: auto; }
.muted { color: var(--muted); }
'''


COMMON_JS = r'''
const D = JSON.parse(document.getElementById('report-data').textContent);
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const SERIES = ['--s1', '--s2', '--s3', '--s4', '--s5', '--s6', '--s7', '--s8'];
const seriesColor = k => css(SERIES[k % SERIES.length]);
const PREFIX = [[1e-15, 'f'], [1e-12, 'p'], [1e-9, 'n'], [1e-6, 'µ'], [1e-3, 'm'], [1, ''], [1e3, 'k'], [1e6, 'M'], [1e9, 'G']];
function esc(s) { return String(s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c])); }
function sig(x, d) {   // significant digits, never exponent notation
  if (!isFinite(x) || x === 0) return String(x);
  const k = Math.max(0, d - 1 - Math.floor(Math.log10(Math.abs(x))));
  return x.toFixed(Math.min(k, 12));
}
function eng(x, u, d = 4) {
  if (x === null || x === undefined || !isFinite(x)) return '–';
  if (x === 0) return '0 ' + u;
  let p = PREFIX[0]; for (const q of PREFIX) if (Math.abs(x) >= q[0] * 0.9995) p = q;
  return sig(x / p[0], d) + ' ' + p[1] + u;
}
function maxAbs(values) {   // loop: spreading large arrays into Math.max overflows the stack
  let a = 0;
  for (const v of values) { const b = Math.abs(v); if (b > a && isFinite(b)) a = b; }
  return a;
}
function unitFor(values, u) {   // choose one prefix for a set of values
  const a = maxAbs(values);
  let p = PREFIX[5]; for (const q of PREFIX) if (a >= q[0] * 0.9995) p = q;
  return { scale: 1 / p[0], label: p[1] + u };
}
const pctStr = (v, d = 1) => (v === null || !isFinite(v)) ? '–' : (v >= 0 ? '+' : '−') + Math.abs(100 * v).toFixed(d) + ' %';
const pm = (s, u) => s ? `${eng(s.mean, u)} <span class="muted">± ${eng(s.std, u, 2)}</span>` : '–';
const utcStr = s => s ? s.replace('T', ' ').slice(0, 19) + ' UTC' : '';
const plotConfig = { responsive: true, displaylogo: false, modeBarButtonsToRemove: ['lasso2d', 'select2d'] };
const HAS_PLOTLY = typeof Plotly !== 'undefined';
function layout(xt, yt, extra = {}) {
  const ax = { gridcolor: css('--grid'), zeroline: false, linecolor: css('--border'), tickcolor: css('--border'),
    tickfont: { color: css('--text-2') } };
  return Object.assign({
    paper_bgcolor: css('--surface'), plot_bgcolor: css('--surface'),
    font: { color: css('--text'), size: 12, family: 'system-ui, -apple-system, Segoe UI, Roboto, sans-serif' },
    margin: { l: 64, r: 14, t: 8, b: 44 }, showlegend: false, hovermode: 'closest',
    hoverlabel: { bgcolor: css('--surface'), bordercolor: css('--border'), font: { color: css('--text') } },
    xaxis: Object.assign({ title: { text: xt, font: { color: css('--text-2') } } }, ax),
    yaxis: Object.assign({ title: { text: yt, font: { color: css('--text-2') } } }, ax),
  }, extra);
}
function plot(id, traces, lay) {
  const el = document.getElementById(id);
  if (!el) return;
  if (!HAS_PLOTLY) { el.innerHTML = '<p class="cap">Charts unavailable: Plotly could not be loaded.</p>'; return; }
  Plotly.react(el, traces, lay, plotConfig);
}
function onThemeChange(fn) {
  try { matchMedia('(prefers-color-scheme: dark)').addEventListener('change', fn); } catch (e) {}
}
function downloadText(name, text, type) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  a.click();
}
function waveX(w, scale, shift = 0) { const out = new Array(w.i.length); for (let j = 0; j < out.length; j++) out[j] = (w.t0 + j * w.dt - shift) * scale; return out; }
function scaled(arr, scale) { const out = new Array(arr.length); for (let j = 0; j < arr.length; j++) out[j] = arr[j] === null ? null : arr[j] * scale; return out; }
'''
