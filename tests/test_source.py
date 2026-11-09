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

import numpy as np
import pytest
from joulescope_event_analyzer.source import (ArraySource, DecimatedSource, JlsSource, open_source, iter_chunks,
                                              sinc3_kernel, sinc3_bandwidth)
from joulescope_event_analyzer.synthetic import Capture, radio


def test_array_source_basic():
    i = np.arange(10, dtype=float)
    v = np.full(12, 2.0)
    s = ArraySource(100, i, v)
    assert s.length == 10
    assert s.duration == pytest.approx(0.1)
    assert s.has_voltage and s.has_power
    d = s.read(-5, 3)
    np.testing.assert_allclose(d['i'], [0, 1, 2])
    np.testing.assert_allclose(d['p'], [0, 2, 4])
    assert len(s.read(8, 100)['i']) == 2
    assert len(s.read(5, 2)['i']) == 0


def test_array_source_current_only():
    s = ArraySource(10, np.ones(5))
    assert not s.has_voltage and not s.has_power
    d = s.read(0, 5)
    assert d['v'] is None and d['p'] is None


def test_iter_chunks_covers_all():
    s = ArraySource(10, np.arange(25, dtype=float))
    parts = list(iter_chunks(s, 10))
    assert [p[0] for p in parts] == [0, 10, 20]
    np.testing.assert_allclose(np.concatenate([p[1]['i'] for p in parts]), np.arange(25))


def test_sinc3_kernel_unity_gain():
    for r in (2, 5, 20):
        h = sinc3_kernel(r)
        assert len(h) == 3 * r - 2
        assert h.sum() == pytest.approx(1.0)


def test_sinc3_bandwidth_reasonable():
    bw = sinc3_bandwidth(1e6, 20)
    assert 0.1 * 50e3 < bw < 0.5 * 50e3


def test_decimated_preserves_mean_and_length():
    rng = np.random.default_rng(0)
    x = 1e-3 + rng.normal(0, 1e-4, 100_000)
    src = ArraySource(1e6, x, np.full(len(x), 3.0))
    d = DecimatedSource(src, 20)
    assert d.fs == 50e3
    assert d.length == 5000
    y = d.read(0, d.length)['i']
    assert len(y) == d.length
    assert y.mean() == pytest.approx(x.mean(), rel=1e-3)
    assert np.std(y) < np.std(x) / 3


def test_decimated_reads_are_consistent():
    rng = np.random.default_rng(1)
    src = ArraySource(1e5, rng.normal(0, 1, 50_000))
    d = DecimatedSource(src, 10)
    whole = d.read(0, d.length)['i']
    parts = np.concatenate([d.read(s, s + 333)['i'] for s in range(0, d.length, 333)])
    np.testing.assert_allclose(parts, whole)


def test_decimated_bad_ratio():
    with pytest.raises(ValueError):
        DecimatedSource(ArraySource(10, np.ones(10)), 0)


def test_jls_round_trip(tmp_path):
    cap = Capture(duration=0.2, fs=100_000, seed=3)
    cap.add(radio(), 0.05)
    path = cap.write_jls(tmp_path / 'x.jls')
    i, v = cap.arrays()
    with open_source(path) as s:
        assert isinstance(s, JlsSource)
        assert s.fs == 100_000
        assert s.length == len(i)
        assert s.has_voltage and s.has_power
        assert s.info['name'] == 'x.jls'
        assert 'SIM' in s.info['instrument']
        d = s.read(100, 200)
        np.testing.assert_allclose(d['i'], i[100:200].astype(np.float32), rtol=1e-6)
        np.testing.assert_allclose(d['v'], v[100:200].astype(np.float32), rtol=1e-6)
        assert len(s.read(5, 5)['i']) == 0


def test_open_source_unsupported(tmp_path):
    with pytest.raises(ValueError):
        open_source(tmp_path / 'x.csv')
