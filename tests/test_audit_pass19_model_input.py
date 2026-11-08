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
SELF_FORMULA = "(0.25 + j*f*0.012) * l"
MUTUAL_FORMULA = "(j*f*0.012) * l"
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
