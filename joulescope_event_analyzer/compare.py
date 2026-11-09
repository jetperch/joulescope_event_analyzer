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

"""Compare two captures event type by event type.

The first capture's event types become templates for the second, so matching
events share the same names.  Both captures are analyzed with the same
detection thresholds (the larger of the two automatic values), so differences
in noise do not change which events are found or where they end.

When the sample rates differ by an integer ratio, the higher-rate capture can
also be filtered down to the lower rate (:class:`~joulescope_event_analyzer.source.DecimatedSource`).
That *model* shows what the lower rate should report for the same events, which
separates instrument bandwidth effects from real differences in the device.

For each type and metric, the difference of means is compared with its
standard error and with the natural event-to-event spread, and classified as:

* ``same``: within the expected variation.
* ``differs``: larger than the expected variation.
* ``bandwidth``: differs from the other capture, but matches the rate model,
  so the difference is explained by the sample rate.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import math
import numpy as np
from .analyze import AnalysisResult, analyze, collect_waveforms, Waveform
from .config import Config
from .source import DecimatedSource


COMPARE_METRICS = ('excess_charge', 'charge', 'excess_energy', 'energy', 'duration', 'i_peak')


@dataclass
class MetricComparison:
    a: dict | None
    b: dict | None
    model: dict | None = None
    diff: float = float('nan')          # (b - a) / a
    allowed: float = float('nan')       # allowed relative difference for 'same'
    model_diff: float = float('nan')    # (lower-rate capture - model) / model
    verdict: str = 'n/a'                # 'same', 'differs', 'bandwidth', 'n/a'


@dataclass
class TypeComparison:
    name: str
    count_a: int
    count_b: int
    count_model: int = 0
    metrics: dict = field(default_factory=dict)    # metric -> MetricComparison


@dataclass
class Comparison:
    a: AnalysisResult
    b: AnalysisResult
    model: AnalysisResult | None
    labels: tuple
    types: list                                    # list[TypeComparison]
    filter_examples: dict = field(default_factory=dict)   # type -> {'a': Waveform, 'model': Waveform}
    filter_examples_source: str | None = None             # 'a' or 'b': which capture is full rate
    model_ratio: int | None = None
    model_bandwidth: float | None = None
    rel_tolerance: float = 0.02
    sigmas: float = 3.0

    def type(self, name):
        for t in self.types:
            if t.name == name:
                return t
        return None


def _relative_allowed(sa, sb, rel_tolerance, sigmas):
    """Allowed |relative difference| from the standard error of the difference of means."""
    if not sa or not sb or not sa['mean']:
        return float('nan')

    def var_of_mean(s, other):
        std = s['std'] if s['n'] > 1 else other['std']
        return std ** 2 / max(1, s['n'])

    se = math.sqrt(var_of_mean(sa, sb) + var_of_mean(sb, sa))
    return max(rel_tolerance, sigmas * se / abs(sa['mean']))


def compare_results(a: AnalysisResult, b: AnalysisResult, model: AnalysisResult | None = None,
                    labels=('A', 'B'), rel_tolerance=0.02, sigmas=3.0, model_of: str = 'a') -> Comparison:
    """Compare two analyses whose types share names (see :func:`compare_sources`).

    :param model: Optional analysis of the higher-rate capture filtered to the lower rate.
    :param model_of: 'a' or 'b': which capture the model was made from.  The model is
        compared against the other (lower-rate) capture.
    """
    if model_of not in ('a', 'b'):
        raise ValueError(f'model_of must be a or b: {model_of}')
    names = [t.name for t in a.types if t.count] + [t.name for t in b.types if t.count and a.type(t.name) is None]
    out = []
    for name in names:
        ta, tb = a.type(name), b.type(name)
        tm = model.type(name) if model is not None else None
        tc = TypeComparison(name=name, count_a=ta.count if ta else 0, count_b=tb.count if tb else 0,
                            count_model=tm.count if tm else 0)
        for m in COMPARE_METRICS:
            sa = ta.stats.get(m) if ta and ta.count else None
            sb = tb.stats.get(m) if tb and tb.count else None
            sm = tm.stats.get(m) if tm and tm.count else None
            mc = MetricComparison(a=sa, b=sb, model=sm)
            if sa and sb and sa['mean']:
                mc.diff = sb['mean'] / sa['mean'] - 1
                mc.allowed = _relative_allowed(sa, sb, rel_tolerance, sigmas)
                if abs(mc.diff) <= mc.allowed:
                    mc.verdict = 'same'
                else:
                    mc.verdict = 'differs'
                    s_lo = sb if model_of == 'a' else sa
                    if sm and sm['mean']:
                        mc.model_diff = s_lo['mean'] / sm['mean'] - 1
                        if abs(mc.model_diff) <= _relative_allowed(sm, s_lo, rel_tolerance, sigmas):
                            mc.verdict = 'bandwidth'
            tc.metrics[m] = mc
        out.append(tc)
    return Comparison(a=a, b=b, model=model, labels=tuple(labels), types=out,
                      rel_tolerance=rel_tolerance, sigmas=sigmas)


def _rate_ratio(fs_hi, fs_lo):
    r = fs_hi / fs_lo
    k = round(r)
    if k >= 2 and abs(r - k) / k < 1e-3:
        return int(k)
    return None


def compare_sources(source_a, source_b, config: Config | None = None, labels=None, model: str = 'auto',
                    rel_tolerance=0.02, sigmas=3.0, progress=None) -> Comparison:
    """Analyze and compare two captures.

    :param source_a: The reference capture.  Its event types become the templates.
    :param source_b: The capture to compare.
    :param config: Analysis configuration for both.
    :param labels: Display labels, default from the source names.
    :param model: 'auto' to model the higher-rate capture at the lower rate when
        the rates differ by an integer ratio, or 'none'.
    :param rel_tolerance: Minimum relative difference considered significant.
    :param sigmas: Standard errors of the difference considered significant.
    :param progress: Optional callable(fraction, message).
    """
    config = (config or Config()).copy()
    labels = labels or (source_a.info.get('name', 'A'), source_b.info.get('name', 'B'))

    def stage(lo, hi, label):
        if progress is None:
            return None
        return lambda f, msg: progress(lo + (hi - lo) * f, f'{label}: {msg}')

    # 1. initial analyses to find each capture's automatic thresholds
    ra = analyze(source_a, config, progress=stage(0.0, 0.2, labels[0]), waveforms=False)
    rb = analyze(source_b, config, progress=stage(0.2, 0.35, labels[1]), waveforms=False)
    cfg = config.copy()
    if config.detect.start_threshold is None:
        cfg.detect.start_threshold = max(ra.thresholds.start, rb.thresholds.start)
    if config.detect.end_threshold is None:
        cfg.detect.end_threshold = max(ra.thresholds.end, rb.thresholds.end)

    # 2. final analyses with common thresholds; a's types are the templates for b
    ra = analyze(source_a, cfg, progress=stage(0.35, 0.55, labels[0]))
    tset = ra.template_set()
    rb = analyze(source_b, cfg, templates=tset, progress=stage(0.55, 0.75, labels[1]))

    # 3. optional model of the higher-rate capture at the lower rate
    rm = None
    ratio = None
    hi_src = None
    msrc = None
    if model == 'auto':
        if source_a.fs > source_b.fs:
            ratio, hi_src = _rate_ratio(source_a.fs, source_b.fs), source_a
        elif source_b.fs > source_a.fs:
            ratio, hi_src = _rate_ratio(source_b.fs, source_a.fs), source_b
        if ratio:
            msrc = DecimatedSource(hi_src, ratio)
            rm = analyze(msrc, cfg, templates=tset, progress=stage(0.75, 0.95, 'rate model'))
    elif model != 'none':
        raise ValueError(f'Unknown model option: {model}')

    cmp = compare_results(ra, rb, rm, labels=labels, rel_tolerance=rel_tolerance, sigmas=sigmas,
                          model_of='a' if hi_src is source_a else 'b')
    if rm is not None:
        cmp.model_ratio = ratio
        cmp.model_bandwidth = msrc.bandwidth
        hi_result = ra if hi_src is source_a else rb
        cmp.filter_examples = filter_examples(hi_src, msrc, hi_result, cfg)
        cmp.filter_examples_source = 'a' if hi_src is source_a else 'b'
    if progress:
        progress(1.0, 'Done')
    return cmp


def filter_examples(hi_src, model_src, hi_result: AnalysisResult, cfg: Config) -> dict:
    """For each type, the medoid event at full rate and the same time span through the rate model."""
    out = {}
    r = round(hi_src.fs / model_src.fs)
    for t in hi_result.types:
        if t.medoid is None:
            continue
        ev = hi_result.events[t.medoid]
        wf, _ = collect_waveforms(hi_src, hi_result.events, [t.medoid], cfg.report)
        if t.medoid not in wf:
            continue
        w = wf[t.medoid]
        a = ev.start + round(w.t0 * hi_src.fs)
        b = a + round(len(w.i) * w.dt * hi_src.fs)
        a5, b5 = -(-a // r), b // r
        d = model_src.read(a5, b5)
        out[t.name] = {
            'a': w,
            'model': Waveform(event=t.medoid, t0=(a5 * r - ev.start) / hi_src.fs, dt=1.0 / model_src.fs,
                              i=[float(f'{x:.5g}') for x in d['i']]),
        }
    return out
