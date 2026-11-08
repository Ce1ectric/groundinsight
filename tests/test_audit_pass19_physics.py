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
