# docs/hooks/project_pages.py
"""
MkDocs build hook: the project pages show files of the repository root.

The changelog, the contribution guide and the citation metadata are kept
once, at the repository root, where GitHub and the package index show them:
``CHANGELOG.md``, ``CONTRIBUTING.md`` and ``CITATION.cff``. The pages under
``project/`` show them on the site without a second copy that could drift.

* ``project/changelog.md`` and ``project/contributing.md`` hold the snippet
  line ``--8<-- "CHANGELOG.md"`` or ``--8<-- "CONTRIBUTING.md"``. The hook
  expands it itself instead of leaving it to ``pymdownx.snippets``, because
  the links of a root file are written for the repository: ``docs/audit-log.md``
  seen from ``project/changelog.md`` would point at a page that does not
  exist and fail the strict build. :func:`rewrite_links` maps a link into
  ``docs/`` to the page, a root file that has a page of its own to that page,
  and any other repository path to the file on GitHub.
* ``project/citation.md`` carries the markers ``<!-- citation: bibtex -->``
  and ``<!-- citation: apa -->``. :func:`citation_formats` writes both from
  ``CITATION.cff``, so the release that bumps the version there updates the
  page as well.

A marker that names a file the hook does not know, or a citation file
without the fields a reference needs, raises, so a strict build stops
instead of publishing an empty section.
"""

import logging
import posixpath
import re
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("mkdocs.hooks.project_pages")

_ROOT = Path(__file__).resolve().parents[2]

#: Pages that show a file of the repository root, and that file.
INCLUDES: Dict[str, str] = {
    "project/changelog.md": "CHANGELOG.md",
    "project/contributing.md": "CONTRIBUTING.md",
}

#: Repository files that have a page of their own (paths relative to docs/).
PAGES_OF_FILES: Dict[str, str] = {
    "CHANGELOG.md": "project/changelog.md",
    "CONTRIBUTING.md": "project/contributing.md",
    "CITATION.cff": "project/citation.md",
    "LICENSE": "project/license.md",
}

#: The page that carries the citation markers.
CITATION_PAGE = "project/citation.md"

_INCLUDE = re.compile(r'^--8<-- "([^"]+)"[ \t]*$', re.M)
_LINK = re.compile(r"(\]\()([^)\s]+)(\))")
_FENCE = re.compile(r"^([ \t]*)(```|~~~).*?^\1\2[^\n]*$", re.M | re.S)
_CITATION = re.compile(r"<!-- citation: (bibtex|apa) -->")
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:")


def rewrite_links(text: str, page: str, repo_url: str, branch: str = "main") -> str:
    """
    Rewrite the relative links of a repository-root file for a docs page.

    Parameters
    ----------
    text : str
        Markdown of a file at the repository root.
    page : str
        The page that shows it, relative to ``docs/``.
    repo_url : str
        URL of the repository on GitHub.
    branch : str, optional
        Branch the links to other repository files point at.

    Returns
    -------
    str
        ``text`` with every relative link outside fenced code blocks pointing
        at the page or the file it means. Absolute URLs and in-page anchors
        are left alone.
    """
    page_dir = posixpath.dirname(page)

    def target(link: str) -> str:
        if _SCHEME.match(link) or link.startswith(("#", "/")):
            return link
        path, sep, anchor = link.partition("#")
        path = posixpath.normpath(path)
        if path.startswith("docs/"):
            new = posixpath.relpath(path[len("docs/"):], page_dir)
        elif path in PAGES_OF_FILES:
            new = posixpath.relpath(PAGES_OF_FILES[path], page_dir)
        else:
            kind = "tree" if (_ROOT / path).is_dir() else "blob"
            new = f"{repo_url.rstrip('/')}/{kind}/{branch}/{path}"
        return new + sep + anchor

    def outside_fences(segment: str) -> str:
        return _LINK.sub(lambda m: m.group(1) + target(m.group(2)) + m.group(3), segment)

    pieces: List[str] = []
    last = 0
    for fence in _FENCE.finditer(text):
        pieces.append(outside_fences(text[last:fence.start()]))
        pieces.append(fence.group(0))
        last = fence.end()
    pieces.append(outside_fences(text[last:]))
    return "".join(pieces)


def _initials(given_names: str) -> str:
    """``"Hans Peter"`` -> ``"H. P."``, ``"Jean-Luc"`` -> ``"J.-L."``."""
    words = []
    for word in given_names.split():
        words.append("-".join(f"{part[0]}." for part in word.split("-") if part))
    return " ".join(words)


def citation_formats(cff: Dict) -> Dict[str, str]:
    """
    A BibTeX entry and an APA reference from the fields of ``CITATION.cff``.

    Parameters
    ----------
    cff : dict
        The parsed citation file. ``title``, ``version``, ``authors`` (with
        ``family-names`` and ``given-names``) and one of
        ``repository-code`` / ``url`` are required; ``date-released`` and
        ``doi`` are used when present.

    Returns
    -------
    dict
        ``{"bibtex": ..., "apa": ...}``; the APA reference is Markdown with
        the title in italics. Without a release date the year is ``n.d.``
        (APA) and left out (BibTeX).

    Raises
    ------
    ValueError
        If a required field is missing.
    """
    missing = [key for key in ("title", "version", "authors") if not cff.get(key)]
    url: Optional[str] = cff.get("repository-code") or cff.get("url")
    if url is None:
        missing.append("repository-code")
    for author in cff.get("authors") or []:
        missing += [key for key in ("family-names", "given-names") if not author.get(key)]
    if missing:
        raise ValueError(f"CITATION.cff lacks {', '.join(sorted(set(missing)))} for a citation.")

    authors = cff["authors"]
    released = cff.get("date-released")
    year = str(released)[:4] if released else None
    doi = cff.get("doi")

    fields = [
        ("author", " and ".join(f"{a['family-names']}, {a['given-names']}" for a in authors)),
        ("title", cff["title"]),
        ("version", str(cff["version"])),
    ]
    if year:
        fields.append(("year", year))
    if doi:
        fields.append(("doi", doi))
    fields.append(("url", url))
    key = re.sub(r"\W+", "", authors[0]["family-names"].lower()) + "_" + re.sub(
        r"\W+", "", cff["title"].split(":")[0].lower()
    )
    width = max(len(name) for name, _ in fields)
    bibtex = "@software{" + key + ",\n" + ",\n".join(
        f"  {name:<{width}} = {{{value}}}" for name, value in fields
    ) + "\n}"

    names = [f"{a['family-names']}, {_initials(a['given-names'])}" for a in authors]
    if len(names) == 1:
        apa_authors = names[0]
    else:
        apa_authors = ", ".join(names[:-1]) + ", & " + names[-1]
    location = f"https://doi.org/{doi}" if doi else url
    apa = (
        f"{apa_authors} ({year or 'n.d.'}). *{cff['title']}* "
        f"(Version {cff['version']}) [Computer software]. {location}"
    )
    return {"bibtex": bibtex, "apa": apa}


def _read_citation_file() -> Dict:
    """``CITATION.cff`` of the repository, parsed."""
    import yaml

    return yaml.safe_load((_ROOT / "CITATION.cff").read_text(encoding="utf-8"))


def on_page_markdown(markdown: str, *, page, config, files) -> str:
    """
    Expand the repository files of the project pages and write the citation.

    Parameters
    ----------
    markdown : str
        Source of the page.
    page : mkdocs.structure.pages.Page
        The page.
    config : mkdocs.config.defaults.MkDocsConfig
        The site configuration; ``repo_url`` is where links to repository
        files point.
    files : mkdocs.structure.files.Files
        Unused.

    Returns
    -------
    str
        The page source with the include expanded or the citation written.

    Raises
    ------
    ValueError
        If an include on a project page names another file than the one
        :data:`INCLUDES` assigns to it, or the citation page lacks a marker.
    """
    source = page.file.src_uri
    if source in INCLUDES:
        expected = INCLUDES[source]

        def expand(match: "re.Match") -> str:
            if match.group(1) != expected:
                raise ValueError(f"{source} includes {match.group(1)}; expected {expected}.")
            text = (_ROOT / expected).read_text(encoding="utf-8")
            return rewrite_links(text, source, config["repo_url"])

        markdown, count = _INCLUDE.subn(expand, markdown)
        if count != 1:
            raise ValueError(f"{source} must include {expected} exactly once, found {count}.")
    elif source == CITATION_PAGE:
        formats = citation_formats(_read_citation_file())
        found = set(_CITATION.findall(markdown))
        if found != {"bibtex", "apa"}:
            raise ValueError(f"{source} needs both citation markers, found {sorted(found)}.")
        markdown = _CITATION.sub(
            lambda m: (
                "```bibtex\n" + formats["bibtex"] + "\n```"
                if m.group(1) == "bibtex"
                else formats["apa"]
            ),
            markdown,
        )
    return markdown
