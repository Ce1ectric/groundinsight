# groundinsight-gui

Desktop and browser front end for [groundinsight](https://github.com/Ce1ectric/groundinsight),
built with [NiceGUI](https://nicegui.io). This is phase 0: a working skeleton
on top of the library's service layer `groundinsight.api`.

## Install and start

```bash
pip install groundinsight-gui            # browser mode
pip install "groundinsight-gui[native]"  # adds the desktop window (pywebview)

python -m groundinsight_gui              # opens http://127.0.0.1:8080 in the browser
python -m groundinsight_gui --native     # desktop window
python -m groundinsight_gui --example mv_ring --lang en
```

`python -m groundinsight_gui --help` lists all options. The server listens on
`127.0.0.1` unless `--host` says otherwise; phase 0 has no login.

## What it does

* start page: a new project from a template, or open a project file
  (the library's JSON format, `groundinsight.save_network_to_json`);
* network tables: buses, branches, sources, faults and the type catalogue,
  editable cell by cell; every edit is a command of the service layer and can
  be undone; a rejected edit changes nothing and says why;
* results: run one fault or all faults as a background job with progress and
  cancel; key-figure tiles, the per-frequency key figures (`res_fault`), the
  EPR per bus (`res_buses`), the diagnostics of the calculation with links to
  the elements they name, and a stale marker after an edit that changes an
  input of the result;
* check: the pre-flight check of the library (`groundinsight.check_network`);
* German and English.

Every number on screen is a field of a service-layer DTO, which carries the
library's result frames unchanged; the GUI only formats it. The GUI tests
check the displayed values against the library frames and against the
library suite's independent reference solver.

## Development

From the repository root:

```bash
pip install -e . -e "./gui[test]"
cd gui && python -m pytest
```

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
