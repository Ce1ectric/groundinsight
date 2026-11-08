# tests/test_audit_pass19_workflows.py

"""
GitHub workflows: test matrix and hardening (audit pass 19, F10, REL6, SEC3,
SEC4).

F10 / REL6 -- with the floor at ``^3.12`` the CI has to back the claim: the
suite runs on 3.12, 3.13 and 3.14. Before this pass the matrix was 3.13/3.14,
which matched the old ``^3.13`` floor.

SEC3 -- every action was referenced by a moving tag, and
``pypa/gh-action-pypi-publish`` by the moving *branch* ``release/v1`` inside
the job that holds ``id-token: write``: whoever controls that branch controls
what runs with the OIDC token that publishes to PyPI. Every reference is now
a full commit SHA with the version as a comment, which Dependabot keeps
current.

SEC4 -- no workflow declared a top-level ``permissions`` block, so jobs
inherited the repository's default token scope, which can be read/write.
Every workflow now defaults to ``contents: read``; the three jobs that need
more (publish, GitHub release, docs deploy) say so themselves. Checkouts in
jobs that never push also drop the token from ``.git/config``
(``persist-credentials: false``), so a later step -- a dependency's install
hook, say -- cannot pick it up.

The workflow files are read as text. PyYAML only comes with the optional
``docs`` group and is therefore missing from the CI test environment, so every
property has a dependency-free primary check. Where PyYAML is importable the
same property is checked a second time on the parsed document, which guards
the hand-written text checks against a layout they do not understand.
"""

from __future__ import annotations

import itertools
import re
import tomllib
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW_NAMES = sorted(path.name for path in WORKFLOWS.glob("*.yml"))

#: The only (workflow, job, scope) triples that may hold a write scope.
_WRITE_SCOPES = {
    ("docs.yml", "build-and-deploy", "contents"),  # gh-deploy pushes gh-pages
    ("release.yml", "publish-pypi", "id-token"),  # OIDC for Trusted Publishing
    ("release.yml", "github-release", "contents"),  # creates the release
}

#: Jobs whose checkout must keep the token: they push with git.
_PUSHING_JOBS = {("docs.yml", "build-and-deploy")}

_USES = re.compile(
    r"^\s*(?:-\s+)?uses:\s*(?P<ref>[^\s#]+)(?:\s+#\s*(?P<comment>.*?))?\s*$"
)
_SHA_PINNED = re.compile(r"^[\w.-]+/[\w.-]+(?:/[^@\s]+)?@[0-9a-f]{40}$")
_VERSION_COMMENT = re.compile(r"^v\d+\.\d+\.\d+$")


def _text(name: str) -> str:
    """
    Read one workflow file.

    Parameters
    ----------
    name : str
        File name below ``.github/workflows``.

    Returns
    -------
    str
        The file content.
    """
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _yaml(name: str) -> dict:
    """
    Parse one workflow file with PyYAML, or skip when PyYAML is missing.

    Parameters
    ----------
    name : str
        File name below ``.github/workflows``.

    Returns
    -------
    dict
        The parsed document. Note that YAML 1.1 reads the key ``on`` as
        ``True``; no test here needs the triggers from the parsed form.
    """
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(_text(name))


def _declared_pythons() -> List[str]:
    """
    The supported minor versions: from the declared floor up to 3.14.

    Returns
    -------
    list of str
        For example ``["3.12", "3.13", "3.14"]`` for ``python = "^3.12"``.
    """
    constraint = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["poetry"]["dependencies"]["python"]
    floor = int(re.fullmatch(r"\^3\.(\d+)", constraint).group(1))
    return [f"3.{minor}" for minor in range(floor, 15)]


def _flow_list(text: str, key: str) -> List[str]:
    """
    Items of the first ``key: [a, "b", ...]`` flow sequence in ``text``.

    Parameters
    ----------
    text : str
        Workflow source.
    key : str
        Mapping key whose value is a flow sequence.

    Returns
    -------
    list of str
        The items with surrounding quotes removed.
    """
    match = re.search(rf"^\s*{re.escape(key)}:\s*\[([^\]]*)\]", text, re.MULTILINE)
    assert match is not None, f"no flow sequence for {key!r}"
    return [item.strip().strip("'\"") for item in match.group(1).split(",") if item.strip()]


def _text_test_matrix(text: str) -> Set[Tuple[str, str]]:
    """
    Expand the ``test`` job matrix of ``ci.yml`` from the text alone.

    Supports exactly the layout used there: ``os`` and ``python-version`` as
    flow sequences, plus an optional ``include`` list of ``os`` /
    ``python-version`` pairs that add combinations.

    Parameters
    ----------
    text : str
        Source of ``ci.yml``.

    Returns
    -------
    set of tuple
        ``(os, python-version)`` combinations.
    """
    combos = set(itertools.product(_flow_list(text, "os"), _flow_list(text, "python-version")))
    for os_name, version in re.findall(
        r"^\s*-\s*os:\s*(\S+)\s*\n\s*python-version:\s*['\"]?([0-9.]+)['\"]?",
        text,
        re.MULTILINE,
    ):
        combos.add((os_name, version))
    return combos


def _yaml_test_matrix(document: dict) -> Set[Tuple[str, str]]:
    """
    Expand the ``test`` job matrix from the parsed document.

    Follows GitHub's rules for the keys used here: the product of the list
    axes, minus ``exclude`` entries, plus ``include`` entries that do not
    match an existing combination.

    Parameters
    ----------
    document : dict
        Parsed ``ci.yml``.

    Returns
    -------
    set of tuple
        ``(os, python-version)`` combinations.
    """
    matrix = document["jobs"]["test"]["strategy"]["matrix"]
    combos = {
        (str(os_name), str(version))
        for os_name, version in itertools.product(matrix["os"], matrix["python-version"])
    }
    for entry in matrix.get("exclude", []):
        combos.discard((str(entry["os"]), str(entry["python-version"])))
    for entry in matrix.get("include", []):
        combos.add((str(entry["os"]), str(entry["python-version"])))
    return combos


# ---------------------------------------------------------------------------
# F10 / REL6 -- every supported Python is tested
# ---------------------------------------------------------------------------


def test_ci_tests_every_supported_python_on_linux():
    """F10: ubuntu runs the full range 3.12 / 3.13 / 3.14."""
    expected = {("ubuntu-latest", version) for version in _declared_pythons()}
    assert _declared_pythons() == ["3.12", "3.13", "3.14"]
    assert expected <= _text_test_matrix(_text("ci.yml"))


def test_ci_matrix_text_and_yaml_expansion_agree():
    """Second method for the matrix: GitHub's expansion on the parsed file."""
    assert _yaml_test_matrix(_yaml("ci.yml")) == _text_test_matrix(_text("ci.yml"))


# ---------------------------------------------------------------------------
# Text helpers for SEC3 / SEC4
# ---------------------------------------------------------------------------


def _indent(line: str) -> int:
    """
    Number of leading spaces of ``line``.

    Parameters
    ----------
    line : str
        A source line.

    Returns
    -------
    int
        The indentation width.
    """
    return len(line) - len(line.lstrip(" "))


def _is_content(line: str) -> bool:
    """
    Whether ``line`` carries YAML content (not blank, not a pure comment).

    Parameters
    ----------
    line : str
        A source line.

    Returns
    -------
    bool
        ``True`` for a content line.
    """
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def _uses(text: str) -> List[Tuple[int, str, Optional[str]]]:
    """
    Every ``uses:`` reference of a workflow, with its trailing comment.

    Parameters
    ----------
    text : str
        Workflow source.

    Returns
    -------
    list of tuple
        ``(line number, reference, comment or None)``.
    """
    found = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        match = _USES.match(line)
        if match:
            found.append((lineno, match.group("ref"), match.group("comment")))
    return found


def _mapping_below(lines: List[str], index: int) -> Dict[str, str]:
    """
    The ``key: value`` pairs nested directly below ``lines[index]``.

    Parameters
    ----------
    lines : list of str
        Source lines.
    index : int
        Index of the parent key line.

    Returns
    -------
    dict
        Child keys mapped to their scalar values (comments and quotes removed).
    """
    parent = _indent(lines[index])
    out: Dict[str, str] = {}
    child = None
    for line in lines[index + 1 :]:
        if not _is_content(line):
            continue
        if _indent(line) <= parent:
            break
        if child is None:
            child = _indent(line)
        if _indent(line) == child:
            key, _, value = line.split("#", 1)[0].strip().partition(":")
            out[key.strip()] = value.strip().strip("'\"")
    return out


def _permissions_at(lines: List[str], indent: int) -> Optional[Dict[str, str]]:
    """
    The ``permissions`` block whose key sits at ``indent`` in ``lines``.

    Parameters
    ----------
    lines : list of str
        Source lines of a workflow or of one job.
    indent : int
        Indentation of the ``permissions:`` key to look for.

    Returns
    -------
    dict or None
        Scope -> access. An inline value (``read-all``, ``{}``) is returned
        as ``{"*": value}``; ``None`` when there is no such block.
    """
    for index, line in enumerate(lines):
        if _is_content(line) and _indent(line) == indent and line.strip().startswith(
            "permissions:"
        ):
            inline = line.split("#", 1)[0].strip()[len("permissions:") :].strip()
            return {"*": inline} if inline else _mapping_below(lines, index)
    return None


def _jobs(text: str) -> Dict[str, List[str]]:
    """
    Split a workflow into its jobs.

    Parameters
    ----------
    text : str
        Workflow source.

    Returns
    -------
    dict
        Job id -> the job's source lines (header line excluded).
    """
    jobs: Dict[str, List[str]] = {}
    in_jobs = False
    job_indent = None
    current = None
    for line in text.splitlines():
        if not _is_content(line):
            if current is not None:
                jobs[current].append(line)
            continue
        if _indent(line) == 0:
            in_jobs = line.split("#", 1)[0].strip() == "jobs:"
            current = None
            continue
        if not in_jobs:
            continue
        if job_indent is None:
            job_indent = _indent(line)
        if _indent(line) == job_indent:
            current = line.split("#", 1)[0].strip().rstrip(":")
            jobs[current] = []
        elif current is not None:
            jobs[current].append(line)
    return jobs


def _key_indent(job_lines: List[str]) -> int:
    """
    Indentation of a job's own keys (``runs-on``, ``steps`` ...).

    Parameters
    ----------
    job_lines : list of str
        Lines of one job, as returned by :func:`_jobs`.

    Returns
    -------
    int
        The smallest indentation of a content line.
    """
    return min(_indent(line) for line in job_lines if _is_content(line))


def _steps(job_lines: List[str]) -> List[str]:
    """
    The source of each step of one job.

    Parameters
    ----------
    job_lines : list of str
        Lines of one job, as returned by :func:`_jobs`.

    Returns
    -------
    list of str
        One string per step, in order.
    """
    key = _key_indent(job_lines)
    start = next(
        (
            i
            for i, line in enumerate(job_lines)
            if _is_content(line) and _indent(line) == key and line.strip() == "steps:"
        ),
        None,
    )
    if start is None:
        return []
    steps: List[List[str]] = []
    item = None
    for line in job_lines[start + 1 :]:
        if _is_content(line) and _indent(line) <= key:
            break
        if _is_content(line) and line.lstrip().startswith("- "):
            if item is None:
                item = _indent(line)
            if _indent(line) == item:
                steps.append([])
        if steps:
            steps[-1].append(line)
    return ["\n".join(step) for step in steps]


def _text_permissions(name: str) -> Tuple[Optional[Dict[str, str]], Dict[str, Optional[Dict[str, str]]]]:
    """
    Top-level and per-job ``permissions`` of a workflow, from the text.

    Parameters
    ----------
    name : str
        File name below ``.github/workflows``.

    Returns
    -------
    tuple
        ``(workflow permissions or None, {job id: permissions or None})``.
    """
    text = _text(name)
    top = _permissions_at(text.splitlines(), 0)
    per_job = {
        job: _permissions_at(lines, _key_indent(lines)) for job, lines in _jobs(text).items()
    }
    return top, per_job


def _yaml_permissions(name: str) -> Tuple[Optional[Dict[str, str]], Dict[str, Optional[Dict[str, str]]]]:
    """
    Top-level and per-job ``permissions`` of a workflow, from PyYAML.

    Parameters
    ----------
    name : str
        File name below ``.github/workflows``.

    Returns
    -------
    tuple
        Same shape as :func:`_text_permissions`.
    """

    def normalise(value):
        if value is None:
            return None
        if isinstance(value, dict):
            return {str(k): str(v) for k, v in value.items()}
        return {"*": str(value)}

    document = _yaml(name)
    return normalise(document.get("permissions")), {
        job: normalise(spec.get("permissions")) for job, spec in document["jobs"].items()
    }


# ---------------------------------------------------------------------------
# SEC3 -- actions pinned to commit SHAs
# ---------------------------------------------------------------------------


def test_the_workflow_set_is_the_expected_one():
    """Guards the parametrisation below against an empty or moved glob."""
    assert {"ci.yml", "docs.yml", "release.yml"} <= set(WORKFLOW_NAMES)


@pytest.mark.parametrize("name", WORKFLOW_NAMES)
def test_every_action_is_pinned_to_a_full_commit_sha(name):
    references = _uses(_text(name))
    assert references, f"{name} uses no action at all?"
    unpinned = [
        (lineno, ref)
        for lineno, ref, _ in references
        if not ref.startswith("./") and not _SHA_PINNED.match(ref)
    ]
    assert unpinned == [], f"{name}: not pinned to a 40-hex commit SHA: {unpinned}"


@pytest.mark.parametrize("name", WORKFLOW_NAMES)
def test_every_pin_names_its_release(name):
    """The trailing ``# vX.Y.Z`` is what Dependabot and a reviewer read."""
    missing = [
        (lineno, ref, comment)
        for lineno, ref, comment in _uses(_text(name))
        if not ref.startswith("./") and not (comment and _VERSION_COMMENT.match(comment))
    ]
    assert missing == [], f"{name}: pin without a '# vX.Y.Z' comment: {missing}"


def test_the_publish_action_is_pinned_in_the_job_holding_the_oidc_token():
    """The concrete case SEC3 is about: ``@release/v1`` with ``id-token: write``."""
    text = _text("release.yml")
    assert not any("@release/" in ref for _, ref, _ in _uses(text))
    publish = _jobs(text)["publish-pypi"]
    references = [ref for _, ref, _ in _uses("\n".join(publish))]
    assert any(ref.startswith("pypa/gh-action-pypi-publish@") for ref in references)
    assert all(_SHA_PINNED.match(ref) for ref in references), references


@pytest.mark.parametrize("name", WORKFLOW_NAMES)
def test_text_and_yaml_find_the_same_action_references(name):
    """Second method: every ``uses`` of the parsed document, job and step level."""
    document = _yaml(name)
    from_yaml = []
    for job in document["jobs"].values():
        if "uses" in job:
            from_yaml.append(job["uses"])
        for step in job.get("steps", []):
            if "uses" in step:
                from_yaml.append(step["uses"])
    assert sorted(from_yaml) == sorted(ref for _, ref, _ in _uses(_text(name)))


def test_dependabot_keeps_the_action_pins_current():
    """A SHA pin without an updater would freeze the actions forever."""
    text = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    assert re.search(
        r'package-ecosystem:\s*"github-actions"\s*\n\s*directory:\s*"/"', text
    ), text


# ---------------------------------------------------------------------------
# SEC4 -- least-privilege token
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", WORKFLOW_NAMES)
def test_every_workflow_defaults_the_token_to_read_only(name):
    top, _ = _text_permissions(name)
    assert top == {"contents": "read"}, f"{name}: top-level permissions {top}"


def test_write_scopes_are_granted_only_to_the_jobs_that_need_them():
    granted = set()
    for name in WORKFLOW_NAMES:
        top, per_job = _text_permissions(name)
        for job, scopes in [("<workflow>", top), *per_job.items()]:
            for scope, access in (scopes or {}).items():
                if "write" in access:
                    granted.add((name, job, scope))
    assert granted == _WRITE_SCOPES


@pytest.mark.parametrize("name", WORKFLOW_NAMES)
def test_permissions_text_and_yaml_agree(name):
    """Second method for both permission checks above."""
    assert _yaml_permissions(name) == _text_permissions(name)


@pytest.mark.parametrize("name", WORKFLOW_NAMES)
def test_checkouts_drop_the_token_unless_the_job_pushes(name):
    keeping = []
    for job, lines in _jobs(_text(name)).items():
        for step in _steps(lines):
            if not re.search(r"uses:\s*actions/checkout@", step):
                continue
            drops = re.search(r"^\s*persist-credentials:\s*false\s*$", step, re.MULTILINE)
            if (name, job) in _PUSHING_JOBS:
                assert not drops, f"{name}/{job} pushes and needs its credentials"
            elif not drops:
                keeping.append(job)
    assert keeping == [], f"{name}: checkout keeps the token in {keeping}"


def test_step_parser_sees_every_checkout():
    """The step split must not lose a checkout -- count against the raw text."""
    for name in WORKFLOW_NAMES:
        text = _text(name)
        in_steps = sum(
            bool(re.search(r"uses:\s*actions/checkout@", step))
            for lines in _jobs(text).values()
            for step in _steps(lines)
        )
        assert in_steps == len(re.findall(r"uses:\s*actions/checkout@", text)), name


# ---------------------------------------------------------------------------
# SEC2 -- the dependency audit job
# ---------------------------------------------------------------------------


def test_the_dependency_audit_runs_on_changes_and_on_a_schedule():
    """New advisories arrive against an unchanged lock, hence the schedule."""
    text = _text("audit.yml")
    for trigger in ("pull_request:", "push:", "schedule:", "workflow_dispatch:"):
        assert re.search(rf"^  {trigger}", text, re.MULTILINE), trigger
    assert re.search(r"^\s*-\s*cron:\s*\"[^\"]+\"", text, re.MULTILINE)
    assert "python scripts/audit_dependencies.py" in text
    assert "--requirement .github/audit/requirements.txt" in text


def test_pip_audit_itself_is_pinned_and_kept_current():
    requirements = (ROOT / ".github" / "audit" / "requirements.txt").read_text(
        encoding="utf-8"
    )
    pins = [line for line in requirements.splitlines() if _is_content(line)]
    assert len(pins) == 1 and re.fullmatch(r"pip-audit==\d+\.\d+\.\d+", pins[0]), pins
    dependabot = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    assert re.search(
        r'package-ecosystem:\s*"pip"\s*\n\s*directory:\s*"/\.github/audit"', dependabot
    ), dependabot
