# gui/scripts/bundle_vendor.py
"""Bundle the browser libraries of the network canvas into the GUI package.

The network canvas is a Vue component built on Vue Flow, with elkjs for the
automatic layout. NiceGUI serves Vue 3 to the page through an import map;
the canvas imports the vendored libraries as the ES module
``groundinsight-vueflow``, which this script builds into
``groundinsight_gui/static/vueflow/``. Nothing is loaded from a CDN at run
time: the GUI has to work offline, in a native window and behind a
firewall, and a page that pulls code from a third-party host on every start
is a supply-chain risk a desktop tool does not need.

Why a bundle and not the npm files as they are
-----------------------------------------------
Vue Flow is published as an ES module that imports ``vue``,
``@vueuse/core`` and five ``d3-*`` packages by bare name, and elkjs as a
CommonJS file. A browser resolves none of that without a bundler. The
bundle keeps ``vue`` external, so the canvas shares NiceGUI's Vue instance
(a second copy of Vue would hold its own reactive state that never reaches
the page), and inlines the four Vue Flow style sheets as a string that the
component injects once.

What is pinned, and where
-------------------------
* ``vendor/package.json`` -- the exact versions of the five libraries and of
  the build tool (vite);
* ``vendor/package-lock.json`` -- the whole tree with the sha512 integrity of
  every tarball; ``npm ci`` installs exactly this tree and refuses a tarball
  whose hash differs;
* ``vendor/.npmrc`` -- ``before=``, the date the tree was resolved at, two
  weeks before the pin, so no release younger than that enters the lock;
* ``static/vueflow/MANIFEST.json`` -- name, version, licence, integrity and
  source of every package whose code is in the bundle;
* ``static/vueflow/SHA256SUMS`` -- the hash of every shipped file.

``check`` needs no Node.js and runs in the GUI test suite: it fails when a
shipped file differs from its recorded hash, when a file is missing or
unlisted, or when the manifest names other versions than ``package.json``
and the lock.

Usage
-----
From the repository root::

    python gui/scripts/bundle_vendor.py build   # needs node and npm
    python gui/scripts/bundle_vendor.py check

Updating a library: change its version in ``vendor/package.json``, move
``before=`` in ``vendor/.npmrc`` to a date two weeks back, run
``npm install --package-lock-only`` in ``vendor/``, then ``build``, then
the GUI tests, and review the diff of ``MANIFEST.json``.
"""

import argparse
import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("groundinsight_gui.bundle_vendor")

SCRIPTS = Path(__file__).resolve().parent
VENDOR = SCRIPTS / "vendor"
TARGET = SCRIPTS.parent / "groundinsight_gui" / "static" / "vueflow"

#: The libraries the canvas imports; everything they pull in at run time is
#: bundled with them.
LIBRARIES = (
    "@vue-flow/core",
    "@vue-flow/minimap",
    "@vue-flow/controls",
    "@vue-flow/background",
    "elkjs",
)

#: Provided by NiceGUI's import map, never bundled.
EXTERNAL = ("vue",)

#: Licences under which a bundled package may be shipped with an MIT
#: package. elkjs is dual-licensed "EPL-2.0 OR GPL-3.0-or-later"; the GUI
#: takes it under EPL-2.0, a weak copyleft that covers elkjs's own files only
#: and is met by shipping the licence text and naming the source.
ALLOWED_LICENSES = (
    "MIT",
    "ISC",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "EPL-2.0 OR GPL-3.0-or-later",
)

MANIFEST = "MANIFEST.json"
SUMS = "SHA256SUMS"
LICENSE_NAMES = re.compile(r"^(licen[cs]e|copying|notice)(\.(md|txt))?$", re.IGNORECASE)


def _sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of a file.

    Parameters
    ----------
    path : Path
        The file to hash.

    Returns
    -------
    str
        64 lower-case hex digits.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> Dict:
    """Read a JSON file as UTF-8.

    Parameters
    ----------
    path : Path
        The file to read.

    Returns
    -------
    dict
        The parsed document.
    """
    return json.loads(path.read_text(encoding="utf-8"))


def _is_external(name: str) -> bool:
    """Tell whether a package is left to the page instead of being bundled.

    Parameters
    ----------
    name : str
        npm package name.

    Returns
    -------
    bool
        True for ``vue`` and the ``@vue/*`` packages it consists of.
    """
    return name in EXTERNAL or name.startswith("@vue/")


def runtime_closure(lock: Dict) -> List[str]:
    """Return the packages whose code ends up in the bundle.

    Walks the ``dependencies`` (not the peer dependencies) of
    :data:`LIBRARIES` through the lock file; ``vue`` and its parts are
    external and stop the walk.

    Parameters
    ----------
    lock : dict
        The parsed ``package-lock.json`` (lockfile version 3).

    Returns
    -------
    list of str
        Package names, sorted.

    Raises
    ------
    KeyError
        If a dependency is not in the lock file.
    """
    packages = lock["packages"]
    seen: Dict[str, None] = {}
    stack = list(LIBRARIES)
    while stack:
        name = stack.pop()
        if name in seen or _is_external(name):
            continue
        seen[name] = None
        entry = packages[f"node_modules/{name}"]
        stack.extend(entry.get("dependencies", {}))
    return sorted(seen)


def _manifest_entries(lock: Dict) -> List[Dict[str, str]]:
    """Describe every bundled package for ``MANIFEST.json``.

    Parameters
    ----------
    lock : dict
        The parsed ``package-lock.json``.

    Returns
    -------
    list of dict
        One record per package: name, version, license, integrity, resolved.
    """
    entries = []
    for name in runtime_closure(lock):
        entry = lock["packages"][f"node_modules/{name}"]
        entries.append({
            "name": name,
            "version": entry["version"],
            "license": entry.get("license", ""),
            "integrity": entry["integrity"],
            "resolved": entry["resolved"],
        })
    return entries


def _license_file(package_dir: Path) -> Optional[Path]:
    """Find the licence text an npm package ships.

    Parameters
    ----------
    package_dir : Path
        The package's directory under ``node_modules``.

    Returns
    -------
    Path or None
        The first file named like ``LICENSE``, ``LICENCE.md`` or ``COPYING``.
    """
    for path in sorted(package_dir.iterdir()):
        if path.is_file() and LICENSE_NAMES.match(path.name):
            return path
    return None


def _run(command: List[str]) -> None:
    """Run a command in the vendor directory and fail loudly.

    Parameters
    ----------
    command : list of str
        The command and its arguments.

    Raises
    ------
    subprocess.CalledProcessError
        If the command exits non-zero.
    """
    logger.info("$ %s", " ".join(command))
    subprocess.run(command, cwd=VENDOR, check=True)


def build() -> None:
    """Install the pinned tree, bundle it and write the shipped files.

    Raises
    ------
    RuntimeError
        If Node.js is missing, a licence is not allowed, a licence text is
        missing, or the bundle contains a copy of Vue.
    """
    if shutil.which("npm") is None:
        raise RuntimeError("bundling needs node and npm on PATH; 'check' does not")
    # --ignore-scripts: nothing in this tree needs an install script for a
    # browser bundle (vue-demi's postinstall only switches between Vue 2 and
    # 3 and defaults to 3; fsevents is a watcher for vite's dev server).
    _run(["npm", "ci", "--ignore-scripts", "--no-audit"])
    _run(["npm", "audit", "--omit=dev", "--audit-level=moderate"])
    _run([str(VENDOR / "node_modules" / ".bin" / "vite"), "build", "--logLevel", "warn"])

    lock = _load_json(VENDOR / "package-lock.json")
    entries = _manifest_entries(lock)
    bad = [f"{e['name']} ({e['license']})" for e in entries if e["license"] not in ALLOWED_LICENSES]
    if bad:
        raise RuntimeError(f"licences not on the allow list: {', '.join(bad)}")

    chunks = sorted((VENDOR / "dist").glob("*.js"))
    bundle = "\n".join(p.read_text(encoding="utf-8") for p in chunks)
    if not re.search(r"""from\s*["']vue["']""", bundle):
        raise RuntimeError("the bundle does not import vue; is it external?")
    if "createApp" in bundle and "__VUE_OPTIONS_API__" in bundle:
        raise RuntimeError("the bundle seems to contain its own copy of Vue")

    if TARGET.exists():
        shutil.rmtree(TARGET)
    (TARGET / "licenses").mkdir(parents=True)
    for chunk in chunks:
        shutil.copy2(chunk, TARGET / chunk.name)
    for entry in entries:
        source = _license_file(VENDOR / "node_modules" / entry["name"])
        if source is None:
            raise RuntimeError(f"{entry['name']} ships no licence file")
        target = TARGET / "licenses" / f"{entry['name'].replace('/', '__')}.txt"
        target.write_bytes(source.read_bytes())

    package = _load_json(VENDOR / "package.json")
    manifest = {
        "module": "groundinsight-vueflow",
        "built_with": {"vite": package["devDependencies"]["vite"]},
        "resolved_before": _npmrc_before(),
        "external": list(EXTERNAL),
        "libraries": {name: package["dependencies"][name] for name in LIBRARIES},
        "packages": entries,
    }
    (TARGET / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    _write_sums()
    logger.info("wrote %s (%d bundled packages)", TARGET, len(entries))


def _npmrc_before() -> str:
    """Return the ``before=`` date of ``vendor/.npmrc``.

    Returns
    -------
    str
        The date as written, e.g. ``2026-11-01``.

    Raises
    ------
    RuntimeError
        If the file sets no date.
    """
    for line in (VENDOR / ".npmrc").read_text(encoding="utf-8").splitlines():
        if line.startswith("before="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("vendor/.npmrc sets no 'before=' date")


def _shipped_files() -> List[Path]:
    """List the shipped files except the checksum file itself.

    Returns
    -------
    list of Path
        Files under :data:`TARGET`, sorted by relative path.
    """
    return sorted(p for p in TARGET.rglob("*") if p.is_file() and p.name != SUMS)


def _write_sums() -> None:
    """Write ``SHA256SUMS`` in the format of ``sha256sum``."""
    lines = [f"{_sha256(p)}  {p.relative_to(TARGET).as_posix()}" for p in _shipped_files()]
    (TARGET / SUMS).write_text("\n".join(lines) + "\n", encoding="utf-8")


def check() -> List[str]:
    """Verify the shipped bundle against its checksums and pins.

    Returns
    -------
    list of str
        One line per problem; empty when the bundle is intact.
    """
    problems: List[str] = []
    sums_path = TARGET / SUMS
    if not sums_path.is_file():
        return [f"{sums_path} is missing; run 'bundle_vendor.py build'"]
    recorded: Dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        recorded[name] = digest
    actual = {p.relative_to(TARGET).as_posix(): _sha256(p) for p in _shipped_files()}
    for name in sorted(set(recorded) | set(actual)):
        if name not in actual:
            problems.append(f"{name}: listed in {SUMS} but missing")
        elif name not in recorded:
            problems.append(f"{name}: shipped but not listed in {SUMS}")
        elif recorded[name] != actual[name]:
            problems.append(f"{name}: checksum differs from {SUMS}")

    manifest = _load_json(TARGET / MANIFEST)
    package = _load_json(VENDOR / "package.json")
    lock = _load_json(VENDOR / "package-lock.json")
    pinned = {name: package["dependencies"][name] for name in LIBRARIES}
    if manifest["libraries"] != pinned:
        problems.append(f"{MANIFEST} names {manifest['libraries']}, package.json pins {pinned}")
    if [e["name"] for e in manifest["packages"]] != runtime_closure(lock):
        problems.append(f"{MANIFEST} lists other packages than the lock file's run-time tree")
    for entry in manifest["packages"]:
        locked = lock["packages"].get(f"node_modules/{entry['name']}", {})
        if (locked.get("version"), locked.get("integrity")) != (entry["version"], entry["integrity"]):
            problems.append(f"{entry['name']}: {MANIFEST} and package-lock.json disagree")
        license_file = TARGET / "licenses" / f"{entry['name'].replace('/', '__')}.txt"
        if not license_file.is_file():
            problems.append(f"{entry['name']}: licence text missing")
    for name, version in package["dependencies"].items():
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            problems.append(f"package.json: {name} {version!r} is not an exact version")
    if manifest["resolved_before"] != _npmrc_before():
        problems.append(f"{MANIFEST} was resolved before {manifest['resolved_before']}, .npmrc says {_npmrc_before()}")
    return problems


def main(argv: Optional[List[str]] = None) -> int:
    """Command-line entry point.

    Parameters
    ----------
    argv : list of str, optional
        Arguments without the program name; ``sys.argv[1:]`` by default.

    Returns
    -------
    int
        0 on success, 1 when ``check`` finds a problem.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("command", choices=("build", "check"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.command == "build":
        build()
    problems = check()
    for problem in problems:
        logger.error(problem)
    if not problems:
        logger.info("bundle intact: %s", TARGET)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
