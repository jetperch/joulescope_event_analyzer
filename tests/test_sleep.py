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
from joulescope_event_analyzer.config import SleepConfig
from joulescope_event_analyzer.sleep import estimate_sleep, Baseline, SleepEstimate
from joulescope_event_analyzer.source import ArraySource
from joulescope_event_analyzer.synthetic import Capture


def test_constant_with_noise():
    cap = Capture(duration=1.0, fs=100_000, sleep=4e-6, noise=1e-6, seed=0)
    e = estimate_sleep(cap.source())
    assert e.level == pytest.approx(4e-6, rel=0.01)
    assert e.noise_sample == pytest.approx(1e-6, rel=0.1)
    assert e.noise_detect >= e.noise_smooth
    assert e.levels[0].is_sleep
    assert not e.manual
    assert e.baseline.is_constant


def test_events_do_not_bias(capture):
    e = estimate_sleep(capture.source())
    assert e.level == pytest.approx(capture.sleep, rel=0.01)


def test_mostly_active_device():
    """Active 80 % of the time; the sleeps between activity are still found."""
    cap = Capture(duration=3.0, fs=50_000, sleep=5e-6, noise=0.5e-6, seed=2)
    for k in range(40):
        cap.add_level(2e-3, k * 0.075, 0.060)    # 60 ms active, 15 ms asleep
    e = estimate_sleep(cap.source())
    assert e.level == pytest.approx(5e-6, rel=0.05)
    assert e.levels[-1].fraction > 0.7


def test_no_long_stays_uses_most_common_level():
    """No level has 10 ms stretches: use the level occupying the most time."""
    cap = Capture(duration=1.0, fs=100_000, sleep=13e-6, noise=0.2e-6, seed=7)
    for k in range(100):
        cap.add_level(-12.7e-6, 0.005 + k * 0.01, 1e-3)      # 9 ms stays at sleep between dips
    e = estimate_sleep(cap.source())
    assert e.level == pytest.approx(13e-6, rel=0.02)
    assert e.selection == 'most_common'


def test_brief_dips_below_sleep_are_not_sleep():
    """Short visits to a lower level (like regulator dropouts) must not become the sleep level."""
    cap = Capture(duration=2.0, fs=100_000, sleep=13e-6, noise=0.2e-6, seed=3)
    for k in range(100):
        cap.add_level(-12.7e-6, 0.005 + k * 0.02, 1e-3)      # 1 ms at 0.3 uA every 20 ms
    e = estimate_sleep(cap.source())
    assert e.level == pytest.approx(13e-6, rel=0.02)
    assert len(e.levels) >= 2
    low = e.levels[0]
    assert low.current < 1e-6 and not low.is_sleep
    assert low.visits >= 90
    assert low.median_dwell == pytest.approx(1e-3, rel=0.3)


def test_long_deep_sleep_is_chosen():
    cap = Capture(duration=2.0, fs=100_000, sleep=1e-6, noise=0.1e-6, seed=4)
    cap.add_level(9e-6, 1.0, 1.0)       # idle at 10 uA for the second half
    e = estimate_sleep(cap.source())
    assert e.level == pytest.approx(1e-6, rel=0.05)
    assert [lv.is_sleep for lv in e.levels] == [True, False]
    assert e.levels[1].current == pytest.approx(10e-6, rel=0.05)


def test_manual_level_and_noise():
    cap = Capture(duration=0.5, fs=100_000, sleep=4e-6, noise=1e-6, seed=5)
    e = estimate_sleep(cap.source(), SleepConfig(level=3.5e-6, noise=2e-6))
    assert e.level == 3.5e-6
    assert e.noise_detect == 2e-6
    assert e.manual


def test_rolling_baseline_tracks_drift():
    cap = Capture(duration=4.0, fs=50_000, sleep=4e-6, noise=0.2e-6, drift=4e-6, seed=6)
    e = estimate_sleep(cap.source(), SleepConfig(baseline_window=0.5))
    assert not e.baseline.is_constant
    n = cap.n
    assert e.baseline.at([0.1 * n])[0] == pytest.approx(4.4e-6, rel=0.05)
    assert e.baseline.at([0.9 * n])[0] == pytest.approx(7.6e-6, rel=0.05)


def test_near_zero_sleep():
    rng = np.random.default_rng(0)
    i = rng.normal(0, 5e-9, 100_000)        # device off: mean ~0, includes negative samples
    e = estimate_sleep(ArraySource(100_000, i))
    assert abs(e.level) < 1e-9


def test_nan_samples_ignored():
    rng = np.random.default_rng(0)
    i = 4e-6 + rng.normal(0, 1e-7, 100_000)
    i[1000:5000] = np.nan
    e = estimate_sleep(ArraySource(100_000, i))
    assert e.level == pytest.approx(4e-6, rel=0.01)


def test_all_nan_raises():
    with pytest.raises(ValueError):
        estimate_sleep(ArraySource(1000, np.full(10_000, np.nan)))


def test_baseline():
    b = Baseline.constant(2.0)
    assert b.is_constant
    np.testing.assert_allclose(b.range(5, 8), [2, 2, 2])
    b = Baseline([0, 10], [0.0, 1.0])
    np.testing.assert_allclose(b.at([5]), [0.5])
    np.testing.assert_allclose(b.range(0, 3), [0, 0.1, 0.2])
    assert Baseline.from_dict(b.to_dict()).at([2.5])[0] == pytest.approx(0.25)


def test_estimate_round_trip_and_charge_noise(capture):
    e = estimate_sleep(capture.source())
    e2 = SleepEstimate.from_dict(e.to_dict())
    assert e2.level == e.level
    assert e2.levels[0].current == e.levels[0].current
    assert e.charge_noise(1e-3, capture.fs) < e.charge_noise(1e-2, capture.fs)
