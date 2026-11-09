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

"""Command line interface.

    python -m joulescope_event_analyzer analyze capture.jls
    python -m joulescope_event_analyzer compare a.jls b.jls --labels "1 MHz" "50 kHz"
    python -m joulescope_event_analyzer template create capture.jls -o templates.json
    python -m joulescope_event_analyzer template show templates.json
"""

from __future__ import annotations
import argparse
import os
import sys
from .version import __version__


def _add_analysis_args(p):
    g = p.add_argument_group('analysis options (override the --config file)')
    g.add_argument('--config', help='Configuration file (.toml or .json).')
    g.add_argument('--sleep', type=float, help='Force the sleep current in A.')
    g.add_argument('--sleep-noise', type=float, help='Force the detection noise in A rms.')
    g.add_argument('--baseline-window', type=float, help='Rolling sleep baseline window in s (for drifting sleep current).')
    g.add_argument('--min-dwell', type=float, help='Minimum continuous time at a level for it to be sleep, in s.')
    g.add_argument('--smooth', type=float, help='Detection smoothing window in s.')
    g.add_argument('--start-sigma', type=float, help='Start threshold in noise sigmas above sleep.')
    g.add_argument('--end-sigma', type=float, help='End threshold in noise sigmas above sleep.')
    g.add_argument('--threshold', type=float, help='Absolute start threshold in A above sleep.')
    g.add_argument('--end-threshold', type=float, help='Absolute end threshold in A above sleep.')
    g.add_argument('--merge-gap', type=float, help='Merge events closer than this many seconds.')
    g.add_argument('--min-duration', type=float, help='Reject events shorter than this many seconds.')
    g.add_argument('--significance', type=float, help='Required excess charge in noise sigmas.')
    g.add_argument('--min-charge', type=float, help='Reject events with less excess charge than this, in C.')
    g.add_argument('--match', type=float, help='Waveform match tolerance (0..1, smaller = more types).')
    g.add_argument('--battery', type=float, help='Battery capacity in mAh for battery-life estimates.')
    g.add_argument('--cdn', action='store_true', help='Load Plotly from the internet instead of embedding it.')
    g.add_argument('--quiet', '-q', action='store_true', help='Only print errors.')


_OVERRIDES = [
    ('sleep', 'sleep', 'level'), ('sleep_noise', 'sleep', 'noise'), ('baseline_window', 'sleep', 'baseline_window'),
    ('min_dwell', 'sleep', 'min_dwell'), ('smooth', 'detect', 'smooth'), ('start_sigma', 'detect', 'start_sigma'),
    ('end_sigma', 'detect', 'end_sigma'), ('threshold', 'detect', 'start_threshold'),
    ('end_threshold', 'detect', 'end_threshold'), ('merge_gap', 'detect', 'merge_gap'),
    ('min_duration', 'detect', 'min_duration'), ('significance', 'detect', 'significance'),
    ('min_charge', 'detect', 'min_charge'), ('match', 'match', 'tolerance'),
    ('battery', 'report', 'battery_capacity_mAh'),
]


def config_from_args(args):
    from .config import Config, load_config
    cfg = load_config(args.config) if getattr(args, 'config', None) else Config()
    for arg, section, name in _OVERRIDES:
        v = getattr(args, arg, None)
        if v is not None:
            setattr(getattr(cfg, section), name, v)
    if getattr(args, 'cdn', False):
        cfg.report.plotly = 'cdn'
    return cfg


class _Progress:
    def __init__(self, quiet):
        self.quiet = quiet
        self.last = None

    def __call__(self, fraction, message):
        if not self.quiet and message != self.last:
            print(f'  {message}', file=sys.stderr)
            self.last = message


def _eng(x, unit):
    import math
    if x is None or not math.isfinite(x):
        return '-'
    for scale, prefix in ((1, ''), (1e-3, 'm'), (1e-6, 'u'), (1e-9, 'n'), (1e-12, 'p')):
        if abs(x) >= scale * 0.9995 or scale == 1e-12:
            return f'{x / scale:.4g} {prefix}{unit}'


def print_result(result, out=None):
    out = sys.stdout if out is None else out
    s = result.sleep
    print(f"{result.capture.get('name')}: {result.capture['duration']:.3f} s at {result.capture['fs']:g} Hz", file=out)
    print(f'  sleep {_eng(s.level, "A")}, noise {_eng(s.noise_detect, "A")} rms, '
          f'thresholds start {_eng(result.thresholds.start, "A")} / end {_eng(result.thresholds.end, "A")} above sleep', file=out)
    print(f'  {len(result.events)} events in {len(result.types)} types, {result.rejected} rejected as noise, '
          f'average current {_eng(result.budget["avg_current"], "A")}, unattributed {100 * result.budget["unattributed_fraction"]:.2f} %', file=out)
    for t in result.types:
        if not t.count:
            continue
        st = t.stats
        print(f'  Type {t.name:>3}: n={t.count:5d}  duration={_eng(st["duration"]["mean"], "s"):>10}  '
              f'excess charge={_eng(st["excess_charge"]["mean"], "C"):>10}  energy={_eng(st["energy"]["mean"], "J"):>10}  '
              f'avg={_eng(t.avg_current, "A"):>10}', file=out)


def cmd_analyze(args):
    from .files import analyze_file
    cfg = config_from_args(args)
    result, files = analyze_file(args.filename, args.output, cfg, templates=args.templates,
                                 progress=_Progress(args.quiet), html=not args.no_html,
                                 save_analysis=args.save_analysis)
    written = list(files.values())
    if args.save_templates:
        result.template_set().save(args.save_templates)
        written.append(args.save_templates)
    if not args.quiet:
        print_result(result)
        for w in written:
            print(f'  wrote {w}')
    return 0


def cmd_compare(args):
    from .compare import compare_sources
    from .report import write_comparison
    from .source import open_source
    cfg = config_from_args(args)
    with open_source(args.a) as a, open_source(args.b) as b:
        labels = tuple(args.labels) if args.labels else None
        cmp = compare_sources(a, b, cfg, labels=labels, model=args.model, rel_tolerance=args.tolerance / 100,
                              sigmas=args.sigmas, progress=_Progress(args.quiet))
    out = args.output or 'compare.html'
    write_comparison(cmp, out, plotly=cfg.report.plotly)
    if not args.quiet:
        print(f'{cmp.labels[0]} vs {cmp.labels[1]}' + (f' (with {cmp.model_ratio}x rate model)' if cmp.model else ''))
        for t in cmp.types:
            m = t.metrics
            verdicts = ', '.join(f'{k} {m[k].verdict}' for k in ('excess_charge', 'excess_energy', 'duration', 'i_peak'))
            d = m['excess_charge'].diff
            dtxt = f'{100 * d:+.2f} %' if d == d else 'n/a'
            print(f'  Type {t.name:>3}: n={t.count_a}/{t.count_b}  charge diff {dtxt:>9}  [{verdicts}]')
        print(f'  wrote {out}')
    return 0


def cmd_template_create(args):
    from .analyze import analyze
    from .source import open_source
    cfg = config_from_args(args)
    with open_source(args.filename) as src:
        result = analyze(src, cfg, progress=_Progress(args.quiet), waveforms=False)
    ts = result.template_set(min_count=args.min_count)
    ts.save(args.output)
    if not args.quiet:
        print(f'{len(ts.templates)} templates from {result.capture.get("name")}: {", ".join(ts.names())}')
        print(f'  wrote {args.output}')
    return 0


def cmd_template_show(args):
    from .templates import TemplateSet
    ts = TemplateSet.load(args.filename)
    src = ts.source or {}
    print(f"{args.filename}: {len(ts.templates)} templates from {src.get('name', '?')} ({src.get('instrument', '')}, {src.get('fs', '?')} Hz)")
    for t in ts.templates:
        st = t.stats or {}
        dur = (st.get('duration') or {}).get('mean')
        q = (st.get('excess_charge') or {}).get('mean')
        print(f"  Type {t.name:>3}: count={st.get('count', '?')}  duration={_eng(dur, 's')}  excess charge={_eng(q, 'C')}")
    return 0


def get_parser():
    p = argparse.ArgumentParser(prog='joulescope_event_analyzer',
                                description='Find, measure, group and compare current events in Joulescope captures.')
    p.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    sub = p.add_subparsers(dest='command', required=True)

    a = sub.add_parser('analyze', help='Analyze one capture and write an HTML report, CSV and summary JSON.')
    a.add_argument('filename', help='The capture file (.jls).')
    a.add_argument('-o', '--output', help='Output directory.  Default: <capture>_events next to the capture.')
    a.add_argument('--templates', help='Apply event type templates from this file.')
    a.add_argument('--save-templates', help='Save this capture\'s event types as templates to this file.')
    a.add_argument('--save-analysis', action='store_true', help='Also save the full analysis as JSON.')
    a.add_argument('--no-html', action='store_true', help='Skip the HTML report.')
    _add_analysis_args(a)
    a.set_defaults(func=cmd_analyze)

    c = sub.add_parser('compare', help='Compare two captures event type by event type.')
    c.add_argument('a', help='The reference capture.  Its event types become the templates.')
    c.add_argument('b', help='The capture to compare.')
    c.add_argument('-o', '--output', help='Output HTML path.  Default: compare.html')
    c.add_argument('--labels', nargs=2, metavar=('LABEL_A', 'LABEL_B'), help='Display names for the captures.')
    c.add_argument('--model', choices=['auto', 'none'], default='auto',
                   help='Model the higher-rate capture at the lower rate when rates differ (default auto).')
    c.add_argument('--tolerance', type=float, default=2.0, help='Minimum significant difference in percent (default 2).')
    c.add_argument('--sigmas', type=float, default=3.0, help='Standard errors for a significant difference (default 3).')
    _add_analysis_args(c)
    c.set_defaults(func=cmd_compare)

    t = sub.add_parser('template', help='Create or show event type templates.')
    tsub = t.add_subparsers(dest='template_command', required=True)
    tc = tsub.add_parser('create', help='Create templates from a capture.')
    tc.add_argument('filename', help='The capture file (.jls).')
    tc.add_argument('-o', '--output', required=True, help='Output template file (.json).')
    tc.add_argument('--min-count', type=int, default=1, help='Only types with at least this many events.')
    _add_analysis_args(tc)
    tc.set_defaults(func=cmd_template_create)
    ts = tsub.add_parser('show', help='Show a template file.')
    ts.add_argument('filename', help='The template file (.json).')
    ts.set_defaults(func=cmd_template_show)
    return p


def main(argv=None):
    args = get_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError, KeyError, RuntimeError) as ex:
        print(f'ERROR: {ex}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
