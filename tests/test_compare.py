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

import pytest
from joulescope_event_analyzer import compare_sources, compare_results, DecimatedSource, analyze
from joulescope_event_analyzer.compare import _relative_allowed, _rate_ratio
from conftest import standard_capture


@pytest.fixture(scope='module')
def same_process():
    a = standard_capture(seed=1, duration=3.0)
    b = standard_capture(seed=2, duration=3.0)
    return compare_sources(a.source(), b.source(), labels=('first', 'second'))


def test_same_process_is_same(same_process):
    c = same_process
    assert c.labels == ('first', 'second')
    assert c.model is None
    assert len(c.types) == 3
    for t in c.types:
        assert t.count_a > 0 and t.count_b > 0
        for m in ('excess_charge', 'excess_energy', 'duration', 'i_peak'):
            assert t.metrics[m].verdict == 'same', (t.name, m, t.metrics[m])
    assert c.a.thresholds.start == c.b.thresholds.start      # common thresholds


def test_changed_event_differs():
    a = standard_capture(seed=1, duration=3.0)
    b = standard_capture(seed=2, duration=3.0, radio_scale=1.10)
    c = compare_sources(a.source(), b.source())
    radio = c.types[0]
    assert radio.metrics['excess_charge'].verdict == 'differs'
    assert radio.metrics['excess_charge'].diff == pytest.approx(0.10, abs=0.02)
    for t in c.types[1:]:
        assert t.metrics['excess_charge'].verdict == 'same'


def test_sample_rate_model():
    a = standard_capture(seed=1, duration=2.0, fs=1_000_000)
    b = standard_capture(seed=2, duration=2.0, fs=1_000_000)
    lo = DecimatedSource(b.source(), 20)               # 50 kHz capture of the same process
    c = compare_sources(a.source(), lo, labels=('1 MHz', '50 kHz'))
    assert c.model is not None and c.model_ratio == 20
    assert c.model_bandwidth > 0
    assert set(c.filter_examples) == {t.name for t in c.a.types}
    assert c.filter_examples_source == 'a'
    for t in c.types:
        assert t.metrics['excess_charge'].verdict == 'same'
        assert t.metrics['i_peak'].verdict in ('bandwidth', 'same')
    assert any(t.metrics['i_peak'].verdict == 'bandwidth' for t in c.types)
    ex = next(iter(c.filter_examples.values()))
    assert ex['model'].dt == pytest.approx(20 * ex['a'].dt)


def test_model_reversed_order():
    a = standard_capture(seed=1, duration=1.0, fs=400_000)
    lo = DecimatedSource(a.source(), 4)
    b = standard_capture(seed=2, duration=1.0, fs=400_000)
    c = compare_sources(lo, b.source())
    assert c.filter_examples_source == 'b'
    # the model (of b) must be compared against the lower-rate capture a, not against b itself
    assert any(t.metrics['i_peak'].verdict == 'bandwidth' for t in c.types)
    for t in c.types:
        assert t.metrics['excess_charge'].verdict == 'same'
        mc = t.metrics['i_peak']
        if mc.verdict == 'bandwidth':
            assert mc.model_diff == pytest.approx(mc.a['mean'] / mc.model['mean'] - 1)


def test_compare_results_model_of_validation(same_process):
    with pytest.raises(ValueError):
        compare_results(same_process.a, same_process.b, model_of='c')


def test_model_none_and_invalid():
    a = standard_capture(seed=1, duration=1.0, fs=400_000)
    lo = DecimatedSource(standard_capture(seed=2, duration=1.0, fs=400_000).source(), 4)
    assert compare_sources(a.source(), lo, model='none').model is None
    with pytest.raises(ValueError):
        compare_sources(a.source(), lo, model='bogus')


def test_types_only_in_one_capture():
    from joulescope_event_analyzer.synthetic import blip
    a = standard_capture(seed=1, duration=2.0)
    b = standard_capture(seed=2, duration=2.0)
    b.add_periodic(blip(), period=0.3, phase=0.17)
    c = compare_sources(a.source(), b.source())
    extra = [t for t in c.types if t.count_a == 0]
    assert len(extra) == 1 and extra[0].count_b > 0
    assert extra[0].metrics['excess_charge'].verdict == 'n/a'


def test_compare_results_direct(same_process):
    c = compare_results(same_process.a, same_process.b, rel_tolerance=0.5)
    assert all(t.metrics['excess_charge'].allowed >= 0.5 for t in c.types)


def test_relative_allowed():
    a = {'mean': 10.0, 'std': 1.0, 'n': 100}
    b = {'mean': 10.0, 'std': 1.0, 'n': 100}
    assert _relative_allowed(a, b, 0.02, 3) == pytest.approx(3 * (0.01 + 0.01) ** 0.5 / 10)   # 3 standard errors
    big = {'mean': 10.0, 'std': 1.0, 'n': 1_000_000}
    assert _relative_allowed(big, big, 0.02, 3) == pytest.approx(0.02)                         # tolerance floor
    a1 = {'mean': 10.0, 'std': 0.0, 'n': 1}      # single event uses the other capture's spread
    b1 = {'mean': 10.0, 'std': 1.0, 'n': 5}
    assert _relative_allowed(a1, b1, 0.02, 3) == pytest.approx(3 * (1 / 1 + 1 / 5) ** 0.5 / 10)
    assert _relative_allowed(None, b, 0.02, 3) != _relative_allowed(None, b, 0.02, 3)   # NaN


def test_rate_ratio():
    assert _rate_ratio(1e6, 50e3) == 20
    assert _rate_ratio(1e6, 300e3) is None
    assert _rate_ratio(1e6, 1e6) is None
