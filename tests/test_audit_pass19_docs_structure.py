# tests/test_audit_pass19_docs_structure.py
"""
Audit pass 19 -- the structure of the documentation site, checked statically.

Finding IDs: D1 (Diataxis structure: getting started, how-to guides,
understanding the model, examples, reference, project), DOC9 (links that do
not reach their target -- the strict build did not check anchors, and it does
not look into notebooks at all), DOC12 (flat navigation; the audit log was a
fifth of the search index).

A ``mkdocs build`` in a subprocess takes tens of seconds and needs the
optional ``docs`` group, which the CI test job does not install. The gates
below therefore read ``mkdocs.yml`` and the files under ``docs/`` directly;
the docs job of the CI builds the site strictly on top of them. They use only
the standard library and PyYAML (dev group).
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

#: Directories under ``docs/`` that hold build code, included sources or
#: static assets rather than pages.
NON_PAGE_DIRS = {"hooks", "snippets", "javascripts"}

#: Top-level navigation sections in Diataxis order (decision D1).
NAV_ORDER = [
    "Home",
    "Getting started",
    "How-to guides",
    "Understanding the model",
    "Examples",
    "Reference",
    "Project",
]


def _mkdocs_config():
    """
    ``mkdocs.yml`` as a dictionary.

    The ``!!python/name:`` tags of the configuration are read as ``None``;
    the tests do not need the objects they name.

    Returns
    -------
    dict
        The parsed configuration.
    """
    yaml = pytest.importorskip("yaml")

    class _Loader(yaml.SafeLoader):
        pass

    _Loader.add_multi_constructor(
        "tag:yaml.org,2002:python/", lambda loader, suffix, node: None
    )
    return yaml.load((ROOT / "mkdocs.yml").read_text(encoding="utf-8"), Loader=_Loader)


def _nav_entries(nav, trail=()):
    """
    Every page of a navigation tree with the section titles above it.

    Parameters
    ----------
    nav : list
        The ``nav`` list of ``mkdocs.yml`` or a nested part of it.
    trail : tuple of str
        Titles of the enclosing sections.

    Returns
    -------
    list of tuple
        ``(page path, section trail)`` in navigation order.
    """
    entries = []
    for item in nav:
        if isinstance(item, str):
            entries.append((item, trail))
            continue
        for title, value in item.items():
            if isinstance(value, list):
                entries.extend(_nav_entries(value, trail + (title,)))
            else:
                entries.append((value, trail))
    return entries


def _documentation_pages():
    """
    Every page source under ``docs/``: Markdown files and notebooks.

    Returns
    -------
    set of str
        Paths relative to ``docs/``, with forward slashes.
    """
    pages = set()
    for path in DOCS.rglob("*"):
        relative = path.relative_to(DOCS)
        if relative.parts[0] in NON_PAGE_DIRS or not path.is_file():
            continue
        if path.suffix in (".md", ".ipynb"):
            pages.add(relative.as_posix())
    return pages


def _front_matter(text):
    """
    The YAML front matter of a Markdown page.

    Parameters
    ----------
    text : str
        Page source.

    Returns
    -------
    dict
        The front matter, empty if the page has none.
    """
    yaml = pytest.importorskip("yaml")
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    return yaml.safe_load(match.group(1)) if match else {}


# --------------------------------------------------------------------------
# D1 / DOC12 -- navigation
# --------------------------------------------------------------------------


def test_every_page_is_in_the_nav_and_every_nav_entry_exists():
    """No orphan pages and no entries that point at a missing file.

    ``validation: omitted_files`` makes the strict build fail on the first
    half as well; this is the static version of it, plus the second half,
    which a build reports only as "not found among documentation files".
    """
    config = _mkdocs_config()
    in_nav = [page for page, _ in _nav_entries(config["nav"])]
    duplicates = sorted({page for page in in_nav if in_nav.count(page) > 1})
    assert duplicates == []
    missing = sorted(page for page in in_nav if not (DOCS / page).is_file())
    assert missing == []
    orphans = sorted(_documentation_pages() - set(in_nav))
    assert orphans == []


def test_nav_follows_the_diataxis_order():
    """Decision D1: the top-level sections, in learning order."""
    config = _mkdocs_config()
    titles = [next(iter(item)) for item in config["nav"] if isinstance(item, dict)]
    assert titles == NAV_ORDER


def test_api_reference_sits_under_reference():
    """The API pages are reference material, one level below "Reference"."""
    config = _mkdocs_config()
    api = [trail for page, trail in _nav_entries(config["nav"]) if page.startswith("api/")]
    assert api, "no API pages in the nav"
    assert all(trail[:2] == ("Reference", "API") for trail in api), api


def test_strict_build_validates_links_and_anchors():
    """DOC9: a link to a missing page or a missing anchor fails the strict
    build, and so does a page that nobody can reach from the nav."""
    validation = _mkdocs_config().get("validation") or {}
    for key in ("omitted_files", "unrecognized_links", "anchors"):
        assert validation.get(key) == "warn", (key, validation)


def test_audit_log_is_kept_out_of_search_and_sits_under_project():
    """DOC12: the audit log was 18.7 % of the search index. It stays on the
    site -- it records how each finding was established -- but under
    "Project", and the search plugin skips it."""
    text = (DOCS / "audit-log.md").read_text(encoding="utf-8")
    assert _front_matter(text).get("search", {}).get("exclude") is True
    config = _mkdocs_config()
    trails = {page: trail for page, trail in _nav_entries(config["nav"])}
    assert trails["audit-log.md"][:1] == ("Project",)
