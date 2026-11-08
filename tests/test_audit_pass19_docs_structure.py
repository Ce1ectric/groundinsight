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

import math
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


# --------------------------------------------------------------------------
# DOC5 -- getting started: installation and two tutorials with numbers
# --------------------------------------------------------------------------

SNIPPETS = DOCS / "snippets"

#: Section ids of the former ``docs/quickstart.md`` and ``docs/installation.md``.
QUICKSTART_ANCHORS = [
    "1-import-and-create-a-network", "2-define-bus-and-branch-types",
    "3-add-buses-and-branches", "4-add-the-source-and-the-fault",
    "5-create-paths-and-solve", "6-inspect-the-results", "7-plot",
    "8-save-and-load", "9-optional-outage-what-if-studies",
    "10-optional-inverse-rho-analysis", "11-optional-import-from-pandapower",
    "12-optional-transient-simulation", "13-logging-and-silencing-output",
]
INSTALLATION_ANCHORS = [
    "install-from-pypi", "install-from-source", "optional-dependency-groups",
    "optional-extras", "verify-the-installation",
]
GETTING_STARTED = [
    "getting-started/index.md",
    "getting-started/installation.md",
    "getting-started/first-calculation.md",
    "getting-started/ring.md",
]


def _run_snippet(name, monkeypatch, tmp_path, capsys):
    """
    Run a documentation snippet as a script in an empty directory.

    Returns
    -------
    tuple
        ``(namespace, printed lines)``.
    """
    import runpy

    monkeypatch.chdir(tmp_path)
    namespace = runpy.run_path(str(SNIPPETS / name), run_name="__main__")
    printed = [line.rstrip() for line in capsys.readouterr().out.splitlines()]
    return namespace, printed


def _text_block_lines(relative):
    """Every line of the ```text blocks of a page."""
    text = (DOCS / relative).read_text(encoding="utf-8")
    lines = set()
    for block in re.findall(r"^```text\n(.*?)^```", text, re.M | re.S):
        lines.update(line.rstrip() for line in block.splitlines())
    return lines


def test_getting_started_holds_the_tutorials_in_order():
    config = _mkdocs_config()
    section = [
        page for page, trail in _nav_entries(config["nav"])
        if trail[:1] == ("Getting started",)
    ]
    assert section == GETTING_STARTED


def test_quickstart_and_installation_forward_with_their_anchors():
    """The old Quickstart forwards to the overview, which carries every former
    section id on a link to its new place; the installation page moved under
    "Getting started" and kept its section ids."""
    redirects = _redirects()
    assert redirects.get("quickstart.md") == "getting-started/index.md"
    assert redirects.get("installation.md") == "getting-started/installation.md"
    text = (DOCS / "getting-started" / "index.md").read_text(encoding="utf-8")
    ids = re.findall(r"\]\([^)]+\)\{\s*#([\w-]+)\s*\}", text)
    assert ids == QUICKSTART_ANCHORS
    assert set(INSTALLATION_ANCHORS) <= _anchors("getting-started/installation.md")


def test_documented_python_floor_matches_pyproject():
    """REL4 / DOC5: the installation page and the README state the floor
    that ``pyproject.toml`` declares (``python = "^3.N"``)."""
    import tomllib

    declared = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    caret = declared["tool"]["poetry"]["dependencies"]["python"]
    floor = re.fullmatch(r"\^(\d+\.\d+)", caret).group(1)
    for page in (DOCS / "getting-started" / "installation.md", ROOT / "README.md"):
        text = page.read_text(encoding="utf-8")
        assert f"**Python {floor} or newer**" in text, page
        stated = set(re.findall(r"Python (\d+\.\d+) or newer", text))
        assert stated == {floor}, (page, stated)


def test_installation_self_test_prints_what_the_page_shows(monkeypatch, tmp_path, capsys):
    _, printed = _run_snippet("installation_check.py", monkeypatch, tmp_path, capsys)
    shown = _text_block_lines("getting-started/installation.md")
    import groundinsight as gi

    assert printed[0] == f"groundinsight {gi.__version__}"
    assert len(printed) == 7
    for line in printed[1:]:
        assert line in shown, line
        assert line.endswith(" agrees")


def _reference_solution(net, fault, bus):
    """|EPR| at ``bus`` and the driving-point impedance there, from the
    independent reference MNA of the test suite (branch currents as unknowns,
    physical sign frame)."""
    from tests import _reference_mna as ref

    rd = ref.reference_network(net, 50.0, fault)
    solution = ref.solve_oneway(rd)
    return abs(solution["u"][bus]), ref.driving_point_impedance(rd, bus)


def test_first_calculation_reproduces_its_numbers_two_ways(monkeypatch, tmp_path, capsys):
    """Tutorial 1: the page shows what the snippet prints, and the numbers
    agree with the closed forms of a single line and with the reference MNA."""
    import cmath

    ns, printed = _run_snippet("first_calculation.py", monkeypatch, tmp_path, capsys)
    shown = _text_block_lines("getting-started/first-calculation.md")
    for line in printed:
        if line.startswith("[") or line.startswith("diagnostics"):
            continue
        assert line in shown, line
    assert "diagnostics: []" in printed
    key = ns["key"]
    z_b, z_s, z_m, current = 1.0 + 0.1j, (0.25 + 0.6j) * 5.0, 0.6j * 5.0, 1000.0
    i_earth = current * (z_s - z_m) / (z_s + 2 * z_b)
    z_e = z_b * (z_s + z_b) / (z_s + 2 * z_b)
    assert key["r_epr"] == pytest.approx(1.25 / 3.25, rel=1e-12)
    assert key["r_current"] == pytest.approx(abs(i_earth) / current, rel=1e-12)
    assert key["U_E_V"] == pytest.approx(abs(z_b * i_earth), rel=1e-12)
    assert key["Z_E_Ohm"] == pytest.approx(abs(z_e), rel=1e-12)
    assert key["Z_E_degree"] == pytest.approx(math.degrees(cmath.phase(z_e)), abs=1e-9)
    u_ref, z_ref = _reference_solution(ns["net"], "fault_at_remote_bus", "bus_fault")
    assert key["U_E_V"] == pytest.approx(u_ref, rel=1e-12)
    assert key["Z_E_Ohm"] == pytest.approx(abs(z_ref), rel=1e-12)
    assert (tmp_path / "first_calculation.json").is_file()


def test_first_calculation_chain_closes_with_an_ideal_infeed_earth(monkeypatch, tmp_path, capsys):
    """The page says: make the infeed electrode ideal and U_E = |1-k| I |Z_E|."""
    import groundinsight as gi
    from groundinsight.models.core_models import ComplexNumber

    ns, _ = _run_snippet("first_calculation.py", monkeypatch, tmp_path, capsys)
    net = ns["net"]
    net.buses["bus_source"].impedance = {50.0: ComplexNumber(real=1e-9, imag=0.0)}
    gi.run_fault(net, fault="fault_at_remote_bus")
    key = net.res_fault("fault_at_remote_bus").row(0, named=True)
    assert key["U_E_V"] == pytest.approx(key["r_epr"] * 1000.0 * key["Z_E_Ohm"], rel=1e-8)
    text = " ".join((DOCS / "getting-started" / "first-calculation.md").read_text(
        encoding="utf-8").split())
    assert "= 231.3 V" in text and "it is 324.6 V" in text
    assert f"{ns['key']['r_current'] * 1000 * ns['key']['Z_E_Ohm']:.1f}" == "231.3"
    assert f"{ns['key']['r_epr'] * 1000 * ns['key']['Z_E_Ohm']:.1f}" == "324.6"


def test_ring_reproduces_its_numbers_two_ways(monkeypatch, tmp_path, capsys):
    """Tutorial 2: the page shows what the snippet prints; the numbers agree
    with the closed forms of the symmetric ring and with the reference MNA;
    the split is 500 A per route; the legacy mode gives (1 - 2k)."""
    ns, printed = _run_snippet("ring.py", monkeypatch, tmp_path, capsys)
    shown = _text_block_lines("getting-started/ring.md")
    for line in printed:
        assert line in shown, line
    assert "ModelAssumptionWarning PHASE_PROXY" in printed
    z_b, z_s, z_m, current = 1.0 + 0.1j, (0.25 + 0.6j) * 2.5, 0.6j * 2.5, 1000.0
    k = z_m / z_s

    def parallel(a, b):
        return a * b / (a + b)

    rows = {row["frequency_Hz"]: row
            for row in ns["net"].res_fault("fault_at_remote_bus").iter_rows(named=True)}
    key = rows[50.0]
    assert key["U_E_V"] == pytest.approx(abs(1 - k) * current * abs(parallel(z_b, z_s / 2)), rel=1e-12)
    assert key["r_current"] == pytest.approx(abs(z_s - z_m) / 2 / abs(z_b + z_s / 2), rel=1e-12)
    z_e = parallel(z_b, z_s / 2 + parallel(z_b / 2, z_s / 2 + z_b))
    assert key["Z_E_Ohm"] == pytest.approx(abs(z_e), rel=1e-12)
    u_ref, z_ref = _reference_solution(ns["net"], "fault_at_remote_bus", "bus_fault")
    assert key["U_E_V"] == pytest.approx(u_ref, rel=1e-12)
    assert key["Z_E_Ohm"] == pytest.approx(abs(z_ref), rel=1e-12)
    phase = ns["net"].electrical_network.phase_currents[50.0]
    assert all(abs(complex(phase[name])) == pytest.approx(500.0, rel=1e-12) for name in phase)
    legacy = ns["old"]
    assert legacy["r_epr"] == pytest.approx(abs(1 - 2 * k), rel=1e-12) == pytest.approx(1.0)
    assert legacy["U_E_V"] == pytest.approx(abs(1 - 2 * k) * current * abs(parallel(z_b, z_s / 2)),
                                            rel=1e-12)
    z_s250, z_m250 = (0.25 + 3.0j), 3.0j
    assert rows[250.0]["r_epr"] == pytest.approx(abs(1 - z_m250 / z_s250), rel=1e-12)
    rms = [row for row in ns["net"].res_fault("fault_at_remote_bus").iter_rows(named=True)
           if row["is_rms"]][0]
    assert rms["U_E_V"] == pytest.approx(math.hypot(rows[50.0]["U_E_V"], rows[250.0]["U_E_V"]),
                                         rel=1e-12)
