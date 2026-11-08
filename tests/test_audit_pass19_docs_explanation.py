# tests/test_audit_pass19_docs_explanation.py
"""
Audit pass 19 -- the numbers on the "Understanding the model" pages are computed.

Finding IDs: D1 (the Concepts page split into explanation pages), PH5 (the
one-way coupling is an approximation, exact when ``k = Z_m/Z_s`` is the same
on every branch of a mesh; ``phase_impedance_formula`` is ``Z_red``), PH11
(the inverse rho analysis scales bus resistivities only), PH14 (a source on
the fault bus counts in the denominator of the current-based reduction
factor), PH4/F4 (``U_E`` and ``Z_E`` at the fault bus, the chain on a long
run), DOC13 (the examples are scripts in ``docs/snippets/`` that run here).

Every statement is checked a second way, independent of the solve that
produced it: the fully coupled multi-conductor model of
``tests/_reference_mna.py`` for the phase split, the ladder closed form for the
long run, a nodal system written out here for the DC chain, re-solves against
the Moebius form of ``bus_response`` for the electrode sweep, superposition
for the local source, and the closed form of Carson's reactance for the
resistivity dependence.
"""

import cmath
import math
import runpy
import warnings
from pathlib import Path

import numpy as np
import pytest

import groundinsight as gi
from groundinsight.models.core_models import ComplexNumber
from groundinsight.utils.impedance_calculator import compute_impedance
from tests import _reference_mna as ref

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
SNIPPETS = DOCS / "snippets"


def _page(relative):
    """Text of a documentation page with whitespace collapsed."""
    return " ".join((DOCS / relative).read_text(encoding="utf-8").split())


def _run(snippet):
    """Namespace of a documentation snippet, executed as a script would be."""
    return runpy.run_path(str(SNIPPETS / snippet), run_name="docs_snippet")


# --------------------------------------------------------------------------
# PH5 -- explanation/phase-currents.md
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def phase_split():
    """Namespace of ``docs/snippets/phase_split.py``."""
    return _run("phase_split.py")


def _coupled(ns, route_2):
    """Phase current on route 1 and |EPR(F)| of the fully coupled model.

    The reference reads the stored impedance tables of the network the
    snippet builds and adds the phase-earth loop ``Z_pp`` of each branch.
    """
    net = ns["ring"](route_2, "Z_red")
    rd = ref.reference_network(net, 50.0, "F1")
    for name, branch in rd["branches"].items():
        construction = ns["CABLE"] if name in ("SA", "AF") else route_2
        branch["zpp"] = construction["zpp"] * net.branches[name].length
    solution = ref.solve_coupled(rd)
    return abs(solution["I_p"]["SA"]), abs(solution["u"]["F"])


def test_one_way_coupling_is_exact_in_a_homogeneous_ring(phase_split):
    """Same construction on both routes: Z_red, Z_pp and the proxy all give
    the coupled result, and the split is the inverse ratio of the lengths."""
    i_coupled, u_coupled = _coupled(phase_split, phase_split["CABLE"])
    for phase in ("Z_red", "Z_pp", "proxy"):
        i_route_1, u_fault = phase_split["TABLE"][("homogeneous", phase)]
        assert i_route_1 == pytest.approx(i_coupled, rel=1e-9)
        assert u_fault == pytest.approx(u_coupled, rel=1e-9)
    assert i_coupled == pytest.approx(1000.0 * 3.0 / 5.4, rel=1e-9)


def test_one_way_coupling_is_an_approximation_in_a_mixed_ring(phase_split):
    """Cable | overhead line with earth wire: no choice is exact; Z_red gives
    the split closest to the coupled one. The numbers are the page's."""
    i_coupled, u_coupled = _coupled(phase_split, phase_split["OVERHEAD_LINE"])
    assert i_coupled == pytest.approx(764.90, abs=0.005)
    assert u_coupled == pytest.approx(289.231, abs=0.0005)
    split_error = {}
    page = {"Z_red": (728.39, 310.735, "+7.4 %"), "Z_pp": (582.47, 298.635, "+3.3 %"),
            "proxy": (653.92, 278.187, "−3.8 %")}
    for phase, (i_page, u_page, deviation) in page.items():
        i_route_1, u_fault = phase_split["TABLE"][("mixed", phase)]
        assert i_route_1 == pytest.approx(i_page, abs=0.005)
        assert u_fault == pytest.approx(u_page, abs=0.0005)
        assert f"{100 * (u_fault / u_coupled - 1):+.1f} %".replace("-", "−") == deviation
        split_error[phase] = abs(i_route_1 - i_coupled)
    assert min(split_error, key=split_error.get) == "Z_red"


def test_phase_currents_page_states_the_numbers_of_the_snippet():
    text = _page("explanation/phase-currents.md")
    for number in ("555.56 A", "205.863 V", "728.39 A", "310.735 V (+7.4 %)",
                   "582.47 A", "298.635 V (+3.3 %)", "653.92 A", "278.187 V (−3.8 %)",
                   "764.90 A, 289.231 V"):
        assert number in text, number


def test_z_red_is_the_elimination_of_the_shield_current(phase_split):
    """Z_red = Z_pp - Z_m^2/Z_s is what eliminating I_s from the 2x2 branch
    law gives: with u_a = u_b (k u telescopes) the phase drop per ampere."""
    for construction in (phase_split["CABLE"], phase_split["OVERHEAD_LINE"]):
        z = np.array([[construction["zpp"], construction["zm"]],
                      [construction["zm"], construction["zs"]]])
        # Shield bonded at both ends with u_a = u_b: second row = 0.
        i_p = 1.0
        i_s = -construction["zm"] / construction["zs"] * i_p
        v_drop = (z @ np.array([i_p, i_s]))[0]
        assert v_drop == pytest.approx(phase_split["z_red"](construction), rel=1e-12)


# --------------------------------------------------------------------------
# PH4 / F4 -- explanation/reduction-factors.md
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def chain():
    """Namespace of ``docs/snippets/reduction_factors.py``."""
    return _run("reduction_factors.py")


def test_long_run_closes_the_chain_with_the_coupling_factor(chain):
    """U_E = |1-k| * I * |Z_inf| and Z_E = Z_inf on 64 sections (the ladder
    closed form, independent of the solve)."""
    assert abs(chain["u_e"]) == pytest.approx(chain["u_closed"], rel=1e-9)
    assert chain["z_e"] == pytest.approx(chain["z_inf"], rel=1e-9)
    assert chain["r_epr"] == pytest.approx(abs(1 - chain["k"]), rel=1e-12)


def test_reduction_factor_page_states_the_numbers_of_the_snippet(chain):
    text = _page("explanation/reduction-factors.md")
    rf_value = f"{chain['r_epr']:.6f}"
    assert f"`value` | {rf_value} | $\\lvert 1 - k\\rvert$ = {rf_value}" in text
    assert f"`value_current` | {chain['r_current']:.6f}" in text
    z = chain["z_e"]
    assert f"{z.real:.4f} + {z.imag:.4f}j Ω" in text
    assert f"{abs(chain['u_e']):.3f} V" in text
    assert f"{chain['r_current'] * 1000.0 * abs(z):.3f} V" in text
    rf = gi.run_fault(chain["net"], "F").reduction_factor
    group_u = abs(complex(rf.u_earthing_group[50.0]))
    group_z = abs(complex(rf.z_earthing_group[50.0]))
    assert f"{group_u:.2f} V and {group_z:.4f} Ω, 43 % and 44.5 % below" in text
    assert f"{100 * (1 - group_u / abs(chain['u_e'])):.0f}" == "43"
    assert f"{100 * (1 - group_z / abs(chain['z_e'])):.1f}" == "44.5"
    assert "on a long homogeneous run it is 44.5 % below `Z_E`" in text
    assert f"{100 * (chain['r_current'] / chain['r_epr'] - 1):.1f} %" == "2.6 %"


def _feeder():
    """The six-station feeder of the decomposition example (fault at S3)."""
    two_pi = 2 * math.pi
    net = gi.create_network(name="feeder", frequencies=[50.0])
    station = gi.BusType(name="Station", system_type="Substation", voltage_level=20,
                         impedance_formula="rho * 0.02")
    cable = gi.BranchType(
        name="cable", grounding_conductor=True,
        self_impedance_formula=f"(0.25 + j * f * 1.9e-3 * {two_pi}) * l",
        mutual_impedance_formula=f"(0.05 + j * f * 1.85e-3 * {two_pi}) * l",
        phase_impedance_formula=f"(0.10 + j * f * 0.4e-3 * {two_pi}) * l",
    )
    for i in range(6):
        gi.create_bus(name=f"S{i}", type=station, network=net)
    for i in range(5):
        gi.create_branch(name=f"C{i}{i + 1}", type=cable, from_bus=f"S{i}",
                         to_bus=f"S{i + 1}", length=1.2, network=net)
    gi.create_source(name="infeed", bus="S0", values={50.0: 3000.0}, network=net)
    gi.create_fault(name="mid", bus="S3", network=net)
    return net


def test_fault_bus_electrode_sweep_moves_z_e_but_not_value():
    """Four decades of the faulted station's electrode: value stays, Z_E and
    U_E grow 80-fold. First way: re-solves. Second way: the Moebius form of
    bus_response from two solves of the unchanged network."""
    base = _feeder()
    gi.run_fault(base, "mid")
    response = gi.bus_response(base, fault="mid", bus="S3")
    rows = {}
    for z_b in (0.01, 100.0):
        net = _feeder()
        net.buses["S3"].impedance = {50.0: ComplexNumber(real=z_b, imag=0.0)}
        rf = gi.run_fault(net, "mid").reduction_factor
        predicted = response.evaluate(z_b)
        assert abs(complex(rf.u_earthing[50.0])) == pytest.approx(predicted["EPR_V"][0], rel=1e-9)
        assert abs(complex(rf.z_earthing[50.0])) == pytest.approx(
            predicted["Z_driving_point_Ohm"][0], rel=1e-9)
        rows[z_b] = (rf.value[50.0], rf.value_current[50.0],
                     abs(complex(rf.z_earthing[50.0])), abs(complex(rf.u_earthing[50.0])))
    low, high = rows[0.01], rows[100.0]
    assert low[0] == pytest.approx(0.310003, abs=5e-7) and high[0] == pytest.approx(low[0], rel=1e-12)
    assert round(high[2] / low[2]) == 80 and round(high[3] / low[3]) == 80
    text = _page("explanation/reduction-factors.md")
    assert (f"({low[2]:.4f} → {high[2]:.3f} Ω, {low[3]:.1f} → {high[3]:.0f} V)" in text)
    assert f"falls from {low[1]:.3f} to {high[1]:.3f}" in text


def _line_with_sources(local_current):
    """Two cable sections S-M-F, 1000 A at S and optionally a source at F."""
    net = gi.create_network(name="ph14", frequencies=[50.0])
    station = gi.BusType(name="st", system_type="MV", voltage_level=20.0,
                         impedance_formula="2.0")
    cable = gi.BranchType(name="cab", grounding_conductor=True,
                          self_impedance_formula="(0.29+0.625*j*f/50)*l",
                          mutual_impedance_formula="(0.049+0.625*j*f/50)*l")
    for name in ("S", "M", "F"):
        gi.create_bus(name=name, type=station, network=net)
    gi.create_branch(name="L1", type=cable, from_bus="S", to_bus="M", length=1.0, network=net)
    gi.create_branch(name="L2", type=cable, from_bus="M", to_bus="F", length=1.0, network=net)
    gi.create_source(name="remote", bus="S", values={50.0: 1000.0}, network=net)
    if local_current:
        gi.create_source(name="local", bus="F", values={50.0: local_current}, network=net)
    gi.create_fault(name="F1", bus="F", network=net)
    return gi.run_fault(net, "F1")


def test_a_source_on_the_fault_bus_dilutes_value_current_only():
    """PH14: 500 A on the fault bus adds to 3I_0, not to I_E. By
    superposition the local source alone drives no current anywhere, so the
    earth current is that of the remote infeed and r_I scales by 1000/1500."""
    remote = _line_with_sources(0.0).reduction_factor
    both = _line_with_sources(500.0).reduction_factor
    assert abs(complex(both.i_earth[50.0])) == pytest.approx(
        abs(complex(remote.i_earth[50.0])), rel=1e-12)
    assert both.value_current[50.0] == pytest.approx(
        remote.value_current[50.0] * 1000.0 / 1500.0, rel=1e-12)
    assert both.value[50.0] == pytest.approx(remote.value[50.0], rel=1e-12)
    text = _page("explanation/theory.md") + _page("explanation/reduction-factors.md")
    assert "two thirds" in text


# --------------------------------------------------------------------------
# PH11 -- explanation/theory.md, inverse rho analysis
# --------------------------------------------------------------------------


def _carson_line(bus_rho, branch_rho):
    """Two stations and a 2 km cable whose formulas contain rho."""
    net = gi.create_network(name="ph11", frequencies=[50.0])
    station = gi.BusType(name="st", system_type="MV", voltage_level=20.0,
                         impedance_formula="0.02*rho")
    cable = gi.BranchType(
        name="carson", grounding_conductor=True,
        self_impedance_formula=(
            "(0.727 + pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/0.017825))*l"),
        mutual_impedance_formula=(
            "(pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/0.017825))*l"))
    for name in ("S", "F"):
        gi.create_bus(name=name, type=station, specific_earth_resistance=bus_rho, network=net)
    gi.create_branch(name="L", type=cable, from_bus="S", to_bus="F", length=2.0,
                     specific_earth_resistance=branch_rho, network=net)
    gi.create_source(name="q", bus="S", values={50.0: 1000.0}, network=net)
    gi.create_fault(name="F1", bus="F", network=net)
    return net


def _epr_rms(net):
    result = gi.run_fault(net, "F1")
    return abs(complex(next(b.uepr_freq[50.0] for b in result.buses if b.name == "F")))


def test_inverse_rho_scales_bus_resistivity_only():
    """PH11: the EPR the search reports at c_max is the EPR with the bus
    resistivities scaled and the branch resistivity left alone -- a second
    solve of each variant decides which one the search used."""
    net = _carson_line(100.0, 100.0)
    result = gi.find_max_rho_scaling(net, "F1", ["S", "F"], 200.0, c_bounds=(0.1, 100.0))
    c_max = result["c_max"]
    assert net.buses["F"].specific_earth_resistance == 100.0  # restored
    buses_only = _epr_rms(_carson_line(100.0 * c_max, 100.0))
    both = _epr_rms(_carson_line(100.0 * c_max, 100.0 * c_max))
    assert result["u_epr_rms_at_c_max"] == pytest.approx(buses_only, rel=1e-9)
    assert abs(both - buses_only) > 1e-3 * buses_only


def test_carson_reactance_per_decade_of_rho():
    """omega * 2e-4 * ln(sqrt(10)) Ohm/km at 50 Hz, the closed form, against
    the formula evaluated at rho = 100 and 1000 Ohm*m; 0.072 Ohm/km and about
    11 % of the screen-loop reactance of the data-sheet cable."""
    closed = 2 * math.pi * 50.0 * 2e-4 * math.log(math.sqrt(10.0))
    formula = "(0.727 + pi**2*1e-4*f) + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/0.017825)"
    x = {rho: complex(compute_impedance(formula, [50.0], {"rho": rho, "l": 1.0})[50.0]).imag
         for rho in (100.0, 1000.0)}
    assert x[1000.0] - x[100.0] == pytest.approx(closed, rel=1e-12)
    assert f"{closed:.3f}" == "0.072"
    assert round(100 * closed / x[100.0]) == 11
    assert "0.072\\ \\Omega/\\text{km}$ per decade" in _page("explanation/theory.md")


# --------------------------------------------------------------------------
# explanation/dc.md
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dc_chain():
    """Namespace of ``docs/snippets/dc_chain.py``."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", gi.DCLimitWarning)
        return _run("dc_chain.py")


def _nodal_chain(z_span):
    """EPR at A and Z_dp(C) of the chain A-B-C, written out as a 3x3 system."""
    y = 1.0 / z_span
    matrix = np.array([[1 / 0.8 + y, -y, 0], [-y, 1 / 12.0 + 2 * y, -y],
                       [0, -y, 1 / 3.5 + y]], dtype=complex)
    u = np.linalg.solve(matrix, np.array([1000.0, 0.0, -1000.0], dtype=complex))
    z_dp = np.linalg.solve(matrix, np.array([0.0, 0.0, 1.0], dtype=complex))[2]
    return abs(u[0]), abs(z_dp)


def test_dc_chain_against_a_nodal_solve_written_out(dc_chain):
    epr_dc, z_dc = _nodal_chain(2.5)
    assert dc_chain["epr_a"] == pytest.approx(epr_dc, rel=1e-12)
    assert dc_chain["z_e"] == pytest.approx(z_dc, rel=1e-12)
    epr_x, z_x = _nodal_chain(2.5 + 6.0j)  # the constant reactance kept at 0.1 Hz
    rows = {(row["spelling"], row["f_Hz"]): row for row in dc_chain["ROWS"]}
    assert rows[("constant j*X", 0.1)]["EPR_A_V"] == pytest.approx(epr_x, rel=1e-12)
    assert rows[("constant j*X", 0.1)]["Z_E_Ohm"] == pytest.approx(z_x, rel=1e-12)
    for spelling in ("j*2*pi*f*L", "constant j*X"):
        assert rows[(spelling, 0.0)]["EPR_A_V"] == pytest.approx(epr_dc, rel=1e-12)
    assert rows[("j*2*pi*f*L", 0.1)]["EPR_A_V"] == pytest.approx(epr_dc, rel=1e-10)


def test_dc_page_states_the_numbers_of_the_snippet(dc_chain):
    text = _page("explanation/dc.md")
    assert f"{dc_chain['epr_a']:.9f} V" in text
    assert f"{dc_chain['z_e']:.6f} Ω" in text
    rows = {(row["spelling"], row["f_Hz"]): row for row in dc_chain["ROWS"]}
    high = rows[("constant j*X", 0.1)]
    assert f"{high['EPR_A_V']:.6f} V | {high['Z_E_Ohm']:.6f} Ω" in text
    assert f"**{high['EPR_A_V']:.2f} V instead of {dc_chain['epr_a']:.2f} V**" in text
    assert round(100 * (high["EPR_A_V"] / dc_chain["epr_a"] - 1)) == 57
    assert f"$Z_E = {high['Z_E_Ohm']:.2f}\\ \\Omega$ instead of" in text
    assert f"${dc_chain['z_e']:.2f}\\ \\Omega$" in text
    result = gi.run_fault(dc_chain["build"](dc_chain["CARSON"], 0.1), "F")
    carson_01 = complex(next(b.uepr_freq[0.1] for b in result.buses if b.name == "A"))
    assert f"{abs(carson_01):.9f}" in text


# --------------------------------------------------------------------------
# explanation/formulas.md
# --------------------------------------------------------------------------


def test_a_formula_is_evaluated_again_when_its_input_changes():
    """The page says the stored tables follow rho, the type, the length and
    the network frequencies; the solve evaluates nothing."""
    net = gi.create_network(name="eval", frequencies=[50.0])
    station = gi.BusType(name="st", system_type="MV", voltage_level=20.0,
                         impedance_formula="0.01*rho + j*f*1e-3")
    cable = gi.BranchType(name="c", grounding_conductor=True,
                          self_impedance_formula="(0.25 + j*f*0.012)*l",
                          mutual_impedance_formula="j*f*0.012*l")
    gi.create_bus(name="A", type=station, specific_earth_resistance=100.0, network=net)
    gi.create_bus(name="B", type=station, specific_earth_resistance=100.0, network=net)
    gi.create_branch(name="L", type=cable, from_bus="A", to_bus="B", length=2.0, network=net)
    net.buses["A"].specific_earth_resistance = 300.0
    assert complex(net.buses["A"].impedance[50.0]) == pytest.approx(3.0 + 0.05j)
    net.branches["L"].length = 3.0
    assert complex(net.branches["L"].self_impedance[50.0]) == pytest.approx((0.25 + 0.6j) * 3.0)
    net.frequencies = [50.0, 250.0]
    assert complex(net.buses["B"].impedance[250.0]) == pytest.approx(1.0 + 0.25j)
