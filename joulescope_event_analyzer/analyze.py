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

"""Run the full analysis: sleep, events, matching, statistics and charge budget.

Example::

    from joulescope_event_analyzer import analyze, open_source
    with open_source('capture.jls') as src:
        result = analyze(src)
    for t in result.types:
        print(t.name, t.count, t.stats['excess_charge']['mean'])

The returned :class:`AnalysisResult` is plain data: it can be rendered as a
report, exported, saved as JSON and loaded again without the source.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import datetime
import json
import math
import numpy as np
from .config import Config
from .detect import Event, Totals, Thresholds, detect_events
from .match import Envelope, extract_envelopes, cluster, distance
from .sleep import SleepEstimate, estimate_sleep
from .templates import Template, TemplateSet
from .version import __version__
from ._util import stats


@dataclass
class EventType:
    """Summary of one event type."""
    name: str
    members: list                  # event indices into AnalysisResult.events
    medoid: int | None             # event index of the most representative member
    from_template: bool = False
    stats: dict = field(default_factory=dict)   # metric -> {mean, std, min, max, n}
    count: int = 0
    rate: float = 0.0              # events per second
    excess_charge_total: float = 0.0
    excess_energy_total: float = float('nan')
    avg_current: float = 0.0       # contribution to the capture average current (A)
    charge_fraction: float = 0.0   # fraction of the total capture charge
    reference: Envelope | None = None

    def to_dict(self):
        d = {k: v for k, v in vars(self).items() if k != 'reference'}
        d['reference'] = self.reference.to_dict() if self.reference is not None else None
        return d

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d['reference'] = Envelope.from_dict(d['reference']) if d.get('reference') else None
        return cls(**d)


@dataclass
class Waveform:
    """Stored samples around one event for plotting."""
    event: int          # event index
    t0: float           # time of the first sample relative to the event start (s)
    dt: float           # sample period after decimation (s)
    i: list
    v: list | None = None
    p: list | None = None

    def to_dict(self):
        return dict(vars(self))

    @classmethod
    def from_dict(cls, d):
        return cls(**d)


@dataclass
class AnalysisResult:
    capture: dict                  # name, path, instrument, utc_start, fs, samples, duration, ...
    config: dict
    sleep: SleepEstimate
    thresholds: Thresholds
    totals: Totals
    events: list                   # list[Event], in time order
    types: list                    # list[EventType]
    budget: dict                   # charge / energy accounting
    rejected: int = 0
    rejected_charge: float = 0.0
    waveforms: dict = field(default_factory=dict)   # event index -> Waveform
    waveform_decimate: int = 1
    overview: dict = field(default_factory=dict)    # decimated current {'dt', 'max', 'min', 'mean'}
    version: str = __version__

    def type(self, name) -> EventType | None:
        for t in self.types:
            if t.name == name:
                return t
        return None

    def template_set(self, min_count: int = 1) -> TemplateSet:
        """Return templates for types with at least min_count events."""
        out = []
        for t in self.types:
            if t.count >= min_count and t.reference is not None:
                out.append(Template(name=t.name, envelope=t.reference,
                                    stats={k: t.stats.get(k) for k in ('duration', 'excess_charge', 'charge', 'energy', 'i_peak')} | {'count': t.count}))
        return TemplateSet(templates=out, source={k: self.capture.get(k) for k in ('name', 'instrument', 'utc_start', 'fs')})

    def to_dict(self, waveforms=True):
        return {
            'format': 'joulescope_event_analyzer.analysis', 'version': self.version,
            'capture': self.capture, 'config': self.config, 'sleep': self.sleep.to_dict(),
            'thresholds': vars(self.thresholds), 'totals': self.totals.to_dict(),
            'events': [e.to_dict() for e in self.events], 'types': [t.to_dict() for t in self.types],
            'budget': self.budget, 'rejected': self.rejected, 'rejected_charge': self.rejected_charge,
            'waveforms': {str(k): w.to_dict() for k, w in self.waveforms.items()} if waveforms else {},
            'waveform_decimate': self.waveform_decimate, 'overview': self.overview,
        }

    @classmethod
    def from_dict(cls, d):
        if d.get('format') != 'joulescope_event_analyzer.analysis':
            raise ValueError('Not a joulescope_event_analyzer analysis file')
        return cls(
            capture=d['capture'], config=d['config'], sleep=SleepEstimate.from_dict(d['sleep']),
            thresholds=Thresholds(**d['thresholds']), totals=Totals.from_dict(d['totals']),
            events=[Event.from_dict(e) for e in d['events']], types=[EventType.from_dict(t) for t in d['types']],
            budget=d['budget'], rejected=d.get('rejected', 0), rejected_charge=d.get('rejected_charge', 0.0),
            waveforms={int(k): Waveform.from_dict(w) for k, w in d.get('waveforms', {}).items()},
            waveform_decimate=d.get('waveform_decimate', 1), overview=d.get('overview', {}),
            version=d.get('version', ''))

    def save(self, path, waveforms=True):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(_json_clean(self.to_dict(waveforms=waveforms)), f, separators=(',', ':'))

    @classmethod
    def load(cls, path):
        with open(path, 'r', encoding='utf-8') as f:
            return cls.from_dict(json.load(f))


def _json_clean(obj):
    """Convert NaN/inf to None and datetimes to ISO strings for strict JSON."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, (np.floating,)):
        return _json_clean(float(obj))
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _json_clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_clean(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return _json_clean(obj.tolist())
    return obj


json_clean = _json_clean

EVENT_METRICS = ('duration', 'charge', 'excess_charge', 'energy', 'excess_energy', 'i_peak', 'i_mean', 'v_min')


def _type_summary(group, events, capture_duration, total_charge):
    full = [events[k] for k in group.members]
    t = EventType(name=group.name, members=list(group.members), medoid=group.medoid,
                  from_template=group.from_template, reference=group.reference)
    t.count = len(full)
    for m in EVENT_METRICS:
        t.stats[m] = stats([getattr(e, m) for e in full])
    starts = [e.t for e in full]
    t.stats['period'] = stats(np.diff(starts)) if len(starts) > 1 else None
    t.rate = t.count / capture_duration if capture_duration > 0 else 0.0
    t.excess_charge_total = float(sum(e.excess_charge for e in full))
    ee = [e.excess_energy for e in full if math.isfinite(e.excess_energy)]
    t.excess_energy_total = float(sum(ee)) if ee else float('nan')
    t.avg_current = t.excess_charge_total / capture_duration if capture_duration > 0 else 0.0
    t.charge_fraction = t.excess_charge_total / total_charge if total_charge else 0.0
    return t


def _budget(result_totals: Totals, types, events, battery_mAh):
    tot = result_totals
    dur = max(tot.duration, 1e-12)
    rows = [{'name': 'Sleep', 'kind': 'sleep', 'charge': tot.sleep_charge, 'energy': tot.sleep_energy}]
    for t in types:
        rows.append({'name': f'Type {t.name}', 'kind': 'type', 'type': t.name,
                     'charge': t.excess_charge_total, 'energy': t.excess_energy_total})
    partial = [e for e in events if e.partial]
    if partial:
        rows.append({'name': 'Partial events at capture edges', 'kind': 'partial',
                     'charge': float(sum(e.excess_charge for e in partial)),
                     'energy': float(np.nansum([e.excess_energy for e in partial]))})
    attributed = sum(r['charge'] for r in rows)
    rows.append({'name': 'Unattributed', 'kind': 'unattributed', 'charge': tot.charge - attributed,
                 'energy': tot.energy - float(np.nansum([r['energy'] for r in rows])) if math.isfinite(tot.energy) else float('nan')})
    for r in rows:
        r['avg_current'] = r['charge'] / dur
        r['charge_fraction'] = r['charge'] / tot.charge if tot.charge else float('nan')
    b = {'rows': rows, 'total_charge': tot.charge, 'total_energy': tot.energy,
         'avg_current': tot.charge / dur, 'avg_power': tot.energy / dur if math.isfinite(tot.energy) else float('nan'),
         'unattributed_fraction': rows[-1]['charge_fraction']}
    if battery_mAh:
        b['battery_capacity_mAh'] = battery_mAh
        b['battery_life_hours'] = battery_mAh * 3.6 / (tot.charge / dur) / 3600 if tot.charge > 0 else float('inf')
    return b


def _select_waveforms(types, events, cfg):
    """Pick evenly spaced members of each type (always including the medoid)."""
    chosen = []
    for t in types:
        m = t.members
        if not m:
            continue
        n = min(len(m), cfg.max_waveforms_per_type)
        sel = set(m[int(round(j))] for j in np.linspace(0, len(m) - 1, n)) if n > 1 else {m[0]}
        if t.medoid is not None:
            sel.add(t.medoid)
        chosen.extend(sorted(sel))
    return sorted(set(chosen))


def collect_waveforms(source, events, indices, cfg) -> tuple:
    """Read waveforms for the selected events.

    :return: (dict event index -> Waveform, decimation factor)
    """
    fs = source.fs
    pads = {k: max(10, round(max(cfg.pad, 0.1 * events[k].duration) * fs)) for k in indices}
    total = sum(events[k].end - events[k].start + 2 * pads[k] for k in indices)
    decim = max(1, int(math.ceil(total / max(1, cfg.max_plot_samples))))
    out = {}
    for k in indices:
        ev = events[k]
        a = max(0, ev.start - pads[k])
        b = min(source.length, ev.end + pads[k])
        n = (b - a) // decim * decim
        if n <= 0:
            continue
        data = source.read(a, a + n)

        def dec(x):
            if x is None:
                return None
            y = x[:n]
            if decim > 1:
                y = y.reshape(-1, decim).mean(axis=1)
            return [float(f'{v:.5g}') if math.isfinite(v) else None for v in y]

        out[k] = Waveform(event=k, t0=(a - ev.start) / fs + 0.5 * (decim - 1) / fs, dt=decim / fs,
                          i=dec(data['i']), v=dec(data['v']), p=dec(data['p']))
    return out, decim


def capture_info(source) -> dict:
    info = dict(source.info)
    info.update({'fs': source.fs, 'samples': source.length, 'duration': source.length / source.fs,
                 'has_voltage': source.has_voltage, 'has_power': source.has_power})
    if isinstance(info.get('utc_start'), datetime.datetime):
        info['utc_start'] = info['utc_start'].isoformat()
    return info


def analyze(source, config: Config | None = None, templates: TemplateSet | None = None,
            progress=None, waveforms: bool = True) -> AnalysisResult:
    """Analyze a capture.

    :param source: The :class:`~joulescope_event_analyzer.source.Source`.
    :param config: The :class:`~joulescope_event_analyzer.config.Config`.
    :param templates: Optional :class:`~joulescope_event_analyzer.templates.TemplateSet`
        whose type names are applied to matching events.
    :param progress: Optional callable(fraction, message) for status updates.
    :param waveforms: Collect waveforms for reports.
    :return: The :class:`AnalysisResult`.
    """
    config = config or Config()

    def stage(lo, hi):
        if progress is None:
            return None
        return lambda f, msg: progress(lo + (hi - lo) * f, msg)

    sleep = estimate_sleep(source, config.sleep, smooth=config.detect.smooth, chunk=config.detect.chunk,
                           progress=stage(0.0, 0.3))
    det = detect_events(source, sleep, config.detect, progress=stage(0.3, 0.7))
    events = det.events
    eligible = [not e.partial for e in events]
    envs = extract_envelopes(source, events, sleep.baseline, config.match, config.detect.chunk,
                             progress=stage(0.7, 0.8)) if events else []
    groups = cluster(envs, config.match, templates=templates.references() if templates else None,
                     eligible=eligible, progress=stage(0.8, 0.95))

    capture_duration = source.length / source.fs
    types = []
    for g in groups:
        g.members.sort()
        for idx, k in enumerate(g.members):
            ev = events[k]
            ev.type = g.name
            ev.index = idx + 1
            ev.match_distance, ev.lag = distance(g.reference, envs[k], config.match)
        types.append(_type_summary(g, events, capture_duration, det.totals.charge))

    result = AnalysisResult(
        capture=capture_info(source), config=config.to_dict(), sleep=sleep, thresholds=det.thresholds,
        totals=det.totals, events=events, types=types,
        budget=_budget(det.totals, types, events, config.report.battery_capacity_mAh),
        rejected=det.rejected, rejected_charge=det.rejected_charge, overview=det.overview)
    if waveforms and events:
        sel = _select_waveforms(types, events, config.report)
        result.waveforms, result.waveform_decimate = collect_waveforms(source, events, sel, config.report)
    if progress:
        progress(1.0, 'Done')
    return result
