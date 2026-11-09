<!--
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
-->

# Joulescope Event Analyzer

Find, measure, group and compare the current events in a Joulescope capture.

Most battery-powered devices sleep, wake up briefly to do something, and sleep
again. This package finds those wake-ups automatically, measures each one
(duration, charge, energy, peak current), groups repeated activities into
*event types* by waveform shape, and accounts for where every coulomb of charge
goes. It writes a self-contained HTML report you can share as one file.

It needs no knowledge of the device: the sleep current, noise and detection
thresholds are measured from the capture itself. Every automatic value can be
overridden.

Status: 0.1.0, alpha. Developers (and AI assistants) should
read [CLAUDE.md](CLAUDE.md) for the design rationale, current state and next
steps.


## Install

```
pip install joulescope_event_analyzer
```

Requires Python 3.12+, numpy and pyjls.


## Command line

```
python -m joulescope_event_analyzer analyze capture.jls
```

writes `capture_events/` next to the capture with:

* `capture_events.html`: interactive report (works offline)
* `capture_events.csv`: one row per event
* `capture_events_summary.json`: machine-readable summary for scripts and automated tests

Compare two captures, for example two firmware versions or two sample rates:

```
python -m joulescope_event_analyzer compare before.jls after.jls --labels "v1.2" "v1.3" -o compare.html
```

The first capture's event types become templates for the second, so the same
letter means the same kind of event in both. When the sample rates differ, the
higher-rate capture is also filtered down to the lower rate to show which
differences come from bandwidth rather than the device.

Reuse event type names across many captures:

```
python -m joulescope_event_analyzer template create reference.jls -o templates.json
python -m joulescope_event_analyzer analyze new.jls --templates templates.json
python -m joulescope_event_analyzer template show templates.json
```

Useful options (see `--help` on each command):

| Option | Purpose |
|---|---|
| `--sleep 4e-6` | Force the sleep current when automatic detection picks the wrong level |
| `--baseline-window 10` | Track a drifting sleep current with a rolling baseline (seconds) |
| `--threshold 50e-6` | Absolute event start threshold above sleep |
| `--merge-gap 1e-3` | Join events closer than this |
| `--match 0.2` | Stricter waveform matching (more event types) |
| `--battery 220` | Battery capacity in mAh for a battery-life estimate |
| `--config cfg.toml` | Load any setting from a TOML or JSON file |
| `--cdn` | Load Plotly from the internet instead of embedding it (smaller HTML) |


## Python

Analyze a capture file and write the same outputs as the command line:

```python
from joulescope_event_analyzer import analyze_file

result, files = analyze_file('capture.jls', progress=lambda f, msg: print(f'{f:.0%} {msg}'))
print(files['html'])
```

To cancel, raise an exception from the progress callback; no output is
written. Each stage can also be used on its own:

```python
from joulescope_event_analyzer import analyze, open_source, write_analysis, Config

cfg = Config()
cfg.detect.merge_gap = 1e-3
with open_source('capture.jls') as src:
    result = analyze(src, cfg, progress=lambda f, msg: print(f'{f:.0%} {msg}'))

for t in result.types:
    print(t.name, t.count, t.stats['excess_charge']['mean'])
write_analysis(result, 'report.html')
```

`ArraySource` analyzes in-memory numpy arrays, so data does not need to come
from a file. `AnalysisResult` is plain data: save it with `result.save()` and
load it again without the capture.

| Module | Purpose |
|---|---|
| `source` | Data access: `JlsSource`, `ArraySource`, `DecimatedSource` (sample-rate model) |
| `sleep` | Sleep current, noise and dwell levels |
| `detect` | Streaming event detection and measurement |
| `match` | Waveform envelopes, distance, and grouping into types |
| `templates` | Save and apply event type templates |
| `analyze` | The full pipeline and the charge budget |
| `files` | `analyze_file`: analyze a capture file and write all outputs |
| `compare` | Type-by-type comparison of two captures |
| `report` | Self-contained HTML reports |
| `export` | CSV and JSON |
| `synthetic` | Synthetic captures with known events for tests and demos |


## How it works

**Sleep current.** The capture is split into 100 µs blocks. A histogram of the
block averages has one peak per current level the device stays at. Sleep is the
lowest level where the device stays for continuous stretches (10 ms by
default). If no level has such stretches, the level occupying the most time is
used; `--sleep` overrides both. Brief dips to lower levels are reported as
separate levels but are never sleep. The histogram is on an asinh scale whose
linear region adapts to the measured noise: logarithmic for µA to mA, linear
near zero so a device that is off still forms one level.

**Noise.** Detection thresholds use the larger of the short-term noise (10 µs
averages) and the block-to-block variation of the sleep current, because some
devices have almost no sample noise but a slowly wandering sleep current.

**Events.** The current, averaged over 10 µs, minus sleep is the *excess*
current. An event starts when the excess exceeds 8 noise sigmas, and its
boundaries extend while the excess stays above 3 sigmas, so edges and decaying
tails are included. Events closer than 200 µs are merged. A candidate is kept
only if its charge above sleep is at least 10 times what sleep noise alone
would produce over the same time. Missing samples neither start nor end an
event; affected events are flagged.

**Event types.** Each event's excess current is averaged on a time grid of
about 256 points. The grid is defined in seconds, not samples, so captures at
different sample rates can be compared. Two events match when the normalized
RMS difference of their smoothed envelopes, allowing a small time shift, is
under the tolerance (0.3).

**Charge budget.** Total charge = sleep + each event type's excess charge +
unattributed. A small unattributed fraction means the sleep level and the
events account for everything. A negative value usually means the device spent
time below the sleep level; the report estimates how much of it each other
current level explains.

**Comparison.** Both captures are analyzed with the same detection thresholds
(the larger of the two automatic values). The first capture's event types are
templates for the second. For each type and metric, the difference of means is
*Same* when within the larger of 2 % and 3 standard errors. Otherwise, if the
sample rates differ by an integer ratio, the higher-rate capture filtered
through a Sinc3 model (three moving averages, then decimation) is compared
with the lower-rate capture: a match makes the verdict *Sample rate*, and no
match makes it *Differs*. The Sinc3 model is an assumption about how the
instrument downsampled, not a measured filter.

**Reports.** Every number and every conclusion in a report is computed from
the analysis. The page text describes what was found (for example, which types
agree and by how much) and never states an outcome the data did not show.
`tests/test_report.py` checks that the templates contain no capture-specific
text.


## Plotly

Reports draw their charts with [plotly.js](https://plotly.com/javascript/)
(MIT license), a JavaScript library, not a Python dependency. By default it
is downloaded once from jsDelivr to a per-user cache, checked against a
pinned SHA-256, and embedded in each report so reports work offline. If it
cannot be downloaded, the report loads it from the CDN instead. To use your
own copy, pass its path as `plotly=` (or `Config.report.plotly`), or set the
`JOULESCOPE_EVENT_ANALYZER_PLOTLY` environment variable.


## Development

```
pip install -e ".[dev]"
python -m pytest
python -m reuse lint
```

This project follows the [REUSE](https://reuse.software/) specification. Every
file states its copyright and license in an SPDX header.


## License

Apache-2.0. See [LICENSE.txt](LICENSE.txt).
