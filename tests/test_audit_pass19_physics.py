# tests/test_audit_pass19_physics.py
"""Physics of the stationary solver against an independent oracle (audit pass 19).

Finding IDs: PQ-REF and PQ-INV (permanent regression tests that pin what the
review verified), PH1 (PQ-PH1b), PH3 (PQ-PH3), PH4 (PQ-PH4) and PH6 (PQ-PH6).

Every assertion here compares ``gi.run_fault`` with a second, independent
method: the reference solver in :mod:`tests._reference_mna` (modified nodal
analysis with the branch currents as unknowns, physical sign frame), a closed
form, or an algebraic identity that must hold for any passive network (KCL,
reciprocity, Tellegen). The networks are built through the public API only.
"""

from __future__ import annotations

import logging
import random
import warnings
from typing import Dict, List, Optional

import numpy as np
import pytest

import groundinsight as gi
from tests import _reference_mna as ref

try:  # hypothesis drives one variant of the invariant tests when available
    from hypothesis import HealthCheck, given, settings
    from hypothesis import strategies as st
except ImportError:  # pragma: no cover - the seeded variant always runs
    given = None


TOL = 1e-12

CAB = dict(zs="(0.29+0.625*j*f/50)*l", zm="(0.049+0.625*j*f/50)*l", zp="(0.13+0.12*j*f/50)*l")
OHL = dict(zs="(0.65+0.785*j*f/50)*l", zm="(0.049+0.372*j*f/50)*l", zp="(0.32+0.62*j*f/50)*l")
NOEW = dict(zs="(0.65+0.785*j*f/50)*l", zm="0", zp="(0.355+0.759*j*f/50)*l", gc=False)


# ---------------------------------------------------------------------------
# building networks from a plain specification
# ---------------------------------------------------------------------------


def build(spec: dict, name: str = "t") -> gi.Network:
    """Build a network from a plain specification through the public API.

    ``spec`` keys: ``freqs``; ``buses`` ``{name: (formula, rho)}``;
    ``branches`` ``{name: dict(a, b, l, zs, zm[, zp][, gc][, rho])}``;
    ``sources`` ``{name: dict(kind="current", bus, I={f: I}) | dict(kind=
    "voltage", bus, E={f: E}, Zsrc={f: Z})}``; ``faults`` ``{name: (bus,
    scalings)}``; optional ``inactive_buses`` / ``inactive_branches``.
    """
    net = gi.create_network(name=name, frequencies=list(spec["freqs"]))
    bus_types: Dict[str, gi.BusType] = {}
    for bus_name, (formula, rho) in spec["buses"].items():
        if formula not in bus_types:
            bus_types[formula] = gi.BusType(
                name=f"bt{len(bus_types)}", system_type="S", voltage_level=20.0,
                impedance_formula=formula,
            )
        gi.create_bus(
            name=bus_name, type=bus_types[formula],
            specific_earth_resistance=rho, network=net,
        )
    branch_types: Dict[tuple, gi.BranchType] = {}
    for branch_name, d in spec["branches"].items():
        key = (d["zs"], d["zm"], d.get("zp"), d.get("gc", True))
        if key not in branch_types:
            kwargs = dict(
                name=f"brt{len(branch_types)}", grounding_conductor=d.get("gc", True),
                self_impedance_formula=d["zs"], mutual_impedance_formula=d["zm"],
            )
            if d.get("zp") is not None:
                kwargs["phase_impedance_formula"] = d["zp"]
            branch_types[key] = gi.BranchType(**kwargs)
        gi.create_branch(
            name=branch_name, type=branch_types[key], from_bus=d["a"], to_bus=d["b"],
            length=d["l"], specific_earth_resistance=d.get("rho", 100.0), network=net,
        )
    for source_name, d in spec["sources"].items():
        if d["kind"] == "current":
            gi.create_source(name=source_name, bus=d["bus"], values=dict(d["I"]), network=net)
        else:
            gi.create_voltage_source(
                name=source_name, bus=d["bus"], voltage=dict(d["E"]),
                source_impedance=dict(d["Zsrc"]), network=net,
            )
    for fault_name, (bus, scalings) in spec["faults"].items():
        gi.create_fault(name=fault_name, bus=bus, scalings=dict(scalings), network=net)
    for bus_name in spec.get("inactive_buses", []):
        net.buses[bus_name].active = False
    for branch_name in spec.get("inactive_branches", []):
        net.branches[branch_name].active = False
    return net


def solve(net: gi.Network, fault: str = "F1", **kwargs) -> gi.Result:
    """``run_fault`` with logging and warnings silenced (they are tested elsewhere)."""
    logging.getLogger("groundinsight").setLevel(logging.ERROR)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return gi.run_fault(net, fault, **kwargs)
    finally:
        logging.getLogger("groundinsight").setLevel(logging.NOTSET)


def package_vectors(result: gi.Result, frequency: float) -> dict:
    """Per-bus and per-branch phasors of a result at one frequency."""
    return {
        "u": {b.name: complex(b.uepr_freq[frequency]) for b in result.buses},
        "ia": {b.name: complex(b.ia_freq[frequency]) for b in result.buses},
        "i_inj": {b.name: complex(b.i_inj_freq[frequency]) for b in result.buses},
        "i_s": {br.name: complex(br.i_s_freq[frequency]) for br in result.branches},
    }


def br(a: str, b: str, length: float, typ: dict, **override) -> dict:
    d = dict(a=a, b=b, l=length)
    d.update(typ)
    d.update(override)
    return d


def at50(value) -> dict:
    return {50.0: value}


def strip_phase(spec: dict) -> dict:
    """The same network without any declared phase impedance (proxy cases)."""
    out = dict(spec)
    out["branches"] = {
        k: {kk: vv for kk, vv in v.items() if kk != "zp"} for k, v in spec["branches"].items()
    }
    return out


# ---------------------------------------------------------------------------
# PQ-REF -- seventeen structural cases against the reference solver
# ---------------------------------------------------------------------------


def _structural_cases() -> Dict[str, dict]:
    cases: Dict[str, dict] = {}
    cases["single line"] = dict(
        freqs=[50.0], buses={"S": ("0.5", 100.0), "F": ("2.0", 100.0)},
        branches={"L": dict(a="S", b="F", l=5.0, zs="(0.2+0.4*j)*l", zm="(0.05+0.35*j)*l")},
        sources={"src": dict(kind="current", bus="S", I=at50(1000.0))},
        faults={"F1": ("F", {50.0: 1.0})},
    )
    cases["chain"] = dict(
        freqs=[50.0],
        buses={"S": ("0.3", 100.0), "A": ("2.0", 100.0), "B": ("3.0", 100.0), "F": ("1.5", 100.0)},
        branches={"L1": br("S", "A", 1.2, CAB), "L2": br("A", "B", 0.8, CAB), "L3": br("B", "F", 1.5, CAB)},
        sources={"src": dict(kind="current", bus="S", I=at50(1000.0))},
        faults={"F1": ("F", {50.0: 1.0})},
    )
    cases["chain reversed orientation"] = dict(
        cases["chain"],
        branches={"L1": br("A", "S", 1.2, CAB), "L2": br("A", "B", 0.8, CAB), "L3": br("F", "B", 1.5, CAB)},
    )
    ring_buses = {n: ("2.0", 100.0) for n in "ABCDE"}
    ring_buses["S"] = ("0.3", 100.0)
    cases["uniform ring"] = dict(
        freqs=[50.0], buses=ring_buses,
        branches={
            "R1": br("S", "A", 1.0, CAB), "R2": br("A", "B", 1.0, CAB), "R3": br("B", "C", 1.0, CAB),
            "R4": br("C", "D", 1.0, CAB), "R5": br("D", "E", 1.0, CAB), "R6": br("E", "S", 1.0, CAB),
        },
        sources={"src": dict(kind="current", bus="S", I=at50(1000.0))},
        faults={"F1": ("C", {50.0: 1.0})},
    )
    grid_buses = {f"N{i}{j}": ("2.5", 100.0) for i in range(3) for j in range(3)}
    grid_buses["N00"] = ("0.3", 100.0)
    grid_branches = {}
    for i in range(3):
        for j in range(3):
            if i < 2:
                grid_branches[f"V{i}{j}"] = br(f"N{i}{j}", f"N{i + 1}{j}", 0.5 + 0.1 * j, CAB)
            if j < 2:
                grid_branches[f"H{i}{j}"] = br(f"N{i}{j}", f"N{i}{j + 1}", 0.7 + 0.1 * i, CAB)
    cases["mesh 3x3"] = dict(
        freqs=[50.0], buses=grid_buses, branches=grid_branches,
        sources={"src": dict(kind="current", bus="N00", I=at50(1000.0))},
        faults={"F1": ("N22", {50.0: 1.0})},
    )
    cases["parallel cables"] = dict(
        freqs=[50.0], buses={"S": ("0.3", 100.0), "F": ("2.0", 100.0)},
        branches={"P1": br("S", "F", 2.0, CAB), "P2": br("S", "F", 2.0, CAB), "P3": br("F", "S", 3.0, OHL)},
        sources={"src": dict(kind="current", bus="S", I=at50(1000.0))},
        faults={"F1": ("F", {50.0: 1.0})},
    )
    cases["mixed ring cable/OHL/no earth wire"] = dict(
        freqs=[50.0],
        buses={"S": ("0.3", 100.0), "A": ("2.0", 100.0), "B": ("8.0", 100.0), "C": ("10.0", 100.0),
               "D": ("1.5", 100.0)},
        branches={
            "K1": br("S", "A", 2.0, CAB), "K2": br("A", "D", 1.5, CAB), "O1": br("S", "B", 3.0, OHL),
            "O2": br("B", "C", 2.0, NOEW), "O3": br("C", "D", 2.5, OHL),
        },
        sources={"src": dict(kind="current", bus="S", I=at50(1000.0))},
        faults={"F1": ("D", {50.0: 1.0})},
    )
    cases["open ends (nan)"] = dict(
        freqs=[50.0],
        buses={"S": ("0.3", 100.0), "A": ("nan", 100.0), "B": ("4.0", 100.0), "F": ("2.0", 100.0)},
        branches={
            "L1": br("S", "A", 1.0, CAB), "L2": br("A", "F", 1.0, CAB), "L3": br("S", "B", 1.0, CAB),
            "L4": br("B", "F", 1.0, CAB, zs="nan"),
        },
        sources={"src": dict(kind="current", bus="S", I=at50(1000.0))},
        faults={"F1": ("F", {50.0: 1.0})},
    )
    cases["two sources"] = dict(
        cases["uniform ring"],
        sources={"s1": dict(kind="current", bus="S", I=at50(800.0)),
                 "s2": dict(kind="current", bus="A", I=at50(300.0 - 200j))},
    )
    cases["source on the fault bus"] = dict(
        cases["chain"],
        sources={"s1": dict(kind="current", bus="S", I=at50(1000.0)),
                 "s2": dict(kind="current", bus="F", I=at50(500.0))},
    )
    multi = [0.0, 50.0, 150.0, 250.0]
    cases["0/50/150/250 Hz chain"] = dict(
        cases["chain"], freqs=multi,
        sources={"src": dict(kind="current", bus="S",
                             I={0.0: 50.0, 50.0: 1000.0, 150.0: 120.0, 250.0: 80.0 + 10j})},
        faults={"F1": ("F", {0.0: 1.0, 50.0: 1.0, 150.0: 0.8, 250.0: 0.5})},
    )
    cases["0/50/150/250 Hz mesh"] = dict(
        cases["mesh 3x3"], freqs=multi,
        sources={"src": dict(kind="current", bus="N00",
                             I={0.0: 50.0, 50.0: 1000.0, 150.0: 120.0, 250.0: 80.0 + 10j})},
        faults={"F1": ("N22", {0.0: 1.0, 50.0: 1.0, 150.0: 0.8, 250.0: 0.5})},
    )
    cases["inactive bus and branch"] = dict(
        cases["mesh 3x3"], inactive_buses=["N11"], inactive_branches=["H01"],
    )
    cases["rho-dependent formulas"] = dict(
        freqs=[50.0, 250.0],
        buses={"S": ("0.002*rho + 0.01*j*f/50", 100.0), "A": ("0.02*rho+0.1*j*f/50", 300.0),
               "F": ("0.015*rho", 80.0)},
        branches={"L1": br("S", "A", 1.0, CAB), "L2": br("A", "F", 2.0, OHL), "L3": br("S", "F", 4.0, CAB)},
        sources={"src": dict(kind="current", bus="S", I={50.0: 1000.0, 250.0: 100.0})},
        faults={"F1": ("F", {50.0: 1.0, 250.0: 1.0})},
    )
    cases["uniform ring, proxy"] = strip_phase(cases["uniform ring"])
    cases["mixed ring, proxy"] = strip_phase(cases["mixed ring cable/OHL/no earth wire"])
    cases["mesh 3x3, proxy"] = strip_phase(cases["mesh 3x3"])
    return cases


STRUCTURAL_CASES = _structural_cases()


def test_there_are_seventeen_structural_cases():
    assert len(STRUCTURAL_CASES) == 17


@pytest.mark.parametrize("case", sorted(STRUCTURAL_CASES))
def test_run_fault_matches_the_reference_solver(case):
    """PQ-REF: EPR, shield and electrode currents, both reduction factors and
    the earth group agree with the branch-current MNA to <= 1e-12."""
    spec = STRUCTURAL_CASES[case]
    net = build(spec, case.replace(" ", "_"))
    result = solve(net)
    factor = result.reduction_factor
    for f in spec["freqs"]:
        rd = ref.reference_network(net, f, "F1")
        solution = ref.solve_oneway(rd, thevenin_phase="norton")
        mapped = ref.to_package_frame(solution)
        pkg = package_vectors(result, f)
        assert ref.max_relative_deviation(pkg["u"], mapped["u"]) <= TOL, (case, f, "u")
        assert ref.max_relative_deviation(pkg["ia"], mapped["ia"]) <= TOL, (case, f, "ia")
        assert ref.max_relative_deviation(pkg["i_s"], mapped["i_s"]) <= TOL, (case, f, "i_s")

        r_ref = ref.reduction_factor_epr(rd, thevenin_phase="norton")
        assert factor.value[f] == pytest.approx(r_ref, rel=TOL), (case, f, "value")

        i_earth, group = ref.half_plane_split(mapped["ia"], rd["fault_bus"])
        assert sorted(factor.earth_buses[f]) == group, (case, f, "group")
        assert abs(complex(factor.i_earth[f]) - i_earth) <= TOL * abs(i_earth), (case, f, "I_E")
        r_current_ref = abs(i_earth) / abs(solution["I_F"])
        assert factor.value_current[f] == pytest.approx(r_current_ref, rel=TOL), (case, f, "r_I")


# ---------------------------------------------------------------------------
# PQ-INV -- invariants every passive solution has to satisfy
# ---------------------------------------------------------------------------


def random_spec(seed: int, n: int = 8, extra: int = 4, freqs=(50.0, 150.0),
                gc_frac: float = 0.85, coupled: bool = True) -> dict:
    """A random meshed network with current sources only (seeded, reproducible)."""
    rnd = random.Random(seed)
    names = [f"B{i}" for i in range(n)]
    buses = {b: (f"{rnd.uniform(0.2, 8):.4f}+{rnd.uniform(0, 0.5):.4f}*j*f/50", 100.0) for b in names}
    edges = [(names[rnd.randrange(i)], names[i]) for i in range(1, n)]
    for _ in range(extra):
        a, b = rnd.sample(names, 2)
        edges.append((a, b))
    branches = {}
    for k, (a, b) in enumerate(edges):
        if rnd.random() < 0.5:
            a, b = b, a
        rs, xs = rnd.uniform(0.1, 0.7), rnd.uniform(0.3, 0.8)
        xm = xs * rnd.uniform(0.3, 0.98)
        branches[f"L{k}"] = dict(
            a=a, b=b, l=round(rnd.uniform(0.2, 3.0), 3),
            zs=f"({rs:.4f}+{xs:.4f}*j*f/50)*l",
            zm=f"(0.049+{xm:.4f}*j*f/50)*l" if coupled else "0",
            gc=rnd.random() < gc_frac,
            zp=f"({rnd.uniform(0.1, 0.4):.4f}+{rnd.uniform(0.1, 0.7):.4f}*j*f/50)*l",
        )
    source_bus, fault_bus = rnd.sample(names, 2)
    sources = {"s1": dict(kind="current", bus=source_bus,
                          I={f: complex(rnd.uniform(200, 1500), rnd.uniform(-300, 300)) for f in freqs})}
    if rnd.random() < 0.5:
        second = rnd.choice([x for x in names if x != fault_bus])
        sources["s2"] = dict(kind="current", bus=second,
                             I={f: complex(rnd.uniform(100, 500), 0.0) for f in freqs})
    return dict(freqs=list(freqs), buses=buses, branches=branches, sources=sources,
                faults={"F1": (fault_bus, {f: 1.0 for f in freqs})})


def _kcl_residuals(spec: dict, result: gi.Result):
    """Largest per-bus and global KCL residual, relative to the injections.

    Package frame: the shield current ``i_s`` of a branch leaves its from-bus
    as ``-i_s`` and its to-bus as ``+i_s``; the electrode current ``ia``
    leaves into the soil; ``i_inj`` is what the sources put into the node.
    """
    worst_bus = worst_global = 0.0
    for f in spec["freqs"]:
        pkg = package_vectors(result, f)
        leaving = dict(pkg["ia"])
        for name, d in spec["branches"].items():
            leaving[d["a"]] += -pkg["i_s"][name]
            leaving[d["b"]] += pkg["i_s"][name]
        scale = max(abs(v) for v in pkg["i_inj"].values())
        worst_bus = max(worst_bus, max(abs(pkg["i_inj"][b] - leaving[b]) for b in leaving) / scale)
        worst_global = max(worst_global, abs(sum(pkg["ia"].values())) / scale)
    return worst_bus, worst_global


def _check_invariants(seed: int) -> None:
    spec = random_spec(seed)
    net = build(spec, f"inv{seed}")
    result = solve(net)
    worst_bus, worst_global = _kcl_residuals(spec, result)
    assert worst_bus <= TOL, ("KCL per bus", seed, worst_bus)
    assert worst_global <= TOL, ("KCL global", seed, worst_global)
    # second way for the same solve: the reference solver
    for f in spec["freqs"]:
        rd = ref.reference_network(net, f, "F1")
        mapped = ref.to_package_frame(ref.solve_oneway(rd, thevenin_phase="norton"))
        pkg = package_vectors(result, f)
        assert ref.max_relative_deviation(pkg["u"], mapped["u"]) <= TOL
        assert ref.max_relative_deviation(pkg["i_s"], mapped["i_s"]) <= TOL

    # reciprocity of the coupling-free network: port (a,b) -> (c,d) == (c,d) -> (a,b)
    base = random_spec(seed, freqs=(50.0,), coupled=False)
    names = list(base["buses"])
    a, b, c, d = random.Random(1000 + seed).sample(names, 4)
    forward = dict(base, sources={"s": dict(kind="current", bus=a, I=at50(1.0))},
                   faults={"F1": (b, {50.0: 1.0})})
    backward = dict(base, sources={"s": dict(kind="current", bus=c, I=at50(1.0))},
                    faults={"F1": (d, {50.0: 1.0})})
    u_forward = package_vectors(solve(build(forward, "fw")), 50.0)["u"]
    u_backward = package_vectors(solve(build(backward, "bw")), 50.0)["u"]
    transfer_1 = u_forward[c] - u_forward[d]
    transfer_2 = u_backward[a] - u_backward[b]
    assert abs(transfer_1 - transfer_2) <= TOL * max(abs(transfer_1), 1e-12), ("reciprocity", seed)

    # passivity and Tellegen on the coupling-free network
    passive = random_spec(seed, freqs=(50.0, 250.0), coupled=False)
    net_p = build(passive, "pass")
    result_p = solve(net_p)
    for f in passive["freqs"]:
        pkg = package_vectors(result_p, f)
        power_in = sum(pkg["u"][bus] * pkg["i_inj"][bus].conjugate() for bus in pkg["u"])
        power_electrodes = sum(
            abs(pkg["ia"][bus]) ** 2 * complex(net_p.buses[bus].impedance[f]) for bus in pkg["ia"]
        )
        power_shields = sum(
            abs(pkg["i_s"][name]) ** 2 * complex(net_p.branches[name].self_impedance[f])
            for name in pkg["i_s"] if net_p.branches[name].type.grounding_conductor
        )
        assert power_in.real > 0, ("passivity", seed, f)
        residual = abs(power_in - power_electrodes - power_shields) / abs(power_in)
        assert residual <= TOL, ("Tellegen", seed, f, residual)


@pytest.mark.parametrize("seed", range(20))
def test_kcl_reciprocity_and_passivity_hold_on_random_networks(seed):
    """PQ-INV, seeded variant (always runs): KCL per bus and globally,
    agreement with the reference solver, reciprocity for ``Z_m = 0`` and
    ``Re sum(u * conj(i)) > 0`` with the Tellegen balance closing."""
    _check_invariants(seed)


if given is not None:

    @settings(
        max_examples=15, derandomize=True, deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    @given(seed=st.integers(min_value=10_000, max_value=2**31 - 1))
    def test_invariants_hold_for_hypothesis_generated_seeds(seed):
        """PQ-INV, hypothesis variant: the same invariants on seeds chosen by
        hypothesis (derandomized, so the suite stays deterministic)."""
        _check_invariants(seed)


# ---------------------------------------------------------------------------
# PQ-PH4 -- the EN 50522 quantities U_E, Z_E (decision F4)
# ---------------------------------------------------------------------------

import cmath  # noqa: E402

ZS_KM, ZM_KM = complex(0.29, 0.625), complex(0.049, 0.625)
SECTION_KM, Z_STATION, I_FAULT = 0.5, 2.0, 1000.0


def _uniform_chain(n_sections: int) -> gi.Network:
    """Homogeneous cable chain, source at the start, fault at the end: the
    norm's ideal case of a fault far from the infeed on a long bonded run."""
    buses = {f"N{i}": (f"{Z_STATION}", 100.0) for i in range(n_sections + 1)}
    buses["N0"] = ("0.2", 100.0)
    cable = dict(zs=f"({ZS_KM.real}+{ZS_KM.imag}*j*f/50)*l",
                 zm=f"({ZM_KM.real}+{ZM_KM.imag}*j*f/50)*l")
    branches = {f"L{i}": dict(a=f"N{i}", b=f"N{i + 1}", l=SECTION_KM, **cable)
                for i in range(n_sections)}
    spec = dict(freqs=[50.0], buses=buses, branches=branches,
                sources={"s": dict(kind="current", bus="N0", I=at50(I_FAULT))},
                faults={"F1": (f"N{n_sections}", {50.0: 1.0})})
    return build(spec, f"chain{n_sections}")


def test_pq_ph4_u_e_and_z_e_are_the_fault_bus_quantities_on_a_long_chain():
    """PQ-PH4: on a homogeneous chain of 64 sections the reported U_E is the
    EPR at the fault bus and Z_E the driving-point impedance there.

    First way, closed form: the Norton injections telescope to (1-k)*I at the
    two ends, so with the source far away u_F = (1-k) * I * Z_dp(F) and
    Z_dp(F) tends to the ladder limit Z_inf = -Z'/2 + sqrt(Z'^2/4 + Z_E*Z').
    Second way: the driving point from bus_response (network without the
    local electrode, recombined in parallel). Third way: the reference MNA.
    """
    net = _uniform_chain(64)
    result = solve(net)
    factor = result.reduction_factor
    fault_bus = "N64"
    z_series, z_mutual = ZS_KM * SECTION_KM, ZM_KM * SECTION_KM
    k = z_mutual / z_series
    z_inf = -z_series / 2 + cmath.sqrt(z_series ** 2 / 4 + Z_STATION * z_series)

    u_f = next(complex(b.uepr_freq[50.0]) for b in result.buses if b.name == fault_bus)
    assert abs(u_f) == pytest.approx(abs(1 - k) * I_FAULT * abs(z_inf), rel=1e-5)

    u_e = complex(factor.u_earthing[50.0])
    z_e = complex(factor.z_earthing[50.0])
    assert u_e == u_f                                   # same number, not a recomputation
    assert abs(z_e) == pytest.approx(abs(z_inf), rel=1e-5)
    assert z_e == pytest.approx(z_inf, rel=1e-5)
    assert complex(result.grounding_impedance.value[50.0]) == z_e

    response = gi.bus_response(net, fault="F1", bus=fault_bus)
    z_dp_response = 1.0 / (1.0 / Z_STATION + 1.0 / response.z_network[50.0])
    assert abs(z_e - z_dp_response) <= 1e-12 * abs(z_e)
    rd = ref.reference_network(net, 50.0, "F1")
    assert abs(z_e - ref.driving_point_impedance(rd, fault_bus)) <= 1e-12 * abs(z_e)

    # The norm's chain with r = |1 - Z_m/Z_s| closes on the reported values
    # in its ideal case -- not by construction, but by physics.
    assert abs(u_e) == pytest.approx(factor.value[50.0] * I_FAULT * abs(z_e), rel=1e-5)
    assert complex(factor.i_fault[50.0]) == I_FAULT


def test_pq_ph4_the_group_quantities_are_kept_as_diagnostics():
    """The previous U_E / Z_E -- current-weighted mean potential of the earth
    group, and that mean over I_E -- move to u_earthing_group /
    z_earthing_group. On the long chain they are the 43 % / 45 % lower values
    the review measured (second way: the reference solver's group)."""
    net = _uniform_chain(64)
    result = solve(net)
    factor = result.reduction_factor
    u_group = complex(factor.u_earthing_group[50.0])
    z_group = complex(factor.z_earthing_group[50.0])
    i_earth = complex(factor.i_earth[50.0])
    assert z_group == pytest.approx(u_group / i_earth, rel=1e-14)
    rd = ref.reference_network(net, 50.0, "F1")
    mapped = ref.to_package_frame(ref.solve_oneway(rd, thevenin_phase="norton"))
    _, group = ref.half_plane_split(mapped["ia"], "N64")
    weights = {b: abs(mapped["ia"][b]) for b in group}
    u_ref = sum(mapped["u"][b] * weights[b] for b in group) / sum(weights.values())
    assert abs(u_group - u_ref) <= 1e-12 * abs(u_ref)
    assert abs(u_group) / abs(complex(factor.u_earthing[50.0])) == pytest.approx(0.570, abs=0.01)
    assert abs(z_group) / abs(complex(factor.z_earthing[50.0])) == pytest.approx(0.555, abs=0.01)


@pytest.mark.parametrize("case", sorted(STRUCTURAL_CASES))
def test_pq_ph4_definitions_hold_on_every_structural_case(case):
    """U_E is the fault-bus EPR, Z_E the driving point of the earthing network
    (reference MNA, shield currents as unknowns, no coupling), I_F the sum of
    the scaled source currents, and the grounding impedance equals Z_E."""
    spec = STRUCTURAL_CASES[case]
    net = build(spec, case.replace(" ", "_"))
    result = solve(net)
    factor = result.reduction_factor
    fault_bus = spec["faults"]["F1"][0]
    for f in spec["freqs"]:
        rd = ref.reference_network(net, f, "F1")
        u_f = next(complex(b.uepr_freq[f]) for b in result.buses if b.name == fault_bus)
        assert complex(factor.u_earthing[f]) == u_f
        z_ref = ref.driving_point_impedance(rd, fault_bus)
        z_e = complex(factor.z_earthing[f])
        assert abs(z_e - z_ref) <= TOL * abs(z_ref), (case, f)
        assert complex(result.grounding_impedance.value[f]) == z_e
        i_f_ref = ref.solve_oneway(rd, thevenin_phase="norton")["I_F"]
        assert abs(complex(factor.i_fault[f]) - i_f_ref) <= TOL * abs(i_f_ref)


def test_pq_ph4_thevenin_loops_are_not_part_of_the_earthing_impedance():
    """Z_E is a property of the earthing network: the loop closure 1/Z_src a
    voltage source puts between its bus and the fault bus is not part of it
    (second way: the reference MNA, which never sees that admittance)."""
    spec = dict(STRUCTURAL_CASES["chain"],
                sources={"v": dict(kind="voltage", bus="S", E=at50(11547.0), Zsrc=at50(1 + 3j))})
    net = build(spec, "thevenin_chain")
    result = solve(net)
    rd = ref.reference_network(net, 50.0, "F1")
    z_ref = ref.driving_point_impedance(rd, "F")
    assert abs(complex(result.reduction_factor.z_earthing[50.0]) - z_ref) <= TOL * abs(z_ref)
    # the solved system (with the loop closure) has a different driving point
    with_loop = gi.analyze_cuts(net, fault="F1", cuts=[gi.Cut(name="all", branches=["L3"])])
    assert abs(with_loop.z_driving_point[50.0] - z_ref) > 1e-3 * abs(z_ref)


LEGACY_RESULT = {
    "buses": [], "branches": [], "fault": "F1",
    "reduction_factor": {
        "name": None, "fault_bus": "F",
        "value": {"50.0": 0.35}, "value_current": {"50.0": 0.36},
        "i_earth": {"50.0": {"real": -300.0, "imag": 20.0}},
        "earth_buses": {"50.0": ["F", "A"]},
        "u_earthing": {"50.0": {"real": -120.0, "imag": 8.0}},
        "z_earthing": {"50.0": {"real": 0.4, "imag": 0.0}},
    },
    "grounding_impedance": {"name": None, "fault_bus": "F",
                            "value": {"50.0": {"real": 0.4, "imag": 0.0}}},
}


def test_a_result_stored_with_the_previous_definition_loads_without_claiming_the_new_one():
    """Results written before F4 carry the group quantities under the names
    that now mean U_E = u_F and Z_E = Z_dp(F). They still load; the stored
    values move to the *_group fields, the redefined fields stay empty, and
    the result says so -- a stored number never claims a meaning it was not
    computed with."""
    import json

    restored = gi.Result.model_validate_json(json.dumps(LEGACY_RESULT))
    factor = restored.reduction_factor
    assert complex(factor.u_earthing_group[50.0]) == complex(-120.0, 8.0)
    assert complex(factor.z_earthing_group[50.0]) == complex(0.4, 0.0)
    assert factor.u_earthing == {} and factor.z_earthing == {}
    assert restored.grounding_impedance.value == {}
    assert factor.value[50.0] == 0.35 and factor.value_current[50.0] == 0.36
    assert [d.code for d in restored.diagnostics] == ["LEGACY_EARTHING_DEFINITION"]
    # a result written now round-trips unchanged
    net = _uniform_chain(4)
    current = solve(net)
    again = gi.Result.model_validate_json(current.model_dump_json())
    assert again.model_dump() == current.model_dump()


@pytest.mark.parametrize("bus", ["A", "B", "F"])
@pytest.mark.parametrize("z_bus", ["0.1", "3.0", "200.0"])
def test_pq_ph4_bus_response_reports_the_same_z_e_for_every_electrode(bus, z_bus):
    """bus_response's Z_G column is the fault-bus driving point for the
    electrode under test (Möbius form, no further solve). Second way: a fresh
    run_fault with that electrode installed. The network carries a Thevenin
    source, so the loop closure has to be taken out of both."""
    def network(electrode: Optional[str]) -> gi.Network:
        spec = dict(STRUCTURAL_CASES["chain"])
        spec["buses"] = dict(spec["buses"])
        if electrode is not None:
            spec["buses"][bus] = (f"rho*0 + {electrode}", 100.0)
        spec["sources"] = {"v": dict(kind="voltage", bus="S", E=at50(11547.0), Zsrc=at50(1 + 3j))}
        return build(spec, f"mobius_{bus}")

    base = network(None)
    solve(base)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        row = gi.bus_response(base, fault="F1", bus=bus).evaluate(float(z_bus))
    fresh = network(z_bus)
    expected = abs(complex(solve(fresh).reduction_factor.z_earthing[50.0]))
    assert row["Z_G_Ohm"][0] == pytest.approx(expected, rel=1e-10)
