# docs/hooks/reference_cases.py
"""
MkDocs build hook: the reference-case table of the verification page.

The verification page lists the closed-form reference cases with what they
compare, under which conditions the closed form holds, the tolerance and the
deviation the documented version actually reaches. Writing that table by hand
is how it went stale before: the API page still described the ``en50522_chain``
case after its quantity had changed. This hook builds the table from
:data:`groundinsight.analysis.reference.REFERENCE_CASES` and one run of
:func:`groundinsight.run_reference_cases` (about 0.2 s) each time the site is
built, and puts it where the page carries the marker
``<!-- reference-cases-table -->``.

The package is imported from the ``src`` directory next to ``mkdocs.yml``,
the same tree mkdocstrings documents, and the hook refuses to run on a
different installed copy: a table generated from another version would
publish numbers the documented code does not produce. A failure raises, so a
strict build stops instead of publishing a page without its table.
"""

import logging
import sys
import types
from pathlib import Path

logger = logging.getLogger("mkdocs.hooks.reference_cases")


def _load_result_tables():
    """
    Load the sibling hook ``result_tables.py`` from its file.

    It writes the column tables of ``reference/results.md``. Until
    ``mkdocs.yml`` lists it under ``hooks:`` itself, :func:`on_page_markdown`
    hands every page on to it; running it twice is harmless, because the
    second run finds no marker left. The source is executed rather than
    imported, so nothing depends on ``sys.path`` and no bytecode is written
    into ``docs/``.

    Returns
    -------
    module
        The loaded hook module.
    """
    path = Path(__file__).resolve().with_name("result_tables.py")
    module = types.ModuleType("result_tables_hook")
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module


result_tables = _load_result_tables()

MARKER = "<!-- reference-cases-table -->"
_SRC = Path(__file__).resolve().parents[2] / "src"


def _import_groundinsight():
    """
    Import ``groundinsight`` from the source tree next to ``mkdocs.yml``.

    Returns
    -------
    module
        The ``groundinsight`` package.

    Raises
    ------
    RuntimeError
        If ``groundinsight`` was already imported from another location.
    """
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    import groundinsight

    location = Path(groundinsight.__file__).resolve()
    if _SRC not in location.parents:
        raise RuntimeError(
            f"The reference-case table would be built from {location}, not from "
            f"the documented source tree {_SRC}. Build the site in an environment "
            "where groundinsight is not imported from elsewhere."
        )
    return groundinsight


def _cell(text):
    """
    Make a string safe for a Markdown table cell.

    Parameters
    ----------
    text : str
        Cell content.

    Returns
    -------
    str
        The text with pipes escaped and line breaks removed.
    """
    return " ".join(str(text).split()).replace("|", r"\|")


def reference_cases_markdown():
    """
    The reference-case table as Markdown, one row per case.

    Returns
    -------
    str
        A Markdown table with the columns case, quantity, condition,
        closed form, model, relative deviation, tolerance and agreement.

    Examples
    --------
    >>> table = reference_cases_markdown()  # doctest: +SKIP
    >>> table.splitlines()[0]  # doctest: +SKIP
    '| case | quantity | condition | closed form | model | rel. deviation | tolerance | agrees |'
    """
    gi = _import_groundinsight()
    frame = gi.run_reference_cases()
    lines = [
        "| case | quantity | condition | closed form | model | rel. deviation | tolerance | agrees |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in frame.iter_rows(named=True):
        lines.append(
            "| `{case}` | {quantity} | {conditions} | {closed:.10g} | {model:.10g} "
            "| {deviation:.1e} | {tolerance:.0e} | {agrees} |".format(
                case=row["case"],
                quantity=_cell(row["quantity"]),
                conditions=_cell(row["conditions"]),
                closed=row["closed_form"],
                model=row["model"],
                deviation=row["rel_deviation"],
                tolerance=row["tolerance"],
                agrees="yes" if row["agrees"] else "**no**",
            )
        )
    footer = (
        f"\n*Generated when this site was built, with groundinsight "
        f"{gi.__version__}.*\n"
    )
    return "\n".join(lines) + "\n" + footer


def on_page_markdown(markdown, *, page, config, files):
    """
    Replace the table marker on a page with the generated table.

    Parameters
    ----------
    markdown : str
        Markdown source of the page.
    page, config, files
        Passed by MkDocs; not used.

    Returns
    -------
    str
        The source with the marker replaced, or unchanged without a marker.
    """
    markdown = result_tables.render(markdown)
    if MARKER not in markdown:
        return markdown
    logger.debug("Generating the reference-case table")
    return markdown.replace(MARKER, reference_cases_markdown())
