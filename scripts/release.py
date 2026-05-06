"""Release automation for groundinsight.

This script bumps the project version in every location where it is
recorded (``pyproject.toml``, ``src/groundinsight/__init__.py`` and
``CITATION.cff``), rolls the ``[Unreleased]`` section of ``CHANGELOG.md``
into a new dated version section, creates a conventional-commit release
commit, tags it as ``vX.Y.Z`` and pushes both the branch and the tag.
The PyPI publishing itself is handled by the GitHub Actions workflow
which triggers on the ``v*`` tag via OIDC Trusted Publishing.

Usage
-----
Invoke via the Poetry script entry point::

    poetry run release            # defaults to a patch bump
    poetry run release patch
    poetry run release minor
    poetry run release major
    poetry run release set 1.2.3

Options
-------
``--dry-run``
    Print the intended changes without modifying any file or calling git.
``--no-push``
    Create the commit and the tag locally but do not push.
``--allow-dirty``
    Allow uncommitted changes in the working tree. By default the script
    refuses to run if ``git status --porcelain`` reports any entries.
``--allow-empty-changelog``
    Proceed even if the ``[Unreleased]`` section of ``CHANGELOG.md`` is
    empty. A housekeeping note will be inserted in the new version
    section instead.
``--date``
    Override the release date written into ``CHANGELOG.md``. Defaults
    to today in ISO-8601 form (``YYYY-MM-DD``).

Notes
-----
The script expects to be run from the repository root, which is
automatically derived from the location of ``pyproject.toml``. Semantic
versioning is enforced: only ``MAJOR.MINOR.PATCH`` strings (with optional
pre-release suffix) are accepted, and ``set`` refuses to move the version
backwards.

``CHANGELOG.md`` is optional — if it does not exist, the changelog step
is skipped silently. If it exists, the script looks for a heading of the
exact form ``## [Unreleased]`` and the next heading beginning with
``## [`` to locate the section boundaries. Unrelated top-level headings
such as ``## Roadmap`` are therefore safe to keep elsewhere in the file.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("groundinsight.release")


# --- constants ---------------------------------------------------------------


SEMVER_RE = re.compile(
    r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?$"
)


# Lower-cased strings that mark an '[Unreleased]' section as effectively
# empty. The section is also considered empty if it only contains
# whitespace or horizontal-rule separators after the '[Unreleased]' heading.
_EMPTY_UNRELEASED_MARKERS: frozenset[str] = frozenset(
    {
        "",
        "_no changes yet._",
        "_nothing yet._",
        "nothing yet.",
        "nothing yet",
    }
)


@dataclass(frozen=True)
class VersionLocation:
    """Description of a file in which the project version is recorded.

    Parameters
    ----------
    path : Path
        File containing the version string.
    pattern : re.Pattern[str]
        Regex that matches the current version line; the version itself
        must be captured in a named group called ``version``.
    template : str
        ``str.format``-compatible template used to rewrite the line with
        the new version. The new version is provided as ``{version}``.
    """

    path: Path
    pattern: re.Pattern[str]
    template: str


# --- helpers -----------------------------------------------------------------


def _repo_root() -> Path:
    """Return the repository root (the directory containing pyproject.toml).

    Returns
    -------
    Path
        Absolute path to the repository root.

    Raises
    ------
    FileNotFoundError
        If no ``pyproject.toml`` can be found walking up from this file.
    """
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        candidate = parent / "pyproject.toml"
        if candidate.is_file():
            return parent
    raise FileNotFoundError("pyproject.toml not found in any parent directory")


def _version_locations(root: Path) -> list[VersionLocation]:
    """List every file in which the project version is tracked.

    Parameters
    ----------
    root : Path
        Repository root.

    Returns
    -------
    list[VersionLocation]
        All files that are kept in lockstep by this script.
    """
    return [
        VersionLocation(
            path=root / "pyproject.toml",
            pattern=re.compile(
                r'^version\s*=\s*"(?P<version>[^"]+)"',
                flags=re.MULTILINE,
            ),
            template='version = "{version}"',
        ),
        VersionLocation(
            path=root / "src" / "groundinsight" / "__init__.py",
            pattern=re.compile(
                r'^__version__\s*=\s*"(?P<version>[^"]+)"',
                flags=re.MULTILINE,
            ),
            template='__version__ = "{version}"',
        ),
        VersionLocation(
            path=root / "CITATION.cff",
            pattern=re.compile(
                r'^version:\s*"?(?P<version>[^\s"]+)"?',
                flags=re.MULTILINE,
            ),
            template='version: "{version}"',
        ),
    ]


def _parse_semver(version: str) -> tuple[int, int, int, str | None]:
    """Parse a semantic version string.

    Parameters
    ----------
    version : str
        Version of the form ``MAJOR.MINOR.PATCH`` optionally followed by a
        pre-release suffix (``-rc.1``, ``-beta.2`` ...).

    Returns
    -------
    tuple[int, int, int, str | None]
        Major, minor, patch and optional pre-release component.

    Raises
    ------
    ValueError
        If ``version`` does not conform to the accepted pattern.
    """
    match = SEMVER_RE.match(version)
    if not match:
        raise ValueError(f"invalid semver string: {version!r}")
    return (
        int(match["major"]),
        int(match["minor"]),
        int(match["patch"]),
        match["pre"],
    )


def _bump(current: str, kind: str) -> str:
    """Return a new version obtained by bumping ``current``.

    Parameters
    ----------
    current : str
        Current version.
    kind : str
        Bump kind: ``"major"``, ``"minor"`` or ``"patch"``.

    Returns
    -------
    str
        New version string, always without pre-release suffix because a
        release bump drops any existing suffix.

    Raises
    ------
    ValueError
        If ``kind`` is unknown.
    """
    major, minor, patch, _ = _parse_semver(current)
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    if kind == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"unknown bump kind: {kind!r}")


def _is_newer(old: str, new: str) -> bool:
    """Return ``True`` if ``new`` is strictly newer than ``old``.

    Parameters
    ----------
    old : str
        Previous version.
    new : str
        Candidate version.

    Returns
    -------
    bool
        ``True`` iff the semver-tuple of ``new`` compares greater than
        ``old``. Pre-release suffixes are ignored for this comparison.
    """
    om, oi, op, _ = _parse_semver(old)
    nm, ni, np_, _ = _parse_semver(new)
    return (nm, ni, np_) > (om, oi, op)


def _read_current_version(location: VersionLocation) -> str:
    """Return the version string recorded in ``location``.

    Parameters
    ----------
    location : VersionLocation
        File to inspect.

    Returns
    -------
    str
        Current version string.

    Raises
    ------
    RuntimeError
        If the file does not contain the expected pattern.
    """
    text = location.path.read_text(encoding="utf-8")
    match = location.pattern.search(text)
    if match is None:
        raise RuntimeError(
            f"version pattern not found in {location.path}; file is out of sync"
        )
    return match["version"]


def _write_new_version(location: VersionLocation, new_version: str) -> None:
    """Replace the version string in ``location`` with ``new_version``.

    Parameters
    ----------
    location : VersionLocation
        File to rewrite.
    new_version : str
        Version to write.
    """
    text = location.path.read_text(encoding="utf-8")
    replacement = location.template.format(version=new_version)
    updated, count = location.pattern.subn(replacement, text, count=1)
    if count != 1:
        raise RuntimeError(
            f"failed to update version in {location.path} (matches: {count})"
        )
    location.path.write_text(updated, encoding="utf-8")
    logger.info("updated %s -> %s", location.path.name, new_version)


def _parse_iso_date(value: str) -> _datetime.date:
    """Parse an ISO-8601 date string for the ``--date`` CLI option.

    Parameters
    ----------
    value : str
        Date in ``YYYY-MM-DD`` format.

    Returns
    -------
    datetime.date
        Parsed calendar date.

    Raises
    ------
    argparse.ArgumentTypeError
        If ``value`` is not a valid ISO-8601 date.
    """
    try:
        return _datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid ISO-8601 date {value!r}: {exc}"
        ) from exc


def _update_changelog(
    root: Path,
    new_version: str,
    release_date: _datetime.date,
    *,
    allow_empty: bool,
    dry_run: bool,
) -> bool:
    """Roll the ``[Unreleased]`` block into a new dated version section.

    The function is idempotent relative to the structure of the file: it
    finds the ``## [Unreleased]`` heading and the first subsequent
    heading of the form ``## [...]`` (which scopes the match to version
    sections and ignores unrelated top-level headings like
    ``## Roadmap``), extracts the body in between, and rewrites the file
    so that:

    * a fresh empty ``## [Unreleased]`` stub remains at the top,
    * a new ``## [vX.Y.Z] — YYYY-MM-DD`` section carries the old body,
    * the ``[Unreleased]: .../compare/v<old>...HEAD`` link reference is
      updated to point to ``v<new>``,
    * a new ``[<new>]: .../releases/tag/v<new>`` link reference is
      inserted directly below it.

    Parameters
    ----------
    root : Path
        Repository root (directory containing ``CHANGELOG.md``).
    new_version : str
        Version string to assign to the new section (without the leading
        ``v`` prefix).
    release_date : datetime.date
        Calendar date to stamp on the new section.
    allow_empty : bool
        Proceed even if ``[Unreleased]`` is empty. When ``True``, a
        housekeeping note is inserted into the new section.
    dry_run : bool
        When ``True``, the intended changes are logged but the file is
        not modified.

    Returns
    -------
    bool
        ``True`` if ``CHANGELOG.md`` was found and processed (so the
        caller should stage it); ``False`` if no ``CHANGELOG.md`` exists
        and the step was skipped.

    Raises
    ------
    RuntimeError
        If ``CHANGELOG.md`` is present but has no ``[Unreleased]``
        heading, or if no version heading follows the ``[Unreleased]``
        section, or if the file has a broken structure.
    ValueError
        If the ``[Unreleased]`` section is empty and ``allow_empty`` is
        ``False``.
    """
    path = root / "CHANGELOG.md"
    if not path.is_file():
        logger.info("no CHANGELOG.md found; skipping changelog update")
        return False

    text = path.read_text(encoding="utf-8")

    # Find the '## [Unreleased]' heading line.
    heading_re = re.compile(r"^## \[Unreleased\][^\n]*\n", re.MULTILINE)
    heading_match = heading_re.search(text)
    if heading_match is None:
        raise RuntimeError(
            "CHANGELOG.md: '## [Unreleased]' heading not found"
        )

    # The section extends from the end of the heading line up to (but not
    # including) the next '## [' heading. Requiring the opening bracket
    # scopes the match to version sections and avoids accidental matches
    # on headings such as '## Roadmap'.
    body_start = heading_match.end()
    next_heading_re = re.compile(r"^## \[", re.MULTILINE)
    next_match = next_heading_re.search(text, body_start)
    if next_match is None:
        raise RuntimeError(
            "CHANGELOG.md: no version section follows '[Unreleased]'; "
            "refusing to rewrite the file"
        )
    body_end = next_match.start()
    raw_body = text[body_start:body_end]

    # Trim a trailing horizontal-rule divider (``---``) which acts as a
    # visual separator; a new one is emitted by the replacement.
    body_without_divider = re.sub(
        r"\n---[ \t]*\n\s*$", "\n", raw_body, flags=re.MULTILINE
    )
    stripped = body_without_divider.strip()
    is_empty = stripped.lower() in _EMPTY_UNRELEASED_MARKERS

    if is_empty and not allow_empty:
        raise ValueError(
            "CHANGELOG.md: the '[Unreleased]' section is empty. "
            "Add release notes under '[Unreleased]' before cutting the "
            "release, or pass '--allow-empty-changelog' to proceed "
            "without notes."
        )

    if is_empty:
        carried_body = "_Housekeeping release; no user-visible changes._\n"
    else:
        # Preserve verbatim body, trim surrounding whitespace, then
        # normalise to exactly one trailing newline before the divider.
        carried_body = stripped + "\n"

    date_str = release_date.isoformat()
    replacement = (
        "## [Unreleased]\n"
        "\n"
        "_No changes yet._\n"
        "\n"
        "---\n"
        "\n"
        f"## [{new_version}] \u2014 {date_str}\n"
        "\n"
        f"{carried_body}"
        "\n"
        "---\n"
        "\n"
    )
    updated = text[: heading_match.start()] + replacement + text[body_end:]

    # Update the link references at the bottom. The '[Unreleased]' link
    # has the form
    #   [Unreleased]: https://.../compare/v<prev>...HEAD
    # We rewrite it to target v<new> and insert a new
    #   [<new>]: https://.../releases/tag/v<new>
    # line directly below it.
    link_re = re.compile(
        r"^\[Unreleased\]:\s+"
        r"(?P<base>https?://\S+/compare/)"
        r"v(?P<prev>\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)"
        r"(?P<suffix>\.\.\.HEAD)\s*$",
        re.MULTILINE,
    )
    link_match = link_re.search(updated)
    if link_match is None:
        logger.warning(
            "CHANGELOG.md: '[Unreleased]' link reference not found; "
            "version-compare links at the bottom of the file were not "
            "touched — please update them by hand"
        )
    else:
        base = link_match["base"]
        tag_base = base.replace("/compare/", "/releases/tag/")
        new_unreleased_line = (
            f"[Unreleased]: {base}v{new_version}{link_match['suffix']}"
        )
        new_version_line = f"[{new_version}]: {tag_base}v{new_version}"
        updated = link_re.sub(
            f"{new_unreleased_line}\n{new_version_line}",
            updated,
            count=1,
        )

    if dry_run:
        logger.info(
            "[dry-run] would rewrite CHANGELOG.md: move '[Unreleased]' "
            "to '[%s] \u2014 %s'",
            new_version,
            date_str,
        )
        if is_empty:
            logger.info(
                "[dry-run] '[Unreleased]' is empty; a housekeeping note "
                "will be inserted in the new section"
            )
        else:
            preview = [
                line for line in carried_body.splitlines() if line.strip()
            ][:6]
            logger.info("[dry-run] new section preview:")
            for line in preview:
                logger.info("[dry-run]   %s", line)
        return True

    path.write_text(updated, encoding="utf-8")
    logger.info(
        "updated CHANGELOG.md: moved '[Unreleased]' to '[%s] \u2014 %s'",
        new_version,
        date_str,
    )
    return True


def _run_git(args: list[str], *, dry_run: bool) -> None:
    """Run a git command, honouring ``dry_run``.

    Parameters
    ----------
    args : list[str]
        Arguments passed to ``git`` (without the ``git`` prefix).
    dry_run : bool
        When ``True`` the command is only logged, not executed.

    Raises
    ------
    subprocess.CalledProcessError
        If git exits with a non-zero status.
    """
    command = ["git", *args]
    if dry_run:
        logger.info("[dry-run] %s", " ".join(command))
        return
    logger.info("$ %s", " ".join(command))
    subprocess.run(command, check=True)


def _git_clean(root: Path) -> bool:
    """Return ``True`` if the working tree has no uncommitted changes.

    Parameters
    ----------
    root : Path
        Repository root.

    Returns
    -------
    bool
        Working tree status.
    """
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() == ""


# --- CLI ---------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser for the release CLI.

    Returns
    -------
    argparse.ArgumentParser
        Fully configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="release",
        description="Bump groundinsight version, commit, tag and push.",
    )
    parser.add_argument(
        "kind",
        nargs="?",
        default="patch",
        choices=("major", "minor", "patch", "set"),
        help=(
            "semver bump kind; defaults to 'patch' when omitted. "
            "'set' requires an explicit VERSION argument"
        ),
    )
    parser.add_argument(
        "version",
        nargs="?",
        default=None,
        help="explicit version when KIND is 'set'",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print actions without modifying files or calling git",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="create commit and tag locally but do not push",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="proceed even if the working tree has uncommitted changes",
    )
    parser.add_argument(
        "--allow-empty-changelog",
        action="store_true",
        help=(
            "proceed even if the '[Unreleased]' section of CHANGELOG.md "
            "is empty; a housekeeping note will be inserted in its place"
        ),
    )
    parser.add_argument(
        "--date",
        type=_parse_iso_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="explicit release date for CHANGELOG.md (default: today)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point of the release script.

    Parameters
    ----------
    argv : list[str] | None
        Command-line arguments. When ``None``, ``sys.argv[1:]`` is used.

    Returns
    -------
    int
        Process exit code.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
    )
    args = _build_parser().parse_args(argv)

    root = _repo_root()
    locations = _version_locations(root)

    # Consistency check: every location must report the same current version.
    current_versions = {loc.path: _read_current_version(loc) for loc in locations}
    distinct = set(current_versions.values())
    if len(distinct) != 1:
        for path, version in current_versions.items():
            logger.error("  %s: %s", path, version)
        logger.error("version drift detected; aborting")
        return 2
    current = distinct.pop()
    logger.info("current version: %s", current)

    # Determine the new version.
    if args.kind == "set":
        if args.version is None:
            logger.error("'set' requires a VERSION argument")
            return 2
        try:
            _parse_semver(args.version)
        except ValueError as exc:
            logger.error("%s", exc)
            return 2
        new_version = args.version
        if not _is_newer(current, new_version):
            logger.error(
                "refusing to set version backwards: %s -> %s",
                current,
                new_version,
            )
            return 2
    else:
        new_version = _bump(current, args.kind)
    logger.info("new version:     %s", new_version)

    if not args.allow_dirty and not _git_clean(root):
        logger.error(
            "working tree is dirty; commit or stash changes, or pass --allow-dirty"
        )
        return 2

    # Rewrite files.
    for location in locations:
        if args.dry_run:
            logger.info(
                "[dry-run] would update %s: %s -> %s",
                location.path.name,
                current,
                new_version,
            )
        else:
            _write_new_version(location, new_version)

    # Roll the '[Unreleased]' block of CHANGELOG.md into a new dated
    # version section. Failures raise and abort the release before any
    # git operations are performed, leaving the working tree untouched
    # except for the version-string files (which are still easy to
    # revert with 'git checkout --').
    release_date = args.date if args.date is not None else _datetime.date.today()
    try:
        changelog_updated = _update_changelog(
            root,
            new_version,
            release_date,
            allow_empty=args.allow_empty_changelog,
            dry_run=args.dry_run,
        )
    except (RuntimeError, ValueError) as exc:
        logger.error("%s", exc)
        return 2

    # Stage, commit, tag, push.
    rel_paths = [str(loc.path.relative_to(root)) for loc in locations]
    if changelog_updated:
        rel_paths.append("CHANGELOG.md")
    _run_git(["add", *rel_paths], dry_run=args.dry_run)
    commit_message = f"chore(release): v{new_version}"
    _run_git(["commit", "-m", commit_message], dry_run=args.dry_run)
    tag = f"v{new_version}"
    _run_git(["tag", "-a", tag, "-m", commit_message], dry_run=args.dry_run)

    if args.no_push:
        logger.info("skipping push (--no-push)")
    else:
        _run_git(["push"], dry_run=args.dry_run)
        _run_git(["push", "origin", tag], dry_run=args.dry_run)

    logger.info("release %s complete", tag)
    return 0


if __name__ == "__main__":
    sys.exit(main())
