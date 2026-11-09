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
from joulescope_event_analyzer.config import DetectConfig, SleepConfig
from joulescope_event_analyzer.detect import detect_events, thresholds, _Segmenter, Event
from joulescope_event_analyzer.sleep import estimate_sleep
from joulescope_event_analyzer.source import ArraySource
from joulescope_event_analyzer.synthetic import Capture, radio, sensor, blip


def _detect(cap, cfg=None, sleep_cfg=None):
    src = cap.source()
    sleep = estimate_sleep(src, sleep_cfg)
    return detect_events(src, sleep, cfg), src, sleep


def test_detects_every_event_with_correct_charge(capture):
    det, src, _ = _detect(capture)
    assert len(det.events) == len(capture.truth)
    assert det.rejected == 0
    for ev, tr in zip(det.events, sorted(capture.truth, key=lambda x: x.start)):
        assert ev.start <= tr.start <= ev.start + 5          # starts at the rising edge
        assert ev.end >= tr.end - 50                          # includes most of the tail
        assert ev.excess_charge == pytest.approx(tr.excess_charge, rel=0.01)
        assert ev.charge > ev.excess_charge
        assert ev.significance > 100
        assert not ev.partial and not ev.gap
        assert ev.v_min < ev.v_mean


def test_noise_only_has_no_events():
    cap = Capture(duration=2.0, fs=100_000, sleep=4e-6, noise=2e-6, seed=11)
    det, _, _ = _detect(cap)
    assert det.events == []


def test_small_real_activity_is_kept():
    cap = Capture(duration=1.0, fs=100_000, sleep=4e-6, noise=1e-6, seed=12)
    cap.add_periodic(blip(), period=0.2, phase=0.05)   # 60 uA for 1 ms, 60 nC
    det, _, _ = _detect(cap)
    assert len(det.events) == len(cap.truth)
    assert det.events[0].excess_charge == pytest.approx(60e-9, rel=0.05)


def test_significance_rejects_and_counts():
    cap = Capture(duration=1.0, fs=100_000, sleep=4e-6, noise=1e-6, seed=12)
    cap.add_periodic(blip(), period=0.2, phase=0.05)
    det, _, _ = _detect(cap, DetectConfig(significance=1e9))
    assert det.events == []
    assert det.rejected == len(cap.truth)
    assert det.rejected_charge == pytest.approx(len(cap.truth) * 60e-9, rel=0.05)


def test_min_charge_and_min_duration():
    cap = Capture(duration=1.0, fs=100_000, sleep=4e-6, noise=1e-6, seed=13)
    cap.add(sensor(), 0.2)
    cap.add(radio(), 0.5)
    det, _, _ = _detect(cap, DetectConfig(min_charge=5e-6))
    assert len(det.events) == 1 and det.rejected == 1
    det, _, _ = _detect(cap, DetectConfig(min_duration=1e-3))
    assert len(det.events) == 1 and det.events[0].duration > 1e-3


def test_merge_gap():
    cap = Capture(duration=0.5, fs=100_000, sleep=4e-6, noise=0.5e-6, seed=14)
    cap.add(sensor(), 0.100)
    cap.add(sensor(), 0.1006)       # starts ~70 us after the first one's tail ends
    det, _, _ = _detect(cap, DetectConfig(merge_gap=1e-3))
    assert len(det.events) == 1
    det, _, _ = _detect(cap, DetectConfig(merge_gap=10e-6))
    assert len(det.events) == 2


@pytest.mark.parametrize('chunk', [1000, 4321, 50_000])
def test_chunk_size_independent(capture, chunk):
    ref, src, sleep = _detect(capture)
    det = detect_events(src, sleep, DetectConfig(chunk=chunk))
    assert [(e.start, e.end) for e in det.events] == [(e.start, e.end) for e in ref.events]
    assert det.totals.charge == pytest.approx(ref.totals.charge, rel=1e-9)


def test_partial_events_at_edges():
    cap = Capture(duration=0.5, fs=100_000, sleep=4e-6, noise=0.5e-6, seed=15)
    cap.add(radio(), -0.001)          # truncated start (ignored by truth)
    cap._excess[:200] += 5e-3         # in progress at the capture start
    cap.add(radio(), 0.499)           # truncated end
    det, _, _ = _detect(cap)
    assert det.events[0].partial and det.events[0].start == 0
    assert det.events[-1].partial and det.events[-1].end == cap.n


def test_missing_samples():
    cap = Capture(duration=0.5, fs=100_000, sleep=4e-6, noise=0.5e-6, seed=16)
    cap.add(radio(), 0.2)
    i, v = cap.arrays()
    i[20_100:20_200] = np.nan        # inside the event (20000 to 20305)
    src = ArraySource(cap.fs, i, v)
    sleep = estimate_sleep(src)
    det = detect_events(src, sleep)
    assert len(det.events) == 1
    assert det.events[0].gap
    assert det.totals.missing == 100
    assert np.isfinite(det.events[0].excess_charge)


def test_totals_and_overview(capture):
    det, src, sleep = _detect(capture)
    i, v = capture.arrays()
    assert det.totals.charge == pytest.approx(i.sum() / capture.fs, rel=1e-9)
    assert det.totals.energy == pytest.approx((i * v).sum() / capture.fs, rel=1e-9)
    assert det.totals.sleep_charge == pytest.approx(sleep.level * capture.duration, rel=1e-6)
    assert det.totals.v_mean == pytest.approx(v.mean(), rel=1e-6)
    ov = det.overview
    assert len(ov['max']) == len(ov['mean']) == len(ov['min'])
    assert max(ov['max']) == pytest.approx(i.max(), rel=1e-9)


def test_thresholds_overrides(capture):
    sleep = estimate_sleep(capture.source())
    th = thresholds(sleep, DetectConfig())
    assert th.start > th.end > 0
    th = thresholds(sleep, DetectConfig(start_threshold=1e-3, end_threshold=2e-3))
    assert th.start == 1e-3 and th.end == 1e-3        # end never exceeds start
    th = thresholds(sleep, DetectConfig(min_excess=1.0))
    assert th.start == 1.0


def _segments_all(x, th_s, th_e, gap, chunk):
    seg = _Segmenter(th_s, th_e, gap)
    for s in range(0, len(x), chunk):
        seg.process(x[s:s + chunk], s)
    return seg.finish(len(x))


@pytest.mark.parametrize('chunk', [1, 2, 3, 5, 7, 100])
def test_segmenter_streaming_edge_cases(chunk):
    x = np.array([0, 0, 2, 5, 2, 0, 0, 0, 0, 2, 2, 0, 2, 5, 5, 0, 0, 0, 0, 0, 5, 2, 2], dtype=float)
    expected = _segments_all(x, 4, 1, 1, len(x))
    assert expected == [(2, 5), (12, 15), (20, 23)]     # (9, 11) never crosses the start threshold
    assert _segments_all(x, 4, 1, 1, chunk) == expected
    merged = _segments_all(x, 4, 1, 10, chunk)
    assert merged == [(2, 23)]


def test_event_round_trip():
    e = Event(start=1, end=5, type='A', excess_charge=1e-6)
    assert repr(Event.from_dict(e.to_dict())) == repr(e)     # repr: NaN fields compare equal


@pytest.mark.parametrize('chunk', [1000, 20_150])
def test_missing_samples_across_chunks(chunk):
    """A gap inside an event that spans a chunk boundary still keeps the event whole."""
    cap = Capture(duration=0.5, fs=100_000, sleep=4e-6, noise=0.5e-6, seed=16)
    cap.add(radio(), 0.2)
    i, v = cap.arrays()
    i[20_100:20_250] = np.nan
    src = ArraySource(cap.fs, i, v)
    det = detect_events(src, estimate_sleep(src), DetectConfig(chunk=chunk))
    assert len(det.events) == 1 and det.events[0].gap


def test_fill_forward():
    from joulescope_event_analyzer._util import fill_forward
    x = np.array([np.nan, 1.0, np.nan, np.nan, 3.0, np.nan])
    np.testing.assert_allclose(fill_forward(x, 9.0), [9, 1, 1, 1, 3, 3])
