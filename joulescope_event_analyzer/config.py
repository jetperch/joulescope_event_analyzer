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

"""Analysis configuration.

Every automatically-determined value has an override here.  A configuration
can be built in code, loaded from a JSON or TOML file, and then updated with
command-line overrides::

    [sleep]
    level = 4e-6            # force the sleep current (A)

    [detect]
    start_sigma = 8
    merge_gap = 200e-6

    [match]
    tolerance = 0.3
"""

from __future__ import annotations
import dataclasses
from dataclasses import dataclass, field
import json
import os


@dataclass
class SleepConfig:
    """Sleep (baseline) current estimation."""

    level: float | None = None
    """Force the sleep current in A.  None estimates it automatically."""

    noise: float | None = None
    """Force the detection noise (A rms) that sets the thresholds.  None estimates it."""

    block: float = 100e-6
    """Block duration in seconds used to build the sleep current histogram."""

    baseline_window: float | None = None
    """Rolling baseline window in seconds to track a drifting sleep current.  None = constant."""

    min_fraction: float = 0.02
    """Minimum fraction of the capture a current level must occupy, in stretches of at least min_dwell."""

    min_dwell: float = 10e-3
    """Minimum continuous time in seconds at a level for it to count as sleep (rejects brief dips)."""


@dataclass
class DetectConfig:
    """Event detection."""

    smooth: float = 10e-6
    """Moving-average window in seconds applied before thresholding."""

    start_sigma: float = 8.0
    """An event starts when the smoothed current exceeds sleep by this many noise sigmas."""

    end_sigma: float = 3.0
    """An event ends when the smoothed current returns within this many noise sigmas of sleep."""

    start_threshold: float | None = None
    """Absolute start threshold in A above sleep.  Overrides start_sigma."""

    end_threshold: float | None = None
    """Absolute end threshold in A above sleep.  Overrides end_sigma."""

    min_excess: float = 0.0
    """Minimum start threshold in A above sleep, regardless of noise."""

    merge_gap: float = 200e-6
    """Merge events separated by less than this many seconds."""

    min_duration: float = 0.0
    """Reject events shorter than this many seconds."""

    significance: float = 10.0
    """Reject events whose charge above sleep is less than this many noise sigmas."""

    min_charge: float = 0.0
    """Reject events whose charge above sleep is less than this many coulombs."""

    chunk: int = 1_000_000
    """Samples processed per chunk."""


@dataclass
class MatchConfig:
    """Grouping events into types by waveform shape."""

    tolerance: float = 0.30
    """Maximum normalized envelope distance (0..1) for two events to be the same type."""

    duration_ratio: float = 2.0
    """Events whose durations differ by more than this ratio are never the same type."""

    charge_ratio: float = 2.0
    """Events whose excess charges differ by more than this ratio are never the same type."""

    grid_points: int = 256
    """Approximate number of envelope points across an event."""

    smooth_steps: int = 8
    """Envelope smoothing in grid steps (tolerates small timing variation)."""

    max_lag_steps: int = 12
    """Maximum time shift in grid steps when aligning two events."""

    refine_iterations: int = 2
    """Number of medoid refinement passes."""

    medoid_sample: int = 64
    """Maximum members sampled when choosing each type's representative event."""


@dataclass
class ReportConfig:
    """Report and export options."""

    pad: float = 200e-6
    """Waveform padding before and after each event in seconds."""

    max_waveforms_per_type: int = 100
    """Maximum event waveforms stored per type."""

    max_plot_samples: int = 2_000_000
    """Maximum total waveform samples per signal stored for plotting."""

    max_table_rows: int = 5000
    """Maximum event rows in the HTML instance table (the CSV always has every event)."""

    plotly: str = 'embed'
    """'embed' to include Plotly in the HTML (works offline), 'cdn' to load it from the internet,
    or the path to a plotly.min.js file to embed."""

    battery_capacity_mAh: float | None = None
    """Battery capacity for battery-life estimates, or None."""


@dataclass
class Config:
    sleep: SleepConfig = field(default_factory=SleepConfig)
    detect: DetectConfig = field(default_factory=DetectConfig)
    match: MatchConfig = field(default_factory=MatchConfig)
    report: ReportConfig = field(default_factory=ReportConfig)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> 'Config':
        c = cls()
        c.update(d or {})
        return c

    def update(self, d: dict) -> 'Config':
        """Update from a nested dict such as {'detect': {'merge_gap': 1e-3}}.

        :raise KeyError: on unknown sections or fields, so typos are not silently ignored.
        """
        for section, values in d.items():
            if not hasattr(self, section):
                raise KeyError(f'Unknown configuration section: {section}')
            obj = getattr(self, section)
            names = {f.name for f in dataclasses.fields(obj)}
            for key, value in (values or {}).items():
                if key not in names:
                    raise KeyError(f'Unknown configuration field: {section}.{key}')
                setattr(obj, key, value)
        return self

    def copy(self) -> 'Config':
        return Config.from_dict(self.to_dict())


def load_config(path: str | os.PathLike) -> Config:
    """Load a configuration from a .json or .toml file."""
    path = os.fspath(path)
    ext = os.path.splitext(path)[1].lower()
    if ext == '.toml':
        import tomllib
        with open(path, 'rb') as f:
            d = tomllib.load(f)
    elif ext == '.json':
        with open(path, 'r', encoding='utf-8') as f:
            d = json.load(f)
    else:
        raise ValueError(f'Unsupported configuration file type: {path}')
    return Config.from_dict(d)
