# tests/test_audit_pass19_docs_reference.py
"""
Audit pass 19 -- the reference pages for result tables and warnings come from the code.

Finding IDs: DOC6/DOC12 (no reference of the result columns and of the
warnings), DOC8 (both reduction factors labelled wherever a table shows them),
DOC10 (``frequency_Hz`` is ``Float64`` with a null RMS row in the ``res_*``
frames and text in the outage comparison frames), GUI4/STD5 (a program
branches on the diagnostic code, so every code needs one documented meaning).

``reference/results.md`` carries markers that the build hook
``docs/hooks/result_tables.py`` replaces with one table per frame: column
names and dtypes come from the schema constants of the code (or, where a frame
has none, from a frame the hook builds on a probe network), units and meanings
from the hook's descriptions. The tests hold the hook to the code two ways --
the constant against a frame of a real solve, and the descriptions against the
columns -- so a new column cannot reach a release undocumented.

``reference/warnings.md`` is written by hand; the tests scan ``src/`` for every
code (``code="..."`` in ``emit`` calls and in records built directly, the
``default_code`` of the warning classes, the keys of ``CHECK_CODES``) and hold
the page to that set in both directions, with the category of each code and
the severity of each check code.
"""

import ast
import re
import types
from pathlib import Path

import polars as pl
import pytest

import groundinsight as gi
from groundinsight.checks import CHECK_CODES
from groundinsight.diagnostics import GroundinsightWarning

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
SRC = ROOT / "src" / "groundinsight"
HOOK = DOCS / "hooks" / "result_tables.py"


def _page(relative):
    """Text of a documentation page."""
    return (DOCS / relative).read_text(encoding="utf-8")


def _flat(text):
    """Collapse whitespace so a sentence wrapped over lines matches."""
    return " ".join(text.split())


@pytest.fixture(scope="module")
def hook():
    """The result-table build hook, imported from its file."""
    module = types.ModuleType("result_tables_hook")
    module.__file__ = str(HOOK)
    exec(compile(HOOK.read_text(encoding="utf-8"), str(HOOK), "exec"), module.__dict__)
    return module


# ---------------------------------------------------------------------------
# reference/results.md
# ---------------------------------------------------------------------------


def test_results_page_markers_name_known_frames(hook):
    """Every marker on the page is a frame the hook knows, and every frame is on the page."""
    keys = re.findall(r"<!-- result-table: ([\w-]+) -->", _page("reference/results.md"))
    assert keys, "no result-table markers on reference/results.md"
    assert len(keys) == len(set(keys))
    assert set(keys) == set(hook.FRAMES)


@pytest.mark.parametrize("key", [
    "res_buses", "res_branches", "res_all_impedances", "res_fault", "compare_buses",
    "compare_branches", "cut_analysis", "conductor_limits", "node_limits", "transient",
    "reference_cases", "rho_f_catalog", "pandapower_preview", "shortcircuit_results",
    "shortcircuit_report",
])
def test_every_column_has_a_unit_and_a_meaning(hook, key):
    """The descriptions cover exactly the columns of the frame, in order."""
    schema = hook.schema_of(key)
    described = hook.FRAMES[key].columns
    assert list(schema) == list(described), key
    for column, (unit, meaning) in described.items():
        assert unit and len(meaning.strip()) >= 8, (key, column)


def test_hook_replaces_every_marker_with_a_table(hook):
    """The rendered page has a table row for every column of every frame."""
    rendered = hook.render(_page("reference/results.md"))
    assert "<!-- result-table:" not in rendered
    for key in hook.FRAMES:
        for column, dtype in hook.schema_of(key).items():
            assert f"| `{column}` | {dtype} |" in rendered, (key, column)


def test_hook_refuses_an_unknown_marker(hook):
    with pytest.raises(ValueError, match="unknown result table"):
        hook.render("<!-- result-table: no_such_frame -->")


def test_hook_is_called_by_the_registered_build_hook():
    """reference_cases.py is the registered hook; it hands the page on (idempotently)."""
    text = (DOCS / "hooks" / "reference_cases.py").read_text(encoding="utf-8")
    assert 'with_name("result_tables.py")' in text and "result_tables.render(" in text


def _solved_network():
    """A three-bus line with two frequencies, solved for one fault."""
    station = gi.BusType(name="s", system_type="Station", voltage_level=20.0, impedance_formula="2.0")
    cable = gi.BranchType(
        name="c", grounding_conductor=True,
        self_impedance_formula="(0.2 + j*2*pi*f*1.2e-3)*l",
        mutual_impedance_formula="(j*2*pi*f*1.0e-3)*l",
    )
    net = gi.create_network(name="n", frequencies=[50.0, 250.0])
    for name in "ABC":
        gi.create_bus(name=name, type=station, network=net)
    gi.create_branch(name="AB", type=cable, from_bus="A", to_bus="B", length=1.0, network=net)
    gi.create_branch(name="BC", type=cable, from_bus="B", to_bus="C", length=1.0, network=net)
    gi.create_source(name="Q", bus="A", values={50.0: 1000.0, 250.0: 50.0}, network=net)
    gi.create_fault(name="F", bus="C", network=net)
    gi.run_fault(net, "F")
    return net


def test_schema_constants_are_the_frames_of_a_real_solve(hook):
    """Second way: the constants the tables come from are what the accessors return."""
    net = _solved_network()
    frames = {
        "res_buses": net.res_buses(fault="F"),
        "res_branches": net.res_branches(fault="F"),
        "res_all_impedances": net.res_all_impedances(),
        "res_fault": net.res_fault("F"),
        "cut_analysis": gi.analyze_cuts(net, fault="F", cuts=[gi.Cut(name="up", branches=["BC"])]).to_polars(),
        "conductor_limits": gi.check_conductor_limits(net, "F", t_k_s=0.5, r_to_x=0.1),
        "node_limits": gi.check_node_limits(net, "F", t_k_s=0.5, r_to_x=0.1),
    }
    for key, frame in frames.items():
        assert dict(frame.schema) == dict(hook.schema_of(key)), key


def test_results_page_states_the_rms_row_and_both_dtypes_of_frequency():
    """DOC10: Float64 with a null RMS row in res_*, text with "RMS" in the outage frames."""
    text = _flat(_page("reference/results.md"))
    assert "`is_rms`" in text and "null" in text
    assert "`frequency_Hz` is text" in text and '`"RMS"`' in text
    net = _solved_network()
    rms = net.res_buses(fault="F").filter(pl.col("is_rms"))
    assert rms["frequency_Hz"].null_count() == rms.height == 3


def test_results_page_labels_both_reduction_factors():
    """DOC8: wherever a factor appears, the page says which one is the EN 50522 r."""
    text = _flat(_page("reference/results.md"))
    for phrase in ("`reduction_factor_current`", "`r_current`", "`reduction_factor`", "`r_epr`"):
        assert phrase in text, phrase
    assert text.count("EN 50522") >= 3


# ---------------------------------------------------------------------------
# reference/warnings.md
# ---------------------------------------------------------------------------


def _categories():
    """Package warning classes by name, from the public namespace."""
    return {
        name: obj for name, obj in vars(gi).items()
        if isinstance(obj, type) and issubclass(obj, GroundinsightWarning)
    }


def scan_codes():
    """
    Every diagnostic code the package can produce, with its category.

    Returns
    -------
    dict of str to set of str
        Code -> names of the categories it is raised with (``"check_network"``
        for the codes of the pre-flight check, ``"Diagnostic"`` for records
        built directly).
    """
    codes = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                for keyword in node.keywords:
                    if (
                        keyword.arg == "code"
                        and isinstance(keyword.value, ast.Constant)
                        and isinstance(keyword.value.value, str)
                    ):
                        if name == "emit" and node.args:
                            category = ast.unparse(node.args[0]).split(".")[-1]
                        else:
                            category = "Diagnostic"
                        codes.setdefault(keyword.value.value, set()).add(category)
            elif isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=True):
                    if (
                        isinstance(key, ast.Constant) and key.value == "code"
                        and isinstance(value, ast.Constant) and isinstance(value.value, str)
                    ):
                        codes.setdefault(value.value, set()).add("Diagnostic")
            elif isinstance(node, ast.ClassDef):
                for statement in node.body:
                    if (
                        isinstance(statement, (ast.Assign, ast.AnnAssign))
                        and getattr(statement, "value", None) is not None
                        and isinstance(statement.value, ast.Constant)
                    ):
                        targets = (
                            statement.targets if isinstance(statement, ast.Assign)
                            else [statement.target]
                        )
                        if any(getattr(t, "id", None) == "default_code" for t in targets):
                            codes.setdefault(statement.value.value, set()).add(node.name)
    for code in CHECK_CODES:
        codes.setdefault(code, set()).add("check_network")
    return codes


def _page_rows():
    """``{code: row text}`` of the code tables of reference/warnings.md."""
    rows = {}
    for line in _page("reference/warnings.md").splitlines():
        match = re.match(r"^\|\s*`([A-Z][A-Z0-9_]+)`\s*\|(.*)$", line)
        if match:
            assert match.group(1) not in rows, f"{match.group(1)} has two rows"
            rows[match.group(1)] = match.group(2)
    return rows


def test_scan_finds_the_known_codes():
    """The scanner sees each kind of definition (guards the scanner itself)."""
    codes = scan_codes()
    assert {"PHASE_PROXY", "IMPORT_EMPTY"} <= set(codes)           # emit(...)
    assert "LEGACY_EARTHING_DEFINITION" in codes                    # a record built directly
    assert {"DC_LIMIT", "TRANSIENT_MODEL", "FREQUENCY_ORDER"} <= set(codes)  # default_code
    assert {"FLOATING_ISLAND", "NOT_A_NETWORK"} <= set(codes)       # CHECK_CODES
    assert codes["PHASE_PROXY"] == {"ModelAssumptionWarning"}


def test_every_code_of_the_package_is_on_the_warnings_page():
    missing = sorted(set(scan_codes()) - set(_page_rows()))
    assert missing == [], f"codes without a row on reference/warnings.md: {missing}"


def test_every_code_on_the_warnings_page_exists_in_the_package():
    unknown = sorted(set(_page_rows()) - set(scan_codes()))
    assert unknown == [], f"rows for codes the package does not produce: {unknown}"


def test_warnings_page_names_the_category_of_every_code():
    """The row of a warning code names the class it is raised with."""
    rows = _page_rows()
    for code, categories in scan_codes().items():
        for category in categories - {"check_network", "Diagnostic"}:
            assert f"`{category}`" in rows[code], (code, category)


def test_warnings_page_states_the_severity_of_every_check_code():
    rows = _page_rows()
    for code, (severity, _meaning) in CHECK_CODES.items():
        assert re.search(rf"\|\s*{severity}\s*\|", rows[code]), (code, severity)


def test_warnings_page_describes_every_warning_class():
    """Each category: its default code and its base class."""
    text = _page("reference/warnings.md")
    for name, cls in _categories().items():
        assert f"`{name}`" in text, name
        assert f"`{cls.default_code}`" in text, name
    flat = _flat(text)
    assert "`GroundinsightWarning`" in flat and "UserWarning" in flat
    assert "DeprecationWarning" in flat


def test_warnings_page_explains_how_to_receive_the_records():
    text = _flat(_page("reference/warnings.md"))
    for phrase in ("Result.diagnostics", "collecting()", "check_network", "severity", "elements"):
        assert phrase in text, phrase
