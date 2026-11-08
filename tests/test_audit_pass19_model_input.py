# tests/test_audit_pass19_model_input.py
"""
Audit pass 19 -- model and input invariants (GUI2, ARC9, USE5, USE2, GUI3/F9).

GUI2
    A solved network must survive ``copy.deepcopy``, ``model_copy(deep=True)``
    and ``pickle``. The sparse LU factors kept by the solver are SciPy
    ``SuperLU`` objects and cannot be copied; the copy has to drop them and
    still be fully usable -- including the solver state that
    :func:`groundinsight.bus_response` reads, which must point at the *copy*.
ARC9
    ``create_bus`` / ``create_branch`` / ``Network.add_*`` are atomic: when
    deriving the impedances fails, the network is exactly as it was and an
    element of the same name can be added afterwards.
USE5
    ``Branch.length <= 0`` (and a non-finite length) is rejected where it is
    set, with a message about the length and its unit.
USE2
    Formula symbols outside the evaluator's set are rejected when the type is
    created, naming the symbol, the allowed set and the case-insensitive match.
GUI3 / F9
    Edit invariants: assigning an input field re-derives the element,
    ``Network.frequencies`` re-derives every element, stored results can be
    asked whether they are stale, ``update_*``/``remove_*`` keep the network
    consistent, and types are read-only.

Every numerical assertion is checked against a second way of getting the same
number: a network rebuilt from scratch with the new inputs, or a direct
evaluation of the formula with :func:`compute_impedance`.
"""

import copy
import math
import pickle

import numpy as np
import polars as pl
import pytest

import groundinsight as gi
from groundinsight.utils.impedance_calculator import compute_impedance


FREQUENCIES = [50.0, 250.0]

BUS_FORMULA = "rho * 0.01 + j*f*1e-4"
# Carson-like: the earth-return reactance grows with log(rho), so that a
# change of the branch's rho changes its impedances.
SELF_FORMULA = "(0.25 + j*f*(0.010 + 2e-4*log(rho))) * l"
MUTUAL_FORMULA = "(j*f*(0.010 + 2e-4*log(rho))) * l"
PHASE_FORMULA = "(0.1 + j*f*0.008) * l"


def _bus_type(name="SB", formula=BUS_FORMULA):
    """Bus type with a frequency-dependent electrode impedance."""
    return gi.BusType(
        name=name, system_type="S", voltage_level=20, impedance_formula=formula
    )


def _cable_type(name="C", **overrides):
    """Shielded cable type with a phase-conductor formula (auto mode)."""
    fields = dict(
        name=name,
        grounding_conductor=True,
        self_impedance_formula=SELF_FORMULA,
        mutual_impedance_formula=MUTUAL_FORMULA,
        phase_impedance_formula=PHASE_FORMULA,
    )
    fields.update(overrides)
    return gi.BranchType(**fields)


def build_ring(name="ring", rho=None, length=None, frequencies=None):
    """Four-bus ring a-b-c-d-a, source at ``a``, fault ``F`` at ``c``.

    ``rho`` and ``length`` map element names to values different from the
    defaults (100 Ohm*m, 1 km), so that a test can rebuild exactly the
    network an edit is supposed to produce.
    """
    rho = rho or {}
    length = length or {}
    freqs = list(frequencies or FREQUENCIES)
    net = gi.create_network(name=name, frequencies=freqs)
    bt = _bus_type()
    ct = _cable_type()
    for bus in "abcd":
        gi.create_bus(
            name=bus, type=bt, network=net,
            specific_earth_resistance=rho.get(bus, 100.0),
        )
    for idx, (u, v) in enumerate(["ab", "bc", "cd", "da"]):
        gi.create_branch(
            name=f"l{idx}", type=ct, from_bus=u, to_bus=v,
            length=length.get(f"l{idx}", 1.0),
            specific_earth_resistance=rho.get(f"l{idx}", 100.0),
            network=net,
        )
    gi.create_source(
        name="s", bus="a", values={f: 1000.0 / (1 + i) for i, f in enumerate(freqs)},
        network=net,
    )
    gi.create_fault(
        name="F", bus="c", scalings={f: 1.0 for f in freqs}, network=net
    )
    return net


def _sorted_frame(frame):
    """Sort a result frame so that two of them can be compared row by row."""
    keys = [
        c for c in ("fault_name", "bus_name", "branch_name", "frequency_Hz")
        if c in frame.columns
    ]
    return frame.sort(keys, nulls_last=True)


def assert_frames_close(a, b, rtol, atol):
    """Same schema and rows; float columns agree to ``rtol``/``atol``.

    Written out instead of ``assert_frame_equal(rtol=...)`` because Polars
    renamed those keywords; this comparison works on every supported version.
    """
    a, b = _sorted_frame(a), _sorted_frame(b)
    assert a.schema == b.schema
    assert a.height == b.height
    for name, dtype in a.schema.items():
        if dtype == pl.Float64:
            x = a[name].to_numpy().astype(float)
            y = b[name].to_numpy().astype(float)
            assert np.array_equal(np.isnan(x), np.isnan(y)), name
            mask = ~np.isnan(x)
            np.testing.assert_allclose(x[mask], y[mask], rtol=rtol, atol=atol, err_msg=name)
        else:
            assert a[name].to_list() == b[name].to_list(), name


def assert_same_results(net_a, net_b, fault="F", rtol=1e-14):
    """Bus, branch and impedance frames of two networks agree to ``rtol``."""
    for accessor in ("res_buses", "res_branches"):
        assert_frames_close(
            getattr(net_a, accessor)(fault), getattr(net_b, accessor)(fault),
            rtol=rtol, atol=1e-9,
        )
    assert_frames_close(
        net_a.res_all_impedances(), net_b.res_all_impedances(),
        rtol=rtol, atol=1e-12,
    )


def assert_same_response(resp_a, resp_b, rtol=1e-14):
    """Two bus responses agree on ``z_network`` and every open-circuit voltage."""
    assert resp_a.frequencies == resp_b.frequencies
    for freq in resp_a.frequencies:
        za, zb = resp_a.z_network[freq], resp_b.z_network[freq]
        assert abs(za - zb) <= rtol * abs(zb)
        for bus, ua in resp_a.u_open[freq].items():
            ub = resp_b.u_open[freq][bus]
            assert abs(ua - ub) <= rtol * max(abs(ub), 1e-12)


# ---------------------------------------------------------------------------
# GUI2 -- copy and pickle of a solved network
# ---------------------------------------------------------------------------

_COPY_PATHS = {
    "deepcopy": copy.deepcopy,
    "model_copy_deep": lambda net: net.model_copy(deep=True),
    "pickle": lambda net: pickle.loads(pickle.dumps(net)),
}


@pytest.fixture
def solved_ring():
    """A ring with a stored result and an assembled solver state."""
    net = build_ring()
    gi.run_fault(net, "F")
    return net


@pytest.mark.parametrize("path", sorted(_COPY_PATHS))
def test_solved_network_can_be_copied_and_pickled(solved_ring, path):
    """All three copy paths work after ``run_fault`` (they raised
    ``TypeError: cannot pickle 'SuperLU' object`` since the LU reuse)."""
    clone = _COPY_PATHS[path](solved_ring)
    assert clone is not solved_ring
    assert clone.model_dump_json() == solved_ring.model_dump_json()
    assert_same_results(clone, solved_ring, rtol=0.0)


@pytest.mark.parametrize("path", sorted(_COPY_PATHS))
def test_copied_solver_state_belongs_to_the_copy(solved_ring, path):
    """The copied solver state must describe the copy, not the original.

    ``bus_response`` reads the assembled matrices back and checks them
    against ``electrical_network.network``. If that back-reference pointed at
    the original (or at a second, orphaned copy), an edit of the clone would
    go unnoticed and the response would mix two states.
    """
    clone = _COPY_PATHS[path](solved_ring)
    electrical = clone.electrical_network
    assert electrical is not None
    assert electrical is not solved_ring.electrical_network
    assert electrical.network is clone
    assert electrical.results is clone.results["F"]
    assert electrical.matches_network_state()

    clone.buses["b"].impedance = {
        f: gi.ComplexNumber(real=3.0, imag=0.0) for f in clone.frequencies
    }
    assert not electrical.matches_network_state()
    assert solved_ring.electrical_network.matches_network_state()


@pytest.mark.parametrize("path", sorted(_COPY_PATHS))
def test_copy_is_fully_usable(solved_ring, path):
    """``bus_response`` on the copy without a new solve, then a fresh solve on
    the copy: both identical to the original *and* to a network rebuilt from
    scratch (the second way)."""
    clone = _COPY_PATHS[path](solved_ring)
    reference = build_ring()
    gi.run_fault(reference, "F")

    response_original = gi.bus_response(solved_ring, fault="F")
    assert_same_response(gi.bus_response(clone, fault="F"), response_original)

    gi.run_fault(clone, "F")
    assert_same_results(clone, solved_ring)
    assert_same_results(clone, reference)
    assert_same_response(gi.bus_response(clone, fault="F"), response_original)
    assert_same_response(
        gi.bus_response(clone, fault="F"), gi.bus_response(reference, fault="F")
    )


def test_copy_is_independent_of_the_original(solved_ring):
    """Re-solving an edited copy leaves the original's results and solver
    state untouched."""
    before = solved_ring.model_dump_json()
    clone = copy.deepcopy(solved_ring)
    clone.sources["s"].values = {f: 2000.0 for f in clone.frequencies}
    gi.run_fault(clone, "F")
    assert solved_ring.model_dump_json() == before
    assert solved_ring.electrical_network.network is solved_ring
    epr_clone = clone.res_buses("F").filter(pl.col("is_rms"))["EPR_V"].to_list()
    epr_orig = solved_ring.res_buses("F").filter(pl.col("is_rms"))["EPR_V"].to_list()
    assert epr_clone != epr_orig


def test_reduction_factor_without_cached_factors_matches(solved_ring):
    """The copied solver state has no LU factors; the reduction factor that
    reuses them falls back to a fresh solve and gives the same numbers."""
    clone = copy.deepcopy(solved_ring)
    electrical = clone.electrical_network
    assert not electrical._lu_factors
    electrical.compute_reduction_factors()
    for freq in FREQUENCIES:
        got = clone.results["F"].reduction_factor.value[freq]
        want = solved_ring.results["F"].reduction_factor.value[freq]
        assert got == pytest.approx(want, rel=1e-13)


# ---------------------------------------------------------------------------
# ARC9 -- creating an element is atomic
# ---------------------------------------------------------------------------

#: Formulas that pass every check made on the type and only fail when they
#: are evaluated for an element: a negative resistance (passivity check) and
#: ``0 * log(0)`` at 50 Hz (NaN check). The NaN has to come from the
#: evaluation -- SymPy simplifies ``0/(f - 50)`` to ``0`` already when parsing.
_BUS_FORMULAS_FAILING_ON_EVALUATION = ["-rho", "rho*0.01 + (f - 50)*log(f - 50)"]


@pytest.mark.parametrize("formula", _BUS_FORMULAS_FAILING_ON_EVALUATION)
def test_failed_create_bus_leaves_network_unchanged(formula):
    net = build_ring()
    before = net.model_dump()
    bad = _bus_type(name="bad", formula=formula)
    with pytest.raises(ValueError):
        gi.create_bus(name="x", type=bad, network=net)
    assert net.model_dump() == before
    assert "x" not in net.buses
    # The name is free again: the same call with a working type succeeds.
    gi.create_bus(name="x", type=_bus_type(), network=net)
    assert net.buses["x"].impedance == compute_impedance(
        BUS_FORMULA, FREQUENCIES, {"rho": 100.0}
    )


@pytest.mark.parametrize(
    "overrides",
    [
        # fails in the first step (self impedance, passivity)
        {"self_impedance_formula": "-l"},
        # fails in the last step, after self/mutual/phase were evaluated
        {"R_self_formula": "j*l"},
    ],
    ids=["first_step", "last_step"],
)
def test_failed_create_branch_leaves_network_unchanged(overrides):
    net = build_ring()
    before = net.model_dump()
    bad = _cable_type(name="bad", **overrides)
    with pytest.raises(ValueError):
        gi.create_branch(
            name="x", type=bad, from_bus="a", to_bus="c", length=1.0, network=net
        )
    assert net.model_dump() == before
    assert "x" not in net.branches
    gi.create_branch(
        name="x", type=_cable_type(), from_bus="a", to_bus="c", length=2.0, network=net
    )
    assert net.branches["x"].self_impedance == compute_impedance(
        SELF_FORMULA, FREQUENCIES, {"rho": 100.0, "l": 2.0}
    )


def test_failed_add_leaves_the_element_object_untouched():
    """A derivation that fails half-way must not leave the element object
    half-derived either -- it may be reused by the caller."""
    net = build_ring()
    branch = gi.Branch(
        name="x", type=_cable_type(name="bad", R_self_formula="j*l"),
        length=1.0, from_bus="a", to_bus="c",
        self_impedance={}, mutual_impedance={},
    )
    snapshot = branch.model_dump()
    with pytest.raises(ValueError):
        net.add_branch(branch)
    assert branch.model_dump() == snapshot


def test_failed_overwrite_keeps_the_previous_element():
    net = build_ring()
    original = net.buses["b"]
    before = net.model_dump()
    replacement = gi.Bus(name="b", type=_bus_type(name="bad", formula="-rho"), impedance={})
    with pytest.raises(ValueError):
        net.add_bus(replacement, overwrite=True)
    assert net.buses["b"] is original
    assert net.model_dump() == before


@pytest.mark.parametrize(
    "call",
    [
        lambda net: gi.create_source(name="s2", bus="nowhere", values={50.0: 1.0}, network=net),
        lambda net: gi.create_source(name="s2", bus="a", values={50.0: "x"}, network=net),
        lambda net: gi.create_fault(name="F2", bus="nowhere", scalings={}, network=net),
        lambda net: gi.create_fault(name="F2", bus="a", scalings={50.0: -1.0}, network=net),
        lambda net: gi.create_branch(
            name="x", type=_cable_type(), from_bus="a", to_bus="nowhere",
            length=1.0, network=net,
        ),
    ],
    ids=["source_bus", "source_values", "fault_bus", "fault_scalings", "branch_bus"],
)
def test_failed_create_source_fault_branch_leave_network_unchanged(call):
    net = build_ring()
    before = net.model_dump()
    with pytest.raises(ValueError):
        call(net)
    assert net.model_dump() == before


def test_create_fault_active_without_network_is_a_clear_error():
    """``active=True`` needs a network to activate the fault in; it used to
    fail with ``AttributeError: 'NoneType' object has no attribute
    'set_active_fault'``."""
    with pytest.raises(ValueError, match="active=True"):
        gi.create_fault(name="F", bus="a", scalings={}, active=True)


# ---------------------------------------------------------------------------
# USE5 -- Branch.length must be a finite positive length in km
# ---------------------------------------------------------------------------

_BAD_LENGTHS = [0.0, -1.0, float("nan"), float("inf"), float("-inf")]


def _assert_length_message(excinfo, value):
    """The error names the length, its unit and the rejected value."""
    message = str(excinfo.value)
    assert "Branch.length" in message
    assert "km" in message
    assert repr(float(value)) in message


@pytest.mark.parametrize("value", _BAD_LENGTHS, ids=repr)
def test_branch_rejects_non_positive_length_at_construction(value):
    """Rejected by the model itself -- detached, before any formula runs."""
    with pytest.raises(ValueError) as excinfo:
        gi.Branch(
            name="x", type=_cable_type(), length=value, from_bus="a", to_bus="b",
            self_impedance={}, mutual_impedance={},
        )
    _assert_length_message(excinfo, value)


@pytest.mark.parametrize("value", _BAD_LENGTHS, ids=repr)
def test_create_branch_reports_the_length_not_the_impedance(value):
    """``length=0`` used to surface as "exactly zero impedance", ``-1`` as a
    negative real part -- neither names the length."""
    net = build_ring()
    before = net.model_dump()
    with pytest.raises(ValueError) as excinfo:
        gi.create_branch(
            name="x", type=_cable_type(), from_bus="a", to_bus="c",
            length=value, network=net,
        )
    _assert_length_message(excinfo, value)
    assert net.model_dump() == before


@pytest.mark.parametrize("value", _BAD_LENGTHS, ids=repr)
@pytest.mark.parametrize("attached", [False, True], ids=["detached", "attached"])
def test_branch_rejects_non_positive_length_on_assignment(value, attached):
    net = build_ring()
    if attached:
        branch = net.branches["l0"]
    else:
        branch = gi.Branch(
            name="x", type=_cable_type(), length=1.0, from_bus="a", to_bus="b",
            self_impedance={}, mutual_impedance={},
        )
    snapshot = branch.model_dump()
    with pytest.raises(ValueError) as excinfo:
        branch.length = value
    _assert_length_message(excinfo, value)
    assert branch.model_dump() == snapshot


def test_short_positive_length_is_accepted():
    """A jumper of 1 mm is a legitimate branch; its impedance is the formula
    evaluated at that length (second way: compute_impedance)."""
    net = build_ring()
    gi.create_branch(
        name="jumper", type=_cable_type(), from_bus="a", to_bus="c",
        length=1e-6, network=net,
    )
    assert net.branches["jumper"].self_impedance == compute_impedance(
        SELF_FORMULA, FREQUENCIES, {"rho": 100.0, "l": 1e-6}
    )


# ---------------------------------------------------------------------------
# USE2 -- unknown formula symbols are rejected when the type is created
# ---------------------------------------------------------------------------

_BUS_FORMULA_FIELDS = ["impedance_formula", "R_formula", "L_formula", "C_formula"]
_BRANCH_FORMULA_FIELDS = [
    "self_impedance_formula", "mutual_impedance_formula", "phase_impedance_formula",
    "R_self_formula", "L_self_formula", "C_self_formula",
    "R_mutual_formula", "M_mutual_formula",
]


def _make_type(kind, field, formula):
    """A bus or branch type whose ``field`` carries ``formula``."""
    if kind == "bus":
        fields = dict(name="T", system_type="S", voltage_level=20,
                      impedance_formula="rho*0.01")
        fields[field] = formula
        return gi.BusType(**fields)
    fields = dict(name="T", grounding_conductor=True,
                  self_impedance_formula="(0.25 + j*f*0.012)*l",
                  mutual_impedance_formula="(j*f*0.012)*l")
    fields[field] = formula
    return gi.BranchType(**fields)


_ALL_FIELDS = [("bus", f) for f in _BUS_FORMULA_FIELDS] + [
    ("branch", f) for f in _BRANCH_FORMULA_FIELDS
]


@pytest.mark.parametrize("kind,field", _ALL_FIELDS)
@pytest.mark.parametrize(
    "formula,symbol,suggestion",
    [("rho*0.01 + j*F*0.1", "F", "f"), ("Rho*0.01", "Rho", "rho"), ("RHO + f", "RHO", "rho")],
)
def test_unknown_symbol_is_rejected_when_the_type_is_created(kind, field, formula, symbol, suggestion):
    with pytest.raises(ValueError) as excinfo:
        _make_type(kind, field, formula)
    message = str(excinfo.value)
    assert f"'{symbol}'" in message
    assert f"did you mean '{suggestion}'" in message
    assert field in message
    for allowed in (("f", "rho") if kind == "bus" else ("f", "rho", "l")):
        assert f"{allowed} (" in message


@pytest.mark.parametrize("field", _BRANCH_FORMULA_FIELDS)
def test_upper_case_length_is_rejected_in_branch_formulas(field):
    """The review case: ``(0.25 + j*f*0.012)*L`` was accepted and only failed
    at create_branch with "Cannot convert expression to float"."""
    with pytest.raises(ValueError, match="did you mean 'l'"):
        _make_type("branch", field, "(0.25 + j*f*0.012)*L")


@pytest.mark.parametrize("field", _BUS_FORMULA_FIELDS)
def test_length_is_not_a_bus_symbol(field):
    with pytest.raises(ValueError) as excinfo:
        _make_type("bus", field, "rho*0.01*l")
    assert "only" in str(excinfo.value) and "branch" in str(excinfo.value)


def test_several_unknown_symbols_are_all_named_without_a_guess():
    with pytest.raises(ValueError) as excinfo:
        _make_type("branch", "self_impedance_formula", "R + j*X*omega")
    message = str(excinfo.value)
    for symbol in ("'R'", "'X'", "'omega'"):
        assert symbol in message
    assert "did you mean" not in message


@pytest.mark.parametrize(
    "formula",
    [
        "rho/(2*pi*1.5)*(1 + j*0.01*f)",
        "rho*0.01 + I*2*pi*f*1e-6",
        "sqrt(rho/f)*exp(-f/1000) + E**0",
        "Abs(rho - 50) + Max(f, 1)*0",
        "nan", "NaN", "  NaN  ",
        "2.5e-3 + 1j*f*0",
    ],
)
def test_allowed_symbols_constants_and_the_open_end_pass(formula):
    assert _make_type("bus", "impedance_formula", formula).impedance_formula == formula


def test_branch_formula_symbols_include_the_length():
    bt = _make_type("branch", "phase_impedance_formula", "(0.1 + I*0.4)*l + rho*0 + f*0")
    assert bt.phase_impedance_formula.endswith("f*0")


def test_symbol_sets_match_what_the_elements_bind():
    """The checked set is the set the evaluator binds: ``f`` plus the
    parameters a bus/branch passes (second way: read off the elements)."""
    from groundinsight.utils.validations import BRANCH_FORMULA_SYMBOLS, BUS_FORMULA_SYMBOLS

    net = build_ring()
    assert set(BUS_FORMULA_SYMBOLS) == {"f"} | set(net.buses["a"]._params())
    assert set(BRANCH_FORMULA_SYMBOLS) == {"f"} | set(net.branches["l0"]._params())


def test_unvalidated_type_copy_still_gets_the_clear_message():
    """``model_copy(update=...)`` skips validation; deriving an element from
    such a type reports the unknown symbol, not a SymPy conversion error."""
    bt = _bus_type().model_copy(update={"impedance_formula": "rho*L"})
    net = gi.create_network(name="n", frequencies=FREQUENCIES)
    with pytest.raises(ValueError, match="did you mean 'l'|unknown symbol 'L'"):
        gi.create_bus(name="a", type=bt, network=net)
    assert not net.buses


def test_importer_and_default_formulas_pass():
    """The formulas the package itself builds are inside the allowed set."""
    from groundinsight.io.defaults import ImportDefaults
    from groundinsight.io.pandapower_import import _phase_impedance_formula

    ImportDefaults(  # the example of the ImportDefaults docstring
        rho=100.0, frequencies=[50.0],
        default_bus_type=gi.BusType(
            name="ImportedBus", system_type="Grounded", voltage_level=20.0,
            impedance_formula="rho * 0 + 1.0 + I * f * 0",
        ),
        default_branch_type=gi.BranchType(
            name="ImportedCable", grounding_conductor=True,
            self_impedance_formula="(0.25 + I * 0.6) * l",
            mutual_impedance_formula="(0.0 + I * 0.6) * l",
        ),
    )
    formula = _phase_impedance_formula({"r_ohm_per_km": 0.161, "x_ohm_per_km": 0.117})
    _make_type("branch", "phase_impedance_formula", formula)
    # The reference cases build their types from f-strings of floats.
    assert gi.run_reference_cases()["agrees"].all()


def _documented_formulas():
    """Every literal type formula in the docs, the curated notebooks and the
    README, with the field it is assigned to (f-strings are skipped; the
    suite constructs those)."""
    import json
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1]
    field = (
        r"(impedance_formula|self_impedance_formula|mutual_impedance_formula|"
        r"phase_impedance_formula|R_formula|L_formula|C_formula|R_self_formula|"
        r"L_self_formula|C_self_formula|R_mutual_formula|M_mutual_formula)"
    )
    pattern = re.compile(r"\b" + field + r"\s*=\s*(f?)(\"|')(.*?)\3")
    texts = []
    for path in sorted(root.glob("docs/**/*.md")) + [root / "README.md"]:
        texts.append((str(path.relative_to(root)), path.read_text(encoding="utf-8")))
    for path in sorted(root.glob("docs/**/*.ipynb")) + sorted(root.glob("notebooks/0*.ipynb")):
        cells = json.loads(path.read_text(encoding="utf-8")).get("cells", [])
        for cell in cells:
            texts.append((str(path.relative_to(root)), "".join(cell.get("source", []))))
    found = set()
    for where, text in texts:
        for match in pattern.finditer(text):
            if not match.group(2):  # not an f-string
                found.add((match.group(1), match.group(4), where))
    return sorted(found)


def test_every_documented_formula_passes_the_type_checks():
    formulas = _documented_formulas()
    assert len(formulas) > 50  # the scan itself works
    failures = []
    for field, formula, where in formulas:
        kind = "bus" if field in _BUS_FORMULA_FIELDS else "branch"
        try:
            _make_type(kind, field, formula)
        except ValueError as exc:
            failures.append(f"{where}: {field}={formula!r}: {str(exc)[:120]}")
    assert not failures, "\n".join(failures)


# ---------------------------------------------------------------------------
# GUI3 / F9 -- edit invariants
# ---------------------------------------------------------------------------

def _rlc_bus_type(name="SB_RLC"):
    """A bus type that also carries lumped R and L formulas."""
    return gi.BusType(
        name=name, system_type="S", voltage_level=20, impedance_formula=BUS_FORMULA,
        R_formula="rho*0.01", L_formula="rho*1e-6",
    )


def _two_fault_ring():
    """The ring with a second fault ``G`` at ``b``; both solved."""
    net = build_ring()
    gi.create_fault(name="G", bus="b", scalings={f: 1.0 for f in FREQUENCIES}, network=net)
    gi.run_fault(net, "F")
    gi.run_fault(net, "G")
    return net


# --- re-derivation on assignment ---------------------------------------------

def test_assigning_bus_rho_rederives_the_impedance():
    net = build_ring()
    net.buses["b"].type = _rlc_bus_type()
    net.buses["b"].specific_earth_resistance = 250.0
    bus = net.buses["b"]
    assert bus.impedance == compute_impedance(BUS_FORMULA, FREQUENCIES, {"rho": 250.0})
    assert bus.R == {f: pytest.approx(2.5) for f in FREQUENCIES}
    assert bus.L == {f: pytest.approx(250e-6) for f in FREQUENCIES}


@pytest.mark.parametrize("field,value,params", [
    ("length", 2.5, {"rho": 100.0, "l": 2.5}),
    ("specific_earth_resistance", 400.0, {"rho": 400.0, "l": 1.0}),
])
def test_assigning_branch_inputs_rederives_every_impedance(field, value, params):
    net = build_ring()
    setattr(net.branches["l1"], field, value)
    branch = net.branches["l1"]
    assert branch.self_impedance == compute_impedance(SELF_FORMULA, FREQUENCIES, params)
    assert branch.mutual_impedance == compute_impedance(MUTUAL_FORMULA, FREQUENCIES, params)
    assert branch.phase_impedance == compute_impedance(PHASE_FORMULA, FREQUENCIES, params)


def test_assigning_a_type_rederives_and_clears_what_the_new_type_lacks():
    net = build_ring()
    bus = net.buses["b"]
    bus.type = _rlc_bus_type()
    assert bus.R is not None
    plain = _bus_type(name="plain", formula="rho*0.02")
    bus.type = plain
    assert bus.impedance == compute_impedance("rho*0.02", FREQUENCIES, {"rho": 100.0})
    assert bus.R is None and bus.L is None and bus.C is None

    cable = _cable_type(name="no_phase", phase_impedance_formula=None)
    net.branches["l2"].type = cable
    assert net.branches["l2"].phase_impedance is None


def test_assignment_on_a_detached_element_does_nothing_extra():
    bus = gi.Bus(name="x", type=_bus_type(), impedance={})
    bus.specific_earth_resistance = 250.0
    assert bus.impedance == {}
    branch = gi.Branch(
        name="x", type=_cable_type(), length=1.0, from_bus="a", to_bus="b",
        self_impedance={}, mutual_impedance={},
    )
    branch.length = 3.0
    assert branch.self_impedance == {}


@pytest.mark.parametrize("edit", [
    lambda net: setattr(net.buses["b"], "specific_earth_resistance", float("nan")),
    lambda net: setattr(net.buses["b"], "type", _bus_type(name="bad", formula="-rho")),
    lambda net: setattr(net.branches["l0"], "type", _cable_type(name="bad", R_self_formula="j*l")),
], ids=["bus_rho_nan", "bus_type_negative", "branch_type_late_failure"])
def test_failing_rederivation_leaves_the_element_as_it_was(edit):
    net = build_ring()
    before = net.model_dump()
    with pytest.raises(ValueError):
        edit(net)
    assert net.model_dump() == before


def test_edited_network_solves_like_one_built_with_the_new_values():
    """Second way: the same inputs entered from the start."""
    net = build_ring()
    gi.run_fault(net, "F")
    net.buses["b"].specific_earth_resistance = 250.0
    net.branches["l2"].length = 2.5
    gi.run_fault(net, "F")
    reference = build_ring(rho={"b": 250.0}, length={"l2": 2.5})
    gi.run_fault(reference, "F")
    assert_same_results(net, reference)


def test_assigning_frequencies_rederives_every_element():
    net = build_ring()
    new = [50.0, 150.0, 350.0]
    net.frequencies = new
    for bus in net.buses.values():
        assert bus.impedance == compute_impedance(BUS_FORMULA, new, {"rho": 100.0})
    for branch in net.branches.values():
        params = {"rho": 100.0, "l": 1.0}
        assert branch.self_impedance == compute_impedance(SELF_FORMULA, new, params)
        assert branch.phase_impedance == compute_impedance(PHASE_FORMULA, new, params)
    # second way: the network built at these frequencies solves the same
    net.sources["s"].values = {f: 1000.0 / (1 + i) for i, f in enumerate(new)}
    net.faults["F"].scalings = {f: 1.0 for f in new}
    gi.run_fault(net, "F")
    reference = build_ring(frequencies=new)
    gi.run_fault(reference, "F")
    assert_same_results(net, reference)


def test_failing_frequency_change_is_atomic():
    net = build_ring()
    # valid at 50 and 250 Hz, 0*log(0) = NaN at 60 Hz
    net.buses["d"].type = _bus_type(
        name="fails_at_60", formula="rho*0.01 + (f - 60)**2*log((f - 60)**2)"
    )
    before = net.model_dump()
    with pytest.raises(ValueError):
        net.frequencies = [50.0, 60.0]
    assert net.model_dump() == before


# --- names and references ----------------------------------------------------

@pytest.mark.parametrize("collection,name", [
    ("buses", "b"), ("branches", "l0"), ("sources", "s"), ("faults", "F"),
])
def test_renaming_an_attached_element_is_refused(collection, name):
    net = build_ring()
    element = getattr(net, collection)[name]
    with pytest.raises(ValueError, match="(?i)remove"):
        element.name = "renamed"
    assert element.name == name
    element.name = name  # assigning the same name is harmless


def test_renaming_a_detached_element_is_allowed():
    net = build_ring()
    branch = net.branches.pop("l0")
    branch.name = "l0_new"
    assert branch.name == "l0_new"


@pytest.mark.parametrize("edit", [
    lambda net: setattr(net.branches["l0"], "to_bus", "nowhere"),
    lambda net: setattr(net.branches["l0"], "from_bus", "nowhere"),
    lambda net: setattr(net.sources["s"], "bus", "nowhere"),
    lambda net: setattr(net.faults["F"], "bus", "nowhere"),
], ids=["to_bus", "from_bus", "source_bus", "fault_bus"])
def test_rewiring_to_an_unknown_bus_is_refused(edit):
    net = build_ring()
    before = net.model_dump()
    with pytest.raises(ValueError, match="nowhere"):
        edit(net)
    assert net.model_dump() == before


# --- stale results -----------------------------------------------------------

def test_a_fresh_result_is_not_stale():
    net = _two_fault_ring()
    assert net.stale_results() == []
    assert net.is_result_stale("F") is False
    assert net.results["F"].input_digest is not None


_STALE_EDITS = {
    "bus_rho": lambda net: setattr(net.buses["b"], "specific_earth_resistance", 250.0),
    "bus_type": lambda net: setattr(net.buses["b"], "type", _bus_type(name="t2", formula="rho*0.02")),
    "bus_active": lambda net: setattr(net.buses["d"], "active", False),
    "branch_length": lambda net: setattr(net.branches["l1"], "length", 2.0),
    "branch_rho": lambda net: setattr(net.branches["l1"], "specific_earth_resistance", 300.0),
    "branch_type": lambda net: setattr(net.branches["l1"], "type", _cable_type(name="c2", self_impedance_formula="(0.3 + j*f*0.012)*l")),
    "branch_active": lambda net: setattr(net.branches["l1"], "active", False),
    "branch_rewired": lambda net: setattr(net.branches["l3"], "to_bus", "b"),
    "parallel_coefficient": lambda net: setattr(net.branches["l1"], "parallel_coefficient", 0.5),
    "impedance_override": lambda net: setattr(net.buses["b"], "impedance", {f: gi.ComplexNumber(real=7.0, imag=0.0) for f in net.frequencies}),
    "source_values": lambda net: setattr(net.sources["s"], "values", {f: 500.0 for f in net.frequencies}),
    "frequencies": lambda net: setattr(net, "frequencies", [50.0, 250.0, 450.0]),
    "add_bus": lambda net: gi.create_bus(name="x", type=_bus_type(), network=net),
    "add_branch": lambda net: gi.create_branch(name="x", type=_cable_type(), from_bus="a", to_bus="c", length=1.0, network=net),
    "add_source": lambda net: gi.create_source(name="s2", bus="c", values={50.0: 10.0}, network=net),
    "update_bus": lambda net: net.update_bus("c", specific_earth_resistance=300.0),
    "remove_branch": lambda net: net.remove_branch("l3"),
    "remove_source": lambda net: (gi.create_source(name="s2", bus="c", values={50.0: 1.0}, network=net), gi.run_fault(net, "F"), gi.run_fault(net, "G"), net.remove_source("s2")),
}


@pytest.mark.parametrize("name", sorted(_STALE_EDITS))
def test_every_input_change_marks_the_results_stale(name):
    net = _two_fault_ring()
    _STALE_EDITS[name](net)
    assert net.stale_results() == ["F", "G"]
    assert net.is_result_stale("F") and net.is_result_stale("G")


def test_a_fault_definition_only_affects_its_own_result():
    """F's numbers do not depend on G's definition (the phase currents and
    the path set are taken for the active fault only)."""
    net = _two_fault_ring()
    net.faults["G"].scalings = {f: 0.5 for f in FREQUENCIES}
    assert net.stale_results() == ["G"]
    net.faults["G"].bus = "d"
    assert net.stale_results() == ["G"]
    gi.create_fault(name="H", bus="a", scalings={}, network=net)
    assert net.stale_results() == ["G"]


def test_metadata_edits_do_not_mark_results_stale():
    net = _two_fault_ring()
    net.buses["b"].description = "renamed in the GUI"
    net.branches["l1"].description = "cable 1"
    net.faults["F"].t_k_s = 0.5
    net.faults["F"].n_factor = 0.9
    net.sources["s"].i_k_a = 1000.0
    net.description = "a network"
    assert net.stale_results() == []


def test_run_fault_clears_the_flag_of_its_fault_only():
    net = _two_fault_ring()
    net.buses["b"].specific_earth_resistance = 250.0
    gi.run_fault(net, "F")
    assert net.stale_results() == ["G"]


def test_undoing_an_edit_makes_the_result_current_again():
    """Staleness is a statement about content: the inputs are back to what
    the result was computed from (what an undo in a GUI produces)."""
    net = _two_fault_ring()
    net.buses["b"].specific_earth_resistance = 250.0
    assert net.stale_results() == ["F", "G"]
    net.buses["b"].specific_earth_resistance = 100.0
    assert net.stale_results() == []


def test_stale_flags_survive_json(tmp_path):
    net = _two_fault_ring()
    net.buses["b"].specific_earth_resistance = 250.0
    gi.run_fault(net, "G")
    assert net.stale_results() == ["F"]
    path = tmp_path / "net.json"
    gi.save_network_to_json(net, str(path))
    loaded = gi.load_network_from_json(str(path))
    assert loaded.stale_results() == ["F"]
    assert loaded.results["G"].input_digest == net.results["G"].input_digest


def test_stale_flags_survive_the_database(tmp_path):
    net = _two_fault_ring()
    net.buses["b"].specific_earth_resistance = 250.0
    gi.run_fault(net, "G")
    gi.start_dbsession(str(tmp_path / "net.db"), force=True)
    try:
        gi.save_network_to_db(net)
        loaded = gi.load_network_from_db(net.name)
    finally:
        gi.close_dbsession()
    assert loaded.stale_results() == ["F"]


def test_results_stored_before_digests_are_not_reported_stale():
    net = _two_fault_ring()
    net.results["F"].input_digest = None
    net.buses["b"].specific_earth_resistance = 250.0
    assert net.stale_results() == ["G"]
    assert net.is_result_stale("F") is False


def test_is_result_stale_needs_a_result():
    net = build_ring()
    with pytest.raises(ValueError, match="No results"):
        net.is_result_stale("F")
    with pytest.raises(ValueError):
        net.is_result_stale("nope")


def test_outage_context_leaves_stale_flags_as_it_found_them():
    net = _two_fault_ring()
    with gi.outage_context(net, gi.Outage(name="o", disabled_branches=["l1"])):
        assert net.stale_results() == ["F", "G"]  # the topology differs in here
    assert net.stale_results() == []


def test_outage_study_flags_only_the_result_it_replaced():
    """The study leaves the last scenario's solution as the stored result of
    its fault; that result was computed under an outage the network no
    longer has. The other fault's flag is untouched."""
    net = _two_fault_ring()
    gi.run_outage_study(
        net, fault="F", scenarios=[gi.Outage(name="o", disabled_branches=["l1"])]
    )
    assert net.stale_results() == ["F"]
    gi.run_outage_study(net, fault="F", scenarios=[], include_base=True)
    assert net.stale_results() == []


def test_sweep_and_rho_search_leave_other_results_as_they_found_them():
    net = _two_fault_ring()
    gi.run_sweep(net, fault="F", points=[gi.SweepPoint(label="p", bus_rho={"b": 500.0})])
    # The sweep leaves the network exactly as found (GUI5), its own fault's
    # result included -- it no longer holds the result of the last point.
    assert net.stale_results() == []
    gi.run_fault(net, "F")
    gi.find_max_rho_scaling(net, "F", ["b", "c"], u_max=1e9, c_bounds=(0.5, 2.0))
    assert "G" not in net.stale_results()


# --- update_* ----------------------------------------------------------------

def test_update_bus_applies_every_field_and_rederives():
    net = build_ring()
    bus = net.update_bus("b", specific_earth_resistance=250.0, description="edited")
    assert bus is net.buses["b"]
    assert bus.description == "edited"
    assert bus.impedance == compute_impedance(BUS_FORMULA, FREQUENCIES, {"rho": 250.0})


def test_update_branch_rewires_and_rederives_like_a_fresh_build():
    net = build_ring()
    net.update_branch("l2", length=2.5, specific_earth_resistance=200.0)
    gi.run_fault(net, "F")
    reference = build_ring(length={"l2": 2.5}, rho={"l2": 200.0})
    gi.run_fault(reference, "F")
    assert_same_results(net, reference)


@pytest.mark.parametrize("call", [
    lambda net: net.update_branch("l0", length=2.0, from_bus="nowhere"),
    lambda net: net.update_branch("l0", length=2.0, type=_cable_type(name="bad", R_self_formula="j*l")),
    lambda net: net.update_branch("l0", description="x", length="not a number"),
    lambda net: net.update_bus("b", specific_earth_resistance=250.0, type=_bus_type(name="bad", formula="-rho")),
    lambda net: net.update_bus("b", description="x", active="maybe"),
], ids=["unknown_bus", "derivation_fails", "invalid_value", "bus_derivation_fails", "bus_invalid_value"])
def test_update_is_all_or_nothing(call):
    net = build_ring()
    before = net.model_dump()
    with pytest.raises(ValueError):
        call(net)
    assert net.model_dump() == before


@pytest.mark.parametrize("call,match", [
    (lambda net: net.update_bus("b", colour="red"), "colour"),
    (lambda net: net.update_bus("b", impedance={}), "derived"),
    (lambda net: net.update_branch("l0", self_impedance={}), "derived"),
    (lambda net: net.update_bus("b", name="x"), "(?i)remove"),
    (lambda net: net.update_bus("nope", description="x"), "nope"),
    (lambda net: net.update_branch("nope", length=1.0), "nope"),
])
def test_update_rejects_unknown_and_derived_fields(call, match):
    net = build_ring()
    with pytest.raises(ValueError, match=match):
        call(net)


# --- remove_* ----------------------------------------------------------------

def test_remove_bus_names_every_reference():
    net = build_ring()
    with pytest.raises(ValueError) as excinfo:
        net.remove_bus("a")
    message = str(excinfo.value)
    for ref in ("l0", "l3", "'s'"):
        assert ref in message
    with pytest.raises(ValueError, match="'F'"):
        net.remove_bus("c")
    assert set(net.buses) == set("abcd")


def test_remove_bus_after_its_references_are_gone():
    net = build_ring()
    net.remove_branch("l1")
    net.remove_branch("l2")
    gi.create_fault(name="F2", bus="d", scalings={}, network=net)
    net.remove_fault("F")
    bus = net.remove_bus("c")
    assert "c" not in net.buses
    bus.specific_earth_resistance = 999.0  # detached now: nothing re-derived
    assert bus.impedance == compute_impedance(BUS_FORMULA, FREQUENCIES, {"rho": 100.0})


def test_remove_fault_drops_its_result_and_the_active_flag():
    net = _two_fault_ring()
    assert net.active_fault == "G"
    net.remove_fault("G")
    assert "G" not in net.faults and "G" not in net.results
    assert net.active_fault is None
    assert net.stale_results() == []  # F does not depend on G


def test_remove_branch_drops_paths_through_it():
    net = build_ring()
    gi.create_paths(net)
    assert any("l1" in [s.name for s in p.segments] for p in net.paths.values())
    net.remove_branch("l1")
    assert not any("l1" in [s.name for s in p.segments] for p in net.paths.values())


@pytest.mark.parametrize("method", ["remove_bus", "remove_branch", "remove_source", "remove_fault"])
def test_remove_unknown_name_is_a_value_error(method):
    net = build_ring()
    with pytest.raises(ValueError, match="nope"):
        getattr(net, method)("nope")


# --- types are read-only -----------------------------------------------------

@pytest.mark.parametrize("make,field,value", [
    (_bus_type, "impedance_formula", "rho*0.02"),
    (_bus_type, "electrode_cross_section_mm2", 50.0),
    (_cable_type, "self_impedance_formula", "(0.3 + j*f*0.012)*l"),
    (_cable_type, "grounding_conductor", False),
])
def test_types_are_read_only(make, field, value):
    element_type = make()
    snapshot = element_type.model_dump()
    with pytest.raises(ValueError, match="read-only") as excinfo:
        setattr(element_type, field, value)
    assert "model_copy" in str(excinfo.value)
    assert element_type.model_dump() == snapshot


def test_changing_a_type_goes_through_a_copy_and_update():
    net = build_ring()
    old = net.buses["b"].type
    new = old.model_copy(update={"impedance_formula": "rho*0.02 + j*f*1e-4"})
    for name, bus in net.buses.items():
        if bus.type == old:
            net.update_bus(name, type=new)
    for bus in net.buses.values():
        assert bus.impedance == compute_impedance("rho*0.02 + j*f*1e-4", FREQUENCIES, {"rho": 100.0})


# --- membership --------------------------------------------------------------

def test_an_element_belongs_to_one_network():
    net1, net2 = build_ring("one"), build_ring("two")
    bus = net1.buses["a"]
    with pytest.raises(ValueError, match="one"):
        net2.add_bus(bus, overwrite=True)
    net2.add_bus(bus.model_copy(deep=True), overwrite=True)
    assert net2.buses["a"] is not bus


@pytest.mark.parametrize("path", sorted(_COPY_PATHS))
def test_edits_on_a_copy_rederive_the_copy_only(path):
    net = build_ring()
    gi.run_fault(net, "F")
    clone = _COPY_PATHS[path](net)
    clone.buses["b"].specific_earth_resistance = 250.0
    assert clone.buses["b"].impedance == compute_impedance(BUS_FORMULA, FREQUENCIES, {"rho": 250.0})
    assert net.buses["b"].impedance == compute_impedance(BUS_FORMULA, FREQUENCIES, {"rho": 100.0})
    assert clone.stale_results() == ["F"] and net.stale_results() == []
    with pytest.raises(ValueError, match="(?i)remove"):
        clone.buses["b"].name = "x"


def test_every_model_field_is_classified_for_the_result_digest():
    """A new field must be declared as an input of the stored results or as
    one they do not depend on -- otherwise a result could stay "current"
    after a change that alters it."""
    from groundinsight.models.core_models import _DIGEST_EXEMPT_FIELDS, _DIGEST_FIELDS

    for cls in (gi.Bus, gi.Branch, gi.Source, gi.Fault, gi.Network):
        digest, exempt = set(_DIGEST_FIELDS[cls.__name__]), set(_DIGEST_EXEMPT_FIELDS[cls.__name__])
        assert digest | exempt == set(cls.model_fields), cls.__name__
        assert not digest & exempt, cls.__name__
