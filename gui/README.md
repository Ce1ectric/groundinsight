# groundinsight-gui

Desktop and browser front end for [groundinsight](https://github.com/Ce1ectric/groundinsight),
built with [NiceGUI](https://nicegui.io), on top of the library's service
layer `groundinsight.api`.

## Install and start

`groundinsight-gui` is not on PyPI yet. Install it from a checkout of the
repository, together with the library of the same checkout -- the GUI pins the
library's exact version and uses features that are not released yet:

```bash
git clone https://github.com/Ce1ectric/groundinsight.git
cd groundinsight
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install . ./gui                    # browser mode
pip install . "./gui[native]"          # adds the desktop window (pywebview)

python -m groundinsight_gui              # opens http://127.0.0.1:8080 in the browser
python -m groundinsight_gui --native     # desktop window
python -m groundinsight_gui --example mv_ring --lang en
```

`python -m groundinsight_gui --help` lists all options. The server listens on
`127.0.0.1` unless `--host` says otherwise; phase 0 has no login. The
documentation walks through a first network in the tutorial
[Draw and solve a network](https://ce1ectric.github.io/groundinsight/getting-started/gui/).

To compute earthing characteristics from the bus dialog, install
[groundfield](https://github.com/Ce1ectric/groundfield) into the same
environment; without it the button is disabled and says why.

## What it does

* start page: a new project from a template, or open a project file
  (the library's project file, `groundinsight.save_project`; `~` and
  environment variables in the path are expanded);
* network plan: draw the network on a canvas -- buses, buses with an infeed,
  branches dragged from bus to bus, faults -- move symbols on a raster,
  double-click for the element dialog (general, source, fault, earthing
  characteristic -- imported from a groundfield BusType JSON or computed with
  groundfield for the bus or its type; branch: general, type parameters,
  switch), a properties panel, colouring by voltage level, type, earthing
  group, infeed and fault, EPR, touch voltage, shield current, touch-voltage
  utilisation, origin of the earthing impedance or diagnostics, and automatic
  layout (groundinsight's, or elkjs in the browser). Every gesture is one
  command of the service layer and one undo step; plan and tables share the
  selection;
* network tables: buses, branches, sources, faults and the type catalogue,
  editable cell by cell; every edit is a command of the service layer and can
  be undone; a rejected edit changes nothing and says why;
* calculation menu: one fault or all faults, the pre-flight check, outage
  study, rho-f variation with statistics, cuts, bus response, the largest
  admissible rho scaling, thermal check, EN 50522 touch-voltage check,
  transient and the reference cases -- each a background job with progress
  and cancel;
* results: key-figure tiles, the per-frequency key figures (`res_fault`),
  the EPR per bus (`res_buses`), the tables and charts of a study, the
  diagnostics of the calculation with links to the elements they name, and a
  stale marker after an edit that changes an input of the result;
* evaluation tabs: charts and tables of chosen result quantities, saved in
  the project;
* import and export: the network tables as CSV, a pandapower net;
* German and English.

Every number on screen is a field of a service-layer DTO, which carries the
library's result frames unchanged; the GUI only formats it. The GUI tests
check the displayed values against the library frames and against the
library suite's independent reference solver.

## Development

From the repository root, both in editable mode:

```bash
pip install -e . -e "./gui[test]"
python -m playwright install chromium   # once, for the browser tests
cd gui && python -m pytest
```

`tests/test_browser.py` starts the app and draws with the mouse in Chromium;
its screenshots land in `tests/screens/` and are compared with the references
in `tests/screens/reference/<platform>/` where they exist
(`GI_UPDATE_SCREENS=1` rewrites them after an intended change).

The GUI is released together with the library and has the same version;
`scripts/release.py` rewrites both, and the release workflow refuses a tag on
which they differ.

## Browser libraries

The network canvas uses [Vue Flow](https://vueflow.dev) (MIT) and
[elkjs](https://github.com/kieler/elkjs) (EPL-2.0) for the automatic layout.
Both ship inside the package under `groundinsight_gui/static/vueflow/`,
bundled into one ES module; the GUI loads nothing from a CDN. The licence
texts of every bundled package and a manifest with versions, sources and
integrity hashes lie next to the bundle.

The bundle is built from pinned versions (`scripts/vendor/package.json`,
`scripts/vendor/package-lock.json`) and needs Node.js only for a rebuild:

```bash
python gui/scripts/bundle_vendor.py build   # npm ci + vite build, writes static/vueflow
python gui/scripts/bundle_vendor.py check   # checksums and pins; also a GUI test
```
