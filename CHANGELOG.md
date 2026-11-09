<!--
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
-->

# CHANGELOG

This file contains the list of changes made to joulescope_event_analyzer.


## 0.1.0

2026 Nov 16

* Initial version, generalized from one-off capture analysis scripts.
* Automatic sleep current detection with dwell-level analysis.
* Streaming two-threshold event detection with charge significance test.
* Sample-rate independent waveform matching into event types, with templates.
* Charge and energy budget.
* Two-capture comparison with sample-rate (Sinc3) model.
* Self-contained HTML reports, CSV and JSON export.
* Command line: analyze, compare, template create, template show.
* Report text is generated entirely from the analysis results (no fixed
  conclusions), with a test that guards against capture-specific text.
* Fixed: when the second capture had the higher sample rate, the sample-rate
  model was compared against the wrong capture.
* Sleep selection rule (dwell, most common, manual) is recorded in results
  and the summary JSON.
* Added `analyze_file()`, which analyzes a capture file and writes the
  report, CSV and summary JSON.  The command line and applications share it.
  Raising an exception from the progress callback cancels the analysis
  without writing any output.
* The downloaded Plotly is verified against a pinned SHA-256 before it is
  cached or embedded, and the CDN fallback uses subresource integrity.
  `plotly` also accepts the path to a plotly.min.js to embed.
* Requires Python 3.12 or newer (pyjls 0.19 requires 3.12).
