# scripts/vendor_mathjax.py
"""
Vendor MathJax into the documentation (no CDN at run time).

The site loads MathJax from ``docs/javascripts/mathjax/``. This script fetches
the pinned npm tarball, checks its sha512 against the pinned integrity, and
copies what the site uses: the combined component ``tex-mml-chtml.js``, the
CHTML web fonts it loads, the TeX extensions its autoloader fetches on demand,
the accessibility modules of its menu, and the licence. ``SHA256SUMS`` lists
every file, so a hand edit or a partial update is found by ``check`` (a test of
the library suite) without network access.

Usage::

    python scripts/vendor_mathjax.py build   # fetch, verify, copy, write SHA256SUMS
    python scripts/vendor_mathjax.py check   # compare the copied files with SHA256SUMS
"""

import base64
import hashlib
import io
import logging
import pathlib
import shutil
import sys
import tarfile
import urllib.request
from typing import List

logger = logging.getLogger(__name__)

VERSION = "3.2.2"
TARBALL = f"https://registry.npmjs.org/mathjax/-/mathjax-{VERSION}.tgz"
INTEGRITY = "sha512-Bt+SSVU8eBG27zChVewOicYs7Xsdt40qm4+UpHyX7k0/O9NliPc+x77k1/FEsPsjKPZGJvtRZM1vO+geW0OhGw=="

#: Paths inside ``package/`` that are copied (files, or directories copied whole).
INCLUDE = ("LICENSE", "es5/tex-mml-chtml.js", "es5/output/chtml/fonts/woff-v2", "es5/input/tex/extensions",
           "es5/a11y")

ROOT = pathlib.Path(__file__).resolve().parents[1]
TARGET = ROOT / "docs" / "javascripts" / "mathjax"
SUMS = "SHA256SUMS"


def _verified_tarball() -> bytes:
    """
    Download the pinned tarball and check its integrity.

    Returns
    -------
    bytes
        The tarball.

    Raises
    ------
    RuntimeError
        If the sha512 differs from :data:`INTEGRITY`.
    """
    with urllib.request.urlopen(TARBALL, timeout=60) as response:  # noqa: S310 -- pinned https URL
        data = response.read()
    digest = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()
    if digest != INTEGRITY:
        raise RuntimeError(f"integrity of {TARBALL} differs: {digest}")
    return data


def _files(base: pathlib.Path) -> List[pathlib.Path]:
    """Every vendored file below ``base``, sorted, without the checksum list."""
    return sorted(p for p in base.rglob("*") if p.is_file() and p.name != SUMS)


def build() -> None:
    """Fetch, verify and copy MathJax; write ``SHA256SUMS``."""
    with tarfile.open(fileobj=io.BytesIO(_verified_tarball()), mode="r:gz") as archive:
        if TARGET.exists():
            shutil.rmtree(TARGET)
        for member in archive.getmembers():
            name = member.name.removeprefix("package/")
            if not member.isfile() or not any(name == p or name.startswith(p + "/") for p in INCLUDE):
                continue
            destination = TARGET / name.removeprefix("es5/")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.extractfile(member).read())
    lines = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(TARGET).as_posix()}"
             for p in _files(TARGET)]
    (TARGET / SUMS).write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("vendored MathJax %s: %d files", VERSION, len(lines))


def check() -> List[str]:
    """
    Compare the vendored files with ``SHA256SUMS``.

    Returns
    -------
    list of str
        One line per problem; empty when everything matches.
    """
    sums = TARGET / SUMS
    if not sums.exists():
        return [f"{SUMS} is missing"]
    listed = dict(reversed(line.split("  ", 1)) for line in sums.read_text(encoding="utf-8").splitlines() if line)
    problems = []
    present = {p.relative_to(TARGET).as_posix(): p for p in _files(TARGET)}
    for name, digest in listed.items():
        if name not in present:
            problems.append(f"{name}: listed but missing")
        elif hashlib.sha256(present[name].read_bytes()).hexdigest() != digest:
            problems.append(f"{name}: checksum differs from {SUMS}")
    problems += [f"{name}: shipped but not listed in {SUMS}" for name in present if name not in listed]
    return problems


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    command = sys.argv[1] if len(sys.argv) > 1 else "check"
    if command == "build":
        build()
    else:
        found = check()
        for line in found:
            logger.error(line)
        sys.exit(1 if found else 0)
