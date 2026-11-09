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

"""Internal numeric helpers."""

from __future__ import annotations
import numpy as np


def moving_average(x: np.ndarray, n: int) -> np.ndarray:
    """Centered moving average with edge extension; same length as x."""
    x = np.asarray(x, dtype=np.float64)
    n = int(max(1, n))
    if n == 1 or len(x) == 0:
        return x.copy()
    if len(x) < n:
        return np.full(len(x), x.mean())
    c = np.cumsum(np.concatenate(([0.0], x)))
    y = (c[n:] - c[:-n]) / n
    pre = (n - 1) // 2
    return np.concatenate((np.full(pre, y[0]), y, np.full(n - 1 - pre, y[-1])))


def fill_forward(x: np.ndarray, initial: float) -> np.ndarray:
    """Replace NaN samples with the previous finite sample (initial if none)."""
    x = np.asarray(x, dtype=np.float64)
    bad = ~np.isfinite(x)
    if not bad.any():
        return x
    idx = np.where(bad, 0, np.arange(len(x)))
    np.maximum.accumulate(idx, out=idx)
    y = x[idx]
    lead = ~np.isfinite(y)          # NaN before the first finite sample
    y[lead] = initial
    return y


def stats(x) -> dict | None:
    """Return mean/std/min/max/n for a sequence, or None if empty."""
    x = np.asarray(x, dtype=np.float64)
    x = x[np.isfinite(x)]
    if not len(x):
        return None
    return {'mean': float(x.mean()), 'std': float(x.std()), 'min': float(x.min()),
            'max': float(x.max()), 'n': int(len(x))}


def type_name(idx: int) -> str:
    """0 -> 'A', 25 -> 'Z', 26 -> 'AA', ..."""
    s = ''
    idx += 1
    while idx:
        idx, r = divmod(idx - 1, 26)
        s = chr(ord('A') + r) + s
    return s


def next_type_names(existing, count):
    """Return count new names that are not in existing."""
    existing = set(existing)
    out, k = [], 0
    while len(out) < count:
        n = type_name(k)
        if n not in existing:
            out.append(n)
        k += 1
    return out
