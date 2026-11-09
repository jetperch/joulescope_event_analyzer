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

"""Sleep (baseline) current estimation.

The capture is divided into short blocks.  The histogram of the block mean
currents shows one peak for each level the device dwells at.  The histogram
uses an asinh scale: logarithmic for currents well above 1 nA (so µA sleep and
mA activity both resolve) and linear near zero (so a device that is off, with
block means scattered around 0 A, still forms one peak).

The sleep current is the lowest level where the device stays for continuous
stretches of at least ``min_dwell``.  Brief visits to a lower level (dips) are
reported as separate levels but are not sleep.  If no level has such stretches,
the level occupying the most time is used.

Noise is measured from the sleep blocks only:

* ``noise_sample``: rms of individual samples (reported).
* ``noise_smooth``: rms of the detection-smoothed current.
* ``noise_block``: standard deviation of block means (slow wander).
* ``noise_detect``: the larger of noise_smooth and noise_block; sets the thresholds.
  Some devices have very little sample noise but a slowly wandering sleep current,
  so both time scales matter.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import math
import numpy as np
from .config import SleepConfig
from ._util import moving_average


MIN_SCALE = 1e-9      # A, minimum asinh scale (linear below, logarithmic above)
BIN_WIDTH = 0.02       # asinh units per histogram bin (about 2 % of current when logarithmic)
MAX_BLOCKS = 2_000_000


@dataclass
class SleepLevel:
    current: float      # mean current of blocks at this level (A)
    fraction: float     # fraction of valid blocks at this level
    low: float          # level region lower bound (A)
    high: float         # level region upper bound (A)
    dwell_fraction: float = 0.0   # fraction of blocks at this level in stretches >= min_dwell
    visits: int = 0               # number of separate stretches at this level
    median_dwell: float = 0.0     # median stretch duration (s)
    is_sleep: bool = False        # True for the level chosen as sleep


class Baseline:
    """The sleep current versus time.

    :param sample_ids: Sample ids where values are defined (sorted).
    :param values: Sleep current at each sample id.  One value means constant.
    """

    def __init__(self, sample_ids, values):
        self.sample_ids = np.asarray(sample_ids, dtype=np.float64)
        self.values = np.asarray(values, dtype=np.float64)

    @classmethod
    def constant(cls, value):
        return cls([0.0], [value])

    @property
    def is_constant(self) -> bool:
        return len(self.values) == 1

    def at(self, sample_ids) -> np.ndarray:
        sample_ids = np.asarray(sample_ids, dtype=np.float64)
        if self.is_constant:
            return np.full(sample_ids.shape, self.values[0])
        return np.interp(sample_ids, self.sample_ids, self.values)

    def range(self, start: int, end: int) -> np.ndarray:
        """Baseline for samples [start, end)."""
        if self.is_constant:
            return np.full(max(0, end - start), self.values[0])
        return self.at(np.arange(start, end))

    @property
    def mean(self) -> float:
        return float(np.mean(self.values))

    def to_dict(self):
        return {'sample_ids': self.sample_ids.tolist(), 'values': self.values.tolist()}

    @classmethod
    def from_dict(cls, d):
        return cls(d['sample_ids'], d['values'])


@dataclass
class SleepEstimate:
    level: float                 # sleep current (A), mean of baseline
    noise_sample: float          # rms of individual samples during sleep (A)
    noise_smooth: float          # rms of the smoothed current during sleep (A)
    noise_block: float           # std of block means during sleep (A)
    noise_detect: float          # noise used for detection thresholds (A)
    block_duration: float        # block duration (s)
    levels: list = field(default_factory=list)   # list[SleepLevel], lowest first
    histogram: dict = field(default_factory=dict)  # {'edges': [A...], 'counts': [...]}
    baseline: Baseline = None
    manual: bool = False         # True if the level was forced by configuration
    selection: str = 'dwell'     # how the level was chosen: 'dwell', 'most_common' or 'manual'

    def charge_noise(self, duration: float, fs: float) -> float:
        """Estimated 1-sigma charge noise (C) of sleep integrated over duration seconds."""
        duration = max(duration, 1.0 / fs)
        white = self.noise_sample * math.sqrt(duration / fs)
        block = self.noise_block * math.sqrt(self.block_duration * duration)
        return max(white, block, 1e-18)

    def to_dict(self):
        return {
            'level': self.level, 'noise_sample': self.noise_sample, 'noise_smooth': self.noise_smooth,
            'noise_block': self.noise_block, 'noise_detect': self.noise_detect, 'block_duration': self.block_duration,
            'levels': [vars(x) for x in self.levels], 'histogram': self.histogram,
            'baseline': self.baseline.to_dict() if self.baseline else None, 'manual': self.manual,
            'selection': self.selection,
        }

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d['levels'] = [SleepLevel(**x) for x in d.get('levels', [])]
        d['baseline'] = Baseline.from_dict(d['baseline']) if d.get('baseline') else None
        return cls(**d)


def _block_statistics(source, block, smooth_n, chunk, progress=None):
    """Pass over the source, computing per-block mean, raw variance and smoothed variance."""
    n_blocks = source.length // block
    means = np.full(n_blocks, np.nan)
    var_raw = np.full(n_blocks, np.nan)
    var_smooth = np.full(n_blocks, np.nan)
    chunk = max(block, chunk // block * block)
    for s in range(0, n_blocks * block, chunk):
        e = min(n_blocks * block, s + chunk)
        x = source.read(s, e)['i']
        nb = len(x) // block
        if nb == 0:
            continue
        b0 = s // block
        xs = x[:nb * block].reshape(nb, block)
        valid = np.isfinite(xs).all(axis=1)
        means[b0:b0 + nb] = np.where(valid, xs.mean(axis=1), np.nan)
        var_raw[b0:b0 + nb] = np.where(valid, xs.var(axis=1), np.nan)
        xf = np.where(np.isfinite(x), x, np.nanmean(x) if np.isfinite(x).any() else 0.0)
        sm = moving_average(xf, smooth_n)[:nb * block].reshape(nb, block)
        var_smooth[b0:b0 + nb] = np.where(valid, sm.var(axis=1), np.nan)
        if progress:
            progress(e / max(1, n_blocks * block), 'Estimating sleep current')
    return means, var_raw, var_smooth




def _scale(block_means):
    """asinh scale constant: at least the block-to-block noise, so a noisy level forms one peak."""
    d = np.diff(block_means)
    d = d[np.isfinite(d)]
    sigma = 1.4826 * float(np.median(np.abs(d))) / math.sqrt(2) if len(d) else 0.0
    return max(MIN_SCALE, 2.0 * sigma)


def _to_scale(x, scale):
    return np.arcsinh(np.asarray(x, dtype=np.float64) / scale)


def _from_scale(y, scale):
    return scale * np.sinh(y)


def _walk(sm, taken, p, direction):
    """Walk from peak p until the histogram fades out or reaches a real valley.

    Small bumps from histogram noise near the peak top do not stop the walk: it
    stops at a valley only once the count has fallen below half the peak and
    then rises 25 % above the lowest value seen.
    """
    k = p
    low = sm[p]
    while 0 <= k + direction < len(sm) and not taken[k + direction]:
        nxt = sm[k + direction]
        if nxt <= 1e-3 * sm[p]:
            break
        if low < 0.5 * sm[p] and nxt > 1.25 * low:
            break
        k += direction
        low = min(low, nxt)
    return k


def _find_levels(log_values, min_fraction):
    """Find dwell levels as prominent peaks in the asinh-scaled current histogram.

    :return: (levels as list of (lo_edge, hi_edge, fraction, peak) in scaled units), edges, counts
    """
    lo, hi = float(np.min(log_values)), float(np.max(log_values))
    nbins = int(max(10, min(4000, math.ceil((hi - lo) / BIN_WIDTH) + 1)))
    counts, edges = np.histogram(log_values, bins=nbins, range=(lo - BIN_WIDTH, hi + BIN_WIDTH))
    k = np.arange(-8, 9)
    kernel = np.exp(-0.5 * (k / 2.0) ** 2)
    kernel /= kernel.sum()
    sm = np.convolve(counts.astype(np.float64), kernel, mode='full')[8:8 + len(counts)]
    total = counts.sum()
    peak_max = sm.max()
    levels = []
    taken = np.zeros(len(sm), dtype=bool)
    for p in np.argsort(-sm):
        if sm[p] < 0.01 * peak_max or taken[p]:
            continue
        a = _walk(sm, taken, p, -1)
        b = _walk(sm, taken, p, 1)
        taken[a:b + 1] = True
        frac = counts[a:b + 1].sum() / total
        if frac >= min_fraction:
            levels.append((edges[a], edges[b + 1], float(frac), 0.5 * (edges[p] + edges[p + 1])))
    levels.sort(key=lambda x: x[3])
    return levels, edges, counts


def estimate_sleep(source, cfg: SleepConfig | None = None, smooth: float = 10e-6,
                   chunk: int = 1_000_000, progress=None) -> SleepEstimate:
    """Estimate the sleep current, its noise, and other dwell levels.

    :param source: The :class:`~joulescope_event_analyzer.source.Source`.
    :param cfg: The sleep configuration.
    :param smooth: The detection smoothing window in seconds (for noise_smooth).
    :param chunk: Samples per read.
    :param progress: Optional callable(fraction, message).
    """
    cfg = cfg or SleepConfig()
    fs = source.fs
    block = max(1, round(cfg.block * fs), math.ceil(source.length / MAX_BLOCKS))
    if source.length < 2 * block:
        block = max(1, source.length // 2)
    smooth_n = max(1, round(smooth * fs))
    means, var_raw, var_smooth = _block_statistics(source, block, smooth_n, chunk, progress)
    valid = np.isfinite(means)
    if not valid.any():
        raise ValueError('No valid samples')
    vm = means[valid]
    scale = _scale(vm)
    logv = _to_scale(vm, scale)
    raw_levels, edges, counts = _find_levels(logv, cfg.min_fraction)
    if not raw_levels:   # no level holds min_fraction: keep the most common one
        raw_levels, edges, counts = _find_levels(logv, 0.0)
        raw_levels = [max(raw_levels, key=lambda x: x[2])]

    # characterize how long the device stays at each level
    logm = np.full(len(means), np.nan)
    logm[valid] = logv
    min_run = max(1, round(cfg.min_dwell * fs / block))
    levels = []
    for lo, hi, frac, _ in raw_levels:
        sel = (logv >= lo) & (logv < hi)
        mask = np.zeros(len(means), dtype=np.int8)
        with np.errstate(invalid='ignore'):
            mask[(logm >= lo) & (logm < hi)] = 1
        d = np.diff(np.concatenate(([0], mask, [0])))
        run_len = np.flatnonzero(d == -1) - np.flatnonzero(d == 1)
        long_blocks = run_len[run_len >= min_run].sum()
        levels.append(SleepLevel(
            current=float(vm[sel].mean()), fraction=frac, low=float(_from_scale(lo, scale)), high=float(_from_scale(hi, scale)),
            dwell_fraction=float(long_blocks / len(vm)), visits=int(len(run_len)),
            median_dwell=float(np.median(run_len) * block / fs) if len(run_len) else 0.0))

    # choose the sleep level: the lowest level the device dwells at, or the one nearest a forced value
    if cfg.level is not None:
        target = float(_to_scale(cfg.level, scale))
        idx = int(np.argmin([abs(0.5 * (lo + hi) - target) for lo, hi, _, _ in raw_levels]))
    else:
        dwell = [k for k, lv in enumerate(levels) if lv.dwell_fraction >= cfg.min_fraction]
        idx = dwell[0] if dwell else int(np.argmax([lv.fraction for lv in levels]))
        selection = 'dwell' if dwell else 'most_common'
    levels[idx].is_sleep = True
    lo, hi = raw_levels[idx][0], raw_levels[idx][1]
    in_sleep = np.zeros(len(means), dtype=bool)
    in_sleep[valid] = (logv >= lo) & (logv < hi)

    level = float(means[in_sleep].mean())
    noise_sample = float(np.sqrt(np.median(var_raw[in_sleep])))
    noise_smooth = float(np.sqrt(np.median(var_smooth[in_sleep])))
    noise_block = float(np.std(means[in_sleep]))
    noise_detect = max(noise_smooth, noise_block, 1e-12)
    if cfg.noise is not None:
        noise_detect = float(cfg.noise)
    manual = cfg.level is not None
    if manual:
        selection = 'manual'
        level = float(cfg.level)

    # baseline
    if cfg.baseline_window and not manual:
        win_blocks = max(1, round(cfg.baseline_window * fs / block))
        centers, values = [], []
        for b0 in range(0, len(means), win_blocks):
            sel = in_sleep[b0:b0 + win_blocks]
            if sel.sum() >= max(1, 0.05 * len(sel)):
                values.append(float(np.median(means[b0:b0 + win_blocks][sel])))
                centers.append((b0 + 0.5 * len(sel)) * block)
        baseline = Baseline(centers, values) if len(values) > 1 else Baseline.constant(level)
        level = baseline.mean
    else:
        baseline = Baseline.constant(level)

    return SleepEstimate(
        level=level, noise_sample=noise_sample, noise_smooth=noise_smooth,
        noise_block=noise_block, noise_detect=noise_detect, block_duration=block / fs, levels=levels,
        histogram={'edges': _from_scale(edges, scale).tolist(), 'counts': counts.tolist()},
        baseline=baseline, manual=manual, selection=selection)
