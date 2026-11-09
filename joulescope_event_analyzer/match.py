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

"""Group events into types by waveform shape.

Each event is summarized by an *envelope*: its excess current (above sleep)
averaged over a time grid.  The grid step is a power-of-two multiple of 1 µs
chosen so the event spans about ``grid_points`` steps.  Envelopes are computed
by integrating charge, so they are independent of the sample rate and preserve
charge.  This lets templates from one capture match events in another capture
at a different sample rate.

Two events are compared by the normalized RMS difference of their smoothed
envelopes, minimized over a small time shift::

    distance = min_lag ||a - shift(b, lag)|| / max(||a||, ||b||)

0 means identical, 1 means unrelated.  Events whose durations or charges differ
by more than the configured ratios are never compared.

Clustering assigns each event to the nearest type reference within tolerance,
creating new types as needed, then refines each type's reference to its medoid
(the member most similar to the others).  Template types keep their template
envelope as the reference so names stay consistent across captures.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import math
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from .config import MatchConfig
from ._util import moving_average, next_type_names


BASE_STEP = 1e-6       # seconds; grid steps are BASE_STEP * 2**level
MAX_LEVEL_DIFF = 2     # envelopes compared at most 2 levels apart (4x step)


@dataclass
class Envelope:
    """Excess current versus time on a grid of step seconds.

    data[pad] is the grid cell that begins at the event start.
    """
    level: int
    step: float
    pad: int
    length: int                # grid cells spanning the event duration
    data: np.ndarray
    duration: float            # (s)
    excess_charge: float       # (C)
    _cache: dict = field(default_factory=dict, repr=False, compare=False)

    def to_dict(self):
        return {'level': self.level, 'step': self.step, 'pad': self.pad, 'length': self.length,
                'data': [float(f'{x:.6g}') for x in self.data], 'duration': self.duration,
                'excess_charge': self.excess_charge}

    @classmethod
    def from_dict(cls, d):
        return cls(level=int(d['level']), step=float(d['step']), pad=int(d['pad']), length=int(d['length']),
                   data=np.asarray(d['data'], dtype=np.float64), duration=float(d['duration']),
                   excess_charge=float(d['excess_charge']))

    def at_level(self, level: int, smooth_steps: int):
        """Return (smoothed data, pad, length) resampled to a coarser level."""
        key = (level, smooth_steps)
        if key not in self._cache:
            f = 2 ** (level - self.level)
            if f < 1:
                raise ValueError('cannot resample to a finer level')
            x = self.data
            n = len(x) // f * f
            x = x[:n].reshape(-1, f).mean(axis=1) if f > 1 else x
            x = moving_average(x, smooth_steps)
            self._cache[key] = (x, self.pad // f, int(math.ceil(self.length / f)))
        return self._cache[key]


def envelope_level(duration: float, cfg: MatchConfig) -> int:
    return max(0, int(math.ceil(math.log2(max(duration, BASE_STEP) / (BASE_STEP * cfg.grid_points)))))


def envelope_pad(cfg: MatchConfig) -> int:
    """Padding in grid cells at the native level, enough for lag search at coarser levels."""
    return (cfg.max_lag_steps + cfg.smooth_steps + 2) * 2 ** MAX_LEVEL_DIFF


def compute_envelope(data_i, baseline, first_sample, fs, ev_start, ev_end, cfg: MatchConfig) -> Envelope:
    """Compute an event envelope from samples.

    :param data_i: Current samples covering at least the envelope window (NaN allowed).
    :param baseline: Sleep baseline for the same samples.
    :param first_sample: Sample id of data_i[0].
    :param fs: Sample rate.
    :param ev_start: Event start sample id.
    :param ev_end: Event end sample id.
    """
    duration = (ev_end - ev_start) / fs
    level = envelope_level(duration, cfg)
    step = BASE_STEP * 2 ** level
    pad = envelope_pad(cfg)
    length = max(1, int(math.ceil(duration / step)))
    x = np.asarray(data_i, dtype=np.float64) - baseline
    x = np.where(np.isfinite(x), x, 0.0)
    q = np.concatenate(([0.0], np.cumsum(x) / fs))       # charge at sample boundaries
    t_q = (first_sample + np.arange(len(q))) / fs        # boundary times
    t0 = ev_start / fs
    edges = t0 + (np.arange(length + 2 * pad + 1) - pad) * step
    qe = np.interp(edges, t_q, q, left=q[0], right=q[-1])
    env = np.diff(qe) / step
    ex = float(np.sum(x[max(0, ev_start - first_sample):ev_end - first_sample]) / fs)
    return Envelope(level=level, step=step, pad=pad, length=length, data=env, duration=duration, excess_charge=ex)


def extract_envelopes(source, events, baseline, cfg: MatchConfig, chunk=1_000_000, progress=None) -> list:
    """Compute envelopes for events (sorted by start) from a source."""
    out = [None] * len(events)
    pad = envelope_pad(cfg)
    fs = source.fs
    windows = []
    for k, ev in enumerate(events):
        step = BASE_STEP * 2 ** envelope_level((ev.end - ev.start) / fs, cfg)
        p = int(math.ceil((pad + 1) * step * fs)) + 1
        windows.append((ev.start - p, ev.end + p))
    k = 0
    while k < len(events):
        w0 = windows[k][0]
        j = k
        while j + 1 < len(events) and windows[j + 1][1] - w0 <= chunk:
            j += 1
        w1 = max(windows[k:j + 1], key=lambda w: w[1])[1]
        a, b = max(0, w0), min(source.length, w1)
        i = source.read(a, b)['i']
        base = baseline.range(a, b)
        for m in range(k, j + 1):
            ev = events[m]
            s0 = max(windows[m][0], a) - a
            s1 = min(windows[m][1], b) - a
            out[m] = compute_envelope(i[s0:s1], base[s0:s1], a + s0, fs, ev.start, ev.end, cfg)
        k = j + 1
        if progress:
            progress(k / len(events), 'Computing envelopes')
    return out


def compatible(a: Envelope, b: Envelope, cfg: MatchConfig) -> bool:
    if abs(a.level - b.level) > MAX_LEVEL_DIFF:
        return False
    da, db = max(a.duration, 1e-12), max(b.duration, 1e-12)
    if max(da, db) / min(da, db) > cfg.duration_ratio:
        return False
    qa, qb = a.excess_charge, b.excess_charge
    if qa <= 0 or qb <= 0:
        return qa <= 0 and qb <= 0
    return max(qa, qb) / min(qa, qb) <= cfg.charge_ratio


def _window(x, start, n):
    """x[start:start+n] with zero fill outside x."""
    out = np.zeros(n)
    a, b = max(0, start), min(len(x), start + n)
    if b > a:
        out[a - start:b - start] = x[a:b]
    return out


def distance(a: Envelope, b: Envelope, cfg: MatchConfig) -> tuple:
    """Return (distance, lag_seconds): b shifted by lag best matches a.

    A positive lag means b's features occur later than a's relative to the event start.
    """
    if not compatible(a, b, cfg):
        return math.inf, 0.0
    level = max(a.level, b.level)
    xa, pa, la = a.at_level(level, cfg.smooth_steps)
    xb, pb, lb = b.at_level(level, cfg.smooth_steps)
    m = cfg.max_lag_steps
    n = max(la, lb) + 2 * cfg.smooth_steps
    x = _window(xa, pa - cfg.smooth_steps, n)
    yw = _window(xb, pb - cfg.smooth_steps - m, n + 2 * m)
    ys = sliding_window_view(yw, n)          # (2m+1, n): row k is lag k - m
    errs = np.linalg.norm(ys - x, axis=1)
    norm = max(np.linalg.norm(x), np.linalg.norm(ys[m]))
    if norm <= 0:
        return 0.0, 0.0
    k = int(np.argmin(errs))
    return float(errs[k] / norm), float((k - m) * BASE_STEP * 2 ** level)


@dataclass
class TypeGroup:
    """A group of matching events."""
    name: str
    members: list                       # event indices
    reference: Envelope                 # envelope used for matching
    medoid: int | None = None           # event index of the most representative member
    from_template: bool = False


def _medoid(members, envs, cfg, rng):
    if len(members) == 1:
        return members[0]
    sample = members if len(members) <= cfg.medoid_sample else list(rng.choice(members, cfg.medoid_sample, replace=False))
    best, best_score = members[0], math.inf
    candidates = sample
    for a in candidates:
        d = [distance(envs[a], envs[b], cfg)[0] for b in sample if b != a]
        d = [x if math.isfinite(x) else 1.0 for x in d]
        score = float(np.mean(d)) if d else 0.0
        if score < best_score:
            best, best_score = a, score
    return best


def cluster(envelopes, cfg: MatchConfig | None = None, templates=None, eligible=None, progress=None) -> list:
    """Group envelopes into types.

    :param envelopes: list of :class:`Envelope`, one per event.
    :param cfg: The match configuration.
    :param templates: Optional list of (name, Envelope) references to match first.
    :param eligible: Optional boolean list; False events are skipped (e.g. partial events).
    :return: list of :class:`TypeGroup`.  New (non-template) groups are named after
        the template names, in order of decreasing total excess charge.
    """
    cfg = cfg or MatchConfig()
    rng = np.random.default_rng(0)
    n = len(envelopes)
    eligible = [True] * n if eligible is None else list(eligible)
    idx = [k for k in range(n) if eligible[k]]
    fixed = [TypeGroup(name=name, members=[], reference=env, from_template=True) for name, env in (templates or [])]

    def assign(groups, allow_new):
        for g in groups:
            g.members = []
        new_groups = []
        for count, k in enumerate(idx):
            best, best_d = None, math.inf
            for g in groups + new_groups:
                d = distance(g.reference, envelopes[k], cfg)[0]
                if d < best_d:
                    best, best_d = g, d
            if best is not None and best_d < cfg.tolerance:
                best.members.append(k)
            elif allow_new:
                new_groups.append(TypeGroup(name='', members=[k], reference=envelopes[k]))
            if progress and count % 256 == 0:
                progress(count / max(1, len(idx)), 'Matching events')
        return groups + new_groups

    groups = assign(fixed, True)
    for _ in range(max(0, cfg.refine_iterations)):
        for g in groups:
            if not g.from_template and g.members:
                g.medoid = _medoid(g.members, envelopes, cfg, rng)
                g.reference = envelopes[g.medoid]
        groups = [g for g in groups if g.from_template or g.members]
        groups = assign(groups, True)
        groups = [g for g in groups if g.from_template or g.members]

    for g in groups:
        if g.members:
            g.medoid = _medoid(g.members, envelopes, cfg, rng)
            if not g.from_template:
                g.reference = envelopes[g.medoid]

    # name new groups by decreasing total excess charge
    new = [g for g in groups if not g.from_template]
    new.sort(key=lambda g: -sum(envelopes[k].excess_charge for k in g.members))
    names = next_type_names([g.name for g in groups if g.from_template], len(new))
    for g, name in zip(new, names):
        g.name = name
    return [g for g in groups if g.from_template] + new
