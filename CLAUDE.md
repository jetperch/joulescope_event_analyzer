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

# CLAUDE.md: joulescope_event_analyzer

Read this before changing the code. README.md describes usage; this file
records why the code is the way it is, what has been validated, and what is
next.


## Purpose and origin

Jetperch (maker of Joulescope) builds this to analyze captures (JLS
files): find wake-up *events* above the sleep current, measure them, group
repeats into *types*, account for all charge, compare captures, and produce a
shareable single-file HTML report.

It grew out of one-off analysis scripts for real device captures (a JS320 at
1 MHz and at 50 kHz with Sinc3 downsampling), generalized into this package.
Those captures are not public and must never be committed (`*.jls` is
gitignored), and nothing that identifies their source may appear in this repo.
Reference results from them are recorded below so behavior changes can be
checked by someone who has the captures.

Intended next use: feeding a battery-life estimation tool being developed
separately (see "Integration with battery-life estimation"). The Joulescope UI
integration (Tools → Event Analyzer, alpha) calls `analyze_file`; see
`pyjoulescope_ui/docs/plans/event_analyzer.md`. The package is designed to be
called from a UI (no global state, plain data results, progress callbacks),
and HTML reports are preferred over Qt output.


## Layout

```
joulescope_event_analyzer/
  config.py      Config dataclasses (sleep, detect, match, report); TOML/JSON load; unknown keys raise
  source.py      Source interface: JlsSource, ArraySource, DecimatedSource (Sinc3 rate model)
  sleep.py       estimate_sleep -> SleepEstimate (level, noise, dwell levels, Baseline)
  detect.py      detect_events: streaming two-threshold segmentation, measurement, significance test
  match.py       Envelopes (time-based grid), distance, cluster -> TypeGroup
  templates.py   TemplateSet: save/load/apply event type references (JSON, versioned format)
  analyze.py     analyze() pipeline -> AnalysisResult (events, types, budget, waveforms); save/load JSON
  compare.py     compare_sources / compare_results -> Comparison with per-metric verdicts
  export.py      CSV (per event) and summary JSON (per capture)
  files.py       analyze_file: capture file -> CSV, summary JSON, HTML (CLI and UI entry point)
  report/        Self-contained HTML: common.py (page, CSS, shared JS, Plotly), analysis.py, compare.py
  synthetic.py   Synthetic captures with ground truth (tests, demos)
  __main__.py    CLI only: analyze | compare | template create | template show
tests/           pytest; conftest.standard_capture() is the shared synthetic fixture
```

Pipeline (`analyze.analyze`): `estimate_sleep` (pass 1 over data) ->
`detect_events` (pass 2, streaming) -> `extract_envelopes` -> `cluster`
(optionally seeded with templates) -> type statistics and charge budget ->
`collect_waveforms` for the report.


## Design decisions (keep unless there is a good reason)

- **Library first.** All logic lives in modules that take a `Source` plus
  config dataclasses and return dataclasses. `__main__.py` only parses
  arguments. Long operations accept `progress(fraction, message)`.
  Applications cancel by raising from `progress`; `analyze_file` writes
  outputs only after a complete analysis, so cancel leaves nothing behind.
- **Sleep = lowest level with long continuous stays** (`min_dwell`, 10 ms).
  "Lowest level" alone fails: a real capture (reference C) dips to 0.25 µA 438
  times for about 1 ms each, while true sleep is 13 µA. If no level has long
  stays, the level with the most time is used. `SleepEstimate.selection`
  records which rule applied ('dwell', 'most_common', 'manual').
- **asinh histogram with an adaptive scale** (2x block-to-block noise, min
  1 nA). A fixed log scale cannot represent a device that is off (block means
  around 0 A, some negative); a fixed 1 nA asinh scale splits a noisy zero
  level into two peaks.
- **Sleep core** (`sleep._robust_core`, 0.1.1): the sleep level and noise
  use only blocks within `CORE_SIGMAS` (5) robust sigmas (median, MAD) of the
  chosen region, iterated.  A slow event tail fills the histogram up to the
  event with no valley, so the region can reach far above sleep; a plain
  mean and std then read high, the thresholds rise, and tails get cut.
  Found on hardware: 2 ms tail, sleep +7 %, charge -5 %.
  `test_event_tails_do_not_bias_sleep` guards it.
- **Valley walk** (`sleep._walk`): a level's histogram region stops only at a
  real valley (below half the peak, then rising 25 %), not at noise bumps.
  Otherwise sparse histograms truncate one side and bias the level.
- **`noise_detect = max(noise_smooth, noise_block)`.** Reference C has 0.04 µA
  sample noise but 2.4 µA sleep wander; using sample noise alone produced
  floods of false events.
- **Two thresholds + significance test.** Start at 8 sigma, extend to 3 sigma
  (captures tails), merge within 200 µs, then keep only events whose excess
  charge is >= 10x the sleep charge noise over the same duration. Rejected
  candidates stay in the "unattributed" budget row, so charge always closes.
- **Missing samples (NaN)** are filled forward for detection only, carried
  across chunks, so a gap neither starts nor ends an event. Charge uses
  nansum; affected events get `gap=True`.
- **Detection is chunk-size independent** (tested with chunks down to 1
  sample in `_Segmenter`).
- **Envelopes are on a time grid** (1 µs x 2^level, about 256 points per
  event), computed by integrating charge. They are sample-rate independent
  and preserve charge, so templates from 1 MHz match events at 50 kHz.
- **Types are named by decreasing total excess charge** (A = most charge).
  With templates, template names come first, new types get the next letters.
- **Comparison uses common thresholds** (max of both automatic thresholds) so
  the lower-noise capture does not find extra tiny events.
- **Verdicts**: `same` if |diff| <= max(2 %, 3 standard errors);
  `bandwidth` if not, but the lower-rate capture matches the Sinc3 model of
  the higher-rate capture; otherwise `differs`. `compare_results(model_of=)`
  says which capture the model came from; the model is always compared with
  the OTHER (lower-rate) capture. This was a real bug once.
- **Reports must be data-driven.** No conclusion may be written as fixed text.
  Every sentence is built from verdicts, signs and values (see the findings
  object `F` in `report/compare.py`). An earlier version reused prose written
  for the first reference capture, which would have misreported other
  captures. `test_templates_have_no_capture_specific_text` guards this; extend
  its forbidden list if you find more.
- **Report JS rules**: never `Math.max(...bigArray)` (stack overflow with
  hundreds of thousands of samples; use `maxAbs` / loops). Embedded JSON
  escapes every `<` as `<`.
- **Plotly is embedded** (offline, single file). plotly.js (JavaScript, MIT)
  is downloaded once to a per-user cache (`report.common.cache_dir()`) and
  must match `PLOTLY_SHA256` before it is cached or embedded; a corrupt cache
  is downloaded again. If unavailable, the page uses the CDN tag with
  subresource integrity. `plotly=` (and `Config.report.plotly`) also accepts
  a path to a plotly.min.js, used by applications that ship their own copy
  (the UI); env `JOULESCOPE_EVENT_ANALYZER_PLOTLY` does the same for 'embed'.
  When bumping `PLOTLY_VERSION`, update the hash (check that jsDelivr and the
  npm tarball agree). Tests patch the cache and network and never download.
- **Public repository.** Never commit captures or anything that identifies
  where a capture came from (customer, file names, serial numbers). The
  reference captures below are anonymized as A, B and C.
- **REUSE compliance**: every file has the SPDX + Apache-2.0 header (Markdown
  inside an HTML comment), following jetperch/joulescope_driver. New files
  need the header; files that cannot hold one go in a REUSE.toml annotation.
  CI runs `reuse lint`.


## Commands

```
pip install -e ".[dev]"
python -m pytest                      # 128 tests, ~5 s
python -m coverage run -m pytest && python -m coverage report   # ~97 %
python -m reuse lint                  # note: run after a commit, or delete __pycache__ first;
                                      # with no commits reuse cannot apply .gitignore
python -m joulescope_event_analyzer analyze capture.jls
python -m joulescope_event_analyzer compare a.jls b.jls --labels "A" "B" -o compare.html
```

Visual check of reports: render with headless Chrome and check the console,
e.g. `chrome --headless=new --enable-logging=stderr --v=0 --screenshot=out.png
--window-size=1300,4400 --virtual-time-budget=15000 file:///path/report.html`
and grep the output for `CONSOLE` (Plotly's "Performance warning" lines are
harmless). `tests/test_report.py` also runs `node --check` on the page script
when node is available.


## Reference results (private captures, not in repo)

Default settings, version 0.1.0:

| Capture | fs | Sleep | Events / types | Unattributed |
|---|---|---|---|---|
| A (4.0 s) | 1 MHz | 3.983 µA | 25 / 4 | +0.01 % |
| B (3.3 s), same device as A | 50 kHz | 3.936 µA | 23 / 6 | -0.02 % |
| C (14.6 s) | 1.00069 MHz | 13.19 µA | 540 / 8 | -5.86 % |

- A types: A 12.33 µC (x10), B 8.79 µC (x4), C 6.99 µC (x3), D 0.726 µC (x8).
- B has two extra ~2-3 nC one-off bumps, found only because of its
  lower noise; they disappear in `compare` (common thresholds).
- Compare A (1 MHz) vs B (50 kHz): charge and energy `same` for every type (max 0.9 %);
  duration and peak current `bandwidth` (50 kHz peak = 30-43 % of 1 MHz).
- C: other levels at 0.25 µA and 5.4 µA (brief dips) explain the negative
  unattributed charge; 427 short events (type B, 168 nC) are 15 % of charge.
- Runtime: C (14.6 M samples, 540 events) about 2.3 s.


## Hardware validation (0.1.1)

ADP3450 W2 -> 100 ohm -> JS320 current, playing repeating custom waveforms
with known events; JS320 at 1 MHz and 50 kHz.  The harness lives outside
this public repo (it needs the private ADP3450 wrapper).  Results with 0.1.1:

- One type, three interleaved types, amplitude jitter, 0.3 uC and 12 nC
  events: right types, exact counts and periods, excess charge within 1 %
  of the raw-data integral.
- Merge gap: pulses 100 us apart merge, 600 us apart stay separate.
- 2 ms exponential tail: sleep, charge and duration match the raw data
  (0.1.0 read sleep +7 %, charge -5 %, duration 5.8 of 12.5 ms).
- 1 MHz vs 50 kHz: same types and counts, charge within 0.02 %.  The Sinc3
  model predicts the 50 kHz peak current within +0.4 to +5.6 %, so
  `i_peak` can read `differs` instead of `bandwidth` for short spikes.
- Known sensitivities: +/-20 % amplitude variation of one shape is near the
  match tolerance and may split into two types; many tiny near-threshold
  events (for example instrument step transients) split into many types.

The reference results below are from 0.1.0; 0.1.1 changes the sleep level
only where the level region includes non-sleep blocks.


## Integration with battery-life estimation

What this package provides that a battery-life model can consume:

- `AnalysisResult.budget`: rows for sleep, each type, partial events and
  unattributed, each with charge, energy, average current and share; plus
  `avg_current`, `avg_power`, and (if `report.battery_capacity_mAh` is set)
  `battery_life_hours`. The current battery estimate is only
  capacity / capture-average current.
- Per type (`EventType`): `rate` (events/s), `stats[...]` mean/std/min/max/n
  for duration, charge, excess_charge, energy, excess_energy, i_peak, i_mean,
  v_min, and `period`; `avg_current` contribution. This is effectively a
  measured activity schedule: sleep current + sum(rate x excess charge).
- `SleepEstimate`: sleep level, noise, all dwell levels.
- Summary JSON (`export.summary`, written by `analyze` as
  `*_events_summary.json`) has all of the above without per-event data.
- Templates give stable type names across captures, so the same event type
  can be tracked across firmware versions or operating modes.

Things a battery-life tool will likely need that are NOT here yet: capture
duration is short relative to slow periodic events (rates of rare types are
uncertain; `count` and `period.n` show how uncertain); no temperature or
battery-voltage dependence; no peak-current or voltage-droop limits beyond
`i_peak` and `v_min` per event.


## Status and next steps

Done: full pipeline, CLI, `analyze_file` entry point, reports, templates,
comparison with rate model, tests (about 97 % line coverage), REUSE headers,
CI (`test.yml`) and PyPI publishing (`packaging.yml`: push a `v*` tag; PyPI
trusted publishing, no token).

Open items, roughly in priority order:

1. Decide how this feeds the battery-life tool (dependency, shared summary
   JSON, or merge).
2. Performance for long captures: `cluster` is O(events x types) with Python
   loops (fine to ~10k events; ~100k is slow); the data is read twice (sleep
   pass, detect pass). `JlsSource` could use pyjls `fsr_statistics` for the
   sleep pass.
3. Verify the Sinc3 model against the actual JS320 50 kHz downsampling filter
   (it reproduces measured noise and peaks closely, but is unconfirmed).
4. Joulescope UI alpha integration (see the UI plan above).
