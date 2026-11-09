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

"""Event detection and measurement.

Events are found on the smoothed current minus the sleep baseline (the
*excess* current) using two thresholds:

* An event **starts** when the excess exceeds the start threshold.
* Its boundaries extend outward while the excess stays above the lower end
  threshold, so rising edges and decaying tails are included.

Nearby events are merged.  Each candidate is then measured, and rejected if its
charge above sleep is not significantly larger than what sleep noise alone
would produce over the same time.  Rejected candidates are counted but not
reported as events; their charge remains in the unattributed total.

Detection streams through the source in chunks, so long captures do not need
to fit in memory, and the result does not depend on the chunk size.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
import math
import warnings
import numpy as np
from .config import DetectConfig
from .sleep import SleepEstimate
from ._util import moving_average, fill_forward


OVERVIEW_POINTS = 4000


@dataclass
class Event:
    """One detected event.  Times are in seconds from the capture start; charge in C; energy in J."""
    start: int                    # first sample id
    end: int                      # sample id after the last sample
    t: float = 0.0                # start time (s)
    duration: float = 0.0         # (s)
    charge: float = 0.0           # total charge, including sleep current (C)
    excess_charge: float = 0.0    # charge above the sleep baseline (C)
    energy: float = float('nan')  # total energy (J)
    excess_energy: float = float('nan')   # energy above sleep (J)
    i_peak: float = 0.0           # (A)
    i_mean: float = 0.0           # (A)
    v_mean: float = float('nan')  # (V)
    v_min: float = float('nan')   # (V)
    significance: float = 0.0     # excess charge / charge noise
    partial: bool = False         # touches the capture start or end
    gap: bool = False             # contains missing samples
    type: str | None = None       # event type name after matching
    index: int = 0                # 1-based index within its type
    match_distance: float = float('nan')
    lag: float = 0.0              # alignment shift relative to the type reference (s)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**d)


@dataclass
class Totals:
    """Whole-capture accumulations used for the charge budget."""
    samples: int = 0
    missing: int = 0
    duration: float = 0.0
    charge: float = 0.0          # (C)
    energy: float = float('nan')  # (J)
    sleep_charge: float = 0.0    # integral of the sleep baseline (C)
    sleep_energy: float = float('nan')   # integral of baseline * voltage (J)
    v_mean: float = float('nan')

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**d)


@dataclass
class Thresholds:
    start: float                 # excess current to start an event (A above sleep)
    end: float                   # excess current to end an event (A above sleep)
    smooth: float                # smoothing window (s)
    merge_gap: float             # (s)
    significance: float          # required excess charge in noise sigmas


@dataclass
class DetectResult:
    events: list = field(default_factory=list)
    rejected: int = 0
    rejected_charge: float = 0.0   # total excess charge of rejected candidates (C)
    totals: Totals = field(default_factory=Totals)
    thresholds: Thresholds | None = None
    overview: dict = field(default_factory=dict)   # {'dt': s, 'max': [...], 'mean': [...], 'min': [...]}


def thresholds(sleep: SleepEstimate, cfg: DetectConfig) -> Thresholds:
    sigma = max(sleep.noise_detect, 1e-12)
    start = cfg.start_threshold if cfg.start_threshold is not None else max(cfg.start_sigma * sigma, cfg.min_excess)
    end = cfg.end_threshold if cfg.end_threshold is not None else cfg.end_sigma * sigma
    end = min(end, start)
    return Thresholds(start=float(start), end=float(end), smooth=cfg.smooth, merge_gap=cfg.merge_gap,
                      significance=cfg.significance)


class _Segmenter:
    """Streaming hysteresis segmentation of the excess current."""

    def __init__(self, th_start, th_end, merge_gap_samples):
        self.th_start = th_start
        self.th_end = th_end
        self.merge_gap = merge_gap_samples
        self.run_start = None          # start of an open run above th_end
        self.run_has_start = False
        self.pending = None            # [start, end] waiting for possible merge
        self.out = []

    def _emit_run(self, s, e):
        if self.pending is not None and s - self.pending[1] < self.merge_gap:
            self.pending[1] = e
        else:
            if self.pending is not None:
                self.out.append(tuple(self.pending))
            self.pending = [s, e]

    def process(self, x, offset):
        """Process excess current x whose first sample has id offset."""
        above_end = x > self.th_end
        above_start = x > self.th_start
        d = np.diff(above_end.astype(np.int8))
        rises = list(np.flatnonzero(d == 1) + 1)
        falls = list(np.flatnonzero(d == -1) + 1)
        if above_end[0]:
            rises.insert(0, 0)
        if above_end[-1]:
            falls.append(len(x))
        cs = np.concatenate(([0], np.cumsum(above_start)))
        for r, f in zip(rises, falls):
            has_start = cs[f] - cs[r] > 0
            if r == 0 and self.run_start is not None:   # continuation of an open run
                s = self.run_start
                has_start = has_start or self.run_has_start
            else:
                if self.run_start is not None:          # open run ended exactly at the chunk boundary
                    if self.run_has_start:
                        self._emit_run(self.run_start, offset)
                s = offset + r
            self.run_start = None
            self.run_has_start = False
            if f == len(x):          # still open at the end of the chunk
                self.run_start = s
                self.run_has_start = bool(has_start)
            elif has_start:
                self._emit_run(s, offset + f)
        if not rises and self.run_start is not None:     # run ended at the chunk boundary
            if self.run_has_start:
                self._emit_run(self.run_start, offset)
            self.run_start = None
            self.run_has_start = False

    def finish(self, end):
        if self.run_start is not None and self.run_has_start:
            self._emit_run(self.run_start, end)
        self.run_start = None
        if self.pending is not None:
            self.out.append(tuple(self.pending))
            self.pending = None
        return self.out


def measure_event(ev: Event, data: dict, baseline: np.ndarray, fs: float, sleep: SleepEstimate, length: int):
    """Fill in event metrics from its samples."""
    i, v, p = data['i'], data['v'], data['p']
    dt = 1.0 / fs
    finite = np.isfinite(i)
    ev.gap = bool(not finite.all())
    ev.partial = ev.start <= 0 or ev.end >= length
    ev.t = ev.start / fs
    ev.duration = (ev.end - ev.start) / fs
    ev.charge = float(np.nansum(i) * dt)
    ev.excess_charge = float(np.nansum(i - baseline) * dt)
    ev.i_peak = float(np.nanmax(i)) if finite.any() else float('nan')
    ev.i_mean = float(np.nanmean(i)) if finite.any() else float('nan')
    if p is not None:
        ev.energy = float(np.nansum(p) * dt)
    if v is not None and np.isfinite(v).any():
        ev.v_mean = float(np.nanmean(v))
        ev.v_min = float(np.nanmin(v))
        if p is not None:
            ev.excess_energy = float(np.nansum(p - baseline * v) * dt)
    ev.significance = float(ev.excess_charge / sleep.charge_noise(ev.duration, fs))


def _measure_all(source, segments, sleep, chunk, progress=None):
    """Measure segments, reading the source in batches."""
    events = []
    k = 0
    fs = source.fs
    while k < len(segments):
        w0 = segments[k][0]
        j = k
        while j + 1 < len(segments) and segments[j + 1][1] - w0 <= chunk:
            j += 1
        w1 = max(segments[j][1], w0 + 1)
        data = source.read(w0, w1)
        base = sleep.baseline.range(w0, w1)
        for s, e in segments[k:j + 1]:
            a, b = s - w0, e - w0
            ev = Event(start=int(s), end=int(e))
            sub = {key: (None if x is None else x[a:b]) for key, x in data.items()}
            measure_event(ev, sub, base[a:b], fs, sleep, source.length)
            events.append(ev)
        k = j + 1
        if progress:
            progress(k / len(segments), 'Measuring events')
    return events


def detect_events(source, sleep: SleepEstimate, cfg: DetectConfig | None = None, progress=None) -> DetectResult:
    """Detect, measure and filter events.

    :param source: The :class:`~joulescope_event_analyzer.source.Source`.
    :param sleep: The :class:`~joulescope_event_analyzer.sleep.SleepEstimate`.
    :param cfg: The detection configuration.
    :param progress: Optional callable(fraction, message).
    :return: The :class:`DetectResult`.
    """
    cfg = cfg or DetectConfig()
    fs = source.fs
    th = thresholds(sleep, cfg)
    n_smooth = max(1, round(cfg.smooth * fs))
    ctx = n_smooth
    seg = _Segmenter(th.start, th.end, max(1, round(cfg.merge_gap * fs)))
    totals = Totals(samples=source.length, duration=source.length / fs)
    sum_i = sum_p = sum_b = sum_bv = sum_v = 0.0
    n_v = 0
    has_p = source.has_power
    has_v = source.has_voltage
    ov_block = max(1, math.ceil(source.length / OVERVIEW_POINTS))
    chunk = max(ov_block, max(1000, int(cfg.chunk)) // ov_block * ov_block)
    ov_max, ov_min, ov_mean = [], [], []
    last_valid = None        # last finite current sample, carried across chunks

    for s in range(0, source.length, chunk):
        e = min(source.length, s + chunk)
        a, b = max(0, s - ctx), min(source.length, e + ctx)
        data = source.read(a, b)
        i = data['i']
        base_ctx = sleep.baseline.range(a, b)
        finite = np.isfinite(i)
        # missing samples hold the last valid value so a gap neither starts nor ends an event
        x = moving_average(fill_forward(i, base_ctx[0] if last_valid is None else last_valid), n_smooth) - base_ctx
        fin = np.flatnonzero(finite[:s - a + (e - s)])
        if len(fin):
            last_valid = float(i[fin[-1]])
        core = slice(s - a, s - a + (e - s))
        seg.process(x[core], s)
        # totals over the core region
        ic = i[core]
        fc = finite[core]
        nb = len(ic) // ov_block
        if nb:
            blk = ic[:nb * ov_block].reshape(nb, ov_block)
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', RuntimeWarning)   # all-NaN blocks
                ov_max.append(np.nanmax(blk, axis=1))
                ov_min.append(np.nanmin(blk, axis=1))
                ov_mean.append(np.nanmean(blk, axis=1))
        bc = base_ctx[core]
        totals.missing += int((~fc).sum())
        sum_i += float(np.nansum(ic))
        sum_b += float(bc.sum())
        if has_p:
            sum_p += float(np.nansum(data['p'][core]))
        if has_v:
            vc = data['v'][core]
            sum_bv += float(np.nansum(bc * vc))
            sum_v += float(np.nansum(vc))
            n_v += int(np.isfinite(vc).sum())
        if progress:
            progress(e / source.length, 'Detecting events')

    segments = seg.finish(source.length)
    dt = 1.0 / fs
    totals.charge = sum_i * dt
    totals.sleep_charge = sum_b * dt
    if has_p:
        totals.energy = sum_p * dt
    if has_v:
        totals.sleep_energy = sum_bv * dt
        totals.v_mean = sum_v / max(1, n_v)

    events = _measure_all(source, segments, sleep, chunk, progress)
    result = DetectResult(totals=totals, thresholds=th)
    if ov_max:
        result.overview = {'dt': ov_block / fs, 'max': np.concatenate(ov_max).tolist(),
                           'min': np.concatenate(ov_min).tolist(), 'mean': np.concatenate(ov_mean).tolist()}
    min_len = cfg.min_duration
    for ev in events:
        ok = (ev.significance >= cfg.significance and ev.excess_charge >= cfg.min_charge
              and ev.duration >= min_len)
        if ok:
            result.events.append(ev)
        else:
            result.rejected += 1
            result.rejected_charge += ev.excess_charge
    return result
