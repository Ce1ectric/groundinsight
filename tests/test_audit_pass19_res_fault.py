# tests/test_audit_pass19_res_fault.py
"""
Audit pass 19 -- the key figures of a fault in one table (USE1, GUI6).

The figures an engineer reads first -- fault current, EPR at the fault and the
largest EPR, both reduction factors, the earth-return current, ``U_E`` and
``Z_E`` -- were spread over four places of a result (``ResultBus``,
``ResultReductionFactor``, ``ResultGroundingImpedance``, ``Network.faults``),
and a GUI that picks them out itself is where "physics in the GUI" goes wrong.
``Network.res_fault(fault)`` returns them as one Polars frame with the pinned
schema ``RES_FAULT_SCHEMA``: one row per network frequency, one RMS row.

Every column is checked against a second way:

* the result objects it is read from, field by field;
* the independent reference solver in :mod:`tests._reference_mna` (modified
  nodal analysis with explicit branch currents, physical sign frame), which
  computes ``I_F``, the potentials, both reduction factors, the earth group
  and the driving-point impedance from the same input data;
* for the RMS row, the root sum of squares of the frequency rows and the RMS
  row of ``res_buses()``.
"""

from __future__ import annotations

import cmath
import json
import math
import warnings

import polars as pl
import pytest

import groundinsight as gi
from groundinsight.models.core_models import Result
from tests import _reference_mna as ref

TOL = 1e-12

CABLE = dict(
    zs="(0.29+0.625*j*f/50)*l", zm="(0.049+0.625*j*f/50)*l", zp="(0.13+0.12*j*f/50)*l"
)
LINE = dict(
    zs="(0.65+0.785*j*f/50)*l", zm="(0.049+0.372*j*f/50)*l", zp="(0.32+0.62*j*f/50)*l"
)


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------


def _build(name, frequencies, buses, branches, sources, faults):
    """A network from plain specifications, through the public API only."""
    net = gi.create_network(name, frequencies=list(frequencies))
    for bus, (formula, rho) in buses.items():
        bus_type = gi.BusType(
            name=f"bt_{bus}", system_type="MV", voltage_level=20.0,
            impedance_formula=formula,
        )
        gi.create_bus(bus, bus_type, rho, network=net)
    for branch, (a, b, length, kind) in branches.items():
        branch_type = gi.BranchType(
            name=f"brt_{branch}", grounding_conductor=True,
            self_impedance_formula=kind["zs"], mutual_impedance_formula=kind["zm"],
            phase_impedance_formula=kind.get("zp"),
        )
        gi.create_branch(branch, branch_type, a, b, length, network=net)
    for source, (bus, values) in sources.items():
        gi.create_source(source, bus, values, network=net)
    for fault, (bus, scalings) in faults.items():
        gi.create_fault(fault, bus, scalings, network=net)
    return net


def _chain():
    """S -- A -- F, three frequencies, the fault at the far end."""
    return _build(
        "chain", [50.0, 150.0, 250.0],
        {"S": ("0.002*rho + 0.05", 100.0), "A": ("0.2*rho", 150.0),
         "F": ("0.25*rho", 300.0)},
        {"L1": ("S", "A", 1.2, CABLE), "L2": ("A", "F", 0.8, CABLE)},
        {"Q": ("S", {50.0: 1000.0, 150.0: 60.0, 250.0: 40.0})},
        {"F1": ("F", {50.0: 1.0, 150.0: 1.0, 250.0: 1.0})},
    )


def _ring():
    """A meshed ring of cable and overhead line, fault opposite the source."""
    return _build(
        "ring", [50.0, 250.0],
        {"R0": ("0.002*rho + 0.01*j*f/50", 100.0), "R1": ("0.02*rho", 200.0),
         "R2": ("0.015*rho + 0.1*j*f/50", 80.0), "R3": ("0.03*rho", 300.0)},
        {"L0": ("R0", "R1", 1.0, CABLE), "L1": ("R1", "R2", 1.5, LINE),
         "L2": ("R2", "R3", 2.0, CABLE), "L3": ("R3", "R0", 2.5, LINE)},
        {"Q": ("R0", {50.0: 800.0, 250.0: 100.0})},
        {"F1": ("R2", {50.0: 1.0, 250.0: 0.5})},
    )


def _two_sources():
    """Two infeeds of different phase angle and a fault in between."""
    return _build(
        "two_sources", [50.0, 150.0],
        {"S1": ("0.003*rho", 100.0), "M": ("0.05*rho", 120.0),
         "F": ("0.1*rho", 250.0), "S2": ("0.004*rho", 100.0)},
        {"L1": ("S1", "M", 1.0, CABLE), "L2": ("M", "F", 1.0, LINE),
         "L3": ("F", "S2", 2.0, CABLE)},
        {"Q1": ("S1", {50.0: 600.0, 150.0: 30.0}),
         "Q2": ("S2", {50.0: complex(300.0, -150.0), 150.0: 10.0})},
        {"F1": ("F", {50.0: 1.0, 150.0: 1.0})},
    )


NETWORKS = {"chain": _chain, "ring": _ring, "two_sources": _two_sources}


def _solved(kind):
    net = NETWORKS[kind]()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gi.run_fault(net, "F1")
    return net


def _rows(frame):
    """The frequency rows by frequency, and the RMS row."""
    per_frequency = {
        row["frequency_Hz"]: row
        for row in frame.iter_rows(named=True)
        if not row["is_rms"]
    }
    rms = [row for row in frame.iter_rows(named=True) if row["is_rms"]]
    assert len(rms) == 1
    return per_frequency, rms[0]


def _degrees(value):
    return math.degrees(cmath.phase(complex(value)))


# --------------------------------------------------------------------------
# schema and shape
# --------------------------------------------------------------------------


def test_the_schema_is_pinned_and_exported():
    assert "RES_FAULT_SCHEMA" in gi.__all__
    frame = _solved("chain").res_fault("F1")
    assert dict(frame.schema) == gi.RES_FAULT_SCHEMA
    assert list(frame.columns) == list(gi.RES_FAULT_SCHEMA)
    assert list(gi.RES_FAULT_SCHEMA) == [
        "fault", "fault_bus", "frequency_Hz", "is_rms", "I_F_A", "I_F_degree",
        "EPR_fault_V", "EPR_fault_degree", "EPR_max_V", "EPR_max_bus", "r_epr",
        "r_current", "I_E_A", "U_E_V", "Z_E_Ohm", "Z_E_degree", "n_diagnostics",
    ]
    assert gi.RES_FAULT_SCHEMA["n_diagnostics"] == pl.Int64
    assert gi.RES_FAULT_SCHEMA["EPR_max_bus"] == pl.Utf8
    assert gi.RES_FAULT_SCHEMA["is_rms"] == pl.Boolean


def test_one_row_per_network_frequency_and_one_rms_row():
    net = _solved("chain")
    frame = net.res_fault("F1")
    assert frame["frequency_Hz"].to_list() == [50.0, 150.0, 250.0, None]
    assert frame["is_rms"].to_list() == [False, False, False, True]
    assert set(frame["fault"].to_list()) == {"F1"}
    assert set(frame["fault_bus"].to_list()) == {"F"}
    n = len(net.results["F1"].diagnostics)
    assert frame["n_diagnostics"].to_list() == [n] * 4


# --------------------------------------------------------------------------
# second way 1: the result objects, field by field
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(NETWORKS))
def test_every_column_is_the_result_field_it_names(kind):
    net = _solved(kind)
    result = net.results["F1"]
    factor = result.reduction_factor
    fault_bus = net.faults["F1"].bus
    per_frequency, _ = _rows(net.res_fault("F1"))
    assert sorted(per_frequency) == sorted(net.frequencies)
    buses = {bus.name: bus for bus in result.buses}
    for f, row in per_frequency.items():
        i_f = complex(factor.i_fault[f])
        assert row["I_F_A"] == abs(i_f)
        assert row["I_F_degree"] == pytest.approx(_degrees(i_f), abs=1e-12)
        u_f = complex(buses[fault_bus].uepr_freq[f])
        assert row["EPR_fault_V"] == abs(u_f)
        assert row["EPR_fault_degree"] == pytest.approx(_degrees(u_f), abs=1e-12)
        magnitudes = {name: abs(complex(bus.uepr_freq[f])) for name, bus in buses.items()}
        top = max(magnitudes.values())
        assert row["EPR_max_V"] == top
        assert magnitudes[row["EPR_max_bus"]] == top
        assert row["r_epr"] == factor.value[f]
        assert row["r_current"] == factor.value_current[f]
        assert row["I_E_A"] == abs(complex(factor.i_earth[f]))
        assert row["U_E_V"] == abs(complex(factor.u_earthing[f]))
        z_e = complex(factor.z_earthing[f])
        assert row["Z_E_Ohm"] == abs(z_e)
        assert row["Z_E_degree"] == pytest.approx(_degrees(z_e), abs=1e-12)
        # Z_E is also the grounding impedance (decision F4), and U_E the EPR
        # at the fault bus: two fields, one number each.
        assert complex(result.grounding_impedance.value[f]) == z_e
        assert row["U_E_V"] == row["EPR_fault_V"]


# --------------------------------------------------------------------------
# second way 2: the independent reference solver
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(NETWORKS))
def test_every_column_agrees_with_the_reference_solver(kind):
    net = _solved(kind)
    per_frequency, rms = _rows(net.res_fault("F1"))
    epr_squares = {}
    i_f_squares, i_e_squares = 0.0, 0.0
    for f, row in per_frequency.items():
        rd = ref.reference_network(net, f, "F1")
        solution = ref.solve_oneway(rd, thevenin_phase="norton")
        mapped = ref.to_package_frame(solution)
        u_f = mapped["u"][rd["fault_bus"]]
        i_f = solution["I_F"]
        assert row["I_F_A"] == pytest.approx(abs(i_f), rel=TOL)
        assert row["I_F_degree"] == pytest.approx(_degrees(i_f), abs=1e-9)
        assert row["EPR_fault_V"] == pytest.approx(abs(u_f), rel=TOL)
        assert row["EPR_fault_degree"] == pytest.approx(_degrees(u_f), abs=1e-9)
        top_bus = max(mapped["u"], key=lambda b: abs(mapped["u"][b]))
        assert row["EPR_max_bus"] == top_bus
        assert row["EPR_max_V"] == pytest.approx(abs(mapped["u"][top_bus]), rel=TOL)
        assert row["r_epr"] == pytest.approx(
            ref.reduction_factor_epr(rd, thevenin_phase="norton"), rel=TOL
        )
        i_earth, _ = ref.half_plane_split(mapped["ia"], rd["fault_bus"])
        assert row["I_E_A"] == pytest.approx(abs(i_earth), rel=TOL)
        assert row["r_current"] == pytest.approx(abs(i_earth) / abs(i_f), rel=TOL)
        assert row["U_E_V"] == pytest.approx(abs(u_f), rel=TOL)
        z_e = ref.driving_point_impedance(rd, rd["fault_bus"])
        assert row["Z_E_Ohm"] == pytest.approx(abs(z_e), rel=TOL)
        assert row["Z_E_degree"] == pytest.approx(_degrees(z_e), abs=1e-9)
        for bus, value in mapped["u"].items():
            epr_squares[bus] = epr_squares.get(bus, 0.0) + abs(value) ** 2
        i_f_squares += abs(i_f) ** 2
        i_e_squares += abs(i_earth) ** 2
    # The RMS row from the same oracle: root sums of squares.
    fault_bus = net.faults["F1"].bus
    assert rms["I_F_A"] == pytest.approx(math.sqrt(i_f_squares), rel=TOL)
    assert rms["EPR_fault_V"] == pytest.approx(math.sqrt(epr_squares[fault_bus]), rel=TOL)
    top_bus = max(epr_squares, key=epr_squares.get)
    assert rms["EPR_max_bus"] == top_bus
    assert rms["EPR_max_V"] == pytest.approx(math.sqrt(epr_squares[top_bus]), rel=TOL)
    assert rms["I_E_A"] == pytest.approx(math.sqrt(i_e_squares), rel=TOL)
    assert rms["U_E_V"] == pytest.approx(math.sqrt(epr_squares[fault_bus]), rel=TOL)


# --------------------------------------------------------------------------
# the RMS row
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(NETWORKS))
def test_the_rms_row_is_the_root_sum_of_squares_and_matches_res_buses(kind):
    net = _solved(kind)
    frame = net.res_fault("F1")
    per_frequency, rms = _rows(frame)
    for column in ("I_F_A", "EPR_fault_V", "I_E_A", "U_E_V"):
        expected = math.sqrt(sum(row[column] ** 2 for row in per_frequency.values()))
        assert rms[column] == pytest.approx(expected, rel=TOL), column
    buses = net.res_buses(fault="F1").filter(pl.col("is_rms"))
    by_bus = dict(zip(buses["bus_name"], buses["EPR_V"], strict=True))
    assert rms["EPR_fault_V"] == by_bus[net.faults["F1"].bus]
    assert rms["EPR_max_V"] == max(by_bus.values())
    assert by_bus[rms["EPR_max_bus"]] == rms["EPR_max_V"]
    for column in ("I_F_degree", "EPR_fault_degree", "r_epr", "r_current", "Z_E_Ohm",
                   "Z_E_degree"):
        assert rms[column] is None, column


# --------------------------------------------------------------------------
# undefined values and diagnostics
# --------------------------------------------------------------------------


def test_a_frequency_without_fault_current_has_no_ratio():
    """Scaling 0 at 250 Hz: no fault current there, so ``r_I`` and ``I_E``
    are undefined (``None`` in the result) and null in the table, while the
    EPR of that frequency is a number."""
    net = _ring()
    net.faults["F1"].scalings = {50.0: 1.0, 250.0: 0.0}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = gi.run_fault(net, "F1")
    per_frequency, rms = _rows(net.res_fault("F1"))
    row = per_frequency[250.0]
    assert result.reduction_factor.value_current[250.0] is None
    assert row["r_current"] is None and row["I_E_A"] is None
    assert row["I_F_A"] == abs(complex(result.reduction_factor.i_fault[250.0]))
    assert row["EPR_fault_V"] is not None
    # A frequency without an earth current adds nothing to the RMS sum.
    assert rms["I_E_A"] == pytest.approx(per_frequency[50.0]["I_E_A"], rel=TOL)


def test_n_diagnostics_counts_the_records_of_the_result():
    """Built to raise exactly two input warnings: an incomplete scaling and a
    source without a value at 250 Hz."""
    net = _ring()
    net.faults["F1"].scalings = {50.0: 1.0}
    net.sources["Q"].values = {50.0: 800.0}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = gi.run_fault(net, "F1")
    assert sorted(d.code for d in result.diagnostics) == [
        "SCALINGS_INCOMPLETE", "SOURCE_FREQUENCY_GAP",
    ]
    assert net.res_fault("F1")["n_diagnostics"].to_list() == [2, 2, 2]
    clean = _solved("chain")
    assert clean.results["F1"].diagnostics == []
    assert set(clean.res_fault("F1")["n_diagnostics"].to_list()) == {0}


def test_a_result_stored_before_decision_f4_reports_no_earthing_quantities():
    """A result loaded from a file written before ``u_earthing``/``z_earthing``
    meant the EN 50522 quantities keeps them empty; the table must not fill
    ``U_E``/``Z_E`` from the old group values or the old grounding impedance."""
    net = _solved("chain")
    stored = json.loads(net.results["F1"].model_dump_json())
    old = stored["reduction_factor"]
    for key in ("i_fault", "u_earthing_group", "z_earthing_group"):
        old.pop(key)
    net.results["F1"] = Result.model_validate(stored)
    assert net.results["F1"].reduction_factor.u_earthing == {}
    per_frequency, rms = _rows(net.res_fault("F1"))
    for row in per_frequency.values():
        assert row["U_E_V"] is None and row["Z_E_Ohm"] is None
        assert row["I_F_A"] is None
        assert row["EPR_fault_V"] is not None and row["r_epr"] is not None
    assert rms["U_E_V"] is None and rms["I_F_A"] is None
    assert rms["EPR_fault_V"] is not None


def test_a_result_without_reduction_factor_still_has_its_epr_columns():
    net = _solved("chain")
    net.results["F1"] = net.results["F1"].model_copy(
        update={"reduction_factor": None, "grounding_impedance": None}
    )
    frame = net.res_fault("F1")
    assert frame["fault_bus"].to_list() == ["F"] * 4
    assert frame["EPR_fault_V"].null_count() == 0
    for column in ("I_F_A", "r_epr", "r_current", "I_E_A", "U_E_V", "Z_E_Ohm"):
        assert frame[column].null_count() == 4, column


# --------------------------------------------------------------------------
# errors, purity, the commit pattern
# --------------------------------------------------------------------------


def test_an_unknown_fault_is_a_value_error():
    net = _solved("chain")
    with pytest.raises(ValueError, match="'nope'"):
        net.res_fault("nope")


def test_a_fault_without_stored_result_names_run_fault_and_solve():
    net = _chain()
    with pytest.raises(RuntimeError, match="run_fault") as caught:
        net.res_fault("F1")
    assert "solve" in str(caught.value) and "'F1'" in str(caught.value)


def test_res_fault_reads_a_result_kept_from_solve_and_changes_nothing():
    net = _chain()
    result = gi.solve(net, "F1")
    net.results["F1"] = result
    before = net.model_dump_json()
    frame = net.res_fault("F1")
    assert net.model_dump_json() == before
    reference = _solved("chain").res_fault("F1")
    assert frame.equals(reference)


def test_the_table_reports_the_stored_result_also_when_it_is_stale():
    """The table shows the numbers of the stored result -- the network may
    have been edited since (``stale_results`` says so)."""
    net = _solved("chain")
    before = net.res_fault("F1")
    net.update_bus("A", specific_earth_resistance=400.0)
    assert net.stale_results() == ["F1"]
    assert net.res_fault("F1").equals(before)


def test_the_quickstart_network_reads_the_same_numbers_as_the_single_fields():
    """The network of the quickstart: 50 Hz scaled, five frequencies solved."""
    net = gi.create_network(name="quickstart_demo", frequencies=[50, 250, 350, 450, 550])
    bus_type = gi.BusType(
        name="SubstationBus", system_type="Substation", voltage_level=20,
        impedance_formula="rho * 0.01 + j * f * 1/50 * 0.1",
    )
    cable_type = gi.BranchType(
        name="MSCable", grounding_conductor=True,
        self_impedance_formula="(0.25 + j * f * 0.012) * l",
        mutual_impedance_formula="(0.0  + j * f * 0.012) * l",
    )
    gi.create_bus(name="bus_source", type=bus_type, network=net)
    gi.create_bus(name="bus_fault", type=bus_type, network=net)
    gi.create_branch(name="cable_1", type=cable_type, from_bus="bus_source",
                     to_bus="bus_fault", length=5.0, network=net)
    gi.create_source(name="infeed", bus="bus_source",
                     values={50: 1000.0, 250: 200.0, 350: 100.0, 450: 50.0, 550: 25.0},
                     network=net)
    gi.create_fault(name="fault_at_remote_bus", bus="bus_fault",
                    scalings={50: 1.0}, network=net)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = gi.run_fault(net, "fault_at_remote_bus")
    frame = net.res_fault("fault_at_remote_bus")
    assert dict(frame.schema) == gi.RES_FAULT_SCHEMA
    row = frame.filter(pl.col("frequency_Hz") == 50.0).row(0, named=True)
    factor = result.reduction_factor
    assert row["r_epr"] == factor.value[50.0]
    assert row["r_current"] == factor.value_current[50.0]
    assert row["Z_E_Ohm"] == abs(complex(result.grounding_impedance.value[50.0]))
    assert row["n_diagnostics"] == 1  # SCALINGS_INCOMPLETE, once


def test_the_columns_are_documented_on_the_result_tables_page():
    """``docs/api/core_models.md`` lists every column, in schema order."""
    import pathlib
    import re

    page = pathlib.Path(__file__).resolve().parents[1] / "docs" / "api" / "core_models.md"
    text = page.read_text(encoding="utf-8")
    section = text.split("## Key figures of a fault", 1)[1].split("\n## ", 1)[0]
    documented = re.findall(r"^\| `([A-Za-z_]+)` \|", section, re.M)
    assert documented == list(gi.RES_FAULT_SCHEMA)
