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
import pytest
from joulescope_event_analyzer.config import Config, load_config


def test_defaults():
    c = Config()
    assert c.sleep.level is None
    assert c.detect.start_sigma > c.detect.end_sigma
    assert 0 < c.match.tolerance < 1
    assert c.report.plotly == 'embed'


def test_round_trip():
    c = Config()
    c.detect.merge_gap = 1e-3
    d = c.to_dict()
    c2 = Config.from_dict(d)
    assert c2.detect.merge_gap == 1e-3
    assert c2.to_dict() == d


def test_update_unknown_raises():
    c = Config()
    with pytest.raises(KeyError):
        c.update({'detect': {'not_a_field': 1}})
    with pytest.raises(KeyError):
        c.update({'nope': {}})


def test_copy_is_independent():
    c = Config()
    c2 = c.copy()
    c2.sleep.level = 1e-6
    assert c.sleep.level is None


def test_load_toml(tmp_path):
    p = tmp_path / 'c.toml'
    p.write_text('[sleep]\nlevel = 5e-6\n[match]\ntolerance = 0.2\n')
    c = load_config(p)
    assert c.sleep.level == 5e-6
    assert c.match.tolerance == 0.2


def test_load_json(tmp_path):
    p = tmp_path / 'c.json'
    p.write_text(json.dumps({'detect': {'start_sigma': 12}}))
    assert load_config(p).detect.start_sigma == 12


def test_load_unsupported(tmp_path):
    p = tmp_path / 'c.yaml'
    p.write_text('x: 1')
    with pytest.raises(ValueError):
        load_config(p)
