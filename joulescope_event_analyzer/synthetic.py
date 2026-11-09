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

"""Synthetic captures with known events, for tests, demos and validation.

Example::

    from joulescope_event_analyzer.synthetic import Capture, radio, sensor
    cap = Capture(duration=2.0, fs=200_000, sleep=4e-6, noise=1e-6, seed=1)
    cap.add_periodic(radio(), period=0.375)
    cap.add_periodic(sensor(), period=0.1, phase=0.05)
    src = cap.source()           # an ArraySource
    truth = cap.truth            # list of TruthEvent
"""

from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
from .source import ArraySource


@dataclass
class Shape:
    """An event current shape above sleep.

    :ivar name: Shape name.
    :ivar segments: List of (duration_s, current_A) plateaus.
    :ivar spike: Optional (current_A, duration_s) spike at the start.
    :ivar tail: Optional (current_A, tau_s) exponential tail after the last segment.
    """
    name: str
    segments: list
    spike: tuple | None = None
    tail: tuple | None = None

    def waveform(self, fs: float, scale: float = 1.0, stretch: float = 1.0) -> np.ndarray:
        """Return the excess current samples at sample rate fs."""
        parts = []
        for d, i in self.segments:
            parts.append(np.full(max(1, round(d * stretch * fs)), i * scale))
        x = np.concatenate(parts) if parts else np.zeros(1)
        if self.spike:
            amp, d = self.spike
            n = max(1, round(d * fs))
            x[:n] += amp * scale
        if self.tail:
            amp, tau = self.tail
            n = max(1, round(5 * tau * fs))
            x = np.concatenate((x, amp * scale * np.exp(-np.arange(n) / (tau * fs))))
        return x

    @property
    def duration(self) -> float:
        d = sum(s[0] for s in self.segments)
        if self.tail:
            d += 5 * self.tail[1]
        return d

    def charge(self, fs: float, scale=1.0, stretch=1.0) -> float:
        return float(self.waveform(fs, scale, stretch).sum() / fs)


def radio() -> Shape:
    """A ~2.8 ms radio-like event: spike, ramp-up plateaus, decaying tail."""
    return Shape('radio', [(0.6e-3, 4e-3), (0.8e-3, 5e-3), (0.9e-3, 8e-3), (0.5e-3, 5e-3)],
                 spike=(25e-3, 10e-6), tail=(1e-3, 50e-6))


def sensor() -> Shape:
    """A ~0.35 ms sensor read: short spike then a low plateau."""
    return Shape('sensor', [(0.35e-3, 1.5e-3)], spike=(20e-3, 8e-6), tail=(0.2e-3, 30e-6))


def cpu() -> Shape:
    """A ~2 ms processing burst."""
    return Shape('cpu', [(2.0e-3, 3e-3)], tail=(0.5e-3, 40e-6))


def blip() -> Shape:
    """A tiny ~1 ms bump (real but small activity)."""
    return Shape('blip', [(1.0e-3, 60e-6)])


@dataclass
class TruthEvent:
    name: str
    start: int          # sample id
    end: int            # sample id after the last sample
    excess_charge: float


@dataclass
class Capture:
    """Build a synthetic capture.

    :ivar duration: Capture duration in seconds.
    :ivar fs: Sample rate in Hz.
    :ivar sleep: Sleep current in A.
    :ivar noise: White noise rms in A.
    :ivar voltage: Supply voltage in V.
    :ivar resistance: Series resistance in ohms (voltage droop under load).
    :ivar drift: Linear sleep current change over the capture in A.
    :ivar seed: Random seed.
    """
    duration: float = 1.0
    fs: float = 200_000
    sleep: float = 4e-6
    noise: float = 1e-6
    voltage: float = 3.0
    resistance: float = 1.0
    drift: float = 0.0
    seed: int = 0
    truth: list = field(default_factory=list)

    def __post_init__(self):
        self.n = int(round(self.duration * self.fs))
        self._excess = np.zeros(self.n)
        self.rng = np.random.default_rng(self.seed)

    def add(self, shape: Shape, t: float, scale: float = 1.0, stretch: float = 1.0):
        """Add one event of shape starting at time t."""
        w = shape.waveform(self.fs, scale, stretch)
        s = int(round(t * self.fs))
        e = min(self.n, s + len(w))
        if s < 0 or s >= self.n:
            return
        self._excess[s:e] += w[:e - s]
        self.truth.append(TruthEvent(shape.name, s, e, float(w[:e - s].sum() / self.fs)))

    def add_periodic(self, shape: Shape, period: float, phase: float = 0.0, jitter: float = 0.0,
                     scale_spread: float = 0.0, stretch_spread: float = 0.0, end: float | None = None):
        """Add events every period seconds with optional timing jitter and size variation."""
        end = self.duration if end is None else end
        t = phase
        while t < end:
            tj = t + (self.rng.normal(0, jitter) if jitter else 0.0)
            scale = 1.0 + (self.rng.normal(0, scale_spread) if scale_spread else 0.0)
            stretch = 1.0 + (self.rng.normal(0, stretch_spread) if stretch_spread else 0.0)
            self.add(shape, tj, scale, stretch)
            t += period

    def add_level(self, current: float, t: float, duration: float):
        """Add a constant offset (e.g. a dip below sleep when negative) without recording it as an event."""
        s, e = int(round(t * self.fs)), int(round((t + duration) * self.fs))
        self._excess[max(0, s):min(self.n, e)] += current

    def arrays(self):
        """Return (current, voltage) sample arrays.  Repeated calls return identical data."""
        base = self.sleep + self.drift * np.arange(self.n) / max(1, self.n)
        i = base + self._excess
        if self.noise:
            i = i + np.random.default_rng(self.seed + 1_000_003).normal(0, self.noise, self.n)
        v = self.voltage - self.resistance * i
        return i, v

    def source(self, info=None) -> ArraySource:
        i, v = self.arrays()
        return ArraySource(self.fs, i, v, info=info or {'name': 'synthetic'})

    def write_jls(self, path):
        """Write the capture to a JLS file with current, voltage and power."""
        from pyjls import Writer, SourceDef, SignalDef, SignalType, DataType
        i, v = self.arrays()
        p = i * v
        with Writer(str(path)) as w:
            w.source_def_from_struct(SourceDef(source_id=1, name='synthetic', vendor='Jetperch LLC',
                                               model='SIM', version='1', serial_number='0001'))
            for sid, name, units in ((1, 'current', 'A'), (2, 'voltage', 'V'), (3, 'power', 'W')):
                w.signal_def_from_struct(SignalDef(signal_id=sid, source_id=1, signal_type=SignalType.FSR,
                                                   data_type=DataType.F32, sample_rate=int(self.fs),
                                                   name=name, units=units))
            for sid, x in ((1, i), (2, v), (3, p)):
                w.fsr_f32(sid, 0, x.astype(np.float32))
        return path
