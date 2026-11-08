# scripts/audit_dependencies.py

"""
Audit every package pinned in ``poetry.lock`` for known vulnerabilities.

Why the lock file and not an installed environment
    CI installs a different subset per job (the ``dev`` group, the ``docs``
    group, the ``pandapower`` extra) and per platform. The lock is the union
    of all of them -- every version this repository can put on a machine --
    so it is what gets audited: in one run, on one runner, without installing
    anything. Auditing a single installed environment would miss whatever
    that job happens not to install.

Why not ``poetry export``
    Poetry 2 no longer ships the export command; it lives in a plugin that
    would be one more tool to install and pin in CI. ``poetry.lock`` is TOML
    and lists every package with its exact version, which is all pip-audit
    needs.

How
    The pins go to a temporary requirements file that is handed to
    ``pip-audit --no-deps --disable-pip --strict``: no resolution, no
    download, only a lookup of each ``name==version`` in the PyPI advisory
    database. pip-audit refuses two pins of the same package in one file,
    and the lock can hold two versions of a package for different Python
    versions (it holds ``networkx`` 3.6 and 3.7), so the pins are audited in
    layers -- the first version of every package, then the second, and so
    on. The exit status is the worst layer's.

Ignore list
    ``.github/pip-audit-ignore.txt``, empty by default. One entry per line::

        <advisory id>  # <why it does not affect groundinsight>; until YYYY-MM-DD

    Reason and end date are mandatory, and an expired entry fails the audit
    instead of being skipped: an exception nobody re-examines becomes
    permanent. Prefer upgrading; ignore only what cannot be fixed yet.

Usage::

    python scripts/audit_dependencies.py            # needs pip-audit installed
    python scripts/audit_dependencies.py --print    # only list the pins

Exit status: 0 no known advisory, 1 advisories found (pip-audit's status),
2 for a malformed or expired ignore list or when pip-audit is missing.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import importlib.util
import logging
import re
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger("groundinsight.audit")

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / "poetry.lock"
DEFAULT_IGNORE_FILE = ROOT / ".github" / "pip-audit-ignore.txt"

_ENTRY = re.compile(
    r"^(?P<advisory>[A-Za-z0-9][A-Za-z0-9._:-]*)\s+#\s*(?P<reason>.*?)\s*;\s*"
    r"until\s+(?P<until>\d{4}-\d{2}-\d{2})\s*$"
)


@dataclass(frozen=True)
class IgnoredAdvisory:
    """
    One entry of the ignore list.

    Attributes
    ----------
    advisory : str
        The advisory id as pip-audit reports it (``PYSEC-…``, ``GHSA-…``,
        ``CVE-…``).
    reason : str
        Why the advisory does not affect groundinsight.
    until : datetime.date
        Last day on which the entry is honoured.
    """

    advisory: str
    reason: str
    until: _datetime.date


def locked_requirements(lock_path: Path) -> List[str]:
    """
    ``name==version`` for every package in ``poetry.lock`` that comes from PyPI.

    Parameters
    ----------
    lock_path : pathlib.Path
        Path of ``poetry.lock``.

    Returns
    -------
    list of str
        Sorted, without duplicates. Packages from a git, URL or path source
        have no PyPI advisory record; they are left out with a warning.
    """
    data = tomllib.loads(Path(lock_path).read_text(encoding="utf-8"))
    pins = set()
    for package in data.get("package", []):
        if "source" in package:
            logger.warning(
                "not audited: %s %s comes from a %s source, not from PyPI",
                package["name"],
                package["version"],
                package["source"].get("type", "custom"),
            )
            continue
        pins.add(f"{package['name']}=={package['version']}")
    return sorted(pins)


def audit_layers(pins: Sequence[str]) -> List[List[str]]:
    """
    Split pins into layers that name every package at most once.

    Parameters
    ----------
    pins : sequence of str
        ``name==version`` strings, possibly several per package.

    Returns
    -------
    list of list of str
        Layer *k* holds the *k*-th smallest version of each package that has
        at least *k + 1* pinned versions; each layer is sorted.

    Examples
    --------
    >>> audit_layers(["a==1", "b==2", "a==3"])
    [['a==1', 'b==2'], ['a==3']]
    """
    versions: Dict[str, List[str]] = {}
    for pin in pins:
        name = pin.split("==", 1)[0].lower()
        versions.setdefault(name, []).append(pin)
    layers: List[List[str]] = []
    for name in sorted(versions):
        for depth, pin in enumerate(sorted(set(versions[name]))):
            if depth == len(layers):
                layers.append([])
            layers[depth].append(pin)
    return [sorted(layer) for layer in layers]


def parse_ignore_list(text: str) -> List[IgnoredAdvisory]:
    """
    Parse the ignore list format described in the module docstring.

    Parameters
    ----------
    text : str
        Content of the ignore file.

    Returns
    -------
    list of IgnoredAdvisory
        One per entry line; blank and comment lines are skipped.

    Raises
    ------
    ValueError
        For a line that lacks the reason or the end date, or whose date does
        not exist. The message names the line.
    """
    entries: List[IgnoredAdvisory] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ENTRY.match(stripped)
        reason = match.group("reason").strip() if match else ""
        if match is None or not reason or reason.lower().startswith("until"):
            raise ValueError(
                f"ignore list line {lineno}: expected "
                f"'<advisory id>  # <reason>; until YYYY-MM-DD', got {stripped!r}"
            )
        try:
            until = _datetime.date.fromisoformat(match.group("until"))
        except ValueError as exc:
            raise ValueError(f"ignore list line {lineno}: {exc}") from exc
        entries.append(IgnoredAdvisory(match.group("advisory"), reason, until))
    return entries


def read_ignore_list(path: Path) -> List[IgnoredAdvisory]:
    """
    Read and parse an ignore file; a missing file is an empty list.

    Parameters
    ----------
    path : pathlib.Path
        The ignore file.

    Returns
    -------
    list of IgnoredAdvisory
        The parsed entries.

    Raises
    ------
    ValueError
        Propagated from :func:`parse_ignore_list`.
    """
    path = Path(path)
    if not path.is_file():
        return []
    return parse_ignore_list(path.read_text(encoding="utf-8"))


def pip_audit_command(
    requirements: Path, ignored: Sequence[str], python: Optional[str] = None
) -> List[str]:
    """
    The pip-audit invocation for one requirements file.

    Parameters
    ----------
    requirements : pathlib.Path
        File with one ``name==version`` per line.
    ignored : sequence of str
        Advisory ids to pass as ``--ignore-vuln``.
    python : str, optional
        Interpreter that has pip-audit installed. Defaults to the one
        running this script.

    Returns
    -------
    list of str
        The command, for :func:`subprocess.run` (no shell involved).
    """
    command = [
        python or sys.executable,
        "-m",
        "pip_audit",
        "--requirement",
        str(requirements),
        "--no-deps",
        "--disable-pip",
        "--strict",
        "--progress-spinner",
        "off",
        "--aliases",
    ]
    for advisory in ignored:
        command += ["--ignore-vuln", advisory]
    return command


def _pip_audit_available() -> bool:
    """
    Whether pip-audit can be imported by the running interpreter.

    Returns
    -------
    bool
        ``True`` when ``python -m pip_audit`` will work.
    """
    return importlib.util.find_spec("pip_audit") is not None


def _build_parser() -> argparse.ArgumentParser:
    """
    The command-line interface.

    Returns
    -------
    argparse.ArgumentParser
        The parser.
    """
    parser = argparse.ArgumentParser(
        description="Audit every package pinned in poetry.lock with pip-audit."
    )
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK, help="poetry.lock to audit")
    parser.add_argument(
        "--ignore-file",
        type=Path,
        default=DEFAULT_IGNORE_FILE,
        help="advisories to ignore (see the module docstring for the format)",
    )
    parser.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        help="only print the pins that would be audited",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """
    Run the audit.

    Parameters
    ----------
    argv : sequence of str, optional
        Command-line arguments without the program name.

    Returns
    -------
    int
        0 clean, 1 advisories found, 2 ignore list or tooling problem.
    """
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    pins = locked_requirements(args.lock)
    if args.print_only:
        sys.stdout.write("\n".join(pins) + "\n")
        return 0

    try:
        ignored = read_ignore_list(args.ignore_file)
    except ValueError as exc:
        logger.error("%s", exc)
        return 2
    today = _datetime.date.today()
    expired = [entry for entry in ignored if entry.until < today]
    if expired:
        for entry in expired:
            logger.error(
                "ignore entry %s expired on %s (%s) -- fix it or renew the entry "
                "with a new reason",
                entry.advisory,
                entry.until.isoformat(),
                entry.reason,
            )
        return 2
    if not _pip_audit_available():
        logger.error(
            "pip-audit is not installed for %s; install it with "
            "'python -m pip install -r .github/audit/requirements.txt'",
            sys.executable,
        )
        return 2

    for entry in ignored:
        logger.info("ignoring %s until %s: %s", entry.advisory, entry.until, entry.reason)
    layers = audit_layers(pins)
    logger.info(
        "auditing %d pinned packages from %s in %d layer(s)", len(pins), args.lock, len(layers)
    )
    status = 0
    with tempfile.TemporaryDirectory() as tmp:
        for index, layer in enumerate(layers):
            requirements = Path(tmp) / f"layer{index}.txt"
            requirements.write_text("\n".join(layer) + "\n", encoding="utf-8")
            result = subprocess.run(
                pip_audit_command(requirements, [entry.advisory for entry in ignored]),
                check=False,
            )
            status = max(status, result.returncode)
    return status


if __name__ == "__main__":
    sys.exit(main())
