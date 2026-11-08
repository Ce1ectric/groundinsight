# tests/test_audit_pass19_floating_earth.py
"""A fault side without an electrode is recognised by structure (audit pass 19).

Finding IDs: follow-up to PH4 / decision F4 (``EARTHING_IMPEDANCE_UNDEFINED``).
``Z_E = (Y_E^-1)_FF`` is undefined when the buses connected to the fault bus
through shields carry no electrode: that group reaches remote earth only
through the loop closure of a voltage source, which is not part of the
earthing network. The decision used to be left to the sparse solver. A fault
bus standing alone gives an exactly zero row and was caught; a fault bus with
shields to further electrode-less buses makes the matrix singular only up to
rounding, and ``z_earthing`` came out as about 4.5e15 Ohm without a
diagnostic. Conversely, a voltage source on a bus without electrode made the
whole earthing matrix singular and ``Z_E`` was reported as undefined for a
fault side that is earthed. Both are now decided on the connected group of
the fault bus in the earthing graph. The same rule applies to the earthing
columns of ``bus_response``, including the case in which the electrode under
study is the only one of the fault side.

Second way: the reference solver of :mod:`tests._reference_mna`, which finds
the group with its own graph search and whose MNA matrix of that group is
rank-deficient; and the closed forms of a series chain and of a parallel pair.
"""

from __future__ import annotations

import warnings
from typing import List

import numpy as np
import pytest

import groundinsight as gi
from groundinsight.diagnostics import ModelAssumptionWarning
from tests import _reference_mna as ref


FREQS = [50.0, 150.0]
STATION = gi.BusType(name="fe_station", system_type="S", voltage_level=20.0,
                     impedance_formula="rho*0.01 + j*f*0.0001")
TOWER = gi.BusType(name="fe_tower", system_type="S", voltage_level=20.0,
                   impedance_formula="nan")
CABLE = gi.BranchType(
    name="fe_cable", grounding_conductor=True,
    self_impedance_formula="(0.25 + j*f*0.012)*l",
    mutual_impedance_formula="(j*f*0.008)*l",
    phase_impedance_formula="(0.2 + j*f*0.010)*l",
)


def _station(f: float) -> complex:
    """Electrode of STATION at rho = 100 Ohm m."""
    return complex(1.0, 1e-4 * f)


def _shield(f: float, length: float) -> complex:
    """Shield self impedance of CABLE."""
    return complex(0.25, 0.012 * f) * length


def _lengths(chain: int) -> List[float]:
    return [0.7 + 0.3 * k for k in range(chain)]


def _thevenin_network(name: str, chain: int, *, earthed_end: bool = False,
                      source_electrode: bool = True) -> gi.Network:
    """Voltage source at S, fault at F, F - B0 - ... shields without electrode.

    S and the fault side are joined by nothing but the source's loop closure.
    ``earthed_end`` gives the last bus of the chain an electrode.
    """
    net = gi.create_network(name=name, frequencies=list(FREQS))
    gi.create_bus(name="S", type=STATION if source_electrode else TOWER,
                  specific_earth_resistance=100.0, network=net)
    gi.create_bus(name="F", type=TOWER, specific_earth_resistance=100.0, network=net)
    previous = "F"
    for k, length in enumerate(_lengths(chain)):
        last = k == chain - 1
        gi.create_bus(name=f"B{k}", type=STATION if (earthed_end and last) else TOWER,
                      specific_earth_resistance=100.0, network=net)
        gi.create_branch(name=f"L{k}", type=CABLE, from_bus=previous, to_bus=f"B{k}",
                         length=length, network=net)
        previous = f"B{k}"
    gi.create_voltage_source(
        name="V", bus="S", voltage={f: complex(8e3, 0.0) for f in FREQS},
        source_impedance={f: complex(1.0, 0.01 * f) for f in FREQS}, network=net,
    )
    gi.create_fault(name="F1", bus="F", network=net)
    return net


def _run(net: gi.Network):
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        result = gi.run_fault(net, "F1")
    return result, [w.message for w in record]


def _undefined(messages) -> list:
    return [m for m in messages if getattr(m, "code", None) == "EARTHING_IMPEDANCE_UNDEFINED"]


# ---------------------------------------------------------------------------
# run_fault: Z_E of a fault side without electrode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("chain", [0, 1, 3])
def test_a_fault_side_without_electrode_has_no_earthing_impedance(chain):
    """F alone, or F with one or three electrode-less buses behind it: Z_E
    and the grounding impedance are None at every frequency, and one
    EARTHING_IMPEDANCE_UNDEFINED names the group. Before, chains 1 and 3 gave
    about 4.5e15 Ohm and no diagnostic."""
    net = _thevenin_network(f"fe_floating_{chain}", chain)
    result, messages = _run(net)
    for f in FREQS:
        assert result.reduction_factor.z_earthing[f] is None
        assert result.grounding_impedance.value[f] is None
    notes = _undefined(messages)
    assert len(notes) == 1
    assert ("fault", "F1") in notes[0].elements and ("bus", "F") in notes[0].elements
    group = ["F"] + [f"B{k}" for k in range(chain)]
    assert all(f"'{name}'" in str(notes[0]) or name in str(notes[0]) for name in group)
    assert "EARTHING_IMPEDANCE_UNDEFINED" in [d.code for d in result.diagnostics]
    # Second way: the reference solver finds the same group by its own search,
    # finds no electrode in it and refuses; the MNA matrix of the group is
    # rank-deficient.
    for f in FREQS:
        rd = ref.reference_network(net, f, "F1")
        assert ref.earthing_group(rd, "F") == group
        with pytest.raises(ref.FloatingEarthError):
            ref.driving_point_impedance(rd, "F")
        A, _, _ = ref.driving_point_system(rd, "F")
        assert np.linalg.matrix_rank(A) < A.shape[0]


def test_an_electrode_at_the_end_of_the_group_gives_the_series_value():
    """Counter-check: the same three-section chain with an electrode on its
    last bus is earthed. Z_E is the series sum of the shields and that
    electrode (closed form) and the reference driving point; no note."""
    net = _thevenin_network("fe_earthed_end", 3, earthed_end=True)
    result, messages = _run(net)
    assert not _undefined(messages)
    for f in FREQS:
        closed = sum(_shield(f, length) for length in _lengths(3)) + _station(f)
        z_e = complex(result.reduction_factor.z_earthing[f])
        assert z_e == pytest.approx(closed, rel=1e-12)
        rd = ref.reference_network(net, f, "F1")
        assert z_e == pytest.approx(ref.driving_point_impedance(rd, "F"), rel=1e-12)
        assert complex(result.grounding_impedance.value[f]) == z_e


def test_a_source_bus_without_electrode_does_not_unground_the_fault_side():
    """The voltage source sits on a bus without electrode, so the earthing
    matrix is singular on the source side -- which says nothing about the
    fault side F - B, earthed at both ends. Z_E is F's electrode in parallel
    with shield plus B's electrode (closed form, and the reference solver on
    the group of F). Before, Z_E was None and the note blamed the fault bus."""
    net = gi.create_network(name="fe_source_floating", frequencies=list(FREQS))
    gi.create_bus(name="S", type=TOWER, specific_earth_resistance=100.0, network=net)
    gi.create_bus(name="F", type=STATION, specific_earth_resistance=100.0, network=net)
    gi.create_bus(name="B", type=STATION, specific_earth_resistance=100.0, network=net)
    gi.create_branch(name="L", type=CABLE, from_bus="F", to_bus="B", length=1.0, network=net)
    gi.create_voltage_source(
        name="V", bus="S", voltage={f: complex(8e3, 0.0) for f in FREQS},
        source_impedance={f: complex(1.0, 0.01 * f) for f in FREQS}, network=net,
    )
    gi.create_fault(name="F1", bus="F", network=net)
    result, messages = _run(net)
    assert not _undefined(messages)
    for f in FREQS:
        closed = 1.0 / (1.0 / _station(f) + 1.0 / (_shield(f, 1.0) + _station(f)))
        z_e = complex(result.reduction_factor.z_earthing[f])
        assert z_e == pytest.approx(closed, rel=1e-12)
        rd = ref.reference_network(net, f, "F1")
        assert ref.earthing_group(rd, "F") == ["F", "B"]
        assert z_e == pytest.approx(ref.driving_point_impedance(rd, "F"), rel=1e-12)


# ---------------------------------------------------------------------------
# bus_response: the earthing columns follow the same rule
# ---------------------------------------------------------------------------


def _tower_behind_station(name: str, z_station=None) -> gi.Network:
    """Fault at the tower F, shield F - B to the station B (the only electrode
    of the fault side), voltage source at S with its own electrode."""
    net = gi.create_network(name=name, frequencies=list(FREQS))
    gi.create_bus(name="S", type=STATION, specific_earth_resistance=100.0, network=net)
    gi.create_bus(name="F", type=TOWER, specific_earth_resistance=100.0, network=net)
    if z_station is None:
        station = STATION
    else:
        station = gi.BusType(name=f"{name}_b", system_type="S", voltage_level=20.0,
                             impedance_formula=f"({complex(z_station).real!r})"
                                               f"+({complex(z_station).imag!r})*j")
    gi.create_bus(name="B", type=station, specific_earth_resistance=100.0, network=net)
    gi.create_branch(name="L", type=CABLE, from_bus="F", to_bus="B", length=1.2, network=net)
    gi.create_voltage_source(
        name="V", bus="S", voltage={f: complex(8e3, 0.0) for f in FREQS},
        source_impedance={f: complex(1.0, 0.01 * f) for f in FREQS}, network=net,
    )
    gi.create_fault(name="F1", bus="F", network=net)
    return net


def test_bus_response_where_the_studied_electrode_is_the_only_one_of_the_fault_side():
    """Without the electrode at B the fault side F - B floats, so the
    open-network columns do not exist -- but Z_E does for every electrode
    at B: all of the current leaves through it, Z_E = Z_B + Z_shield. Before,
    the columns came from a matrix singular up to rounding (about 1e15 Ohm).
    Second ways: the closed form, and run_fault with that electrode installed."""
    net = _tower_behind_station("fe_response")
    _run(net)
    response = gi.bus_response(net, fault="F1", bus="B")
    for f in FREQS:
        shield = _shield(f, 1.2)
        for z_bus in (0.5, 2.0, complex(1.0, -4.0), _station(f)):
            predicted = response.fault_driving_point(z_bus, f)
            assert predicted == pytest.approx(complex(z_bus) + shield, rel=1e-12)
        installed = _tower_behind_station(f"fe_installed_{f:g}", z_station=complex(0.5, 0.0))
        solved, _ = _run(installed)
        assert response.fault_driving_point(0.5, f) == pytest.approx(
            complex(solved.reduction_factor.z_earthing[f]), rel=1e-12)
        assert response.fault_driving_point(0.0, f) == pytest.approx(shield, rel=1e-12)
        assert response.fault_driving_point(None, f) is None
    # the rows report the same Z_E as run_fault for the installed electrode
    frame = response.evaluate(_station(50.0))
    row = frame.filter(frame["frequency_Hz"] == 50.0).row(0, named=True)
    solved, _ = _run(_tower_behind_station("fe_rows"))
    assert row["Z_G_Ohm"] == pytest.approx(abs(complex(solved.reduction_factor.z_earthing[50.0])), rel=1e-12)


def test_bus_response_of_a_bus_outside_a_floating_fault_side():
    """The fault side F - B0 floats whatever the electrode at S does: no Z_E
    for any electrode there (run_fault says None as well). S itself stays
    characterisable through the second station S2 behind it."""
    net = _thevenin_network("fe_response_floating", 1)
    gi.create_bus(name="S2", type=STATION, specific_earth_resistance=100.0, network=net)
    gi.create_branch(name="LS", type=CABLE, from_bus="S", to_bus="S2", length=0.8, network=net)
    result, _ = _run(net)
    assert all(result.reduction_factor.z_earthing[f] is None for f in FREQS)
    response = gi.bus_response(net, fault="F1", bus="S")
    for f in FREQS:
        for z_bus in (None, 0.5, 0.0, complex(1.0, -4.0)):
            assert response.fault_driving_point(z_bus, f) is None


# ---------------------------------------------------------------------------
# solve_network: a floating group is refused, not solved to rounding noise
# ---------------------------------------------------------------------------


OVERHEAD = gi.BranchType(
    name="fe_ohl", grounding_conductor=False,
    self_impedance_formula="(0.3 + j*f*0.015)*l",
    mutual_impedance_formula="(j*f*0.004)*l",
    phase_impedance_formula="(0.15 + j*f*0.012)*l",
)


@pytest.mark.parametrize("chain", [0, 1, 3])
def test_a_floating_group_is_refused_instead_of_solved_to_noise(chain):
    """Current source at S, overhead line without earth wire S - F, and the
    tower F with 0, 1 or 3 electrode-less buses behind it through shields.
    The fault side has no path to earth, so its potential is undefined. F
    alone gave an exactly zero row and raised; with buses behind it the matrix
    is singular only up to rounding, and run_fault reported 2.2e18 V at the
    fault bus without a word. Now every chain raises the documented ValueError
    naming the group. Second way: the reference solver's own search finds the
    same group without electrode, and its MNA matrix is rank-deficient."""
    net = gi.create_network(name=f"fe_island_{chain}", frequencies=list(FREQS))
    gi.create_bus(name="S", type=STATION, specific_earth_resistance=100.0, network=net)
    gi.create_bus(name="F", type=TOWER, specific_earth_resistance=100.0, network=net)
    gi.create_branch(name="O", type=OVERHEAD, from_bus="S", to_bus="F", length=1.0, network=net)
    previous = "F"
    for k, length in enumerate(_lengths(chain)):
        gi.create_bus(name=f"B{k}", type=TOWER, specific_earth_resistance=100.0, network=net)
        gi.create_branch(name=f"L{k}", type=CABLE, from_bus=previous, to_bus=f"B{k}",
                         length=length, network=net)
        previous = f"B{k}"
    gi.create_source(name="Q", bus="S", values={f: 1000.0 for f in FREQS}, network=net)
    gi.create_fault(name="F1", bus="F", network=net)
    group = ["F"] + [f"B{k}" for k in range(chain)]
    with pytest.raises(ValueError, match="no path to reference earth") as excinfo:
        gi.run_fault(net, "F1")
    for name in group:
        assert f"'{name}'" in str(excinfo.value)
    for f in FREQS:
        rd = ref.reference_network(net, f, "F1")
        assert ref.earthing_group(rd, "F") == group
        assert not any(ref.has_electrode(rd, b) for b in group)
        A, _, _ = ref.driving_point_system(rd, "F")
        assert np.linalg.matrix_rank(A) < A.shape[0]
