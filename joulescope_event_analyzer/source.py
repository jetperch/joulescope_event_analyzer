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

"""Sample data sources.

All analysis reads data through the :class:`Source` interface, so the same code
works on JLS files, in-memory arrays (tests, the Joulescope UI), and derived
sources such as :class:`DecimatedSource`.

A source provides current and optionally voltage and power at a fixed sample
rate.  :meth:`Source.read` returns float64 numpy arrays; missing samples are NaN.
"""

from __future__ import annotations
import datetime
import os
import numpy as np


JLS_EPOCH = datetime.datetime(2018, 1, 1, tzinfo=datetime.timezone.utc)


class Source:
    """Base class for sample sources.

    :ivar fs: The sample rate in Hz.
    :ivar length: The number of samples.
    :ivar has_voltage: True if voltage is available.
    :ivar has_power: True if power is available (directly or computed from i * v).
    :ivar info: dict with optional 'name', 'path', 'instrument', 'utc_start' (datetime).
    """

    fs: float
    length: int
    has_voltage: bool = False
    has_power: bool = False

    def __init__(self):
        self.info = {}

    @property
    def duration(self) -> float:
        return self.length / self.fs

    def read(self, start: int, end: int) -> dict:
        """Read samples [start, end), clipped to the available range.

        :return: dict with 'i', 'v', 'p' float64 arrays ('v' and 'p' may be None).
        """
        raise NotImplementedError()

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class ArraySource(Source):
    """A source backed by numpy arrays.

    :param fs: The sample rate in Hz.
    :param current: Current samples in A.
    :param voltage: Voltage samples in V, or None.
    :param power: Power samples in W, or None to compute current * voltage.
    :param info: Optional metadata dict.
    """

    def __init__(self, fs, current, voltage=None, power=None, info=None):
        super().__init__()
        self.fs = float(fs)
        arrays = [None if x is None else np.asarray(x, dtype=np.float64) for x in (current, voltage, power)]
        n = min(len(x) for x in arrays if x is not None)
        self._i, self._v, self._p = [None if x is None else x[:n] for x in arrays]
        if self._p is None and self._v is not None:
            self._p = self._i * self._v
        self.length = int(n)
        self.has_voltage = self._v is not None
        self.has_power = self._p is not None
        self.info = dict(info or {})
        self.info.setdefault('name', 'array')

    def read(self, start, end):
        start, end = max(0, int(start)), min(self.length, int(end))
        if end < start:
            end = start
        return {
            'i': self._i[start:end],
            'v': None if self._v is None else self._v[start:end],
            'p': None if self._p is None else self._p[start:end],
        }


class JlsSource(Source):
    """A source backed by a Joulescope JLS file.

    :param path: The JLS file path.
    """

    def __init__(self, path):
        super().__init__()
        from pyjls import Reader
        self._path = os.fspath(path)
        self._reader = Reader(self._path)
        r = self._reader
        sig_i = self._find('current')
        if sig_i is None:
            r.close()
            raise ValueError(f'{self._path}: no current signal')
        self._sig_i = sig_i
        self._sig_v = self._find('voltage')
        self._sig_p = self._find('power')
        n = sig_i.length
        for s in (self._sig_v, self._sig_p):
            if s is not None:
                n = min(n, s.length)
        self.length = int(n)
        self.fs = float(sig_i.sample_rate)
        self.has_voltage = self._sig_v is not None
        self.has_power = self._sig_p is not None or self.has_voltage
        self.info = {'name': os.path.basename(self._path), 'path': os.path.abspath(self._path)}
        for src in r.sources.values():
            if src.source_id == sig_i.source_id:
                self.info['instrument'] = f'{src.model} {src.serial_number}'.strip()
        try:
            ts = r.sample_id_to_timestamp(sig_i.signal_id, 0)
            self.info['utc_start'] = JLS_EPOCH + datetime.timedelta(seconds=ts / 2 ** 30)
        except Exception:
            pass

    def _find(self, name):
        for s in self._reader.signals.values():
            if s.name == name and s.length > 0:
                return s
        return None

    def read(self, start, end):
        start, end = max(0, int(start)), min(self.length, int(end))
        if end <= start:
            e = np.zeros(0)
            return {'i': e, 'v': e if self.has_voltage else None, 'p': e if self.has_power else None}
        n = end - start
        r = self._reader

        def get(sig):
            return np.asarray(r.fsr(sig.signal_id, start, n), dtype=np.float64)

        i = get(self._sig_i)
        v = get(self._sig_v) if self._sig_v is not None else None
        if self._sig_p is not None:
            p = get(self._sig_p)
        elif v is not None:
            p = i * v
        else:
            p = None
        return {'i': i, 'v': v, 'p': p}

    def close(self):
        if self._reader is not None:
            self._reader.close()
            self._reader = None


def sinc3_kernel(ratio: int) -> np.ndarray:
    """Return the impulse response of three cascaded ratio-sample moving averages (unity DC gain)."""
    h = np.ones(ratio) / ratio
    return np.convolve(np.convolve(h, h), h)


def sinc3_bandwidth(fs: float, ratio: int) -> float:
    """Return the -3 dB bandwidth in Hz of :func:`sinc3_kernel` at input sample rate fs."""
    f = np.linspace(0, fs / 2, 200001)[1:]
    resp = np.abs(np.sinc(f * ratio / fs) / np.sinc(f / fs)) ** 3
    return float(f[np.argmax(resp < 10 ** (-3 / 20))])


class DecimatedSource(Source):
    """Model a lower sample rate by filtering and decimating another source.

    The filter is three cascaded moving averages (a Sinc3 model), centered so
    that it adds no delay.  It approximates instrument downsampling filters
    and preserves the mean (charge and energy).

    :param source: The full-rate source.
    :param ratio: The integer decimation ratio.
    """

    def __init__(self, source: Source, ratio: int):
        super().__init__()
        ratio = int(ratio)
        if ratio < 1:
            raise ValueError('ratio must be >= 1')
        self._src = source
        self.ratio = ratio
        self._h = sinc3_kernel(ratio)
        self.fs = source.fs / ratio
        self.length = source.length // ratio
        self.has_voltage = source.has_voltage
        self.has_power = source.has_power
        self.info = dict(source.info)
        self.info['name'] = f"{source.info.get('name', 'source')} (Sinc3 model at {self.fs:g} Hz)"
        self.info['model_of'] = source.info.get('name')
        self.bandwidth = sinc3_bandwidth(source.fs, ratio)

    def read(self, start, end):
        start, end = max(0, int(start)), min(self.length, int(end))
        if end <= start:
            e = np.zeros(0)
            return {'i': e, 'v': e if self.has_voltage else None, 'p': e if self.has_power else None}
        r, h = self.ratio, self._h
        half = len(h) // 2
        a = start * r - half
        b = (end - 1) * r - half + len(h)
        data = self._src.read(max(0, a), min(self._src.length, b))
        out = {}
        for k in ('i', 'v', 'p'):
            x = data[k]
            if x is None:
                out[k] = None
                continue
            # pad with edge values outside the source range
            pre, post = max(0, -a), max(0, b - self._src.length)
            if pre or post:
                x = np.concatenate((np.full(pre, x[0] if len(x) else 0.0), x, np.full(post, x[-1] if len(x) else 0.0)))
            y = np.convolve(x, h, mode='valid')     # y[j] uses x[j : j + len(h)]
            out[k] = y[::r][:end - start]
        return out


def open_source(path) -> Source:
    """Open a capture file as a :class:`Source`."""
    ext = os.path.splitext(os.fspath(path))[1].lower()
    if ext == '.jls':
        return JlsSource(path)
    raise ValueError(f'Unsupported file type: {path}')


def iter_chunks(source: Source, chunk: int, start: int = 0, end: int | None = None):
    """Yield (sample_start, data) for consecutive chunks."""
    end = source.length if end is None else min(end, source.length)
    chunk = max(1, int(chunk))
    for s in range(start, end, chunk):
        yield s, source.read(s, min(end, s + chunk))
