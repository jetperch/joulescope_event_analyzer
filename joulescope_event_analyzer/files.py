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

"""Analyze a capture file and write all outputs: the entry point for applications.

The command line and the Joulescope UI both use :func:`analyze_file`.
"""

from __future__ import annotations
import os
from .analyze import analyze, AnalysisResult
from .config import Config
from .export import write_events_csv, write_summary_json
from .report import write_analysis
from .source import open_source
from .templates import TemplateSet


def default_output_dir(path) -> str:
    """Return the default output directory: ``<capture>_events`` next to the capture."""
    path = os.path.abspath(os.fspath(path))
    stem = os.path.splitext(os.path.basename(path))[0]
    return os.path.join(os.path.dirname(path), f'{stem}_events')


def analyze_file(path, output_dir=None, config: Config | None = None, templates=None,
                 progress=None, plotly: str | None = None, html: bool = True,
                 save_analysis: bool = False) -> tuple[AnalysisResult, dict[str, str]]:
    """Analyze a capture file and write the report, CSV and summary JSON.

    :param path: The capture file path (JLS).
    :param output_dir: The output directory, created if needed.  None uses
        :func:`default_output_dir`.
    :param config: The :class:`Config`, or None for defaults.
    :param templates: A :class:`TemplateSet`, a template file path, or None.
    :param progress: Optional callable(fraction, message).  To cancel, raise an
        exception from it: the exception propagates, the capture is closed and
        no output is written.
    :param plotly: Overrides ``config.report.plotly``: 'embed', 'cdn', or the
        path to a plotly.min.js file to embed.  None uses the config.
    :param html: True to write the HTML report.
    :param save_analysis: True to also save the full analysis JSON.
    :return: (result, files) where files maps 'html', 'csv', 'summary' and
        'analysis' to the paths written.
    """
    config = Config() if config is None else config
    if templates is not None and not isinstance(templates, TemplateSet):
        templates = TemplateSet.load(templates)
    stem = os.path.splitext(os.path.basename(os.fspath(path)))[0]
    output_dir = default_output_dir(path) if output_dir is None else os.fspath(output_dir)
    with open_source(path) as src:
        result = analyze(src, config, templates=templates, progress=progress)

    # Write outputs only after a complete analysis, so cancel leaves nothing behind.
    os.makedirs(output_dir, exist_ok=True)
    base = os.path.join(output_dir, f'{stem}_events')
    files = {
        'csv': write_events_csv(result, base + '.csv'),
        'summary': write_summary_json(result, base + '_summary.json'),
    }
    if save_analysis:
        result.save(base + '_analysis.json')
        files['analysis'] = base + '_analysis.json'
    if html:
        files['html'] = write_analysis(result, base + '.html',
                                       plotly=config.report.plotly if plotly is None else plotly)
    return result, files
