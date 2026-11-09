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

from collections import Counter
import numpy as np
import pytest
from joulescope_event_analyzer import analyze, AnalysisResult, Config, TemplateSet, ArraySource
from joulescope_event_analyzer.synthetic import Capture, radio
from conftest import standard_capture, by_shape


def test_types_match_truth(capture, result):
    truth_counts = Counter(t.name for t in capture.truth)
    shapes = by_shape(result, capture.truth)
    assert len(result.types) == 3
    for t in result.types:
        assert len(shapes[t.name]) == 1                       # each type is one shape
        assert t.count == truth_counts[next(iter(shapes[t.name]))]
    # ordered by total charge: radio first
    assert shapes[result.types[0].name] == {'radio'}
    for ev in result.events:
        assert ev.type is not None and ev.index >= 1
        assert ev.match_distance < Config().match.tolerance


def test_type_statistics(capture, result):
    radio_type = result.types[0]
    truth = sorted([t for t in capture.truth if t.name == 'radio'], key=lambda x: x.start)
    q = np.array([t.excess_charge for t in truth])
    assert radio_type.stats['excess_charge']['mean'] == pytest.approx(q.mean(), rel=0.01)
    assert radio_type.stats['period']['mean'] == pytest.approx(0.375, rel=1e-3)
    assert radio_type.rate == pytest.approx(radio_type.count / capture.duration)
    assert radio_type.avg_current == pytest.approx(radio_type.excess_charge_total / capture.duration)


def test_charge_budget_closes(result):
    b = result.budget
    total = sum(r['charge'] for r in b['rows'])
    assert total == pytest.approx(b['total_charge'], rel=1e-9)
    assert abs(b['unattributed_fraction']) < 0.005
    assert b['rows'][0]['kind'] == 'sleep'
    assert b['rows'][-1]['kind'] == 'unattributed'
    assert sum(r['charge_fraction'] for r in b['rows']) == pytest.approx(1.0)


def test_battery_life():
    cap = standard_capture(duration=1.0)
    cfg = Config()
    cfg.report.battery_capacity_mAh = 220
    r = analyze(cap.source(), cfg, waveforms=False)
    expected_h = 220 * 3.6 / r.budget['avg_current'] / 3600
    assert r.budget['battery_life_hours'] == pytest.approx(expected_h)


def test_waveforms_limited_and_include_medoid():
    cap = standard_capture(duration=2.0)
    cfg = Config()
    cfg.report.max_waveforms_per_type = 3
    r = analyze(cap.source(), cfg)
    for t in r.types:
        stored = [k for k in t.members if k in r.waveforms]
        assert len(stored) <= 4             # evenly spaced members plus the medoid
        assert t.medoid in r.waveforms
    w = r.waveforms[r.types[0].medoid]
    assert w.t0 < 0 and len(w.i) == len(w.v) == len(w.p)


def test_waveform_decimation():
    cap = standard_capture(duration=1.0)
    cfg = Config()
    cfg.report.max_plot_samples = 2000
    r = analyze(cap.source(), cfg)
    assert r.waveform_decimate > 1
    total = sum(len(w.i) for w in r.waveforms.values())
    assert total <= 2000 + len(r.waveforms)


def test_no_events():
    cap = Capture(duration=0.5, fs=50_000, sleep=4e-6, noise=1e-6, seed=9)
    r = analyze(cap.source())
    assert r.events == [] and r.types == []
    assert r.budget['rows'][0]['charge'] == pytest.approx(r.budget['total_charge'], rel=0.01)


def test_current_only_source(capture):
    i, _ = capture.arrays()
    r = analyze(ArraySource(capture.fs, i), waveforms=True)
    assert len(r.types) == 3
    assert np.isnan(r.totals.energy)
    assert r.waveforms[r.types[0].medoid].v is None


def test_partial_event_excluded_from_types():
    cap = Capture(duration=0.5, fs=100_000, sleep=4e-6, noise=0.5e-6, seed=15)
    cap.add(radio(), 0.1)
    cap.add(radio(), 0.3)
    cap.add(radio(), 0.4985)          # truncated by the capture end
    r = analyze(cap.source())
    assert r.events[-1].partial and r.events[-1].type is None
    assert [t.count for t in r.types] == [2]
    assert any(row['kind'] == 'partial' for row in r.budget['rows'])


def test_round_trip(tmp_path, result):
    path = tmp_path / 'a.json'
    result.save(path)
    r2 = AnalysisResult.load(path)
    assert len(r2.events) == len(result.events)
    assert [t.name for t in r2.types] == [t.name for t in result.types]
    assert r2.types[0].reference.length == result.types[0].reference.length
    assert r2.sleep.level == pytest.approx(result.sleep.level)
    assert set(r2.waveforms) == set(result.waveforms)
    assert r2.budget['total_charge'] == pytest.approx(result.budget['total_charge'])
    with pytest.raises(ValueError):
        AnalysisResult.from_dict({'format': 'other'})


def test_templates_give_consistent_names(tmp_path, result):
    ts = result.template_set()
    assert ts.names() == [t.name for t in result.types]
    path = tmp_path / 't.json'
    ts.save(path)
    ts2 = TemplateSet.load(path)
    other = standard_capture(seed=7, duration=2.0)
    r2 = analyze(other.source(), templates=ts2, waveforms=False)
    shapes1 = by_shape(result, standard_capture().truth)
    shapes2 = by_shape(r2, other.truth)
    for name in ts2.names():
        assert shapes1[name] == shapes2[name]


def test_templates_add_new_types_after_template_names(result):
    from joulescope_event_analyzer.synthetic import blip
    cap = standard_capture(seed=8, duration=2.0)
    cap.add_periodic(blip(), period=0.4, phase=0.2)
    r = analyze(cap.source(), templates=result.template_set(), waveforms=False)
    names = [t.name for t in r.types]
    assert names[:3] == ['A', 'B', 'C']
    assert names[3] == 'D' and r.types[3].count == len([t for t in cap.truth if t.name == 'blip'])


def test_template_min_count(result):
    assert len(result.template_set(min_count=1000).templates) == 0


def test_template_bad_file(tmp_path):
    p = tmp_path / 'x.json'
    p.write_text('{"format": "nope"}')
    with pytest.raises(ValueError):
        TemplateSet.load(p)


def test_progress_callback(capture):
    calls = []
    analyze(capture.source(), progress=lambda f, m: calls.append((f, m)), waveforms=False)
    fractions = [c[0] for c in calls]
    assert fractions[-1] == 1.0
    assert all(0 <= f <= 1 for f in fractions)
    assert fractions == sorted(fractions)
