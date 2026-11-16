# tests/test_reduction_factor.py
"""
The reduction factor of EN 50522: one definition, checked against a short circuit.

``r = U_E / (Z_E * I_F)`` with ``U_E`` the EPR at the fault bus and ``Z_E``
the driving-point impedance of the earthing network there; ``I_E = r * I_F =
U_E / Z_E`` is the earth current. By Norton's theorem ``I_E`` is the current
that flows into the fault bus's earthing when that earthing is ideal
(``Z -> 0``): the network seen from the fault bus is linear, so
``U_E = Z_E * I_short`` holds for every ``Z_E > 0``. The chain of the norm,
``U_E = r * Z_E * I_F``, is then exact, and no balanced state far from the
fault is needed to define ``r``.

Checked a second way, never by the identity itself (it holds by
construction):

* a solve with the fault-bus electrode made ideal, through the public API,
  gives the same ``r`` as the current into that electrode;
* the independent oracle ``tests/_reference_mna.py`` shorts the fault bus in
  its own formulation;
* closed forms: one cable between source and fault,
  ``r = (Z_s - Z_m) / (Z_s + Z_A)``, and the long-line limit
  ``|1 - Z_m / Z_s|``;
* ``r`` does not depend on the fault-bus electrode, because the short
  circuit takes it out.
"""

import cmath
import json
import warnings
from pathlib import Path

import polars as pl
import pytest

import groundinsight as gi
from tests import _reference_mna as mna

FREQ = 50.0
DATA = Path(__file__).parent / "data"


def _bus_type(name: str, z: complex) -> gi.BusType:
    return gi.BusType(name=name, system_type="MV", voltage_level=20.0,
                      impedance_formula=f"({z.real}+{z.imag}*j)")


def _cable(name: str, zs: complex, zm: complex, zp: complex = complex(0.2, 0.3)) -> gi.BranchType:
    return gi.BranchType(name=name, grounding_conductor=True,
                         self_impedance_formula=f"({zs.real}+{zs.imag}*j)*l",
                         mutual_impedance_formula=f"({zm.real}+{zm.imag}*j)*l",
                         phase_impedance_formula=f"({zp.real}+{zp.imag}*j)*l")


ZS, ZM = complex(0.1, 0.2), complex(0.05, 0.1)


def _line(z_source: complex, z_fault: complex, i_f: float = 1000.0) -> gi.Network:
    """One cable of 1 km: source at A, fault at B."""
    net = gi.create_network(name="line", frequencies=[FREQ])
    gi.create_bus(name="A", type=_bus_type("ZA", z_source), network=net)
    gi.create_bus(name="B", type=_bus_type("ZB", z_fault), network=net)
    gi.create_branch(name="K", type=_cable("K", ZS, ZM), from_bus="A", to_bus="B", length=1.0, network=net)
    gi.create_source(name="Q", bus="A", values={FREQ: i_f}, network=net)
    gi.create_fault(name="F", bus="B", network=net)
    return net


def _meshed(z_fault: complex = complex(8.6, 0.0)) -> gi.Network:
    """A ring, two sources inside the earthing network, a dead end; two cable
    types, but one per mesh: the ring is Al 1x300, the radial L6 Cu 1x95. The
    phase split then does not depend on the earthing, and Norton's short
    circuit is exact (a ring of mixed cables: tests/test_coupled_solve.py)."""
    net = gi.create_network(name="mesh", frequencies=[FREQ, 250.0])
    station = _bus_type("station", complex(8.6, 0.0))
    for name in ("B1", "B2", "B3", "B5", "B6", "B7", "B8"):
        gi.create_bus(name=name, type=station, network=net)
    gi.create_bus(name="B4", type=_bus_type("fault station", z_fault), network=net)
    al = gi.standard_branch_types()["NA2XS(F)2Y 1x300RM/25 12/20 kV"]
    cu = gi.standard_branch_types()["N2XS(F)2Y 1x95RM/16 12/20 kV"]
    for name, a, b, length, kind in (("L1", "B7", "B1", 1.0, al), ("L2", "B1", "B2", 1.0, al),
                                     ("L4", "B3", "B4", 1.0, cu), ("L5", "B4", "B5", 2.0, al),
                                     ("L6", "B5", "B6", 1.0, al), ("L7", "B7", "B6", 5.0, al),
                                     ("L8", "B8", "B5", 1.0, cu), ("L9", "B5", "B2", 10.0, al)):
        gi.create_branch(name=name, type=kind, from_bus=a, to_bus=b, length=length,
                         specific_earth_resistance=100.0, network=net)
    gi.create_source(name="I1", bus="B7", values={FREQ: 1000.0, 250.0: 50.0}, network=net)
    gi.create_source(name="I2", bus="B8", values={FREQ: 1000.0, 250.0: 0.0}, network=net)
    gi.create_fault(name="F1", bus="B4", network=net)
    return net


def _factor(net: gi.Network, fault: str):
    return gi.run_fault(net, fault).reduction_factor


def _epr(net: gi.Network, fault: str, bus: str, f: float) -> complex:
    row = net.res_buses(fault).filter((pl.col("bus_name") == bus) & (pl.col("frequency_Hz") == f)).row(
        0, named=True)
    return row["EPR_V"] * cmath.exp(1j * cmath.pi * row["EPR_degree"] / 180)


def test_one_cable_has_the_closed_form():
    for z_source in (complex(1e-6, 0.0), complex(0.5, 0.0), complex(10.0, 0.0)):
        factor = _factor(_line(z_source, complex(10.0, 0.0)), "F")
        closed = (ZS - ZM) / (ZS + z_source)
        assert factor.value[FREQ] == pytest.approx(abs(closed), rel=1e-12)
        assert abs(complex(factor.i_earth[FREQ])) == pytest.approx(1000.0 * abs(closed), rel=1e-12)


def test_ideal_bonding_gives_the_cable_value():
    factor = _factor(_line(complex(1e-9, 0.0), complex(10.0, 0.0)), "F")
    assert factor.value[FREQ] == pytest.approx(abs(1 - ZM / ZS), rel=1e-8)


def test_the_earth_current_flows_into_an_ideal_fault_earth():
    """Christian's construction: the fault bus earthed with Z -> 0."""
    net = _meshed()
    factor = _factor(net, "F1")
    shorted = _meshed(z_fault=complex(1e-9, 0.0))
    gi.run_fault(shorted, "F1")
    for f in (FREQ, 250.0):
        i_short = _epr(shorted, "F1", "B4", f) / 1e-9
        i_f = complex(factor.i_fault[f])
        assert factor.value[f] == pytest.approx(abs(i_short / i_f), rel=1e-6)
        assert complex(factor.i_earth[f]) == pytest.approx(i_short, rel=1e-6)


def test_the_oracle_shorts_the_fault_bus_its_own_way():
    """The coupled model of the oracle with the fault bus earthed ideally
    (1e-9 Ohm) against r of the solve."""
    net = _meshed()
    factor = _factor(net, "F1")
    for f in (FREQ, 250.0):
        data = mna.reference_network(net, f, "F1")
        data["buses"]["B4"]["z"] = 1e-9
        shorted = mna.solve_package(data)
        i_short = shorted["u"]["B4"] / 1e-9
        assert factor.value[f] == pytest.approx(abs(i_short) / abs(shorted["I_F"]), rel=1e-6)


def test_r_does_not_depend_on_the_fault_bus_electrode():
    values = [_factor(_meshed(z_fault=complex(z, 0.0)), "F1").value[FREQ] for z in (0.1, 1.0, 8.6, 100.0)]
    assert max(values) - min(values) < 1e-12 * max(values)


def test_the_chain_of_the_norm_holds_with_the_measured_earthing_impedance():
    """U_E = r * Z_E * I_F with Z_E the driving point (what a measurement sees)."""
    net = _meshed()
    factor = _factor(net, "F1")
    for f in (FREQ, 250.0):
        u_e = _epr(net, "F1", "B4", f)
        z_e = complex(net.results["F1"].grounding_impedance.value[f])
        i_f = complex(factor.i_fault[f])
        assert abs(u_e) == pytest.approx(factor.value[f] * abs(z_e) * abs(i_f), rel=1e-9)


def test_a_long_line_tends_to_one_minus_k():
    """The end effect of the source station decays as exp(-n * Re(gamma)),
    gamma = arccosh(1 + Z'/(2 Z_e)): Re(gamma) = 0.17 here, so 100 stations
    leave about 4e-8 (60 stations: 3.7e-5, as observed)."""
    net = gi.create_network(name="long", frequencies=[FREQ])
    station = _bus_type("st", complex(5.0, 0.0))
    names = [f"S{i}" for i in range(100)]
    for name in names:
        gi.create_bus(name=name, type=station, network=net)
    for i in range(99):
        gi.create_branch(name=f"K{i}", type=_cable("K", ZS, ZM), from_bus=names[i], to_bus=names[i + 1],
                         length=1.0, network=net)
    gi.create_source(name="Q", bus=names[0], values={FREQ: 1000.0}, network=net)
    gi.create_fault(name="F", bus=names[-1], network=net)
    assert _factor(net, "F").value[FREQ] == pytest.approx(abs(1 - ZM / ZS), rel=1e-6)


def test_a_fault_on_the_feeding_bus_has_no_earth_current():
    net = _line(complex(1.0, 0.0), complex(1.0, 0.0))
    gi.create_fault(name="FA", bus="A", network=net)
    result = gi.run_fault(net, "FA")
    assert result.reduction_factor.value[FREQ] == pytest.approx(0.0, abs=1e-12)
    assert "FAULT_AT_FEEDING_BUS" in {d.code for d in result.diagnostics}


def test_no_fault_current_leaves_r_undefined():
    net = _line(complex(1.0, 0.0), complex(1.0, 0.0))
    net.frequencies = [FREQ, 250.0]
    net.sources["Q"].values = {FREQ: 1000.0, 250.0: 0.0}
    factor = _factor(net, "F")
    assert factor.value[250.0] is None and factor.i_earth[250.0] is None


def test_value_current_is_a_deprecated_alias():
    factor = _factor(_line(complex(1.0, 0.0), complex(1.0, 0.0)), "F")
    with pytest.warns(DeprecationWarning, match="value_current"):
        assert factor.value_current == factor.value
    assert "value_current" not in json.loads(factor.model_dump_json())


def test_res_fault_has_one_reduction_factor():
    net = _meshed()
    gi.run_fault(net, "F1")
    table = net.res_fault("F1")
    assert "r" in table.columns and not {"r_epr", "r_current"} & set(table.columns)
    row = table.filter(pl.col("frequency_Hz") == FREQ).row(0, named=True)
    assert row["r"] == pytest.approx(net.results["F1"].reduction_factor.value[FREQ])
    assert row["I_E_A"] == pytest.approx(row["r"] * row["I_F_A"])


def test_a_stored_result_of_2026_11_is_read_with_the_new_r():
    """Stored U_E, Z_E and I_F give r exactly; the old ratios are not carried."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", gi.GroundinsightWarning)
        project = gi.load_project(DATA / "project_written_by_2026_11_1.json")
    net = project.network
    for fault, stored in net.results.items():
        factor = stored.reduction_factor
        assert "LEGACY_REDUCTION_FACTOR" in {d.code for d in stored.diagnostics}
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", gi.GroundinsightWarning)  # the file's partial scalings
            fresh = gi.solve(net, fault).reduction_factor
        for f, value in fresh.value.items():
            assert factor.value[f] == pytest.approx(value, rel=1e-12)
            assert complex(factor.i_earth[f]) == pytest.approx(complex(fresh.i_earth[f]), rel=1e-12)
