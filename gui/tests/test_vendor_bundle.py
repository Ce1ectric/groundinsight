# gui/tests/test_vendor_bundle.py
"""
The vendored browser libraries of the network canvas are intact.

``groundinsight_gui/static/vueflow`` is built by ``gui/scripts/bundle_vendor.py``
from pinned npm packages. These tests need no Node.js: they compare the
shipped files with their recorded SHA-256 sums and the manifest with the pins,
so a hand edit of the minified bundle, a forgotten rebuild after a version
change or a missing licence text fails here and not in a user's browser.
"""

import importlib.util
import json
import pathlib
import re

GUI_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = GUI_ROOT / "scripts" / "bundle_vendor.py"
STATIC = GUI_ROOT / "groundinsight_gui" / "static" / "vueflow"


def _bundle_vendor():
    spec = importlib.util.spec_from_file_location("bundle_vendor", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_shipped_bundle_matches_its_checksums_and_pins():
    assert _bundle_vendor().check() == []


def test_the_bundle_leaves_vue_to_nicegui_and_loads_nothing_from_the_network():
    code = "\n".join(p.read_text(encoding="utf-8") for p in sorted(STATIC.glob("*.js")))
    assert re.search(r"""from\s*["']vue["']""", code)
    assert not re.search(r"""(import|from)\s*\(?\s*["']https?://""", code)


def test_every_bundled_package_has_an_allowed_licence_and_its_text():
    bundle_vendor = _bundle_vendor()
    manifest = json.loads((STATIC / "MANIFEST.json").read_text(encoding="utf-8"))
    names = {entry["name"] for entry in manifest["packages"]}
    assert set(bundle_vendor.LIBRARIES) <= names
    assert "vue" not in names
    for entry in manifest["packages"]:
        assert entry["license"] in bundle_vendor.ALLOWED_LICENSES, entry
        assert entry["integrity"].startswith("sha512-"), entry


def test_a_modified_file_is_reported(tmp_path, monkeypatch):
    bundle_vendor = _bundle_vendor()
    copy = tmp_path / "vueflow"
    copy.mkdir()
    for path in STATIC.rglob("*"):
        if path.is_file():
            target = copy / path.relative_to(STATIC)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    (copy / "index.js").write_text("// tampered\n", encoding="utf-8")
    (copy / "extra.js").write_text("", encoding="utf-8")
    monkeypatch.setattr(bundle_vendor, "TARGET", copy)
    problems = bundle_vendor.check()
    assert "index.js: checksum differs from SHA256SUMS" in problems
    assert "extra.js: shipped but not listed in SHA256SUMS" in problems
