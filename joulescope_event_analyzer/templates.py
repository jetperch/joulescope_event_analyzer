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

"""Event type templates.

A template set records the reference envelope and summary statistics of each
event type from one analysis.  Applying it to another capture gives matching
events the same type names, which makes captures directly comparable
(firmware versions, boards, sample rates, ...).  Envelopes are defined in
absolute time, so templates work across sample rates.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import datetime
import json
import os
from .match import Envelope
from .version import __version__


FORMAT = 'joulescope_event_analyzer.templates'
FORMAT_VERSION = 1


@dataclass
class Template:
    name: str
    envelope: Envelope
    stats: dict = field(default_factory=dict)     # summary statistics from the source analysis
    description: str = ''

    def to_dict(self):
        return {'name': self.name, 'description': self.description, 'stats': self.stats,
                'envelope': self.envelope.to_dict()}

    @classmethod
    def from_dict(cls, d):
        return cls(name=d['name'], envelope=Envelope.from_dict(d['envelope']),
                   stats=d.get('stats', {}), description=d.get('description', ''))


@dataclass
class TemplateSet:
    templates: list = field(default_factory=list)
    source: dict = field(default_factory=dict)    # info about the capture the templates came from

    def references(self):
        """Return [(name, Envelope)] for :func:`~joulescope_event_analyzer.match.cluster`."""
        return [(t.name, t.envelope) for t in self.templates]

    def names(self):
        return [t.name for t in self.templates]

    def to_dict(self):
        return {'format': FORMAT, 'format_version': FORMAT_VERSION, 'created_by': f'joulescope_event_analyzer {__version__}',
                'created': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'source': self.source, 'templates': [t.to_dict() for t in self.templates]}

    @classmethod
    def from_dict(cls, d):
        if d.get('format') != FORMAT:
            raise ValueError('Not a joulescope_event_analyzer template file')
        if int(d.get('format_version', 0)) > FORMAT_VERSION:
            raise ValueError(f"Unsupported template format version {d.get('format_version')}")
        return cls(templates=[Template.from_dict(t) for t in d.get('templates', [])], source=d.get('source', {}))

    def save(self, path):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, indent=1)

    @classmethod
    def load(cls, path):
        with open(os.fspath(path), 'r', encoding='utf-8') as f:
            return cls.from_dict(json.load(f))
