"""Generate ``THIRD_PARTY_NOTICES.md`` from the installed distributions.

This script enumerates every Poetry runtime dependency declared under
``[tool.poetry.dependencies]`` in ``pyproject.toml``, looks the
distribution up in the current environment via
``importlib.metadata`` and collects name, version, license identifier,
project URL and the license text shipped with the wheel. The collected
records are written to ``THIRD_PARTY_NOTICES.md`` in the repository
root.

Usage
-----
Run from within the Poetry environment::

    poetry run python scripts/generate_third_party_licenses.py

Notes
-----
The script intentionally does not fetch licenses from the network. Only
metadata present locally is used, which means the script must be run
inside an environment where ``poetry install`` has been executed.

Packages without a detectable license text produce a warning and an
entry marked as ``License text unavailable``.
"""

from __future__ import annotations

import argparse
import logging
import sys
import tomllib
from importlib import metadata
from pathlib import Path

logger = logging.getLogger("groundinsight.third_party_licenses")


# --- helpers -----------------------------------------------------------------


def _repo_root() -> Path:
    """Return the repository root.

    Returns
    -------
    Path
        Absolute path to the directory containing ``pyproject.toml``.

    Raises
    ------
    FileNotFoundError
        If no ``pyproject.toml`` can be located.
    """
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    raise FileNotFoundError("pyproject.toml not found in any parent directory")


def _runtime_dependency_names(pyproject: Path) -> list[str]:
    """Return the PyPI package names declared as Poetry runtime dependencies.

    Parameters
    ----------
    pyproject : Path
        Path to ``pyproject.toml``.

    Returns
    -------
    list[str]
        Dependency names, sorted case-insensitively, excluding the
        implicit ``python`` entry.
    """
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    deps = (
        data.get("tool", {})
        .get("poetry", {})
        .get("dependencies", {})
    )
    return sorted(
        (name for name in deps if name.lower() != "python"),
        key=str.lower,
    )


def _license_identifier(meta: metadata.PackageMetadata) -> str:
    """Return a best-effort SPDX-like license identifier.

    Parameters
    ----------
    meta : metadata.PackageMetadata
        Distribution metadata.

    Returns
    -------
    str
        License expression if available, otherwise ``"UNKNOWN"``.
    """
    expression = meta.get("License-Expression")
    if expression:
        return expression
    classifiers = [c for c in meta.get_all("Classifier") or [] if c.startswith("License")]
    if classifiers:
        return ", ".join(c.split(" :: ")[-1] for c in classifiers)
    lic = meta.get("License")
    if lic and lic != "UNKNOWN":
        return lic.splitlines()[0]
    return "UNKNOWN"


def _project_url(meta: metadata.PackageMetadata) -> str:
    """Return the canonical homepage URL of the distribution if known.

    Parameters
    ----------
    meta : metadata.PackageMetadata
        Distribution metadata.

    Returns
    -------
    str
        Best URL found, empty string if none is available.
    """
    home = meta.get("Home-page")
    if home:
        return home
    for entry in meta.get_all("Project-URL") or []:
        label, _, url = entry.partition(", ")
        if label.strip().lower() in {"homepage", "source", "repository"}:
            return url.strip()
    return ""


def _license_text(dist: metadata.Distribution) -> str | None:
    """Return the bundled license text if any file in the distribution looks like one.

    Parameters
    ----------
    dist : metadata.Distribution
        Distribution to inspect.

    Returns
    -------
    str | None
        License file content, or ``None`` if no license file is shipped.
    """
    if not dist.files:
        return None
    candidates = [
        f
        for f in dist.files
        if f.name.lower().startswith(("license", "licence", "copying", "notice"))
    ]
    if not candidates:
        return None
    # Prefer the shortest name (usually the top-level LICENSE over
    # LICENSE-extra-files in vendored subdirectories).
    candidates.sort(key=lambda f: (len(f.parts), len(f.name)))
    for candidate in candidates:
        try:
            text = candidate.read_text()
        except (FileNotFoundError, UnicodeDecodeError):
            continue
        return text
    return None


# --- report ------------------------------------------------------------------


def _render_report(names: list[str]) -> str:
    """Produce the Markdown report body.

    Parameters
    ----------
    names : list[str]
        Runtime dependency names declared in ``pyproject.toml``.

    Returns
    -------
    str
        Markdown text ready to be written to ``THIRD_PARTY_NOTICES.md``.
    """
    lines: list[str] = [
        "# Third-Party Notices",
        "",
        "This file lists the third-party Python distributions that groundinsight",
        "depends on at runtime, together with their license information. The list is",
        "generated automatically from the Poetry runtime dependencies declared in",
        "``pyproject.toml`` and the metadata of the currently installed distributions.",
        "",
    ]

    for name in names:
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            logger.warning(
                "%s is declared as a dependency but is not installed; skipping",
                name,
            )
            lines.extend(
                [
                    f"## {name}",
                    "",
                    "Distribution not installed in the current environment.",
                    "",
                ]
            )
            continue

        meta = dist.metadata
        version = meta.get("Version", "unknown")
        license_id = _license_identifier(meta)
        url = _project_url(meta)
        text = _license_text(dist)

        lines.append(f"## {name} {version}")
        lines.append("")
        lines.append(f"- License: {license_id}")
        if url:
            lines.append(f"- Homepage: <{url}>")
        lines.append("")
        if text:
            lines.append("```text")
            lines.append(text.strip())
            lines.append("```")
        else:
            lines.append("License text unavailable in the installed distribution.")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# --- CLI ---------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser.

    Returns
    -------
    argparse.ArgumentParser
        Fully configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="generate-third-party-licenses",
        description="Generate THIRD_PARTY_NOTICES.md from installed distributions.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="output path (default: <repo_root>/THIRD_PARTY_NOTICES.md)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the license-generator script.

    Parameters
    ----------
    argv : list[str] | None
        Command-line arguments.

    Returns
    -------
    int
        Process exit code.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _build_parser().parse_args(argv)

    root = _repo_root()
    output = args.output or root / "THIRD_PARTY_NOTICES.md"
    names = _runtime_dependency_names(root / "pyproject.toml")
    logger.info("collecting license information for %d dependencies", len(names))
    report = _render_report(names)
    output.write_text(report, encoding="utf-8")
    logger.info("written %s", output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
