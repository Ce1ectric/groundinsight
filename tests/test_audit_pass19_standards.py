# tests/test_audit_pass19_standards.py
"""
Code standards as tests (audit pass 19, STD1, STD2, STD3, STD5, STD7, STD8,
STD10, STD11; documentation decision D4).

The house conventions -- module headers, NumPy docstrings, logging instead of
``print``, warnings with a package category, canonical parameter names -- were
written down but checked by nobody, so every review found a few more
violations. This module checks what can be checked statically and fast, so a
violation fails the ordinary test run instead of waiting for the next audit.

The linters themselves (``ruff check`` and ``numpydoc lint``) run in the
blocking ``lint`` job of ``.github/workflows/ci.yml``; here only their
configuration is pinned: lint, never format (STD8), the Python floor as the
target version, and the same exact tool versions in the dev group and in the
CI job.
"""

from __future__ import annotations

import importlib
import inspect
import re
import tomllib
import warnings
from pathlib import Path
from typing import Any, Dict, Iterator, List, Set, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
LOCK = ROOT / "poetry.lock"
CI = ROOT / ".github" / "workflows" / "ci.yml"

#: The linters of the lint gate, pinned exactly in the dev group.
LINTERS = ("ruff", "numpydoc")


def _pyproject() -> Dict[str, Any]:
    """
    The parsed ``pyproject.toml``.

    Returns
    -------
    dict
        The TOML document.
    """
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _python_floor() -> str:
    """
    The lowest supported Python as ``"3.X"``, from the Poetry constraint.

    Returns
    -------
    str
        For example ``"3.12"`` for ``python = "^3.12"``.
    """
    constraint = _pyproject()["tool"]["poetry"]["dependencies"]["python"]
    return re.fullmatch(r"\^(3\.\d+)", constraint).group(1)


def _locked_versions() -> Dict[str, List[str]]:
    """
    Every package in ``poetry.lock`` with its locked version(s).

    Returns
    -------
    dict of str to list of str
        Package name to the versions locked for it (more than one where the
        lock splits a package by environment marker).
    """
    lock = tomllib.loads(LOCK.read_text(encoding="utf-8"))
    versions: Dict[str, List[str]] = {}
    for package in lock["package"]:
        versions.setdefault(package["name"], []).append(package["version"])
    return versions


# ---------------------------------------------------------------------------
# STD8 -- the lint configuration
# ---------------------------------------------------------------------------


def test_ruff_targets_the_declared_python_floor():
    """ruff's target version is the floor of the Poetry constraint."""
    ruff = _pyproject()["tool"]["ruff"]
    assert ruff["target-version"] == "py" + _python_floor().replace(".", "")


def test_the_python_floor_agrees_with_the_lowest_ci_python():
    """Second method for the floor: the smallest version the CI matrix runs."""
    text = CI.read_text(encoding="utf-8")
    matrix = re.search(r"python-version:\s*\[([^\]]+)\]", text).group(1)
    versions = [v.strip().strip("\"'") for v in matrix.split(",")]
    lowest = min(versions, key=lambda v: tuple(int(p) for p in v.split(".")))
    assert lowest == _python_floor()


def test_ruff_lints_and_never_formats():
    """Lint, never format: no formatter section, no formatter in a dependency
    group, no formatter in CI."""
    config = _pyproject()
    ruff = config["tool"]["ruff"]
    assert "format" not in ruff
    assert "lint" in ruff and ruff["lint"]["select"]
    for group in config["tool"]["poetry"].get("group", {}).values():
        assert not set(group.get("dependencies", {})) & {"black", "autopep8", "yapf"}
    ci = CI.read_text(encoding="utf-8")
    assert not re.search(r"\bruff\s+format\b|\bblack\b|--fix\b|--add-noqa\b", ci)


def test_ruff_keeps_the_typing_house_style():
    """``typing.Optional/List/Dict`` stay: the rewrites to ``X | None`` and
    ``list[...]`` are switched off, everything else of pyupgrade is on."""
    lint = _pyproject()["tool"]["ruff"]["lint"]
    assert "UP" in lint["select"]
    assert {"UP006", "UP007", "UP035", "UP045"} <= set(lint["ignore"])


def test_numpydoc_lint_is_configured():
    """The numpydoc gate checks everything except the listed style codes."""
    config = _pyproject()["tool"]["numpydoc_validation"]
    assert config["checks"][0] == "all"
    # The section checks of the contract stay on.
    for code in ("PR01", "PR02", "PR04", "RT01", "RT03", "GL06", "GL07", "GL08"):
        assert code not in config["checks"], code
    assert config["exclude"]


def test_the_linters_are_pinned_exactly_in_the_dev_group():
    dev = _pyproject()["tool"]["poetry"]["group"]["dev"]["dependencies"]
    for tool in LINTERS:
        assert re.fullmatch(r"\d+\.\d+\.\d+", dev[tool]), (tool, dev.get(tool))


def test_the_lock_holds_the_pinned_linter_versions():
    """Second method for the pins: what ``poetry.lock`` resolved."""
    dev = _pyproject()["tool"]["poetry"]["group"]["dev"]["dependencies"]
    locked = _locked_versions()
    for tool in (*LINTERS, "radon"):
        assert locked.get(tool) == [dev[tool]], (tool, locked.get(tool), dev[tool])


# ---------------------------------------------------------------------------
# STD1 -- the fields of the public models are documented
# ---------------------------------------------------------------------------

#: Modules whose ``__all__`` is the public surface: the package and the
#: service layer for front ends.
PUBLIC_MODULES = ("groundinsight", "groundinsight.api")


def _public_classes() -> Iterator[Tuple[str, type]]:
    """
    Every class of the public surface, with its package base classes.

    Yields
    ------
    tuple of (str, type)
        Dotted name and class; each class once.
    """
    seen: Set[type] = set()
    for module_name in PUBLIC_MODULES:
        module = importlib.import_module(module_name)
        for name in module.__all__:
            obj = getattr(module, name)
            if not inspect.isclass(obj):
                continue
            for klass in obj.__mro__:
                if klass in seen or not klass.__module__.startswith("groundinsight"):
                    continue
                seen.add(klass)
                yield f"{klass.__module__}.{klass.__qualname__}", klass


def _model_fields(klass: type) -> Set[str]:
    """
    The pydantic or dataclass fields of ``klass``, inherited ones included.

    Parameters
    ----------
    klass : type
        Any class.

    Returns
    -------
    set of str
        Field names; empty for a class that is neither.
    """
    if hasattr(klass, "model_fields"):
        return set(klass.model_fields)
    if hasattr(klass, "__dataclass_fields__"):
        return set(klass.__dataclass_fields__)
    return set()


def _declared_fields(klass: type) -> Set[str]:
    """
    The fields ``klass`` adds to those of its bases.

    A field is documented on the class that introduces it; a subclass that
    only narrows an inherited field (a ``Literal`` discriminator, say) relies
    on the base's entry.

    Parameters
    ----------
    klass : type
        A pydantic model or dataclass.

    Returns
    -------
    set of str
        The new field names.
    """
    inherited: Set[str] = set()
    for base in klass.__bases__:
        inherited |= _model_fields(base)
    return _model_fields(klass) - inherited


def _documented_names(klass: type) -> Set[str]:
    """
    Names listed under ``Parameters``, ``Attributes`` or ``Other Parameters``.

    Parameters
    ----------
    klass : type
        The class whose docstring is read.

    Returns
    -------
    set of str
        Every documented name; ``a, b : str`` counts for both.
    """
    from numpydoc.docscrape import ClassDoc

    with warnings.catch_warnings():
        # Custom sections are allowed in prose; docscrape only warns.
        warnings.simplefilter("ignore", UserWarning)
        doc = ClassDoc(klass, config={"show_class_members": False})
    names: Set[str] = set()
    for section in ("Parameters", "Attributes", "Other Parameters"):
        for entry in doc[section]:
            for name in entry.name.split(":")[0].split(","):
                names.add(name.strip().lstrip("*"))
    return names


def _field_documentation_gaps() -> Dict[str, List[str]]:
    """
    Public model classes with fields their docstring does not name.

    Returns
    -------
    dict of str to list of str
        Dotted class name to the undocumented fields it declares.
    """
    pytest.importorskip("numpydoc")
    gaps: Dict[str, List[str]] = {}
    for name, klass in _public_classes():
        missing = sorted(_declared_fields(klass) - _documented_names(klass))
        if missing:
            gaps[name] = missing
    return gaps


def test_public_model_fields_are_documented():
    """Every pydantic or dataclass field of the public surface is listed
    under ``Attributes`` (or ``Parameters``) of the class that declares it."""
    gaps = _field_documentation_gaps()
    assert not gaps, gaps


def test_the_field_check_sees_the_models():
    """Second method: the check is not vacuous -- it walks the core models
    and the DTOs, and their declared fields add up to the model fields."""
    import groundinsight as gi
    from groundinsight.api import schemas

    names = dict(_public_classes())
    assert {"groundinsight.models.core_models.Bus", "groundinsight.api.schemas.BaseDTO"} <= set(names)
    assert _declared_fields(gi.Bus) == _model_fields(gi.Bus)
    assert "dto_schema_version" in _declared_fields(schemas.BaseDTO)
    assert "dto_schema_version" not in _declared_fields(schemas.KeyFigureRowDTO)


# ---------------------------------------------------------------------------
# STD1 -- every object of groundinsight.__all__ has examples that run
# ---------------------------------------------------------------------------

#: Objects whose examples need not run the object, and why. Four reasons
#: count: the example writes or reads a file, draws a figure or needs the
#: optional pandapower extra (it is then marked ``+SKIP``), or the object is
#: deprecated and the example shows its replacement instead. The list
#: shrinks; it does not grow without one of these reasons.
EXAMPLES_NEED_NOT_RUN = {
    "get_pathfinder_cache_size": "deprecated, the example shows the replacement",
    "set_pathfinder_cache_size": "deprecated, the example shows the replacement",
    "plot_bus_voltages": "draws a figure",
    "plot_branch_currents": "draws a figure",
    "plot_bus_currents": "draws a figure",
    "plot_epr_transient": "draws a figure",
    "plot_branch_current_transient": "draws a figure",
    "from_pandapower": "needs the pandapower extra",
    "preview_pandapower_import": "needs the pandapower extra",
    "read_shortcircuit_results": "needs the pandapower extra",
    "apply_shortcircuit_characteristics": "needs the pandapower extra",
    "close_dbsession": "uses a database file",
    "save_bustype_to_db": "uses a database file",
    "load_bustypes_from_db": "uses a database file",
    "save_branchtype_to_db": "uses a database file",
    "load_branchtypes_from_db": "uses a database file",
    "save_network_to_db": "uses a database file",
    "load_network_from_db": "uses a database file",
    "migrate_database": "uses a database file",
    "needs_migration": "uses a database file",
    "start_dbsession": "uses a database file",
    "save_network_to_json": "uses a project file",
    "load_network_from_json": "uses a project file",
}


def _documented_api() -> Dict[str, Any]:
    """
    The functions, classes and modules of ``groundinsight.__all__``.

    Returns
    -------
    dict of str to object
        Name to object; the data constants of ``__all__`` (dicts, lists)
        carry no docstring of their own and are left out.
    """
    import groundinsight as gi

    return {
        name: getattr(gi, name)
        for name in gi.__all__
        if inspect.isclass(getattr(gi, name))
        or inspect.isfunction(inspect.unwrap(getattr(gi, name)))
        or inspect.ismodule(getattr(gi, name))
    }


def _doctest_examples(name: str, obj: Any) -> list:
    """
    The doctest examples of ``obj``'s own docstring.

    Parameters
    ----------
    name : str
        Name used for the doctest.
    obj : object
        Function, class or module.

    Returns
    -------
    list of doctest.Example
        The examples, without those of members.
    """
    import doctest

    finder = doctest.DocTestFinder(recurse=False)
    return [example for test in finder.find(obj, name, globs={}) for example in test.examples]


def _runs_the_object(name: str, obj: Any, examples: list) -> bool:
    """
    Whether one example that is not ``+SKIP`` exercises ``obj``.

    An import does not count. For a function the statement has to name it;
    a class or module is exercised by any other statement (an example
    usually works with an instance).

    Parameters
    ----------
    name : str
        The name of the object in ``groundinsight.__all__``.
    obj : object
        Function, class or module.
    examples : list of doctest.Example
        Its examples.

    Returns
    -------
    bool
        ``True`` when such an example exists.
    """
    import doctest

    for example in examples:
        source = example.source.strip()
        if example.options.get(doctest.SKIP) or source.startswith(("import ", "from ")):
            continue
        if inspect.isclass(obj) or inspect.ismodule(obj) or name in source:
            return True
    return False


def test_every_public_object_has_an_examples_section():
    """numpydoc's parser finds an ``Examples`` section on every object."""
    NumpyDocString = pytest.importorskip("numpydoc.docscrape").NumpyDocString

    missing = []
    for name, obj in _documented_api().items():
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            section = NumpyDocString(inspect.getdoc(obj) or "")["Examples"]
        if not "".join(section).strip():
            missing.append(name)
    assert not missing, missing


def test_public_examples_run_unless_they_need_files_plots_or_pandapower():
    """Second method, doctest's own finder: an example of every object runs
    it (not ``+SKIP``, not just an import), except for the listed reasons."""
    not_run = [
        name
        for name, obj in _documented_api().items()
        if name not in EXAMPLES_NEED_NOT_RUN
        and not _runs_the_object(name, obj, _doctest_examples(name, obj))
    ]
    assert not not_run, not_run


def test_the_skip_allowlist_names_only_skipped_public_objects():
    """The allowlist shrinks: an entry whose examples run, or that is not
    public any more, has to go."""
    api = _documented_api()
    stale = [
        name
        for name in EXAMPLES_NEED_NOT_RUN
        if name not in api or _runs_the_object(name, api[name], _doctest_examples(name, api[name]))
    ]
    assert not stale, stale


# ---------------------------------------------------------------------------
# STD2 -- module header and module docstring
# ---------------------------------------------------------------------------

#: The trees the conventions apply to: package, tests, maintainer scripts,
#: the GUI distribution, the how-to scripts of the documentation.
LINT_TREES = ("src/groundinsight", "tests", "scripts", "gui", "docs/snippets")


def _python_modules() -> List[str]:
    """
    Every Python file of the lint trees, relative to the repository root.

    Returns
    -------
    list of str
        POSIX paths, sorted; caches and hidden directories left out.
    """
    found = []
    for tree in LINT_TREES:
        for path in (ROOT / tree).rglob("*.py"):
            rel = path.relative_to(ROOT)
            if any(part.startswith((".", "__pycache__")) for part in rel.parts):
                continue
            found.append(rel.as_posix())
    return sorted(found)


def _accepted_headers(rel: str) -> Set[str]:
    """
    The first lines a module may start with.

    Package modules name their path inside the package (``# utils/serialize.py``,
    also ``# groundinsight/utils/serialize.py``); everything else names its
    path from the repository root (``# tests/test_json.py``).

    Parameters
    ----------
    rel : str
        Repository-relative POSIX path of the module.

    Returns
    -------
    set of str
        The accepted header comments.
    """
    if rel.startswith("src/groundinsight/"):
        inner = rel[len("src/groundinsight/"):]
        return {f"# {inner}", f"# groundinsight/{inner}"}
    if rel.startswith("gui/groundinsight_gui/"):
        return {f"# {rel[len('gui/'):]}"}
    return {f"# {rel}"}


@pytest.mark.parametrize("rel", _python_modules())
def test_module_starts_with_its_path_and_has_a_docstring(rel):
    """Line 1 is ``# <path>.py``; the module docstring says why it exists."""
    import ast

    text = (ROOT / rel).read_text(encoding="utf-8")
    first = text.split("\n", 1)[0]
    assert first in _accepted_headers(rel), f"line 1 of {rel} is {first!r}"
    docstring = ast.get_docstring(ast.parse(text))
    assert docstring and len(docstring.split()) >= 5, f"{rel} has no module docstring"


def test_module_headers_name_their_own_file():
    """Second method: the path in each header, resolved from the package
    root or the repository root, is the file itself."""
    bases = (ROOT / "src" / "groundinsight", ROOT / "src", ROOT / "gui", ROOT)
    wrong = []
    for rel in _python_modules():
        first = (ROOT / rel).read_text(encoding="utf-8").split("\n", 1)[0]
        named = first[2:] if first.startswith("# ") else ""
        if not any((base / named).resolve() == (ROOT / rel).resolve() for base in bases if named):
            wrong.append(f"{rel}: {first!r}")
    assert not wrong, wrong


# ---------------------------------------------------------------------------
# D4 -- docstrings and comments state contract and reason, not history
# ---------------------------------------------------------------------------

#: The shipped code the rule applies to. The test suite is left out on
#: purpose: its files and docstrings are organised by audit pass and finding.
SHIPPED_TREES = ("src/groundinsight", "gui/groundinsight_gui", "scripts")

#: Phrases that only ever date a change. A finding ID on its own may stay
#: where it names the rule a test pins ("guards against PH3"); the audit pass,
#: release or decision that changed something belongs in CHANGELOG.md.
HISTORY_MARKERS = re.compile(
    r"\baudit[- ]pass(?:es)?\b"
    r"|\baudit finding\b"
    r"|\bdecision [A-Z]{1,3}\d+\b"
    r"|\b(?:until|up to|since|before|as of)\s+(?:v|version\s+)?\d+\.\d+(?:\.\d+)?\b"
    r"|\b(?:added|changed|introduced)\s+in\s+v?\d+\.\d+"
    r"|\bpre-v?\d+\.\d+"
    r"|\b(?:until|since|before|after|up to)\s+pass\s+\d+\b"
    r"|\bformerly\b|\bhistorically\b|\bhistorical behaviou?r\b"
    r"|\bused to be\b",
    re.IGNORECASE,
)


def _prose_blocks(text: str) -> Iterator[Tuple[int, str]]:
    """
    The comments and docstrings of a module, each with whitespace collapsed.

    Consecutive comment lines form one block, so a phrase broken across two
    lines (``audit`` / ``pass 19``) is still one phrase.

    Parameters
    ----------
    text : str
        Source of a Python module.

    Yields
    ------
    tuple of (int, str)
        First line of the block and its text.
    """
    import ast
    import io
    import tokenize

    run: List[str] = []
    start = last = 0
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type != tokenize.COMMENT:
            continue
        line, words = token.start[0], token.string.lstrip("#:").strip()
        if run and line == last + 1:
            run.append(words)
        else:
            if run:
                yield start, " ".join(run)
            run, start = [words], line
        last = line
    if run:
        yield start, " ".join(run)
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            yield first.value.lineno, " ".join(first.value.value.split())


def _history_narration(sources: Dict[str, str]) -> List[str]:
    """
    Every history marker in the comments and docstrings of ``sources``.

    Parameters
    ----------
    sources : dict of str to str
        Module path to module source.

    Returns
    -------
    list of str
        ``"path:line: marker"`` per hit.
    """
    return [
        f"{rel}:{line}: {match.group(0)!r}"
        for rel, text in sources.items()
        for line, block in _prose_blocks(text)
        for match in HISTORY_MARKERS.finditer(block)
    ]


def test_shipped_docstrings_and_comments_carry_no_history():
    """No comment or docstring of the shipped code dates a change (D4)."""
    sources = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for tree in SHIPPED_TREES
        for path in sorted((ROOT / tree).rglob("*.py"))
        if "__pycache__" not in path.parts
    }
    assert len(sources) > 50
    found = _history_narration(sources)
    assert not found, "history belongs in CHANGELOG.md:\n" + "\n".join(found)


def test_the_history_check_finds_a_planted_passage():
    """Second method: planted passages in both places, one of them broken
    across two comment lines, are each found; runtime wording is not."""
    planted = (
        "# Until 0.6.1 the matrix was dense. It is used to\n"
        "# build Y; since audit\n"
        "# pass 19 it is sparse.\n"
        "def f():\n"
        '    """Return Y (audit finding P2). Used to anchor the group."""\n'
    )
    found = _history_narration({"planted.py": planted})
    assert found == [
        "planted.py:1: 'Until 0.6.1'",
        "planted.py:1: 'audit pass'",
        "planted.py:5: 'audit finding'",
    ]


# ---------------------------------------------------------------------------
# STD8 -- the lint job of CI runs the pinned linters and blocks
# ---------------------------------------------------------------------------


def _ci_jobs() -> Dict[str, Any]:
    """
    The jobs of the CI workflow.

    Returns
    -------
    dict
        Job id to job definition.
    """
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]


def test_the_ci_lint_job_runs_the_pinned_linters():
    """The ``lint`` job installs exactly the dev-group versions and runs both
    linters over the lint trees, without ``continue-on-error``."""
    job = _ci_jobs()["lint"]
    assert not job.get("continue-on-error")
    script = "\n".join(step.get("run", "") for step in job["steps"])
    dev = _pyproject()["tool"]["poetry"]["group"]["dev"]["dependencies"]
    for linter in LINTERS:
        assert f"{linter}=={dev[linter]}" in script, f"lint job does not pin {linter} {dev[linter]}"
    assert re.search(r"ruff check src tests scripts gui docs/snippets\b", script)
    assert "numpydoc lint" in script
    assert all(not step.get("continue-on-error") for step in job["steps"])


# ---------------------------------------------------------------------------
# STD5 -- the package logs instead of printing; warnings carry a category
# ---------------------------------------------------------------------------


def _package_trees() -> Iterator[Tuple[str, Any]]:
    """
    The parsed modules of the package.

    Yields
    ------
    tuple of (str, ast.Module)
        Path below ``src/groundinsight`` and its syntax tree.
    """
    import ast

    package = ROOT / "src" / "groundinsight"
    for path in sorted(package.rglob("*.py")):
        yield path.relative_to(package).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def test_the_package_does_not_print():
    """Library output goes through ``logging`` or a return value; ``print``
    would write into the stdout of every application that imports it."""
    import ast

    found = [
        f"{rel}:{node.lineno}"
        for rel, tree in _package_trees()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"
    ]
    assert not found, found


#: Categories a ``warnings.warn`` call of the package may name besides the
#: package's own: the standard ones for announcing a removal.
_STANDARD_CATEGORIES = {"DeprecationWarning", "PendingDeprecationWarning", "FutureWarning"}

#: ``warnings.warn`` calls without a category argument, with the reason.
_WARN_WITHOUT_CATEGORY = {
    # emit() checks that its category is a GroundinsightWarning subclass and
    # warns with an instance of it, which carries the category itself.
    "diagnostics.py": "emit",
}


def test_every_warning_names_a_package_or_removal_category():
    """A ``UserWarning`` cannot be filtered or collected as a diagnostic; every
    warning of the package is a GroundinsightWarning subclass or announces a
    removal."""
    import ast

    import groundinsight as gi

    package_categories = {
        name for name in dir(gi)
        if isinstance(getattr(gi, name), type) and issubclass(getattr(gi, name), gi.GroundinsightWarning)
    }
    allowed = package_categories | _STANDARD_CATEGORIES
    wrong = []
    for rel, tree in _package_trees():
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "warn"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "warnings"
            ):
                continue
            category = node.args[1] if len(node.args) > 1 else next(
                (k.value for k in node.keywords if k.arg == "category"), None
            )
            if category is None:
                if rel not in _WARN_WITHOUT_CATEGORY:
                    wrong.append(f"{rel}:{node.lineno}: no category")
                continue
            name = category.attr if isinstance(category, ast.Attribute) else getattr(category, "id", None)
            if name not in allowed:
                wrong.append(f"{rel}:{node.lineno}: {ast.unparse(category)}")
    assert not wrong, wrong


# ---------------------------------------------------------------------------
# STD10 -- CHANGELOG [Unreleased] in Keep-a-Changelog order
# ---------------------------------------------------------------------------

#: Keep a Changelog's sections in its order, with the house additions:
#: ``Breaking`` first, ``Docs`` and ``Internal`` last.
CHANGELOG_ORDER = (
    "Breaking", "Added", "Changed", "Deprecated", "Removed", "Fixed", "Security", "Docs", "Internal",
)


def _unreleased_sections() -> List[str]:
    """
    The ``###`` headings of the ``[Unreleased]`` section of CHANGELOG.md.

    Returns
    -------
    list of str
        In the order they appear.
    """
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    block = re.search(r"^## \[Unreleased\][^\n]*\n(.*?)(?=^## )", text, re.S | re.M)
    assert block, "CHANGELOG.md has no [Unreleased] section"
    return re.findall(r"^### (.+?)\s*$", block.group(1), re.M)


def test_the_unreleased_changelog_sections_follow_keep_a_changelog():
    """Known headings only, each once, in the documented order."""
    sections = _unreleased_sections()
    assert sections, "the [Unreleased] section has no ### headings"
    unknown = [s for s in sections if s not in CHANGELOG_ORDER]
    assert not unknown, f"unknown sections {unknown}; known: {CHANGELOG_ORDER}"
    assert len(sections) == len(set(sections)), f"a section appears twice: {sections}"
    positions = [CHANGELOG_ORDER.index(s) for s in sections]
    assert positions == sorted(positions), f"out of order: {sections}"


# ---------------------------------------------------------------------------
# STD11 -- the documented Python floor is the declared one
# ---------------------------------------------------------------------------

#: How the documentation states a minimum Python version.
_FLOOR_STATEMENT = re.compile(
    r"Python\W{0,3}(3\.\d+)\W{0,3}(?:or (?:newer|later|higher)|\+)|Python\s*(?:>=|≥)\s*(3\.\d+)",
    re.IGNORECASE,
)


def test_the_documented_python_floor_is_the_declared_one():
    """README, documentation pages and the GUI distribution state the floor
    of ``pyproject.toml`` -- and the README states it at all."""
    floor = _python_floor()
    stated: Dict[str, Set[str]] = {}
    pages = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
    for page in pages:
        for match in _FLOOR_STATEMENT.finditer(page.read_text(encoding="utf-8")):
            stated.setdefault(page.relative_to(ROOT).as_posix(), set()).add(match.group(1) or match.group(2))
    assert "README.md" in stated, "the README does not state the supported Python versions"
    wrong = {page: versions for page, versions in stated.items() if versions != {floor}}
    assert not wrong, f"floor is {floor}: {wrong}"
    gui = tomllib.loads((ROOT / "gui" / "pyproject.toml").read_text(encoding="utf-8"))
    assert gui["project"]["requires-python"] == f">={floor}"


# ---------------------------------------------------------------------------
# STD3 -- one name per concept in the public API (USE8)
# ---------------------------------------------------------------------------

#: Spelling that was replaced -> the canonical parameter name.
CANONICAL_NAMES = {
    "fault_name": "fault",
    "u_max": "u_limit",
    "t_k": "t_k_s",
    "n": "n_factor",
    "f": "frequency",
    "freq": "frequency",
}

#: Old spellings still accepted, with a DeprecationWarning, per public
#: callable. The list only shrinks: a new alias needs a decision, and removing
#: an alias removes its line here.
DEPRECATED_ALIASES: Dict[str, Dict[str, str]] = {
    "groundinsight.BusResponse.driving_point": {"freq": "frequency"},
    "groundinsight.BusResponse.voltages": {"freq": "frequency"},
    "groundinsight.BusResponse.voltages_no_mutual": {"freq": "frequency"},
    "groundinsight.BusResponse.worst_case_electrode": {"freq": "frequency"},
    "groundinsight.Network.set_active_fault": {"fault_name": "fault"},
    "groundinsight.Network.sources_reaching": {"fault_name": "fault"},
    "groundinsight.TransientStudy": {"fault_name": "fault"},
    "groundinsight.admissible_short_circuit_current": {"t_k": "t_k_s"},
    "groundinsight.apply_shortcircuit_characteristics": {"fault_name": "fault"},
    "groundinsight.check_conductor_limits": {
        "fault_name": "fault", "t_k": "t_k_s", "n": "n_factor", "f": "frequency",
    },
    "groundinsight.check_node_limits": {
        "fault_name": "fault", "t_k": "t_k_s", "n": "n_factor", "f": "frequency",
    },
    "groundinsight.find_max_rho_scaling": {"fault_name": "fault", "u_max": "u_limit"},
    "groundinsight.iec60909_m": {"f": "frequency", "t_k": "t_k_s"},
    "groundinsight.read_shortcircuit_results": {"f": "frequency"},
    "groundinsight.resolve_fault_sc_characteristics": {"fault_name": "fault"},
    "groundinsight.run_fault": {"fault_name": "fault"},
    "groundinsight.set_active_fault": {"fault_name": "fault"},
    "groundinsight.thermal_equivalent_current": {"n": "n_factor"},
}

#: Signatures that contain an old spelling for another reason.
NOT_A_PARAMETER_RENAME = {
    # A stored record: the field names the fault the short-circuit data was
    # read for, and renaming it changes the persisted form (an API decision).
    "groundinsight.FaultShortCircuitData": {"fault_name"},
}


def _public_callables() -> Iterator[Tuple[str, Any]]:
    """
    The public functions, classes and methods of the package.

    Yields
    ------
    tuple of (str, callable)
        Qualified name and object. Classes are yielded with their constructor;
        methods are those a package class defines itself, public ones only.
    """
    for module_name in ("groundinsight", "groundinsight.api"):
        module = importlib.import_module(module_name)
        for name in module.__all__:
            obj = getattr(module, name)
            qualified = f"{module_name}.{name}"
            if inspect.isclass(obj):
                if issubclass(obj, BaseException):
                    continue
                yield qualified, obj
                for klass in obj.__mro__:
                    if not getattr(klass, "__module__", "").startswith("groundinsight"):
                        continue
                    for member_name, member in vars(klass).items():
                        if member_name.startswith("_") or not callable(getattr(member, "__func__", member)):
                            continue
                        yield f"{qualified}.{member_name}", getattr(member, "__func__", member)
            elif callable(obj):
                yield qualified, obj


def test_public_signatures_use_the_canonical_parameter_names():
    """No public signature spells a concept the replaced way."""
    wrong = []
    for qualified, obj in _public_callables():
        try:
            parameters = inspect.signature(obj).parameters
        except (TypeError, ValueError):
            continue
        for old in set(parameters) & set(CANONICAL_NAMES):
            if old not in NOT_A_PARAMETER_RENAME.get(qualified, set()):
                wrong.append(f"{qualified}({old}=...) -> {CANONICAL_NAMES[old]}")
    assert not wrong, wrong


def test_the_deprecated_aliases_are_exactly_the_allowlist():
    """Second method: what ``renamed_parameters`` actually accepts, read off
    the wrappers, is the list above -- no alias more, none forgotten."""
    found: Dict[str, Dict[str, str]] = {}
    for qualified, obj in _public_callables():
        target = obj.__init__ if inspect.isclass(obj) else obj
        mapping = getattr(target, "__deprecated_parameters__", None)
        if mapping:
            found[qualified] = dict(mapping)
    assert found == DEPRECATED_ALIASES
    for mapping in DEPRECATED_ALIASES.values():
        assert all(CANONICAL_NAMES[old] == new for old, new in mapping.items())


# ---------------------------------------------------------------------------
# STD7 -- complexity ratchet
# ---------------------------------------------------------------------------

RATCHET = ROOT / "tests" / "data" / "complexity_ratchet.toml"


def _complexities() -> Dict[str, int]:
    """
    The radon cyclomatic complexity of every function of the package.

    Returns
    -------
    dict of str to int
        ``<path below src/groundinsight>::<qualified name>`` to complexity;
        nested functions are named after their enclosing one.
    """
    radon_complexity = pytest.importorskip("radon.complexity")

    def walk(block, prefix):
        name = f"{prefix}.{block.name}" if prefix else block.fullname
        yield name, block.complexity
        for closure in block.closures:
            yield from walk(closure, name)

    package = ROOT / "src" / "groundinsight"
    table: Dict[str, int] = {}
    for path in sorted(package.rglob("*.py")):
        rel = path.relative_to(package).as_posix()
        for block in radon_complexity.cc_visit(path.read_text(encoding="utf-8")):
            if not hasattr(block, "closures"):  # a class: its methods come separately
                continue
            for name, complexity in walk(block, ""):
                key = f"{rel}::{name}"
                table[key] = max(table.get(key, 0), complexity)
    return table


def test_no_function_is_more_complex_than_its_ratchet():
    """A function above the limit must be listed, at no more than its
    recorded complexity."""
    ratchet = tomllib.loads(RATCHET.read_text(encoding="utf-8"))
    limit, recorded = ratchet["limit"], ratchet["functions"]
    worse = [
        f'"{key}" = {complexity}  (recorded: {recorded.get(key, f"not listed, limit {limit}")})'
        for key, complexity in sorted(_complexities().items())
        if complexity > limit and complexity > recorded.get(key, limit)
    ]
    assert not worse, "simplify, or record a reasoned exception:\n" + "\n".join(worse)


def test_the_ratchet_lists_only_functions_above_the_limit():
    """The list only shrinks: an entry for a function that is gone or no
    longer above the limit must be removed."""
    ratchet = tomllib.loads(RATCHET.read_text(encoding="utf-8"))
    current = _complexities()
    stale = [
        key for key in ratchet["functions"]
        if current.get(key, 0) <= ratchet["limit"]
    ]
    assert not stale, f"remove from {RATCHET.name}: {stale}"
