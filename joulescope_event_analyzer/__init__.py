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

"""Find, measure, group and compare current events in Joulescope captures.

Typical use::

    from joulescope_event_analyzer import analyze_file
    result, files = analyze_file('capture.jls')   # writes capture_events/
    print(files['html'])

Each stage is also available on its own: :func:`estimate_sleep`,
:func:`detect_events`, :func:`cluster`, :func:`compare_sources`.
"""

from .version import __version__
from .config import Config, SleepConfig, DetectConfig, MatchConfig, ReportConfig, load_config
from .source import Source, ArraySource, JlsSource, DecimatedSource, open_source
from .sleep import estimate_sleep, SleepEstimate, Baseline
from .detect import detect_events, Event
from .match import cluster, extract_envelopes, Envelope
from .templates import Template, TemplateSet
from .analyze import analyze, AnalysisResult, EventType
from .compare import compare_sources, compare_results, Comparison
from .export import write_events_csv, write_summary_json, summary
from .report import write_analysis, write_comparison, render_analysis, render_comparison
from .files import analyze_file, default_output_dir

__all__ = [
    '__version__',
    'Config', 'SleepConfig', 'DetectConfig', 'MatchConfig', 'ReportConfig', 'load_config',
    'Source', 'ArraySource', 'JlsSource', 'DecimatedSource', 'open_source',
    'estimate_sleep', 'SleepEstimate', 'Baseline',
    'detect_events', 'Event',
    'cluster', 'extract_envelopes', 'Envelope',
    'Template', 'TemplateSet',
    'analyze', 'AnalysisResult', 'EventType',
    'compare_sources', 'compare_results', 'Comparison',
    'write_events_csv', 'write_summary_json', 'summary',
    'write_analysis', 'write_comparison', 'render_analysis', 'render_comparison',
    'analyze_file', 'default_output_dir',
]
