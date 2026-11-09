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

"""Export analysis results to CSV and JSON."""

from __future__ import annotations
import csv
import json
import math
from .analyze import json_clean


EVENT_COLUMNS = [
    ('type', 'type', None), ('index', 'index', None), ('t', 'start_s', '.7f'), ('duration', 'duration_s', '.7g'),
    ('charge', 'charge_C', '.7g'), ('excess_charge', 'excess_charge_C', '.7g'), ('energy', 'energy_J', '.7g'),
    ('excess_energy', 'excess_energy_J', '.7g'), ('i_peak', 'i_peak_A', '.7g'), ('i_mean', 'i_mean_A', '.7g'),
    ('v_mean', 'v_mean_V', '.7g'), ('v_min', 'v_min_V', '.7g'), ('significance', 'significance', '.4g'),
    ('match_distance', 'match_distance', '.4g'), ('partial', 'partial', None), ('gap', 'gap', None),
]


def _fmt(v, f):
    if v is None:
        return ''
    if isinstance(v, bool):
        return '1' if v else '0'
    if f and isinstance(v, (int, float)):
        return '' if (isinstance(v, float) and not math.isfinite(v)) else format(v, f)
    return str(v)


def write_events_csv(result, path):
    """Write one row per event."""
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow([c[1] for c in EVENT_COLUMNS])
        for ev in result.events:
            w.writerow([_fmt(getattr(ev, c[0]), c[2]) for c in EVENT_COLUMNS])
    return path


def summary(result) -> dict:
    """Compact machine-readable summary (no per-event data), e.g. for automated tests."""
    return json_clean({
        'capture': result.capture,
        'sleep': {k: getattr(result.sleep, k) for k in ('level', 'noise_sample', 'noise_detect', 'manual', 'selection')},
        'levels': [vars(x) for x in result.sleep.levels],
        'thresholds': vars(result.thresholds),
        'budget': result.budget,
        'events': len(result.events),
        'rejected': result.rejected,
        'types': [{'name': t.name, 'count': t.count, 'rate': t.rate, 'avg_current': t.avg_current,
                   'charge_fraction': t.charge_fraction, 'stats': t.stats} for t in result.types],
        'version': result.version,
    })


def write_summary_json(result, path):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(summary(result), f, indent=1)
    return path
