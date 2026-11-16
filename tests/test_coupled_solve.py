# tests/test_coupled_solve.py
"""
One calculation method: phase conductors, shields and earth solved together.

``run_fault`` determines the phase currents from the full multi-conductor
model -- per branch the 2x2 impedance matrix ``[[Z_s, Z_m], [Z_m, Z_pp]]``
of shield and phase conductor (both loops with earth return), every
electrode, the sources and the fault bond in one linear system per
frequency -- instead of a phase-only network with ``Z_red`` that ignores the
galvanic shield currents. The types are unchanged: a type's
``phase_impedance_formula`` is ``Z_red = Z_pp - Z_m^2 / Z_s``, so
``Z_pp = Z_red + Z_m^2 / Z_s`` follows without loss.

Checked a second way against the coupled solver of the independent oracle
``tests/_reference_mna.py`` (branch currents as unknowns, physical sign
frame), on a meshed network with mixed cables where the one-way model was
off by about 1 %, on a homogeneous ring and a radial feeder where both agree
exactly, and with a Thevenin source, whose loop current is now part of the
solve (no ``THEVENIN_COUPLING`` any more).
"""

import cmath
import math
import warnings

import pytest

import groundinsight as gi
from tests import _reference_mna as ref

FREQ = 50.0
AL300 = "NA2XS(F)2Y 1x300RM/25 12/20 kV"
CU95 = "N2XS(F)2Y 1x95RM/16 12/20 kV"
AL95 = "NA2XS(F)2Y 1x95RM/25 12/20 kV"
TOL = 1e-10


def _station(z: float = 8.6) -> gi.BusType:
    return gi.BusType(name=f"station {z}", system_type="MV/LV", voltage_level=20.0, impedance_formula=repr(z))


def _mesh(l6: str = AL300, l9: str = AL300, *, thevenin: bool = False) -> gi.Network:
    """Christian's test network: a ring with a dead end and two infeeds bonded through the shields."""
    net = gi.create_network(name="mesh", frequencies=[FREQ, 250.0])
    for name in ("B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8"):
        gi.create_bus(name=name, type=_station(), network=net)
    types = gi.standard_branch_types()
    for name, a, b, length, kind in (("L1", "B7", "B1", 1.0, AL300), ("L2", "B1", "B2", 1.0, AL300),
                                     ("L4", "B3", "B4", 1.0, AL300), ("L5", "B4", "B5", 2.0, AL300),
                                     ("L6", "B5", "B6", 1.0, l6), ("L7", "B7", "B6", 5.0, AL300),
                                     ("L8", "B8", "B5", 1.0, AL300), ("L9", "B5", "B2", 10.0, l9)):
        gi.create_branch(name=name, type=types[kind], from_bus=a, to_bus=b, length=length,
                         specific_earth_resistance=100.0, network=net)
    if thevenin:
        gi.create_voltage_source(name="I1", bus="B7", voltage={FREQ: 11547.0, 250.0: 300.0},
                                 source_impedance={FREQ: 0.5 + 2.0j, 250.0: 0.5 + 10.0j}, network=net)
    else:
        gi.create_source(name="I1", bus="B7", values={FREQ: 1000.0, 250.0: 50.0}, network=net)
    gi.create_source(name="I2", bus="B8", values={FREQ: 1000.0, 250.0: 0.0}, network=net)
    gi.create_fault(name="F1", bus="B4", network=net)
    return net


def _oracle(net: gi.Network, f: float) -> dict:
    """The oracle's coupled solution in the package frame, Z_pp = Z_red + Z_m^2/Z_s."""
    rd = ref.reference_network(net, f, "F1")
    for branch in rd["branches"].values():
        zs, zm, zp = branch["zs"], branch["zm"], branch["zp"]
        coupled = branch["gc"] and zs is not None and not ref._isinf(zs) and zs != 0
        branch["zpp"] = zp + zm * zm / zs if coupled else zp
    solution = ref.solve_coupled(rd)
    return {"u": {b: -u for b, u in solution["u"].items()}, "I_p": solution["I_p"], "I_F": solution["I_F"]}


def _epr(result: gi.Result, f: float) -> dict:
    return {b.name: complex(b.uepr_freq[f]) for b in result.buses}


@pytest.mark.parametrize("l6, l9", [(AL300, AL300), (AL95, CU95)], ids=["one type", "mixed"])
def test_the_solve_is_the_coupled_multi_conductor_model(l6, l9):
    net = _mesh(l6, l9)
    with warnings.catch_warnings():
        warnings.simplefilter("error", gi.GroundinsightWarning)
        result = gi.run_fault(net, "F1")
    for f in net.frequencies:
        oracle = _oracle(net, f)
        assert ref.max_relative_deviation(_epr(result, f), oracle["u"]) <= TOL, f
        phase = net.electrical_network.phase_currents[f]
        scale = max(abs(v) for v in oracle["I_p"].values())
        for name, value in oracle["I_p"].items():
            assert abs(complex(phase[name]) - value) <= TOL * scale, (f, name)


def test_mixed_cables_in_a_ring_differ_from_the_one_way_model():
    """The one-way model (the phase split from Z_red alone, galvanic shield
    currents ignored) is about 1 % off in EPR on this ring; the solve is not."""
    net = _mesh(AL95, CU95)
    result = gi.run_fault(net, "F1")
    rd = ref.reference_network(net, FREQ, "F1")
    one_way = ref.to_package_frame(ref.solve_oneway(rd))["u"]
    coupled = _epr(result, FREQ)
    deviation = ref.max_relative_deviation(one_way, coupled)
    assert 1e-3 < deviation < 3e-2


def test_a_thevenin_source_is_part_of_the_solve():
    net = _mesh(AL95, CU95, thevenin=True)
    with warnings.catch_warnings():
        warnings.simplefilter("error", gi.GroundinsightWarning)
        result = gi.run_fault(net, "F1")
    assert "THEVENIN_COUPLING" not in {d.code for d in result.diagnostics}
    for f in net.frequencies:
        oracle = _oracle(net, f)
        assert ref.max_relative_deviation(_epr(result, f), oracle["u"]) <= TOL, f
        assert complex(result.reduction_factor.i_fault[f]) == pytest.approx(oracle["I_F"], rel=TOL)


def test_a_radial_feeder_is_unchanged():
    """Without a mesh the phase currents are the source currents whatever the
    model: the coupled solve equals the one-way oracle."""
    net = _mesh()
    net.update_branch("L7", active=False)
    net.update_branch("L9", active=False)
    result = gi.run_fault(net, "F1")
    rd = ref.reference_network(net, FREQ, "F1")
    one_way = ref.to_package_frame(ref.solve_oneway(rd))["u"]
    assert ref.max_relative_deviation(_epr(result, FREQ), one_way) <= TOL


def test_the_path_mode_is_deprecated():
    net = _mesh()
    with pytest.warns(DeprecationWarning, match="phase_current_mode='paths'"), warnings.catch_warnings():
        warnings.simplefilter("ignore", gi.GroundinsightWarning)
        gi.run_fault(net, "F1", phase_current_mode="paths")


def test_the_reduction_factor_of_the_mixed_ring():
    """r = |U_E| / (|Z_E| |I_F|) with the oracle's own coupled potential and
    driving point (exact), and against the oracle's coupled model with the
    fault bus earthed ideally (Z -> 1e-9 Ohm). The second agrees to 4e-4
    only, and that is physics, not tolerance: in a ring with mixed cables
    the phase split depends slightly on the shield currents, so earthing the
    fault bus ideally also moves the phase currents a little. Norton's
    construction is exact where the phase split does not depend on the
    earthing -- radial feeders, one cable type per mesh."""
    net = _mesh(AL95, CU95)
    factor = gi.run_fault(net, "F1").reduction_factor
    oracle = _oracle(net, FREQ)
    rd = ref.reference_network(net, FREQ, "F1")
    z_e = ref.driving_point_impedance(rd, "B4")
    assert factor.value[FREQ] == pytest.approx(abs(oracle["u"]["B4"]) / (abs(z_e) * abs(oracle["I_F"])), rel=TOL)
    for branch in rd["branches"].values():
        branch["zpp"] = branch["zp"] + branch["zm"] ** 2 / branch["zs"]
    rd["buses"]["B4"]["z"] = 1e-9
    shorted = ref.solve_coupled(rd)
    i_short = shorted["u"]["B4"] / 1e-9
    assert factor.value[FREQ] == pytest.approx(abs(i_short) / abs(shorted["I_F"]), rel=1e-3)
    assert math.isfinite(factor.value[FREQ]) and cmath.isfinite(complex(factor.i_earth[FREQ]))
