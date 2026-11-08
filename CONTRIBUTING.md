# Contributing to groundinsight

Contributions are welcome: bug reports, documentation, tests and code. For
anything beyond a small fix, please open an issue first so the approach can
be agreed before you invest the time. This page describes how to set up a
working copy, how to run the tests and build the documentation, and the
conventions the code base follows — a pull request is reviewed against them.

## Setting up a working copy

`groundinsight` needs Python 3.12 or newer and uses
[Poetry](https://python-poetry.org/) for its environments.

```bash
git clone https://github.com/Ce1ectric/groundinsight.git
cd groundinsight
poetry install --with dev,docs --extras pandapower
```

The `dev` group holds the test tools, the `docs` group the documentation
toolchain, and the `pandapower` extra the optional importer, which the test
suite and the example notebooks exercise.

## Running the tests

```bash
poetry run pytest                          # the suite and the doctests
GROUNDINSIGHT_CI=1 poetry run pytest       # as in CI
poetry run pytest -m "not slow"            # without the size and timing checks
poetry run pytest --cov=groundinsight      # with coverage
```

Locally, a test that needs an optional package or tool that is not
installed skips. With `GROUNDINSIGHT_CI=1`, as in the CI jobs, such a skip
fails instead, because there it means the environment is incomplete.

## Building the documentation

```bash
poetry run mkdocs serve                    # live preview
poetry run mkdocs build --strict           # what CI runs
```

`--strict` turns every broken link or anchor into an error. The build
executes the example notebooks in `docs/examples/`, which takes about a
minute the first time and is cached afterwards. Three rules keep the pages
honest, and the test suite checks all of them:

- Example code of the pages lives in `docs/snippets/*.py` and is included
  with `--8<-- "docs/snippets/<file>.py:<section>"`; the tests run the
  snippets and compare what they print with what the page shows.
- Every `python` block on a page runs, or starts with `# fragment` (or
  `# fragment: <what it assumes>`) to mark it as an excerpt.
- Notebooks are committed without outputs. The build runs them, so a page
  always shows the output of the code next to it.

## Language

Code, comments, docstrings, documentation, commit messages and changelog
entries are written in English.

## Code conventions

**Module header.** The first line of a module names its path, followed by a
module docstring that says what the module is for and why it is built the
way it is. Inside the package the path is relative to `src/groundinsight/`
(`# analysis/response.py`), elsewhere relative to the repository root
(`# tests/test_audit_pass19_docs_structure.py`).

**Docstrings.** Every public module, class, function and method has a
docstring in [NumPy style](https://numpydoc.readthedocs.io/en/latest/format.html)
— `Parameters`, `Returns`, `Raises`, `Warns`, `Notes`, `Examples` as they
apply. A docstring states the contract and the reasons for it. The history
of a change belongs in `CHANGELOG.md`, not in the docstring.

**Type hints.** Use the `typing` forms the code base uses throughout:
`Optional[float]`, `List[str]`, `Dict[str, complex]`, `Tuple[int, ...]`.

**Logging, not `print`.** Library code reports progress through
`logging.getLogger(__name__)`. The package attaches a `NullHandler`, so it
stays quiet until the user opts in with `gi.set_log_level(...)`. A library
function never prints.

**Errors.** Input the package cannot use raises the built-in exception that
fits — `ValueError` for a value, `TypeError` for a type, `RuntimeError` for a
computation that failed — with a message that names the element, the value
and what to do about it. Where a caller needs to tell errors apart, the
package defines a subclass of the built-in it specialises, so an existing
`except ValueError` keeps working: `ProjectError` and `CommandError` of the
service layer are `ValueError`s, `JobFailed` and `StudyCancelled` are
`RuntimeError`s. A result that only looks plausible is worse than an error:
an input that cannot be computed raises, it does not return zeros.

**Diagnostics.** A condition that does not stop the calculation but changes
how a result must be read — a heuristic phase split, a frequency the fault
scalings do not cover — is a warning with a stable code. Emit it with
`groundinsight.diagnostics.emit`:

```python
# fragment: inside the package, `branch` is the branch concerned
from groundinsight.diagnostics import ModelAssumptionWarning, emit

emit(
    ModelAssumptionWarning,
    f"Branch '{branch.name}' declares no phase impedance; ...",
    code="PHASE_PROXY",
    elements=[("branch", branch.name)],
)
```

The category is a subclass of `GroundinsightWarning` (`ModelAssumptionWarning`,
`InputWarning`, ...). `run_fault` keeps every warning of a run in
`Result.diagnostics`, where a program reads the code and the elements
instead of parsing the text. A new code is documented on the reference page
*Warnings and diagnostics* (`docs/reference/warnings.md`); a test checks that
every code of the package is listed there.

**Public and internal API.** The public API is what `groundinsight` and
`groundinsight.api` export in `__all__`; every exported name is rendered in
the API reference, which CI checks. Functions that other modules of the
package share but application code should not use carry no leading
underscore and start the second paragraph of their docstring with
"Internal API" — see [Internal API](docs/api/index.md#internal-api).
Underscore names stay inside the module that defines them; a test fails when
one module uses an underscore name of another.

## Tests first, and two methods for every number

- Write the test first. A bug fix starts with a test that fails on the
  current code and passes with the fix; say in the pull request that it
  failed before.
- Every numerical claim is checked by at least two independent methods: a
  closed form, an independent formulation of the same model, a second
  solver, or a measurement. `tests/_reference_mna.py`, for example, solves
  the documented model by modified nodal analysis in the physical sign frame,
  independently of the package's nodal solver. A test that compares the code
  with a number it produced last month checks nothing.
- What the documentation states, the tests reproduce: a page that shows a
  number gets a snippet or a notebook that computes it, and a test that
  computes it a second way.

## Changelog, breaking changes and deprecation

Every user-visible change gets an entry under `[Unreleased]` in
`CHANGELOG.md`, in its category: **Breaking**, **Added**, **Changed**,
**Deprecated**, **Removed**, **Fixed**, **Security**, **Docs** or
**Internal**. An entry starts with a bold sentence that says what changed,
then why, with the measured numbers where there are any.

- A change that can break working user code is listed under **Breaking** as
  well, with a migration note that says what to change.
- A deprecated feature keeps working and emits a `DeprecationWarning` for at
  least **two releases and six months** before it is removed. A renamed
  parameter keeps its old name as an alias with a warning for that period.

## Versions and releases

`groundinsight` uses calendar versioning, `YYYY.M.MICRO`: the year, the month
without a leading zero, and a counter that restarts at 0 every month
(`2026.11.0`, `2026.11.1`, `2026.12.0`). The maintainer cuts a release with

```bash
poetry run python scripts/release.py --dry-run     # show what would happen
poetry run python scripts/release.py               # next calendar version
```

The script sets the version in every place that records it (the library,
`CITATION.cff` and the GUI distribution), moves the `[Unreleased]` block of
the changelog into a dated section, commits, tags the commit and pushes it.
The release workflow then runs the tests on the tagged commit and publishes
to PyPI.

## Formatting and linting

Lint, do not reformat. Do not run a formatter over files your change does
not otherwise touch, and keep formatting changes out of functional pull
requests: a mass reformat hides the real change from review and from
`git blame`. Match the style of the code around your change.

## Commits and pull requests

- Commit subjects follow [Conventional Commits](https://www.conventionalcommits.org/):
  `fix(solver): ...`, `docs: ...`, `test: ...`, `perf(results): ...`. The
  body says why.
- One topic per pull request; the tests and the documentation of a change
  belong to the same pull request as the change.
- Run the test suite and the strict documentation build before you ask for a
  review.
