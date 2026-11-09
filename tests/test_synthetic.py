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
from joulescope_event_analyzer.synthetic import Capture, Shape, radio, sensor, cpu, blip


def test_shape_charge_and_duration():
    s = Shape('box', [(1e-3, 2e-3)])
    assert s.charge(100_000) == pytest.approx(2e-6)
    assert s.duration == pytest.approx(1e-3)
    assert s.charge(100_000, scale=2.0) == pytest.approx(4e-6)
    assert len(s.waveform(100_000, stretch=2.0)) == 200
    for shape in (radio(), sensor(), cpu(), blip()):
        assert shape.charge(1e6) > 0 and shape.duration > 0


def test_capture_truth_and_arrays():
    cap = Capture(duration=1.0, fs=10_000, sleep=1e-6, noise=0.0, voltage=3.0, resistance=2.0)
    cap.add_periodic(Shape('box', [(1e-3, 1e-3)]), period=0.25, phase=0.1)
    assert [t.start for t in cap.truth] == [1000, 3500, 6000, 8500]
    i, v = cap.arrays()
    assert i[0] == pytest.approx(1e-6)
    assert i[1005] == pytest.approx(1.001e-3)
    np.testing.assert_allclose(v, 3.0 - 2.0 * i)
    i2, _ = cap.arrays()
    np.testing.assert_array_equal(i, i2)


def test_noise_is_reproducible():
    a = Capture(duration=0.1, fs=10_000, noise=1e-6, seed=5).arrays()[0]
    b = Capture(duration=0.1, fs=10_000, noise=1e-6, seed=5).arrays()[0]
    c = Capture(duration=0.1, fs=10_000, noise=1e-6, seed=6).arrays()[0]
    np.testing.assert_array_equal(a, b)
    assert not np.array_equal(a, c)


def test_events_outside_capture_ignored():
    cap = Capture(duration=0.1, fs=10_000)
    cap.add(blip(), -1.0)
    cap.add(blip(), 5.0)
    assert cap.truth == []


def test_add_level_and_drift():
    cap = Capture(duration=1.0, fs=1000, sleep=1e-6, noise=0, drift=1e-6)
    cap.add_level(5e-6, 0.5, 0.1)
    i, _ = cap.arrays()
    assert i[0] == pytest.approx(1e-6)
    assert i[550] == pytest.approx(1e-6 + 0.55e-6 + 5e-6)
    assert cap.truth == []
