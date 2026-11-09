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

import hashlib
import io
import json
import re
import shutil
import subprocess
import pytest
from joulescope_event_analyzer import render_analysis, render_comparison, write_analysis, compare_sources
from joulescope_event_analyzer.report import common
from conftest import standard_capture


def _data(page):
    m = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', page, re.S)
    assert m
    return json.loads(m.group(1))


def _script(page):
    return page[page.rindex('<script>') + len('<script>'):page.rindex('</script>')]


@pytest.fixture(scope='module')
def comparison():
    a = standard_capture(seed=1, duration=1.0, fs=400_000)
    from joulescope_event_analyzer import DecimatedSource
    lo = DecimatedSource(standard_capture(seed=2, duration=1.0, fs=400_000).source(), 4)
    return compare_sources(a.source(), lo, labels=('400 kHz', '100 kHz'))


def test_analysis_page(result):
    page = render_analysis(result, plotly='cdn')
    assert common.PLOTLY_URL in page
    d = _data(page)
    assert len(d['events']) == len(result.events)
    assert 'reference' not in d['types'][0]
    assert d['sleep']['histogram']['counts']


def test_comparison_page(comparison):
    page = render_comparison(comparison, plotly='cdn')
    d = _data(page)
    assert d['labels'] == ['400 kHz', '100 kHz']
    assert d['model'] is not None
    assert d['types'][0]['metrics']['excess_charge']['verdict'] == 'same'
    assert d['filter_examples']


def test_data_escapes_script_close(result):
    result.capture['name'] = 'evil</script><script>alert(1)</script>.jls'
    try:
        page = render_analysis(result, plotly='cdn')
    finally:
        result.capture['name'] = 'synthetic'
    assert _data(page)['capture']['name'].startswith('evil</script>')
    assert page.count('<script>alert(1)') == 0


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
@pytest.mark.parametrize('kind', ['analysis', 'compare'])
def test_page_javascript_parses(tmp_path, result, comparison, kind):
    page = render_analysis(result, plotly='cdn') if kind == 'analysis' else render_comparison(comparison, plotly='cdn')
    js = tmp_path / 'page.js'
    js.write_text(_script(page), encoding='utf-8')
    r = subprocess.run(['node', '--check', str(js)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


FAKE_PLOTLY = b'var Plotly = {}; // </script> inside'


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class _Downloads(list):
    body = FAKE_PLOTLY


@pytest.fixture
def fake_plotly(tmp_path, monkeypatch):
    """Isolate the Plotly cache and network; return the list of download URLs."""
    monkeypatch.delenv(common.PLOTLY_ENV, raising=False)
    monkeypatch.setattr(common, 'cache_dir', lambda: str(tmp_path / 'cache'))
    monkeypatch.setattr(common, 'PLOTLY_SHA256', hashlib.sha256(FAKE_PLOTLY).hexdigest())
    downloads = _Downloads()

    def urlopen(url, timeout=None):
        downloads.append(url)
        return _Response(downloads.body)

    monkeypatch.setattr(common.urllib.request, 'urlopen', urlopen)
    return downloads


def _cache_file(tmp_path):
    return tmp_path / 'cache' / f'plotly-{common.PLOTLY_VERSION}.min.js'


def _assert_embedded(tag):
    assert tag.startswith('<script>') and tag.endswith('</script>')
    assert tag.count('</script') == 1          # the embedded one is escaped


def test_plotly_path_mode(tmp_path):
    p = tmp_path / 'plotly.js'
    p.write_bytes(FAKE_PLOTLY)
    _assert_embedded(common.plotly_tag(str(p)))
    with pytest.raises(FileNotFoundError):
        common.plotly_tag(str(tmp_path / 'missing.js'))


def test_plotly_env_override(tmp_path, monkeypatch):
    p = tmp_path / 'plotly.js'
    p.write_bytes(FAKE_PLOTLY)
    monkeypatch.setenv(common.PLOTLY_ENV, str(p))
    _assert_embedded(common.plotly_tag('embed'))


def test_plotly_download_verified_and_cached(tmp_path, fake_plotly):
    _assert_embedded(common.plotly_tag('embed'))
    assert _cache_file(tmp_path).read_bytes() == FAKE_PLOTLY
    _assert_embedded(common.plotly_tag('embed'))
    assert fake_plotly == [common.PLOTLY_URL]  # second call uses the cache


def test_plotly_download_bad_hash_not_cached(tmp_path, fake_plotly):
    fake_plotly.body = b'tampered'
    tag = common.plotly_tag('embed')
    assert common.PLOTLY_URL in tag and 'integrity="sha256-' in tag
    assert not _cache_file(tmp_path).exists()


def test_plotly_corrupt_cache_downloaded_again(tmp_path, fake_plotly):
    _cache_file(tmp_path).parent.mkdir(parents=True)
    _cache_file(tmp_path).write_bytes(b'truncated')
    _assert_embedded(common.plotly_tag('embed'))
    assert fake_plotly == [common.PLOTLY_URL]
    assert _cache_file(tmp_path).read_bytes() == FAKE_PLOTLY


def test_plotly_offline_falls_back_to_cdn(tmp_path, monkeypatch, fake_plotly):
    monkeypatch.setattr(common.urllib.request, 'urlopen', lambda *a, **k: (_ for _ in ()).throw(OSError('offline')))
    assert common.PLOTLY_URL in common.plotly_tag('embed')


def test_plotly_sri_matches_pinned_hash():
    import base64
    assert base64.b64decode(common._sri(common.PLOTLY_SHA256)).hex() == common.PLOTLY_SHA256


def test_write_analysis_creates_directories(tmp_path, result):
    path = tmp_path / 'a' / 'b' / 'r.html'
    write_analysis(result, path, plotly='cdn')
    assert path.read_text(encoding='utf-8').startswith('<!DOCTYPE html>')


def test_cache_dir():
    assert 'joulescope_event_analyzer' in common.cache_dir()


@pytest.mark.parametrize('module', ['analysis', 'compare'])
def test_templates_have_no_capture_specific_text(module):
    """Report text must come from the data, never from a particular capture."""
    import importlib
    m = importlib.import_module(f'joulescope_event_analyzer.report.{module}')
    text = m.BODY + m.SCRIPT
    for s in ['1 MHz', '50 kHz', '50.0 kHz', 'JS320', '100 µs', '20 µs', '60 µs',
              'barely', 'not measurement error', 'nearly constant', 'adds up the same', 'slightly longer',
              'reads lower', 'gives the same result']:
        assert s not in text, s


def test_sleep_selection_recorded(result):
    assert result.sleep.selection == 'dwell'
    from joulescope_event_analyzer import estimate_sleep, SleepConfig
    from conftest import standard_capture
    e = estimate_sleep(standard_capture(duration=0.5).source(), SleepConfig(level=4e-6))
    assert e.selection == 'manual'
