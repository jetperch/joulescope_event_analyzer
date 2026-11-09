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

import json
import os
import pytest
from joulescope_event_analyzer import analyze_file, default_output_dir, Config
from joulescope_event_analyzer.source import JlsSource
from conftest import standard_capture


@pytest.fixture(scope='module')
def jls(tmp_path_factory):
    return standard_capture(seed=3, duration=1.5).write_jls(tmp_path_factory.mktemp('jls') / 'cap.jls')


class _Cancel(Exception):
    pass


def test_default_output_dir(tmp_path):
    assert default_output_dir(tmp_path / 'x.jls') == os.path.join(str(tmp_path), 'x_events')


def test_analyze_file_default_dir(jls):
    fractions = []
    result, files = analyze_file(jls, plotly='cdn', progress=lambda f, msg: fractions.append(f))
    d = default_output_dir(jls)
    assert sorted(files) == ['csv', 'html', 'summary']
    assert all(os.path.dirname(p) == d and os.path.isfile(p) for p in files.values())
    assert os.path.basename(files['html']) == 'cap_events.html'
    s = json.loads(open(files['summary'], encoding='utf-8').read())
    assert s['events'] == len(result.events)
    assert fractions and fractions == sorted(fractions) and fractions[-1] <= 1.0


def test_analyze_file_options(tmp_path, jls):
    tpl = tmp_path / 't.json'
    ref, _ = analyze_file(jls, tmp_path / 'ref', html=False)
    ref.template_set().save(tpl)
    plotly = tmp_path / 'plotly.js'
    plotly.write_text('var Plotly = {};', encoding='utf-8')
    cfg = Config()
    cfg.report.plotly = 'cdn'              # overridden by the plotly argument
    result, files = analyze_file(str(jls), tmp_path / 'out', cfg, templates=str(tpl),
                                 plotly=str(plotly), save_analysis=True)
    assert sorted(files) == ['analysis', 'csv', 'html', 'summary']
    page = open(files['html'], encoding='utf-8').read()
    assert 'var Plotly = {};' in page
    assert [t.name for t in result.types] == [t.name for t in ref.types]


def test_analyze_file_html_uses_config_plotly(tmp_path, jls):
    cfg = Config()
    cfg.report.plotly = 'cdn'
    _, files = analyze_file(jls, tmp_path, cfg)
    assert 'cdn.jsdelivr.net' in open(files['html'], encoding='utf-8').read()


@pytest.mark.parametrize('cancel_at', [0.0, 0.5, 0.9])
def test_analyze_file_cancel(tmp_path, jls, monkeypatch, cancel_at):
    closed = []
    close = JlsSource.close
    monkeypatch.setattr(JlsSource, 'close', lambda self: (closed.append(True), close(self)))

    def progress(fraction, msg):
        if fraction >= cancel_at:
            raise _Cancel()

    out = tmp_path / 'out'
    with pytest.raises(_Cancel):
        analyze_file(jls, out, progress=progress, plotly='cdn')
    assert closed == [True]
    assert not out.exists()


def test_analyze_file_missing(tmp_path):
    with pytest.raises(Exception):
        analyze_file(tmp_path / 'missing.jls', tmp_path / 'out')
    assert not (tmp_path / 'out').exists()
