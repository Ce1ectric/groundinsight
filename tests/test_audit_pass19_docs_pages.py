# tests/test_audit_pass19_docs_pages.py
"""
Audit pass 19 -- the new reference and how-to pages say what the code does.

Finding IDs: DOC6 (conventions and formula syntax only in docstrings), DOC7
item 3 (``Z_B`` for the bus electrode, never ``Z_E``), DOC8 (which reduction
factor is the EN 50522 one), DOC12 (Diataxis structure), DOC13 (code blocks
run), PH8 (sign frame), USE2/SEC1 (formula symbols and guard limits), and the
maintainer's documentation decision D1.

Every number or rule these pages state is recomputed here from the code: the
whitelist and the limits are read from :mod:`groundinsight.utils.validations`,
the error table is replayed against the guard, the sign-frame example is
solved by ``run_fault`` and by hand (closed form of the two-bus loop), and the
RMS convention is checked against a transient run.
"""

import ast
import cmath
import math
import re
import warnings
from pathlib import Path

import numpy as np
import pytest

import groundinsight as gi
from groundinsight.utils import validations
from groundinsight.utils.impedance_calculator import compute_impedance

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

NEW_PAGES = {
    "howto/types-from-datasheets.md": "How-to guides",
    "reference/conventions.md": "Reference",
    "reference/formula-syntax.md": "Reference",
    "reference/glossary.md": "Reference",
}

#: Decision D10, verbatim.
def _page(relative):
    """Text of a documentation page."""
    return (DOCS / relative).read_text(encoding="utf-8")


def _flat(text):
    """Collapse whitespace so a sentence wrapped over lines matches."""
    return " ".join(text.split())


def _python_blocks(text):
    """
    The fenced ``python`` blocks of a page, without snippet includes.

    Parameters
    ----------
    text : str
        Markdown source.

    Returns
    -------
    list of str
        Block bodies, dedented to the fence's indentation.
    """
    blocks = []
    pattern = re.compile(r"^( *)```python[^\n]*\n(.*?)^\1```", re.M | re.S)
    for match in pattern.finditer(text):
        indent = len(match.group(1))
        body = "\n".join(line[indent:] for line in match.group(2).splitlines())
        if "--8<--" in body:
            continue
        blocks.append(body)
    return blocks


def _names_field(text, field):
    """Whether ``text`` names ``field`` in code style, bare or qualified
    (``value`` or ``ResultReductionFactor.value``)."""
    return re.search(r"`(?:\w+\.)*" + re.escape(field) + r"`", text) is not None


def _nav_sections():
    """``{page: top-level nav section}`` from ``mkdocs.yml``."""
    yaml = pytest.importorskip("yaml")

    class _Loader(yaml.SafeLoader):
        pass

    _Loader.add_multi_constructor("tag:yaml.org,2002:python/", lambda loader, suffix, node: None)
    config = yaml.load((ROOT / "mkdocs.yml").read_text(encoding="utf-8"), Loader=_Loader)
    sections = {}

    def walk(items, top):
        for item in items:
            if isinstance(item, dict):
                for title, value in item.items():
                    if isinstance(value, list):
                        walk(value, top or title)
                    else:
                        sections[value] = top
            else:
                sections[item] = top

    walk(config["nav"], None)
    return sections, config


# --- structure ----------------------------------------------------------------


def test_new_pages_are_in_the_nav_at_their_diataxis_place():
    """Each new page sits in its Diataxis section; the sections come in the
    order of the target structure (how-tos and explanation before the
    examples, reference after them)."""
    sections, config = _nav_sections()
    for page, section in NEW_PAGES.items():
        assert (DOCS / page).is_file(), page
        assert sections.get(page) == section, (page, sections.get(page))
    order = [next(iter(item)) for item in config["nav"] if isinstance(item, dict)]
    assert order.index("How-to guides") < order.index("Examples")
    assert order.index("Examples") < order.index("Reference")


def test_snippet_includes_point_at_existing_sections():
    """Every ``--8<--`` include on the new pages names an existing file and,
    where given, a section that the file marks with start and end."""
    pattern = re.compile(r'--8<-- "([^":]+)(?::([\w-]+))?"')
    seen = 0
    for page in NEW_PAGES:
        for path, section in pattern.findall(_page(page)):
            source = ROOT / path
            assert source.is_file(), (page, path)
            if section:
                text = source.read_text(encoding="utf-8")
                assert f"--8<-- [start:{section}]" in text, (page, path, section)
                assert f"--8<-- [end:{section}]" in text, (page, path, section)
            seen += 1
    assert seen >= 5


# --- formula syntax -----------------------------------------------------------


def test_formula_syntax_lists_every_whitelisted_function():
    """Each name the guard lets a formula call is on the page, in code style."""
    text = _page("reference/formula-syntax.md")
    missing = [name for name in sorted(validations._ALLOWED_FORMULA_FUNCTIONS) if f"`{name}`" not in text]
    assert not missing


def test_formula_syntax_states_the_symbols():
    """Bus and branch symbols and the constants, as the evaluator binds them."""
    text = _page("reference/formula-syntax.md")
    for name in validations.BRANCH_FORMULA_SYMBOLS + validations.FORMULA_CONSTANTS:
        assert f"`{name}`" in text, name
    assert validations.BUS_FORMULA_SYMBOLS == ("f", "rho")
    assert "only in branch formulas" in _flat(text)


def test_formula_syntax_states_the_guard_limits():
    """The limits table carries the values of the code constants."""
    flat = _flat(_page("reference/formula-syntax.md"))
    limits = {
        "characters in a formula string": validations._MAX_FORMULA_LENGTH,
        "digits of a number literal": validations._MAX_LITERAL_DIGITS,
        "exponent of a power": validations._MAX_LITERAL_EXPONENT,
        "digits of an exact intermediate number": validations._MAX_EXACT_DIGITS,
        "digits of the integer built by `floor`/`ceiling`": validations._MAX_INTEGER_PART_DIGITS,
        "terms (numbers, names, operations and calls)": validations._MAX_FORMULA_TERMS,
        "function calls": validations._MAX_FORMULA_CALLS,
        "arguments of one call": validations._MAX_CALL_ARGUMENTS,
    }
    for label, value in limits.items():
        row = re.search(r"\| " + re.escape(label) + r" \| ([^|]+) \|", flat)
        assert row, label
        number = int(re.sub(r"[^\d]", "", row.group(1).split("(")[0]))
        assert number == value, (label, row.group(1), value)
    exp_row = re.search(r"\| argument of `exp`, `sinh`, `cosh` \| ([^|]+) \|", flat)
    assert exp_row and f"10^{validations._MAX_EXP_ARGUMENT_DIGITS}" in exp_row.group(1).replace("<sup>", "^").replace("</sup>", "")


def _error_table(text, header):
    """Rows ``(formula, excerpt)`` of the table that starts with ``header``."""
    rows = []
    start = text.index(header)
    for line in text[start:].splitlines()[2:]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        formula = re.search(r"`([^`]+)`", cells[0]).group(1)
        excerpt = re.search(r"`([^`]+)`", cells[1]).group(1)
        rows.append((formula, excerpt))
    return rows


def test_formula_syntax_error_table_is_what_the_guard_says():
    """Replay every documented mistake: a BranchType with that self-impedance
    formula fails with a message containing the documented excerpt."""
    rows = _error_table(_page("reference/formula-syntax.md"), "| Formula | Message (excerpt) |")
    assert len(rows) >= 10
    for formula, excerpt in rows:
        with pytest.raises(ValueError) as caught:
            gi.BranchType(
                name="t", grounding_conductor=True,
                self_impedance_formula=formula, mutual_impedance_formula="0",
            )
        assert excerpt in str(caught.value), (formula, excerpt, str(caught.value)[:300])


def test_formula_syntax_evaluation_errors_are_what_the_model_says():
    """The two value errors at ``create_bus`` the page quotes."""
    rows = _error_table(_page("reference/formula-syntax.md"), "| Bus formula | Message (excerpt) |")
    assert len(rows) == 2
    for formula, excerpt in rows:
        bus_type = gi.BusType(name="b", system_type="Station", voltage_level=20.0, impedance_formula=formula)
        net = gi.create_network(name="n", frequencies=[50.0])
        with pytest.raises(ValueError, match=re.escape(excerpt)):
            gi.create_bus(name="B", type=bus_type, specific_earth_resistance=100.0, network=net)


def test_page_formulas_pass_the_guard_and_evaluate():
    """Every formula string a new page passes to a type passes the guard and the
    symbol check and evaluates at 0, 50 and 250 Hz without NaN."""
    found = 0
    for page in NEW_PAGES:
        for block in _python_blocks(_page(page)):
            tree = ast.parse(block)
            for node in ast.walk(tree):
                if not isinstance(node, ast.keyword) or not (node.arg or "").endswith("formula"):
                    continue
                if not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
                    continue
                formula = node.value.value
                validations.assert_safe_formula(formula)
                is_branch = re.search(r"\bl\b", formula) is not None
                symbols = validations.BRANCH_FORMULA_SYMBOLS if is_branch else validations.BUS_FORMULA_SYMBOLS
                validations.check_formula_symbols(formula, symbols)
                params = {"rho": 100.0, "l": 1.0} if is_branch else {"rho": 100.0}
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    values = compute_impedance(formula, [0.0, 50.0, 250.0], params)
                assert all(not math.isnan(v.real) and not math.isnan(v.imag) for v in values.values())
                found += 1
    assert found >= 8


def test_types_page_shows_the_snippet_formulas():
    """The literal ``BranchType`` calls on the how-to page are exactly the
    formulas the snippet builds and checks three ways."""
    import runpy

    ns = runpy.run_path(str(DOCS / "snippets" / "types_from_datasheets.py"), run_name="docs_snippet")
    literal = {}
    for block in _python_blocks(_page("howto/types-from-datasheets.md")):
        for node in ast.walk(ast.parse(block)):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "BranchType":
                fields = {kw.arg: kw.value.value for kw in node.keywords if isinstance(kw.value, ast.Constant)}
                literal[fields["name"]] = fields
    for key in ("cable", "cable_system", "overhead_line"):
        branch_type = ns[key]
        shown = literal[branch_type.name]
        for field in ("self_impedance_formula", "mutual_impedance_formula", "phase_impedance_formula"):
            assert shown.get(field) == getattr(branch_type, field), (key, field)


@pytest.mark.parametrize("page", sorted(NEW_PAGES))
def test_python_blocks_on_new_pages_run(page, tmp_path, monkeypatch):
    """The python blocks of a page run, in order, in one namespace (DOC13)."""
    monkeypatch.chdir(tmp_path)
    namespace = {"__name__": "docs_page"}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for block in _python_blocks(_page(page)):
            exec(compile(block, f"{page}", "exec"), namespace)


# --- conventions --------------------------------------------------------------


def _two_bus_example():
    """The two-bus network of the sign-convention section, solved."""
    s_type = gi.BusType(name="S", system_type="Substation", voltage_level=20.0, impedance_formula="0.5")
    f_type = gi.BusType(name="F", system_type="Station", voltage_level=20.0, impedance_formula="2.0")
    line = gi.BranchType(
        name="line", grounding_conductor=True,
        self_impedance_formula="(0.2 + 0.4*j)*l", mutual_impedance_formula="(0.05 + 0.35*j)*l",
    )
    net = gi.create_network(name="signs", frequencies=[50.0])
    gi.create_bus(name="S", type=s_type, network=net)
    gi.create_bus(name="F", type=f_type, network=net)
    gi.create_branch(name="L", type=line, from_bus="S", to_bus="F", length=5.0, network=net)
    gi.create_source(name="Q", bus="S", values={50.0: 1000.0}, network=net)
    gi.create_fault(name="F1", bus="F", scalings={50.0: 1.0}, network=net)
    return net, gi.run_fault(net, "F1")


def test_conventions_sign_frame_example():
    """The two-bus example: package values, and the physical loop by hand.

    Hand calculation (second way): with the phase current I = 1000 A from S to
    F, the shield returns ``I_s = I*(Z_S + Z_F + Z_m)/(Z_S + Z_F + Z_s)`` from F
    to S, the soil carries ``I_E = I - I_s``, and ``u_F = Z_F*I_E``,
    ``u_S = -Z_S*I_E``. The package reports the negatives of the potentials,
    electrode currents and injections, and the shield current counted from
    ``from_bus`` to ``to_bus``.
    """
    net, result = _two_bus_example()
    buses = {b.name: b for b in result.buses}

    def c(value):
        return complex(value.real, value.imag)

    i = 1000.0
    z_s, z_m, z_bs, z_bf = (0.2 + 0.4j) * 5, (0.05 + 0.35j) * 5, 0.5, 2.0
    i_shield_f_to_s = i * (z_bs + z_bf + z_m) / (z_bs + z_bf + z_s)
    i_earth = i - i_shield_f_to_s
    u_f_phys, u_s_phys = z_bf * i_earth, -z_bs * i_earth
    assert c(buses["F"].uepr_freq[50.0]) == pytest.approx(-u_f_phys, abs=1e-9)
    assert c(buses["S"].uepr_freq[50.0]) == pytest.approx(-u_s_phys, abs=1e-9)
    assert c(buses["F"].ia_freq[50.0]) == pytest.approx(-u_f_phys / z_bf, abs=1e-9)
    assert c(buses["S"].i_inj_freq[50.0]) == pytest.approx(1000.0, abs=1e-9)
    assert c(buses["F"].i_inj_freq[50.0]) == pytest.approx(-1000.0, abs=1e-9)
    assert c(result.branches[0].i_s_freq[50.0]) == pytest.approx(-i_shield_f_to_s, abs=1e-9)
    assert complex(net.electrical_network.phase_currents[50.0]["L"]) == pytest.approx(1000.0)
    # the numbers printed on the page
    text = _flat(_page("reference/conventions.md"))
    for shown in ("−384.6 + 76.9j", "+384.6 − 76.9j", "96.2 − 19.2j", "−807.7 − 38.5j", "168.7°", "−11.3°"):
        assert shown in text, shown
    assert -u_f_phys == pytest.approx(complex(-384.615, 76.923), abs=1e-3)
    assert math.degrees(cmath.phase(-u_f_phys)) == pytest.approx(168.69, abs=0.01)


def test_conventions_rms_phasor_and_peak_amplitude():
    """``values`` are RMS phasors, waveform amplitudes are peaks: a transient run
    with amplitude sqrt(2)*I reproduces the stationary RMS of a run with I."""
    bus = gi.BusType(name="b", system_type="Station", voltage_level=20.0, impedance_formula="2.0")
    line = gi.BranchType(
        name="k", grounding_conductor=True,
        self_impedance_formula="(0.2 + j*2*pi*f*1.2e-3)*l", mutual_impedance_formula="0",
    )
    net = gi.create_network(name="rms", frequencies=[50.0])
    gi.create_bus(name="S", type=bus, network=net)
    gi.create_bus(name="F", type=bus, network=net)
    gi.create_branch(name="K", type=line, from_bus="S", to_bus="F", length=2.0, network=net)
    gi.create_source(name="Q", bus="S", values={50.0: 1000.0}, network=net)
    gi.create_fault(name="F1", bus="F", scalings={50.0: 1.0}, network=net)
    result = gi.run_fault(net, "F1")
    u_rms = {b.name: b.uepr for b in result.buses}["F"]
    study = gi.TransientStudy(net, "F1")
    study.set_source_waveform(
        "Q", gi.waveforms.sinusoidal_with_dc_offset(amplitude=math.sqrt(2) * 1000.0, frequency_hz=50.0)
    )
    study.set_observation(buses=["F"])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        transient = study.solve(t_end=0.2, dt=1e-5, solver="fft")
    period = int(round(0.02 / 1e-5))
    last = np.asarray(transient.epr_t["F"])[-period:]
    assert math.sqrt(np.mean(last**2)) == pytest.approx(u_rms, rel=1e-9)
    assert np.max(np.abs(last)) == pytest.approx(math.sqrt(2) * u_rms, rel=1e-4)
    assert u_rms == pytest.approx(382.39, abs=0.005)
    # time series in the earth-side frame: at the source peak the EPR equals
    # Re(sqrt(2) * u_pkg), negative for resistive earthing (second way: phasor)
    source = np.asarray(transient.source_t["Q"])[-period:]
    u_pkg = {b.name: b.uepr_freq[50.0] for b in result.buses}["F"]
    at_peak = last[int(np.argmax(source))]
    assert at_peak == pytest.approx(math.sqrt(2) * u_pkg.real, rel=1e-6)
    assert at_peak < 0


def test_conventions_missing_scaling_is_one():
    """A frequency missing from a non-empty ``scalings`` counts 1.0 and is
    announced once (``SCALINGS_INCOMPLETE``); ``None`` means 1.0 everywhere."""

    def solve(scalings):
        bus = gi.BusType(name="b", system_type="Station", voltage_level=20.0, impedance_formula="2.0")
        line = gi.BranchType(
            name="k", grounding_conductor=True,
            self_impedance_formula="(0.2 + j*f*0.008)*l", mutual_impedance_formula="(j*f*0.007)*l",
        )
        net = gi.create_network(name="s", frequencies=[50.0, 250.0])
        gi.create_bus(name="S", type=bus, network=net)
        gi.create_bus(name="F", type=bus, network=net)
        gi.create_branch(name="K", type=line, from_bus="S", to_bus="F", length=2.0, network=net)
        gi.create_source(name="Q", bus="S", values={50.0: 1000.0, 250.0: 100.0}, network=net)
        gi.create_fault(name="F1", bus="F", scalings=scalings, network=net)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = gi.run_fault(net, "F1")
        return {b.name: b.uepr for b in result.buses}["F"], [d.code for d in result.diagnostics]

    full, codes_full = solve({50.0: 1.0, 250.0: 1.0})
    partial, codes_partial = solve({50.0: 1.0})
    default, codes_default = solve(None)
    assert partial == full == default
    assert "SCALINGS_INCOMPLETE" in codes_partial
    assert "SCALINGS_INCOMPLETE" not in codes_full + codes_default


def test_conventions_name_both_reduction_factors():
    """``value`` is the EPR ratio, ``value_current`` the EN 50522 r."""
    for page in ("reference/conventions.md",):
        flat = _flat(_page(page))
        assert _names_field(flat, "value_current") and _names_field(flat, "value"), page
        assert "EN 50522" in flat, page


# --- glossary -------------------------------------------------------------------


def test_glossary_defines_the_symbols_and_resolves_the_collision():
    """Every symbol of the brief has an entry; ``Z_B`` is the bus electrode and
    ``Z_E`` is never called the bus electrode."""
    text = _page("reference/glossary.md")
    flat_terms = set()
    for cell in re.findall(r"^\| ([^|]+?) \|", text, re.M):
        symbols = re.findall(r"`([^`]+)`", cell)
        flat_terms.update(symbols or [part.strip("* ") for part in cell.split(",")])
    required = {
        "EPR", "U_E", "Z_E", "I_E", "I_F", "3I_0", "r", "k", "Z_s", "Z_m", "Z_red", "Z_B",
        "Z_dp", "D_E", "rho", "RMS", "U_T", "U_Tp", "Z_G",
    }
    assert required <= flat_terms, sorted(required - flat_terms)
    z_b = re.search(r"^\| `Z_B` \|([^\n]+)", text, re.M).group(1)
    assert "electrode" in z_b
    z_e = re.search(r"^\| `Z_E` \|([^\n]+)", text, re.M).group(1)
    assert "driving-point" in z_e and "never" in _flat(text)
