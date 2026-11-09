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

import math
import numpy as np
import pytest
from joulescope_event_analyzer.config import MatchConfig
from joulescope_event_analyzer.detect import detect_events
from joulescope_event_analyzer.match import (compute_envelope, extract_envelopes, distance, compatible, cluster,
                                             Envelope, envelope_level)
from joulescope_event_analyzer.sleep import estimate_sleep, Baseline
from joulescope_event_analyzer.source import DecimatedSource
from joulescope_event_analyzer.synthetic import Capture, radio, sensor, cpu


CFG = MatchConfig()


def _env(shape, fs=200_000, offset=0.0, scale=1.0, stretch=1.0, noise=0.0, seed=0):
    w = shape.waveform(fs, scale, stretch)
    pad = int(0.003 * fs)
    x = np.zeros(len(w) + 2 * pad)
    s = pad + int(round(offset * fs))
    x[s:s + len(w)] += w
    if noise:
        x += np.random.default_rng(seed).normal(0, noise, len(x))
    return compute_envelope(x, np.zeros(len(x)), 0, fs, pad, pad + len(w), CFG)


def test_envelope_preserves_charge():
    e = _env(radio())
    assert e.data.sum() * e.step == pytest.approx(radio().charge(200_000), rel=1e-6)
    assert e.excess_charge == pytest.approx(radio().charge(200_000), rel=1e-6)
    assert e.length <= CFG.grid_points
    assert e.level == envelope_level(e.duration, CFG)


def test_envelope_round_trip():
    e = _env(sensor())
    e2 = Envelope.from_dict(e.to_dict())
    assert e2.level == e.level and e2.length == e.length
    np.testing.assert_allclose(e2.data, e.data, rtol=1e-5)


def test_distance_identity_and_symmetry():
    a, b = _env(radio()), _env(radio(), noise=50e-6, seed=1)
    assert distance(a, a, CFG)[0] == pytest.approx(0.0, abs=1e-12)
    d_ab, d_ba = distance(a, b, CFG)[0], distance(b, a, CFG)[0]
    assert d_ab < 0.1
    assert d_ab == pytest.approx(d_ba, rel=0.2)


def test_distance_different_shapes():
    assert distance(_env(radio()), _env(cpu()), CFG)[0] > CFG.tolerance


def test_lag_recovered():
    a = _env(radio())
    # shift the event content 40 us later relative to the event start
    b = _env(Capture and radio(), offset=0.0)
    w = radio().waveform(200_000)
    x = np.zeros(len(w) + 2000)
    x[600 + 8:600 + 8 + len(w)] = w
    b = compute_envelope(x, np.zeros(len(x)), 0, 200_000, 600, 600 + len(w), CFG)
    d, lag = distance(a, b, CFG)
    assert lag == pytest.approx(40e-6, abs=a.step)
    assert d < 0.1


def test_sample_rate_independent():
    """The same events at 1 MHz and through a 50 kHz rate model give near-identical envelopes."""
    cap = Capture(duration=0.4, fs=1_000_000, sleep=4e-6, noise=1e-6, seed=2)
    cap.add(radio(), 0.1)
    hi = cap.source()
    lo = DecimatedSource(hi, 20)
    envs = []
    for src in (hi, lo):
        sleep = estimate_sleep(src)
        det = detect_events(src, sleep)
        assert len(det.events) == 1
        envs.append(extract_envelopes(src, det.events, sleep.baseline, CFG)[0])
    d, _ = distance(envs[0], envs[1], CFG)
    assert d < 0.15


def test_compatible_gating():
    a = _env(radio())
    assert not compatible(a, _env(radio(), stretch=2.5), CFG)      # duration ratio
    assert not compatible(a, _env(radio(), scale=3.0), CFG)        # charge ratio
    assert compatible(a, _env(radio(), scale=1.2), CFG)
    assert math.isinf(distance(a, _env(radio(), stretch=2.5), CFG)[0])


def test_cluster_groups_shapes():
    rng = np.random.default_rng(3)
    shapes = [radio(), sensor(), cpu()]
    envs, labels = [], []
    for k in range(60):
        j = int(rng.integers(0, 3))
        envs.append(_env(shapes[j], scale=1 + rng.normal(0, 0.02), stretch=1 + rng.normal(0, 0.01),
                         noise=20e-6, seed=k))
        labels.append(j)
    groups = cluster(envs, CFG)
    assert len(groups) == 3
    for g in groups:
        assert len({labels[k] for k in g.members}) == 1
        assert g.medoid in g.members
    # named by decreasing total charge: radio first
    assert labels[groups[0].members[0]] == 0
    assert [g.name for g in groups] == ['A', 'B', 'C']


def test_cluster_eligible_and_empty():
    envs = [_env(radio()), _env(radio())]
    groups = cluster(envs, CFG, eligible=[True, False])
    assert groups[0].members == [0]
    assert cluster([], CFG) == []


def test_cluster_with_templates():
    t_radio = _env(radio())
    envs = [_env(cpu()), _env(radio(), scale=1.02), _env(cpu(), scale=0.98)]
    groups = cluster(envs, CFG, templates=[('R', t_radio), ('S', _env(sensor()))])
    names = {g.name: g for g in groups}
    assert names['R'].members == [1] and names['R'].from_template
    assert names['S'].members == [] and names['S'].from_template      # kept even when empty
    new = [g for g in groups if not g.from_template]
    assert len(new) == 1 and new[0].name == 'A' and new[0].members == [0, 2]


def test_extract_envelopes_matches_direct(capture):
    src = capture.source()
    sleep = estimate_sleep(src)
    det = detect_events(src, sleep)
    envs = extract_envelopes(src, det.events, sleep.baseline, CFG, chunk=10_000)
    for ev, env in zip(det.events, envs):
        assert env.excess_charge == pytest.approx(ev.excess_charge, rel=1e-6)
        assert env.data.sum() * env.step == pytest.approx(ev.excess_charge, rel=0.02)
