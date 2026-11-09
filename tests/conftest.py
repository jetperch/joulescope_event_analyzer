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

import pytest
from joulescope_event_analyzer.synthetic import Capture, radio, sensor, cpu


FS = 200_000


def standard_capture(seed=1, duration=3.0, fs=FS, radio_scale=1.0, noise=1e-6, sleep=4e-6):
    """Three non-overlapping event types with small natural variation."""
    cap = Capture(duration=duration, fs=fs, sleep=sleep, noise=noise, seed=seed)
    r = radio()
    if radio_scale != 1.0:
        r.segments = [(d, i * radio_scale) for d, i in r.segments]
    cap.add_periodic(r, period=0.375, phase=0.10, scale_spread=0.01)
    cap.add_periodic(sensor(), period=0.25, phase=0.03, scale_spread=0.01)
    cap.add_periodic(cpu(), period=1.0, phase=0.62, scale_spread=0.01)
    return cap


@pytest.fixture(scope='session')
def capture():
    return standard_capture()


@pytest.fixture(scope='session')
def result(capture):
    from joulescope_event_analyzer import analyze
    return analyze(capture.source())


def by_shape(result, truth):
    """Map each event type name to the truth shape name of its events (by start time)."""
    out = {}
    for t in result.types:
        names = set()
        for k in t.members:
            ev = result.events[k]
            best = min(truth, key=lambda x: abs(x.start - ev.start))
            names.add(best.name)
        out[t.name] = names
    return out
