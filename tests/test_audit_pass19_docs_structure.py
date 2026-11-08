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


# --------------------------------------------------------------------------
# DOC9 -- links and anchors, statically
# --------------------------------------------------------------------------

_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
_FENCE = re.compile(r"^( *)(```|~~~).*?^\1\2", re.M | re.S)
_EXPLICIT_ID = re.compile(r"\{\s*#([\w-]+)[^}]*\}")


def _slugify(text):
    """
    The heading id the ``toc`` extension gives a heading text.

    Same algorithm as ``markdown.extensions.toc.slugify`` (NFKD, ASCII, drop
    everything that is neither a word character, whitespace nor a hyphen,
    lower case, runs of whitespace and hyphens become one hyphen), applied to
    the heading text with its inline markup removed.

    Parameters
    ----------
    text : str
        Heading source text.

    Returns
    -------
    str
        The id.
    """
    import unicodedata

    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links -> their text
    text = text.replace("`", "").replace("*", "").replace("$", "")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text)


def _anchors(relative):
    """
    The anchor ids a Markdown page provides, or ``None`` if they cannot be
    determined statically (mkdocstrings pages, pages that include Markdown).

    Parameters
    ----------
    relative : str
        Page path relative to ``docs/``.

    Returns
    -------
    set of str or None
        Heading ids (with the ``_1`` suffixes of repeated headings) and
        explicit ``{ #id }`` attributes.
    """
    text = (DOCS / relative).read_text(encoding="utf-8")
    if re.search(r"^:::", text, re.M) or re.search(r'--8<-- "[^"]+\.md"', text):
        return None
    body = _FENCE.sub("", text)
    ids = set(_EXPLICIT_ID.findall(body))
    seen = {}
    for match in re.finditer(r"^#{1,6}\s+(.*?)\s*$", body, re.M):
        heading = match.group(1)
        explicit = _EXPLICIT_ID.search(heading)
        if explicit:
            ids.add(explicit.group(1))
            continue
        slug = _slugify(heading)
        count = seen.get(slug, 0)
        ids.add(slug if count == 0 else f"{slug}_{count}")
        seen[slug] = count + 1
    return ids


def _page_links(relative):
    """
    Relative links of a Markdown page outside code blocks.

    Parameters
    ----------
    relative : str
        Page path relative to ``docs/``.

    Returns
    -------
    list of str
        Link targets as written.
    """
    text = _FENCE.sub("", (DOCS / relative).read_text(encoding="utf-8"))
    return [
        target
        for target in _LINK.findall(text)
        if not re.match(r"^[a-z][a-z0-9+.-]*:", target)  # http:, mailto: ...
    ]


def test_markdown_links_reach_existing_pages_and_anchors():
    """Every relative link of every Markdown page points at a file under
    ``docs/`` and, where it names one, at an anchor that page has. The docs
    job finds the same with ``validation`` and ``--strict``; this runs with
    the test suite, which does not build the site."""
    broken = []
    for page in sorted(p for p in _documentation_pages() if p.endswith(".md")):
        for target in _page_links(page):
            path, _, anchor = target.partition("#")
            if path:
                resolved = ((DOCS / page).parent / path).resolve()
                try:
                    relative = resolved.relative_to(DOCS.resolve()).as_posix()
                except ValueError:
                    broken.append((page, target, "outside docs/"))
                    continue
                if not resolved.is_file():
                    broken.append((page, target, "no such file"))
                    continue
            else:
                relative = page
            if anchor and relative.endswith(".md"):
                ids = _anchors(relative)
                if ids is not None and anchor not in ids:
                    broken.append((page, target, "no such anchor"))
    assert broken == []


# --------------------------------------------------------------------------
# D1 -- the Concepts page split into "Understanding the model"
# --------------------------------------------------------------------------

#: The section ids of the former ``docs/concepts.md``, as the site rendered
#: them before the split.
CONCEPTS_ANCHORS = [
    "problem-statement", "objects", "impedance-formulas",
    "which-values-a-formula-may-produce", "direct-current-f-0",
    "three-singularities-told-apart-numerically",
    "a-finite-reactance-has-no-reading-at-dc",
    "an-ideal-bond-is-a-short-circuit-and-a-short-circuit-has-no-admittance",
    "every-transient-study-contains-dc", "what-a-source-and-a-fault-need-at-dc",
    "nodal-admittance-formulation", "path-finding", "derived-quantities",
    "earth-potential-rise-epr", "branch-currents", "reduction-factor",
    "reduction-factor-on-a-current-basis",
    "characterising-a-location-without-its-electrode",
    "which-one-is-the-en-50522-reduction-factor",
    "the-two-factors-are-not-two-computations-of-one-number",
    "splitting-the-network-at-the-fault",
    "frequency-dependence-of-the-reduction-factor", "grounding-impedance",
    "active-flag-and-outage-studies", "inverse-rho-analysis",
    "transient-simulations", "worked-example",
    "summary-of-the-calculation-pipeline",
]

EXPLANATION_PAGES = [
    "explanation/index.md",
    "explanation/theory.md",
    "explanation/formulas.md",
    "explanation/phase-currents.md",
    "explanation/reduction-factors.md",
    "explanation/decomposition.md",
    "explanation/dc.md",
    "transient.md",
    "explanation/verification.md",
    "explanation/standards.md",
]


def _redirects():
    """``redirect_maps`` of the ``redirects`` plugin in ``mkdocs.yml``."""
    for plugin in _mkdocs_config().get("plugins", []):
        if isinstance(plugin, dict) and "redirects" in plugin:
            return plugin["redirects"]["redirect_maps"]
    return {}


def test_understanding_the_model_holds_the_explanation_pages_in_order():
    """D1: the Concepts page is replaced by six explanation pages under an
    overview; the transient chapter, verification and standards follow."""
    config = _mkdocs_config()
    section = [
        page for page, trail in _nav_entries(config["nav"])
        if trail[:1] == ("Understanding the model",)
    ]
    assert section == EXPLANATION_PAGES


def test_moved_pages_forward_from_their_old_address():
    """Old URLs keep working: a moved page is not a file any more, and its
    address forwards to a page that exists."""
    redirects = _redirects()
    assert redirects.get("concepts.md") == "explanation/index.md"
    for old, new in redirects.items():
        assert not (DOCS / old).exists(), old
        assert (DOCS / new.partition("#")[0]).is_file(), new


def test_every_former_concepts_section_link_lands_on_its_new_place():
    """The forwarding keeps the ``#anchor`` of an old link, so the overview
    that ``concepts.md`` forwards to carries every former section id -- on a
    link to the section's new home."""
    text = (DOCS / "explanation" / "index.md").read_text(encoding="utf-8")
    rows = dict(
        (anchor, target)
        for target, anchor in re.findall(r"\]\(([^)]+)\)\{\s*#([\w-]+)\s*\}", text)
    )
    assert sorted(rows) == sorted(CONCEPTS_ANCHORS)
    for anchor, target in rows.items():
        path, _, section = target.partition("#")
        page = f"explanation/{path}"
        assert (DOCS / page).is_file(), (anchor, target)
        if section:
            assert section in _anchors(page), (anchor, target)


def test_nothing_links_to_the_removed_concepts_page():
    """Links go to the new pages directly, not through the forwarding."""
    hits = [
        (page, target)
        for page in sorted(p for p in _documentation_pages() if p.endswith(".md"))
        for target in _page_links(page)
        if target.partition("#")[0].endswith("concepts.md")
    ]
    assert hits == []
