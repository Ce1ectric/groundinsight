# tests/test_audit_pass19_docs_api.py
"""
Audit pass 19 -- the API pages render every public name and say what the code does.

Finding IDs: DOC4 (public names missing from the API pages), DOC7 (seven false
statements on the API pages), DOC3 (the path enumeration and the deprecated
``auto_parallel_coefficients`` taught as if they were needed), DOC8 (an EPR
limit called a touch-voltage limit), DOC9 (a reference to an ADR that does not
exist in this repository), DOC10 (the text ``frequency_Hz`` of the outage
comparison frames), and the two open points the first documentation package
left on these pages (the formula-size sentence, the hand-written reference
table).

Two independent methods decide whether a public name is rendered:

* here, statically, from the ``:::`` directives of ``docs/api/*.md`` and the
  member selection mkdocstrings applies (explicit ``members`` lists render
  what they name; a module without a list renders the documented members of
  its ``__all__``; a constant without a docstring only when named);
* in CI, after ``mkdocs build``, from the inventory the build writes
  (``scripts/check_api_inventory.py site/objects.inv``), which lists what was
  actually rendered.

Every numerical claim checked here is recomputed a second way: the electrode
currents that sum to zero by the result objects, by the result frame and by
the independent reference solver of the test suite; the formula sizes by an
AST count and by the guard itself with its limits lowered to the stated
numbers.
"""

import ast
import cmath
import importlib
import inspect
import io
import os
import re
import subprocess
import sys
import tokenize
import types
import zlib
from pathlib import Path

import polars as pl
import pytest

import groundinsight as gi
import groundinsight.api as gi_api
from groundinsight.utils import validations
from tests import _reference_mna as ref

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
API = DOCS / "api"
SCRIPT = ROOT / "scripts" / "check_api_inventory.py"

#: Pages another documentation package replaces in the same release (see
#: tests/test_audit_pass19_docs_codeblocks.py); not scanned while they exist.
RESTRUCTURED_ELSEWHERE = ("concepts.md", "quickstart.md")


def _page(relative):
    """Text of a documentation page."""
    return (DOCS / relative).read_text(encoding="utf-8")


def _flat(text):
    """Collapse whitespace so a sentence wrapped over lines matches."""
    return " ".join(text.split())


_INCLUDE_LINE = re.compile(r'^--8<-- "(?P<path>[^":]+)(?::(?P<section>[\w-]+))?"[ \t]*$', re.M)


def _section(path, section):
    """The lines of a snippet section, as ``pymdownx.snippets`` includes them."""
    text = (ROOT / path).read_text(encoding="utf-8")
    if section is None:
        return text
    match = re.search(
        rf"^# --8<-- \[start:{re.escape(section)}\]\n(.*?)^# --8<-- \[end:{re.escape(section)}\]",
        text, re.M | re.S,
    )
    assert match, f"section {section!r} missing in {path}"
    return match.group(1)


def _expanded(relative):
    """Text of a page with its snippet includes replaced by the included code."""
    return _INCLUDE_LINE.sub(lambda m: _section(m.group("path"), m.group("section")),
                             _page(relative))


# ---------------------------------------------------------------------------
# DOC4 -- every public name is rendered
# ---------------------------------------------------------------------------


def _directives():
    """
    The mkdocstrings directives of the API pages.

    Returns
    -------
    list of tuple
        ``(page, identifier, members)`` -- ``members`` is the explicit list of
        the directive's ``options.members`` or ``None``.
    """
    found = []
    for page in sorted(API.glob("*.md")):
        lines = page.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            match = re.match(r"^:::\s+([\w.]+)\s*$", line)
            if not match:
                continue
            members = None
            option_lines = []
            for following in lines[index + 1:]:
                if not following.startswith("    ") or not following.strip():
                    break
                option_lines.append(following)
            in_members = False
            for option in option_lines:
                stripped = option.strip()
                if stripped == "members:":
                    in_members, members = True, []
                    continue
                item = re.match(r"^-\s*([\w.]+)\s*$", stripped)
                if in_members and item:
                    members.append(item.group(1))
                elif in_members:
                    in_members = False
            found.append((page.name, match.group(1), members))
    return found


def _resolve(identifier):
    """Import the object a dotted identifier names."""
    parts = identifier.split(".")
    for cut in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:cut]))
        except ImportError:
            continue
        for attribute in parts[cut:]:
            obj = getattr(obj, attribute)
        return obj
    raise ImportError(identifier)


def _has_attribute_docstring(module, name):
    """Whether ``name`` is assigned in ``module`` with a docstring right after it."""
    try:
        tree = ast.parse(inspect.getsource(module))
    except (OSError, TypeError):
        return False
    body = tree.body
    for position, node in enumerate(body[:-1]):
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        following = body[position + 1]
        if (
            name in targets
            and isinstance(following, ast.Expr)
            and isinstance(following.value, ast.Constant)
            and isinstance(following.value.value, str)
        ):
            return True
    return False


def _defining_module(obj, name, fallback):
    """The module in which a plain value is assigned under ``name``."""
    for module_name, module in sorted(sys.modules.items()):
        if module_name.startswith("groundinsight") and isinstance(module, types.ModuleType):
            if module.__dict__.get(name) is obj and name in _assigned_names(module):
                return module
    return fallback


def _assigned_names(module):
    """Names assigned at module level in the source of ``module``."""
    try:
        tree = ast.parse(inspect.getsource(module))
    except (OSError, TypeError):
        return set()
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _documented(obj, name, module):
    """Whether mkdocstrings renders a member it was not told to render by name."""
    if isinstance(obj, types.ModuleType) or inspect.isclass(obj) or inspect.isroutine(obj):
        return bool(inspect.getdoc(obj))
    return _has_attribute_docstring(_defining_module(obj, name, module), name)


def statically_rendered():
    """
    The objects the API pages render, as a set of ``id()`` values.

    Returns
    -------
    set of int
        Identities of every object a directive renders.
    """
    rendered = set()
    for _page_name, identifier, members in _directives():
        target = _resolve(identifier)
        rendered.add(id(target))  # a module directive renders the module itself too
        if not isinstance(target, types.ModuleType):
            continue
        if members is not None:
            for member in members:
                rendered.add(id(getattr(target, member)))
            continue
        public = getattr(target, "__all__", None)
        if public is None:
            public = [
                n for n, o in vars(target).items()
                if not n.startswith("_") and getattr(o, "__module__", None) == target.__name__
            ]
        for member in public:
            obj = getattr(target, member)
            if _documented(obj, member, target):
                rendered.add(id(obj))
    return rendered


@pytest.mark.parametrize("package", [gi, gi_api], ids=["groundinsight", "groundinsight.api"])
def test_every_public_name_is_rendered_on_an_api_page(package):
    """DOC4: each name of ``__all__`` is rendered by a ``:::`` directive."""
    rendered = statically_rendered()
    missing = [name for name in package.__all__ if id(getattr(package, name)) not in rendered]
    assert missing == [], f"{package.__name__}: not on any API page: {missing}"


def test_toplevel_page_lists_every_public_name_with_its_page():
    """api/toplevel.md names every public name and links pages that exist."""
    text = _page("api/toplevel.md")
    for package in (gi, gi_api):
        absent = [n for n in package.__all__ if f"`{n}`" not in text]
        assert absent == [], f"{package.__name__} names missing on api/toplevel.md: {absent}"
    for target in re.findall(r"\]\(([\w-]+\.md)(?:#[\w-]+)?\)", text):
        assert (API / target).is_file(), target


def test_api_overview_links_every_api_page():
    """api/index.md links every page of the API section (DOC12)."""
    text = _page("api/index.md")
    linked = set(re.findall(r"\]\(([\w-]+\.md)(?:#[\w-]+)?\)", text))
    pages = {p.name for p in API.glob("*.md")} - {"index.md"}
    assert pages - linked == set()


def test_api_overview_keeps_the_internal_api_convention():
    text = _flat(_page("api/index.md"))
    assert "## Internal API" in _page("api/index.md")
    assert 'their docstring starts its second paragraph with "Internal API"' in text


# --- the inventory script (the CI step) --------------------------------------


def _write_inventory(path, names):
    """Write a version-2 inventory listing ``names``."""
    header = (
        b"# Sphinx inventory version 2\n# Project: groundinsight\n# Version: 0.0.0\n"
        b"# The remainder of this file is compressed using zlib.\n"
    )
    body = "".join(f"{n} py:function 1 api/x/#$ -\n" for n in sorted(names)).encode("utf-8")
    path.write_bytes(header + zlib.compress(body))


def _run_script(inventory):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(inventory)], env=env, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )


def test_inventory_script_finds_a_missing_name_and_accepts_a_complete_site(tmp_path):
    """The CI step fails on one missing public name and passes on a full inventory."""
    complete = {f"groundinsight.{n}" for n in gi.__all__} | {
        f"groundinsight.api.{n}" for n in gi_api.__all__
    }
    full = tmp_path / "full.inv"
    _write_inventory(full, complete)
    ok = _run_script(full)
    assert ok.returncode == 0, ok.stderr
    gap = tmp_path / "gap.inv"
    _write_inventory(gap, complete - {"groundinsight.DCLimitWarning"})
    failed = _run_script(gap)
    assert failed.returncode == 1
    assert "DCLimitWarning" in failed.stderr and "1 of" in failed.stderr
    broken = tmp_path / "broken.inv"
    broken.write_bytes(b"not an inventory")
    assert _run_script(broken).returncode == 2


def test_inventory_script_accepts_the_canonical_path(tmp_path):
    """A name rendered only under its defining module still counts."""
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        import check_api_inventory as script
    finally:
        sys.path.pop(0)
    assert "groundinsight.utils.impedance_calculator.DCLimitWarning" in script.accepted_names(
        "groundinsight", "DCLimitWarning"
    )
    assert "groundinsight.analysis.thermal.FINAL_TEMPERATURES" in script.accepted_names(
        "groundinsight", "FINAL_TEMPERATURES"
    )
    assert "groundinsight.simulation.waveforms" in script.accepted_names("groundinsight", "waveforms")


def test_ci_checks_the_inventory_after_the_strict_build():
    """DOC4: the docs job runs the inventory check on the site it just built."""
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    steps = [step.get("run", "") or "" for step in workflow["jobs"]["docs"]["steps"]]
    build = next(i for i, run in enumerate(steps) if "mkdocs build --strict" in run)
    check = next(i for i, run in enumerate(steps) if "scripts/check_api_inventory.py site/objects.inv" in run)
    assert check > build


# ---------------------------------------------------------------------------
# DOC7 -- the seven false statements
# ---------------------------------------------------------------------------


def _line_network():
    """A four-bus line with two sources, solved; also the reference solution."""
    station = gi.BusType(name="station", system_type="Station", voltage_level=20.0,
                         impedance_formula="2.0")
    cable = gi.BranchType(
        name="cable", grounding_conductor=True,
        self_impedance_formula="(0.2 + j*2*pi*f*1.2e-3)*l",
        mutual_impedance_formula="(j*2*pi*f*1.0e-3)*l",
    )
    net = gi.create_network(name="line", frequencies=[50.0, 250.0])
    names = ["A", "B", "C", "D"]
    for name in names:
        gi.create_bus(name=name, type=station, network=net)
    for a, b in zip(names[:-1], names[1:]):
        gi.create_branch(name=a + b, type=cable, from_bus=a, to_bus=b, length=1.5, network=net)
    gi.create_source(name="Q1", bus="A", values={50.0: 1000.0, 250.0: 80.0}, network=net)
    gi.create_source(name="Q2", bus="D", values={50.0: 300.0, 250.0: 20.0}, network=net)
    gi.create_fault(name="F", bus="C", network=net)
    return net, gi.run_fault(net, "F")


def test_plotting_page_states_that_the_bus_currents_sum_to_zero():
    """DOC7 item 1: the electrode currents of all buses add up to zero, not to r*I_F."""
    text = _flat(_page("api/plotting.md"))
    assert "equals the fault current scaled by" not in text
    assert "sum to zero" in text
    assert "`I_bus_A`" in text and "`I_inj_A`" in text


def test_electrode_currents_sum_to_zero_three_ways():
    """The statement on the plotting page, by objects, by frame and by the reference solver."""
    net, result = _line_network()
    frame = net.res_buses(fault="F").filter(~pl.col("is_rms"))
    for f in net.frequencies:
        by_objects = sum(complex(b.ia_freq[f].real, b.ia_freq[f].imag) for b in result.buses)
        rows = frame.filter(pl.col("frequency_Hz") == f)
        by_frame = sum(
            cmath.rect(a, cmath.pi * d / 180.0)
            for a, d in zip(rows["I_bus_A"], rows["I_bus_degree"])
        )
        solution = ref.solve_oneway(ref.reference_network(net, f, "F"))
        by_reference = sum(solution["I_a"].values())
        scale = max(abs(c) for c in solution["I_a"].values())
        for total in (by_objects, by_frame, by_reference):
            assert abs(total) < 1e-9 * scale
        # ... while the injections are +I at the sources and -I_F at the fault bus.
        injected = sum(complex(b.i_inj_freq[f].real, b.i_inj_freq[f].imag) for b in result.buses)
        assert abs(injected) < 1e-9 * scale


def test_plotting_page_states_the_branch_sign_frame():
    """DOC7 item 2: the branch current is counted positive from from_bus to to_bus."""
    text = _flat(_page("api/plotting.md"))
    assert "traversal direction" not in text
    assert "positive from `from_bus` to `to_bus`" in text
    assert "../reference/conventions.md#signs-and-phase-angles" in text


def test_electrical_network_page_names_both_factors_and_the_electrode_symbol():
    """DOC7 item 3: the EN 50522 factor is listed, the bus electrode is Z_B, never Z_E."""
    text = _flat(_page("api/electrical_network.md"))
    assert "1/Z_E(f, \\rho)" not in text and "1/Z_E" not in text
    assert "Z_B" in text
    assert "`value`" in text and "`value_current`" in text
    assert "EN 50522" in text


def test_reference_page_table_is_generated_from_the_cases():
    """DOC7 item 4 / docs1 open point 4: the build hook writes the table."""
    text = _page("api/reference.md")
    assert "<!-- reference-cases-table -->" in text
    assert "read back from one solved fault" not in text
    assert "U_E = 3I_0" not in text


def test_io_example_imports_a_connected_mv_network():
    """DOC7 item 5: with switch_branch_type no bus of the example is isolated."""
    pytest.importorskip("pandapower")
    text = _page("api/io.md")
    assert "4-bus MV ring" not in text
    assert '--8<-- "docs/snippets/pandapower_topology.py' in text
    assert "switch_branch_type=" in _expanded("api/io.md")
    snippet = ROOT / "docs" / "snippets" / "pandapower_topology.py"
    import runpy

    names = runpy.run_path(str(snippet), run_name="docs_snippet")
    net = names["net"]
    assert len(net.buses) == 4 and len(net.branches) == 4
    adjacency = {b: set() for b in net.buses}
    for branch in net.branches.values():
        adjacency[branch.from_bus].add(branch.to_bus)
        adjacency[branch.to_bus].add(branch.from_bus)
    seen, todo = set(), [next(iter(adjacency))]
    while todo:
        bus = todo.pop()
        if bus not in seen:
            seen.add(bus)
            todo.extend(adjacency[bus] - seen)
    assert seen == set(net.buses)


def test_io_short_circuit_example_is_a_complete_snippet():
    """DOC7 item 6: the page includes the runnable snippet, with the minimum data named."""
    text = _flat(_page("api/io.md"))
    assert '--8<-- "docs/snippets/pandapower_shortcircuit.py' in _page("api/io.md")
    for column in ("s_sc_max_mva", "rx_max", "x0x_max", "r0x0_max", "r0_ohm_per_km",
                   "x0_ohm_per_km", "c0_nf_per_km"):
        assert column in text, column
    assert "net_gi" not in text


def test_io_voltage_level_comparison_is_the_implemented_one():
    """DOC7 item 7: the table states math.isclose with rel_tol 1e-9, as the code does."""
    text = _flat(_page("api/io.md"))
    assert "`bus.vn_kv != voltage_level_kV`" not in text
    assert "math.isclose(vn_kv, voltage_level_kV, rel_tol=1e-9)" in text
    source = inspect.getsource(importlib.import_module("groundinsight.io.pandapower_import"))
    assert "math.isclose(vn, float(voltage_level_kV), rel_tol=1e-9, abs_tol=0.0)" in source


def test_io_voltage_level_comparison_behaves_as_stated():
    """The tolerance named on the page decides which bus is kept (second way)."""
    pp = pytest.importorskip("pandapower")
    net_pp = pp.create_empty_network()
    kept = pp.create_bus(net_pp, vn_kv=20.0 * (1 + 4e-10), name="near")
    dropped = pp.create_bus(net_pp, vn_kv=20.0 * (1 + 4e-9), name="off")
    preview = gi.preview_pandapower_import(net_pp, voltage_level_kV=20.0)
    status = dict(zip(preview["pp_index"], preview["status"]))
    assert status[kept] == "keep" and status[dropped] == "skip"


def test_io_skip_reason_table_is_complete():
    """Every reason the preview can report is in the vocabulary table, and only those."""
    module = importlib.import_module("groundinsight.io.pandapower_import")
    reasons = {v for k, v in vars(module).items() if k.startswith("_PREVIEW_") and "REASON" in k}
    page = _page("api/io.md")
    start = page.index("## Skip-reason vocabulary")
    section = page[start:page.index("\n## ", start)]
    table = re.findall(r"^\|\s*`([a-z_]+)`\s*\|", section, re.M)
    assert reasons - set(table) == set()
    assert set(table) - reasons == set()


# ---------------------------------------------------------------------------
# docs1 open point 3 -- the formula-size sentence
# ---------------------------------------------------------------------------


def _snippet_formulas():
    """Every ``*_formula`` string of the data-sheet how-to's snippet."""
    import runpy

    names = runpy.run_path(str(ROOT / "docs" / "snippets" / "types_from_datasheets.py"),
                           run_name="docs_snippet")
    formulas = set()
    for value in names.values():
        if isinstance(value, (gi.BusType, gi.BranchType)):
            for field, text in value.model_dump().items():
                if field.endswith("_formula") and isinstance(text, str):
                    formulas.add(text)
    return formulas


def _size(formula):
    """Terms, calls, literal digits and the largest constant exponent, by the AST."""
    tree = ast.parse("(\n" + formula + "\n)", mode="eval")
    nodes = [n for n in ast.walk(tree) if isinstance(n, ast.expr)]
    calls = [n for n in nodes if isinstance(n, ast.Call)]
    numbers = [
        tok.string for tok in tokenize.generate_tokens(io.StringIO(formula).readline)
        if tok.type == tokenize.NUMBER
    ]
    digits = max((sum(c.isdigit() for c in number) for number in numbers), default=0)
    exponents = [
        n.right.value for n in nodes
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Pow)
        and isinstance(n.right, ast.Constant)
    ]
    return len(nodes), len(calls), digits, max(exponents, default=1)


def test_formula_size_sentence_states_the_longest_snippet_formula(monkeypatch):
    """The sentence on api/core_models.md, by an AST count and by the guard itself."""
    text = _flat(_page("api/core_models.md"))
    assert "The largest formula in this documentation uses 6 digits" not in text
    match = re.search(
        r"has (\d+) terms and (\d+) calls, number literals of at most (\d+) digits "
        r"and no exponent above (\d+)", text,
    )
    assert match, "formula-size sentence missing"
    terms, calls, digits, exponent = (int(g) for g in match.groups())
    sizes = {f: _size(f) for f in _snippet_formulas()}
    longest = max(sizes, key=lambda f: sizes[f][0])
    assert sizes[longest][:2] == (terms, calls)
    assert max(s[2] for s in sizes.values()) == digits
    assert max(s[3] for s in sizes.values()) == exponent
    # Second way: the guard accepts the formula at exactly these limits and
    # refuses it one below -- it counts what the page says it counts.
    validations.assert_safe_formula(longest)
    monkeypatch.setattr(validations, "_MAX_FORMULA_TERMS", terms)
    monkeypatch.setattr(validations, "_MAX_FORMULA_CALLS", calls)
    validations.assert_safe_formula(longest)
    monkeypatch.setattr(validations, "_MAX_FORMULA_TERMS", terms - 1)
    with pytest.raises(ValueError, match="terms"):
        validations.assert_safe_formula(longest)
    monkeypatch.setattr(validations, "_MAX_FORMULA_TERMS", terms)
    monkeypatch.setattr(validations, "_MAX_FORMULA_CALLS", calls - 1)
    with pytest.raises(ValueError, match="function calls"):
        validations.assert_safe_formula(longest)
    monkeypatch.setattr(validations, "_MAX_FORMULA_CALLS", calls)
    widest = max(sizes, key=lambda f: sizes[f][2])
    monkeypatch.setattr(validations, "_MAX_LITERAL_DIGITS", digits)
    validations.assert_safe_formula(widest)
    monkeypatch.setattr(validations, "_MAX_LITERAL_DIGITS", digits - 1)
    with pytest.raises(ValueError, match="digits"):
        validations.assert_safe_formula(widest)


# ---------------------------------------------------------------------------
# DOC3, DOC8, DOC9, DOC10 and two stale statements found on the way
# ---------------------------------------------------------------------------

#: Patterns that teach the path enumeration as a required step (DOC3), with
#: the pages where it is the subject.
STALE_PATTERNS = {
    r"\bcreate_paths\(": {"api/pathfinder.md", "howto/performance.md"},
    r"auto_parallel_coefficients\s*=\s*True": {"api/pathfinder.md", "howto/performance.md"},
    r"`define_paths`\s*(—|--|-)\s*if no paths": set(),
    r"Z_G[^|\n]{0,40}as RMS": set(),
}


def _doc_pages():
    for path in sorted(DOCS.rglob("*.md")):
        relative = path.relative_to(DOCS).as_posix()
        if relative in RESTRUCTURED_ELSEWHERE or relative == "audit-log.md":
            continue
        yield relative, path.read_text(encoding="utf-8")


def test_no_documentation_page_teaches_the_path_enumeration_as_a_step():
    """DOC3: run_fault needs no create_paths; a deprecation note may mention the old switch."""
    offending = []
    for relative, text in _doc_pages():
        for pattern, allowed in STALE_PATTERNS.items():
            if relative in allowed:
                continue
            for line in text.splitlines():
                if re.search(pattern, line) and "deprecat" not in line.lower():
                    offending.append(f"{relative}: {line.strip()[:100]}")
    assert offending == []


def test_epr_limit_is_not_called_a_touch_voltage_limit():
    """DOC8: the EPR limit of the searches is not the touch voltage, and EPR is no proxy for it."""
    offending = []
    for relative, text in _doc_pages():
        flat = _flat(text)
        if re.search(r"touch[- ]voltage proxy", flat, re.I):
            offending.append(f"{relative}: touch-voltage proxy")
        for block in re.findall(r"```python.*?```", text, re.S):
            if re.search(r"#.*touch[- ]voltage limit", block, re.I):
                offending.append(f"{relative}: code comment calls u_limit a touch-voltage limit")
    for snippet in sorted((DOCS / "snippets").glob("*.py")):
        if re.search(r"#.*touch[- ]voltage limit", snippet.read_text(encoding="utf-8"), re.I):
            offending.append(snippet.name)
    assert offending == []
    analysis = _flat(_page("api/analysis.md"))
    assert "exceeds a touch-voltage limit" not in analysis
    assert "../howto/en50522-check.md" in analysis


def test_database_page_has_no_adr_reference():
    """DOC9: no ADR directory exists in this repository."""
    assert "ADR-" not in _page("api/database.md")


def test_outage_page_documents_the_text_frequency_column():
    """DOC10: the comparison frames carry frequency_Hz as text with an "RMS" row."""
    text = _flat(_page("api/outage.md"))
    assert '`frequency_Hz` is text' in text and '`"RMS"`' in text
    station = gi.BusType(name="s", system_type="Station", voltage_level=20.0, impedance_formula="2.0")
    cable = gi.BranchType(name="c", grounding_conductor=True,
                          self_impedance_formula="(0.2 + j*0.4)*l",
                          mutual_impedance_formula="(j*0.3)*l")
    net = gi.create_network(name="o", frequencies=[50.0])
    for name in "ABC":
        gi.create_bus(name=name, type=station, network=net)
    gi.create_branch(name="AB", type=cable, from_bus="A", to_bus="B", length=1.0, network=net)
    gi.create_branch(name="BC", type=cable, from_bus="B", to_bus="C", length=1.0, network=net)
    gi.create_source(name="Q", bus="A", values={50.0: 100.0}, network=net)
    gi.create_fault(name="F", bus="C", network=net)
    study = gi.run_outage_study(net, fault="F", scenarios=[gi.Outage(name="x", disabled_branches=["BC"])])
    frame = study.compare_buses()
    assert frame.schema["frequency_Hz"] == pl.String
    assert set(frame["frequency_Hz"]) == {"50.0", "RMS"}
    assert net.res_buses(fault="F").schema["frequency_Hz"] == pl.Float64


def test_analysis_page_states_the_steel_default_of_the_code():
    """The steel default is 300 degC since 0.5.0; the page claimed 400 degC."""
    text = _flat(_page("api/analysis.md"))
    assert "default of 400 °C" not in text
    assert gi.IEC60949_MATERIALS["Steel"]["theta_final_default_C"] == 300.0
    assert "300 °C" in text


def test_analysis_page_lists_every_search_status():
    """The status table of the rho searches names all statuses of the code."""
    bisection = importlib.import_module("groundinsight.analysis._bisection")
    statuses = {v for k, v in vars(bisection).items() if k.startswith("STATUS_")}
    text = _page("api/analysis.md")
    assert statuses and all(f'`"{status}"`' in text for status in statuses)
