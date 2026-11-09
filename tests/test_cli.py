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

import csv
import json
import pytest
from joulescope_event_analyzer.__main__ import main, get_parser
from joulescope_event_analyzer.export import summary, write_events_csv
from joulescope_event_analyzer.source import DecimatedSource
from conftest import standard_capture


@pytest.fixture(scope='module')
def jls_files(tmp_path_factory):
    d = tmp_path_factory.mktemp('jls')
    a = standard_capture(seed=1, duration=1.5).write_jls(d / 'a.jls')
    b = standard_capture(seed=2, duration=1.5).write_jls(d / 'b.jls')
    return a, b


def test_analyze(tmp_path, jls_files, capsys):
    out = tmp_path / 'out'
    tpl = tmp_path / 't.json'
    rc = main(['analyze', str(jls_files[0]), '-o', str(out), '--cdn', '--save-analysis', '--save-templates', str(tpl)])
    assert rc == 0
    names = sorted(p.name for p in out.iterdir())
    assert names == ['a_events.csv', 'a_events.html', 'a_events_analysis.json', 'a_events_summary.json']
    with open(out / 'a_events.csv', newline='') as f:
        rows = list(csv.DictReader(f))
    assert len(rows) > 10 and {r['type'] for r in rows} == {'A', 'B', 'C'}
    s = json.loads((out / 'a_events_summary.json').read_text())
    assert s['events'] == len(rows) and len(s['types']) == 3
    assert tpl.exists()
    text = capsys.readouterr().out
    assert 'Type   A' in text and 'wrote' in text


def test_analyze_default_output_dir(jls_files):
    assert main(['analyze', str(jls_files[1]), '--no-html', '-q']) == 0
    d = jls_files[1].parent / 'b_events'
    assert (d / 'b_events.csv').exists() and not (d / 'b_events.html').exists()


def test_analyze_options_and_config(tmp_path, jls_files):
    cfg = tmp_path / 'c.toml'
    cfg.write_text('[detect]\nmerge_gap = 0.5\n')
    out = tmp_path / 'o'
    assert main(['analyze', str(jls_files[0]), '-o', str(out), '--no-html', '-q', '--config', str(cfg),
                 '--sleep', '4e-6', '--battery', '100']) == 0
    s = json.loads((out / 'a_events_summary.json').read_text())
    assert s['sleep']['manual'] and s['sleep']['level'] == 4e-6
    assert s['sleep']['selection'] == 'manual'
    assert s['thresholds']['merge_gap'] == 0.5
    assert s['budget']['battery_capacity_mAh'] == 100


def test_compare(tmp_path, jls_files, capsys):
    out = tmp_path / 'c.html'
    assert main(['compare', str(jls_files[0]), str(jls_files[1]), '-o', str(out), '--cdn', '--labels', 'one', 'two']) == 0
    assert out.exists()
    text = capsys.readouterr().out
    assert 'one vs two' in text and 'same' in text


def test_template_create_and_show(tmp_path, jls_files, capsys):
    t = tmp_path / 't.json'
    assert main(['template', 'create', str(jls_files[0]), '-o', str(t), '-q']) == 0
    assert main(['template', 'show', str(t)]) == 0
    text = capsys.readouterr().out
    assert '3 templates' in text and 'Type   A' in text
    out = tmp_path / 'o'
    assert main(['analyze', str(jls_files[1]), '-o', str(out), '--templates', str(t), '--no-html', '-q']) == 0


def test_errors(tmp_path, capsys):
    assert main(['analyze', str(tmp_path / 'missing.jls'), '-q']) == 1
    assert 'ERROR' in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(['--version'])
    with pytest.raises(SystemExit):
        main([])


def test_parser_has_subcommands():
    p = get_parser()
    for cmd in (['analyze', 'x.jls'], ['compare', 'a.jls', 'b.jls'], ['template', 'create', 'x.jls', '-o', 't'],
                ['template', 'show', 't']):
        assert p.parse_args(cmd).func


def test_export_functions(tmp_path, result):
    p = write_events_csv(result, tmp_path / 'e.csv')
    lines = p.read_text().strip().splitlines()
    assert len(lines) == len(result.events) + 1
    s = summary(result)
    json.dumps(s, allow_nan=False)
    assert s['events'] == len(result.events)
