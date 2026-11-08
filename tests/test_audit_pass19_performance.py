# tests/test_audit_pass19_performance.py

"""
Audit pass 19, performance track: the sparse stationary kernel.

Findings covered (see the audit log): **P1** (phase-conductor network solved
densely, per source and frequency), **P2/P6** (dense nodal matrix per frequency,
all kept; per-element Python loops and a tuple fingerprint on every build),
**P5a** (cyclic garbage collector running over the network while millions of
result objects are allocated), **P7** (``bus_response`` and ``analyze_cuts``
solving densely) and **P13** (what accuracy a float64 kernel can promise on a
large ring at all).

How the tests are organised -- and why every number is checked twice:

* **Equivalence battery.** Random networks (chains, rings, trees, meshes,
  parallel cables, open ends, broken shields, 0 Hz, Thevenin sources, inactive
  elements, proxy branches) and structured ones up to 300 buses are solved by
  ``run_fault`` and by ``tests/_dense_oracle.py``, an independent dense
  re-implementation of the model that shares no code with the kernel. Every
  physical quantity must agree to 1e-12 on its physical scale.
* **Structure.** The assembled sparse matrix equals the oracle's dense one, the
  phase network is factorised once per frequency for all sources together, and
  no dense ``n x n`` array is allocated on the default path (guarded
  allocation functions *and* the ``tracemalloc`` peak).
* **Large networks (P13).** For a 1000-bus ring the reduced phase network
  has the condition number ``4 n**2 / pi**2 = 4.05e5`` (checked numerically),
  so *no* float64 kernel is exact to 1e-12. The reference there is an iteratively refined solution (residual in
  ``clongdouble``) of the bit-identical stored system; on a uniform ring it is
  checked against the closed-form current split, on a radial feeder against the
  exact KCL currents. The kernel must be no worse than twice the dense LAPACK
  solve it replaces.
* **Performance acceptance** (marked ``slow``): ring 4000 at seven frequencies
  under 3 s and 400 MB, radial 4000 with three sources and a 50x50 mesh with
  four sources under 3 s, ``bus_response``/``analyze_cuts`` on large rings.
  Times are process CPU time, so that other load on a shared machine does not
  decide the outcome.
"""

from __future__ import annotations

import gc
import math
import random
import threading
import time
import tracemalloc
import warnings
from typing import Dict, List

import numpy as np
import pytest
import scipy.sparse as sp

import groundinsight as gi
from groundinsight import electrical_network as en_mod
from tests import _dense_oracle as oracle

pytestmark = pytest.mark.filterwarnings("ignore")

TOL = 1e-12


# ---------------------------------------------------------------------------
# network builders (public factory API only)
# ---------------------------------------------------------------------------

BT = gi.BusType(
    name="BT", system_type="Grounded", voltage_level=20,
    impedance_formula="rho*0.01 + j*f*0.0001",
)
BT_OPEN = gi.BusType(
    name="BT_OPEN", system_type="Tower", voltage_level=20, impedance_formula="nan"
)
CABLE = gi.BranchType(
    name="CAB", grounding_conductor=True,
    self_impedance_formula="(0.25 + j*f*0.012)*l",
    mutual_impedance_formula="(j*f*0.008)*l",
    phase_impedance_formula="(0.2 + j*f*0.010)*l",
)
OHL = gi.BranchType(
    name="OHL", grounding_conductor=False,
    self_impedance_formula="(0.3 + j*f*0.015)*l",
    mutual_impedance_formula="(j*f*0.004)*l",
    phase_impedance_formula="(0.15 + j*f*0.012)*l",
)
CABLE_NO_PHASE = gi.BranchType(
    name="CAB_NP", grounding_conductor=True,
    self_impedance_formula="(0.3 + j*f*0.011)*l",
    mutual_impedance_formula="(j*f*0.007)*l",
)
BROKEN_SHIELD = gi.BranchType(
    name="BROKEN", grounding_conductor=True,
    self_impedance_formula="nan",
    mutual_impedance_formula="(j*f*0.008)*l",
    phase_impedance_formula="(0.2 + j*f*0.010)*l",
)
BROKEN_SHIELD_NO_PHASE = gi.BranchType(
    name="BROKEN_NP", grounding_conductor=True,
    self_impedance_formula="nan",
    mutual_impedance_formula="(j*f*0.008)*l",
)
INDUCTIVE = gi.BranchType(
    name="IND", grounding_conductor=True,
    self_impedance_formula="(j*f*0.012)*l",
    mutual_impedance_formula="(j*f*0.008)*l",
    phase_impedance_formula="(0.2 + j*f*0.010)*l",
)


def _freqs(count: int, with_dc: bool = False) -> List[float]:
    base = [50.0 * k for k in range(1, count + 1)]
    if with_dc:
        base = [0.0] + base[:-1] if count > 1 else [0.0, 50.0]
    return base


def _finish(net, freqs, source_buses, fault_bus, amp=1000.0):
    for s, bus in enumerate(source_buses):
        gi.create_source(
            name=f"S{s}", bus=bus, values={f: amp / (1 + s) for f in freqs},
            network=net,
        )
    gi.create_fault(
        name="F", bus=fault_bus, scalings={f: 1.0 for f in freqs}, network=net
    )
    return net


def ring(n: int, F: int = 7, n_sources: int = 1, freqs=None):
    """Uniform ring ``B0 .. B(n-1)``, sources spread evenly, fault opposite."""
    freqs = freqs or _freqs(F)
    net = gi.create_network(name=f"ring{n}", frequencies=freqs)
    for k in range(n):
        gi.create_bus(name=f"B{k}", type=BT, specific_earth_resistance=100, network=net)
    for k in range(n):
        gi.create_branch(
            name=f"L{k}", type=CABLE, from_bus=f"B{k}", to_bus=f"B{(k + 1) % n}",
            length=0.5, network=net,
        )
    sources = [f"B{(k * n) // n_sources}" for k in range(n_sources)]
    return _finish(net, freqs, sources, f"B{n // 2 + 1}")


def radial(n: int, F: int = 7, n_sources: int = 1, lateral: int = 3, freqs=None):
    """A trunk with a lateral chain of ``lateral`` buses at every trunk bus."""
    freqs = freqs or _freqs(F)
    net = gi.create_network(name=f"radial{n}", frequencies=freqs)
    n_trunk = max(2, math.ceil(n / (lateral + 1)))
    for k in range(n_trunk):
        gi.create_bus(name=f"T{k}", type=BT, specific_earth_resistance=100, network=net)
        if k:
            gi.create_branch(
                name=f"LT{k}", type=CABLE, from_bus=f"T{k - 1}", to_bus=f"T{k}",
                length=0.4, network=net,
            )
    count, k = n_trunk, 0
    while count < n:
        previous = f"T{k % n_trunk}"
        for j in range(lateral):
            if count >= n:
                break
            name = f"A{k}_{j}"
            gi.create_bus(name=name, type=BT, specific_earth_resistance=150, network=net)
            gi.create_branch(
                name=f"LA{k}_{j}", type=CABLE, from_bus=previous, to_bus=name,
                length=0.2, network=net,
            )
            previous = name
            count += 1
        k += 1
    sources = [f"T{(i * n_trunk) // n_sources}" for i in range(n_sources)]
    return _finish(net, freqs, sources, f"T{n_trunk - 1}")


def mesh(k: int, F: int = 7, n_sources: int = 1, freqs=None):
    """A ``k x k`` grid, sources in the corners, fault in the middle."""
    freqs = freqs or _freqs(F)
    net = gi.create_network(name=f"mesh{k}x{k}", frequencies=freqs)
    for i in range(k):
        for j in range(k):
            gi.create_bus(
                name=f"M{i}_{j}", type=BT, specific_earth_resistance=100, network=net
            )
    for i in range(k):
        for j in range(k):
            if j + 1 < k:
                gi.create_branch(
                    name=f"H{i}_{j}", type=CABLE, from_bus=f"M{i}_{j}",
                    to_bus=f"M{i}_{j + 1}", length=0.3, network=net,
                )
            if i + 1 < k:
                gi.create_branch(
                    name=f"V{i}_{j}", type=CABLE, from_bus=f"M{i}_{j}",
                    to_bus=f"M{i + 1}_{j}", length=0.3, network=net,
                )
    corners = ["M0_0", f"M{k - 1}_{k - 1}", f"M0_{k - 1}", f"M{k - 1}_0"]
    return _finish(net, freqs, corners[:n_sources], f"M{k // 2}_{k // 2}")


def random_network(seed: int, n_min: int = 4, n_max: int = 40):
    """One random network that exercises the corner cases of the solver."""
    rnd = random.Random(seed)
    F = rnd.choice([1, 2, 3, 7])
    with_dc = rnd.random() < 0.3
    freqs = _freqs(F, with_dc=with_dc)
    net = gi.create_network(name=f"rnd{seed}", frequencies=freqs)
    n = rnd.randint(n_min, n_max)
    for k in range(n):
        typ = BT_OPEN if (k > 0 and rnd.random() < 0.15) else BT
        gi.create_bus(
            name=f"B{k}", type=typ,
            specific_earth_resistance=rnd.choice([30, 100, 300, 1000]), network=net,
        )
    topo = rnd.choice(["chain", "ring", "tree", "mesh"])
    if topo == "chain":
        edges = [(k, k + 1) for k in range(n - 1)]
    elif topo == "ring":
        edges = [(k, (k + 1) % n) for k in range(n)]
    else:
        edges = [(rnd.randrange(k), k) for k in range(1, n)]
        if topo == "mesh":
            for _ in range(rnd.randint(1, max(1, n // 3))):
                edges.append(tuple(rnd.sample(range(n), 2)))
    for _ in range(rnd.randint(0, 2)):
        edges.append(rnd.choice(edges))  # parallel cables
    for e, (a, b) in enumerate(edges):
        r = rnd.random()
        if r < 0.58:
            typ = CABLE
        elif r < 0.70:
            typ = OHL
        elif r < 0.82:
            typ = CABLE_NO_PHASE
        elif r < 0.89:
            typ = BROKEN_SHIELD
        elif r < 0.93:
            typ = BROKEN_SHIELD_NO_PHASE
        else:
            typ = INDUCTIVE
        gi.create_branch(
            name=f"L{e}", type=typ, from_bus=f"B{a}", to_bus=f"B{b}",
            length=rnd.choice([0.1, 0.3, 0.5, 1.2]),
            specific_earth_resistance=rnd.choice([50, 100, 500]), network=net,
        )
    fault_bus = f"B{rnd.randrange(n)}"
    for s in range(rnd.randint(1, 3)):
        bus = f"B{rnd.randrange(n)}"
        if rnd.random() < 0.3:
            gi.create_voltage_source(
                name=f"S{s}", bus=bus,
                voltage={
                    f: complex(rnd.uniform(5e3, 12e3), rnd.uniform(-1e3, 1e3))
                    for f in freqs
                },
                source_impedance={
                    f: complex(rnd.uniform(0.5, 3.0), 0.01 * max(f, 1.0))
                    for f in freqs
                },
                network=net,
            )
        else:
            gi.create_source(
                name=f"S{s}", bus=bus,
                values={
                    f: complex(rnd.uniform(200, 2000), rnd.uniform(-300, 300))
                    for f in freqs
                },
                network=net,
            )
    gi.create_fault(
        name="F", bus=fault_bus,
        scalings={f: rnd.choice([1.0, 0.8, 0.3]) for f in freqs}, network=net,
    )
    for _ in range(rnd.randint(0, 2)):
        if rnd.random() < 0.5:
            net.branches[rnd.choice(list(net.branches))].active = False
        else:
            bus = f"B{rnd.randrange(n)}"
            if bus != fault_bus:
                net.buses[bus].active = False
    return net


def thevenin_ring(n: int = 60, F: int = 3):
    """A ring fed by two Thevenin sources and one current source."""
    net = ring(n, F=F, n_sources=1)
    freqs = list(net.frequencies)
    for s, bus in enumerate([f"B{n // 4}", f"B{3 * n // 4}"]):
        gi.create_voltage_source(
            name=f"V{s}", bus=bus,
            voltage={f: complex(8e3 + 500 * s, -300.0) for f in freqs},
            source_impedance={f: complex(1.2 + 0.3 * s, 0.002 * f) for f in freqs},
            network=net,
        )
    return net


def dc_ring(n: int = 40):
    """A ring with a 0 Hz bin and purely inductive shields (DC substitute)."""
    freqs = [0.0, 50.0, 150.0, 250.0]
    net = gi.create_network(name="dc_ring", frequencies=freqs)
    for k in range(n):
        gi.create_bus(name=f"B{k}", type=BT, specific_earth_resistance=100, network=net)
    for k in range(n):
        gi.create_branch(
            name=f"L{k}", type=INDUCTIVE if k % 3 else CABLE, from_bus=f"B{k}",
            to_bus=f"B{(k + 1) % n}", length=0.5, network=net,
        )
    return _finish(net, freqs, ["B0", "B7"], f"B{n // 2}")


def open_ends_mesh(k: int = 8):
    """A mesh with towers (open-ended buses), broken shields and OHL spans."""
    net = mesh(k, F=3, n_sources=2)
    for name in list(net.buses)[3::5]:
        if name != net.faults["F"].bus:
            bus = net.buses[name]
            bus.type = BT_OPEN
            bus.calculate_impedance(net.frequencies)
    for idx, name in enumerate(list(net.branches)):
        branch = net.branches[name]
        if idx % 7 == 3:
            branch.type = BROKEN_SHIELD
        elif idx % 11 == 5:
            branch.type = OHL
        elif idx % 13 == 6:
            branch.type = CABLE_NO_PHASE
        else:
            continue
        branch.calculate_impedance(net.frequencies)
        if branch.type.phase_impedance_formula is None:
            # calculate_impedance keeps a stale phase impedance from the
            # previous type; a type without a phase formula means "proxy"
            branch.phase_impedance = None
    net.branches[list(net.branches)[10]].active = False
    return net


STRUCTURED = {
    "line50": lambda: radial(50, F=3, n_sources=1, lateral=0),
    "ring120_2src": lambda: ring(120, F=7, n_sources=2),
    "mesh12_4src": lambda: mesh(12, F=3, n_sources=4),
    "radial300_3src": lambda: radial(300, F=3, n_sources=3),
    "thevenin_ring60": lambda: thevenin_ring(60, F=3),
    "dc_ring40": lambda: dc_ring(40),
    "open_ends_mesh8": lambda: open_ends_mesh(8),
}


def _solve_both(net, **oracle_kwargs):
    gi.run_fault(net, "F")
    return oracle.solve(net, "F", **oracle_kwargs)


# ---------------------------------------------------------------------------
# 1. equivalence battery (P1, P2/P6, P5a: every physical quantity)
# ---------------------------------------------------------------------------


def _condition_limited_bins(net, reference, frequencies, label):
    """P13 at 0 Hz: a DC stand-in makes the bin ill-conditioned by design.

    The stand-in for a short is ``sqrt(eps)`` times the smallest other
    impedance, so the 0 Hz nodal matrix has a condition number of 1e9..1e11
    and its solution is only determined to about ``kappa * eps`` -- by any
    float64 solver. Measured on this battery the sparse LU of the nodal
    system (used before and after the pass-19 rewrite alike) lands at 1-5 %
    of that bound, dense LAPACK somewhat lower. The reference is the
    clongdouble-refined solution of the stored system, the yardstick the
    forward-error bound ``kappa_2(Y) * eps``. Quantities that do not pass
    through the nodal solve -- phase currents, source injections -- stay at
    the strict 1e-12.
    """
    refined = oracle.solve(net, "F", refine=3, division="numpy")
    pkg = oracle.package_arrays(net, "F")
    cols = [refined.frequencies.index(f) for f in frequencies]
    u_scale = float(np.abs(refined.u[:, cols]).max())
    i_scale = float(np.abs(refined.i_inj[:, cols]).max())
    if u_scale == 0.0 or i_scale == 0.0:
        # nothing is injected (a source on the fault bus): zero everywhere
        assert np.abs(pkg["u"][:, cols]).max() == 0.0, label
        return
    eps = np.finfo(float).eps
    for f, col in zip(frequencies, cols, strict=True):
        bound = max(float(np.linalg.cond(reference.Y[f])) * eps, TOL)
        for key, scale, tol in (("u", u_scale, bound), ("ia", i_scale, bound),
                                ("i_shield", i_scale, bound),
                                ("i_phase", i_scale, TOL), ("i_inj", i_scale, TOL)):
            want = getattr(refined, key)[:, col]
            err = float(np.abs(pkg[key][:, col] - want).max()) / scale
            assert err <= tol, (label, f, key, err, tol)


def _assert_equivalent(net, label):
    """Strict 1e-12 on every regular bin, condition bound on DC stand-ins.

    The reference is the dense oracle with two refinement steps on the stored
    system of the kernel (NumPy division, same stamp order). Without the
    refinement the *oracle* would be the less accurate side from about 300
    buses on: on a radial feeder of 300 buses the dense LAPACK solve is
    1.9e-12 away from the exact KCL currents, the sparse kernel 8e-14
    (finding P13). The assembly test below checks the stored system itself
    against an independent rounding (Python division).
    """
    try:
        reference = oracle.solve(net, "F", refine=2, division="numpy")
    except (np.linalg.LinAlgError, ZeroDivisionError, RuntimeError):
        # no solution in the model (e.g. a floating island): the package must
        # refuse as well rather than report numbers
        with pytest.raises((ValueError, RuntimeError, ZeroDivisionError)):
            gi.run_fault(net, "F")
        return
    gi.run_fault(net, "F")
    freqs = [float(f) for f in net.frequencies]
    dc_bins = [0.0] if reference.dc_substitute is not None else []
    regular = [f for f in freqs if f not in dc_bins]
    deviation = oracle.compare(net, "F", reference, frequencies=regular)
    worst = {k: v for k, v in deviation.items() if v > TOL}
    assert not worst, f"{label}: deviations above {TOL}: {worst}"
    if dc_bins:
        _condition_limited_bins(net, reference, dc_bins, label)


@pytest.mark.parametrize("seed", range(150))
def test_random_networks_match_the_dense_oracle(seed):
    """Every quantity of ``run_fault`` against the independent dense model."""
    _assert_equivalent(random_network(seed), f"rnd{seed}")


@pytest.mark.parametrize("name", sorted(STRUCTURED))
def test_structured_networks_match_the_dense_oracle(name):
    _assert_equivalent(STRUCTURED[name](), name)


# ---------------------------------------------------------------------------
# 2. P1: one phase factorisation per frequency, for all sources together
# ---------------------------------------------------------------------------


def test_the_phase_network_is_factorised_once_per_frequency(monkeypatch):
    """Three sources, three frequencies: three phase factorisations, not nine
    dense solves. The reduced phase system has ``n - 1`` rows."""
    net = ring(80, F=3, n_sources=3)
    n = len(net.buses)
    shapes: List[tuple] = []
    real_splu = en_mod.splu

    def counting(matrix, *args, **kwargs):
        shapes.append(matrix.shape)
        return real_splu(matrix, *args, **kwargs)

    monkeypatch.setattr(en_mod, "splu", counting)
    gi.run_fault(net, "F")
    assert shapes.count((n - 1, n - 1)) == len(net.frequencies)
    assert shapes.count((n, n)) == len(net.frequencies)


def test_multi_source_superposition_equals_single_source_solves():
    """The multi-column solve is superposition: each source alone, summed."""
    full = ring(90, F=2, n_sources=3)
    gi.run_fault(full, "F")
    combined = full.electrical_network.phase_currents
    summed: Dict[float, Dict[str, complex]] = {f: {} for f in full.frequencies}
    for keep in full.sources:
        single = ring(90, F=2, n_sources=3)
        for name in list(single.sources):
            if name != keep:
                del single.sources[name]
        gi.run_fault(single, "F")
        for f in single.frequencies:
            for b, v in single.electrical_network.phase_currents[f].items():
                summed[f][b] = summed[f].get(b, 0j) + v
    for f in full.frequencies:
        got = np.array([combined[f][b] for b in full.branches])
        want = np.array([summed[f][b] for b in full.branches])
        assert np.abs(got - want).max() <= 1e-12 * np.abs(want).max()


def test_isolated_source_and_proxy_warnings_are_unchanged(caplog):
    """A source cut off from the fault in the phase network is named, and the
    proxy warning fires once per solve in a meshed network."""
    import logging

    net = ring(10, F=2, n_sources=1)
    gi.create_bus(name="X", type=BT, network=net)
    gi.create_branch(
        name="LX", type=OHL, from_bus="B0", to_bus="X", length=0.3, network=net
    )
    gi.create_source(
        name="SX", bus="X", values={f: 50.0 for f in net.frequencies}, network=net
    )
    net.branches["L3"].type = CABLE_NO_PHASE
    net.branches["L3"].calculate_impedance(net.frequencies)
    net.branches["L3"].phase_impedance = None  # stale value of the old type
    # open the phase conductor of the OHL span: the source X is reachable
    # through the earthing network only if a grounding branch exists -- add one
    gi.create_branch(
        name="LXg", type=BROKEN_SHIELD, from_bus="B0", to_bus="X", length=0.3,
        network=net,
    )
    net.branches["LX"].phase_impedance = {
        f: gi.ComplexNumber(real=math.inf, imag=math.inf) for f in net.frequencies
    }
    net.branches["LXg"].phase_impedance = {
        f: gi.ComplexNumber(real=math.inf, imag=math.inf) for f in net.frequencies
    }
    with caplog.at_level(logging.WARNING, logger="groundinsight"):
        result = gi.run_fault(net, "F")
    # Either channel counts: the log line of the base, or a structured
    # diagnostic once the CORE package converts these log lines.
    messages = {r.getMessage() for r in caplog.records}
    messages |= {d.message for d in getattr(result, "diagnostics", [])}
    isolated = [m for m in messages if "'SX'" in m and "phase" in m]
    proxy = [m for m in messages if "phase_impedance_formula" in m and "L3" in m]
    assert isolated, messages
    assert len(proxy) == 1, proxy
    reference = oracle.solve(net, "F")
    deviation = oracle.compare(net, "F", reference)
    assert max(deviation.values()) <= TOL, deviation


def test_a_singular_phase_network_still_raises_runtime_error():
    """A resonant pair in the phase network keeps the historic RuntimeError.

    Two parallel branches between the source bus and the fault bus with phase
    impedances ``+2j`` and ``-2j`` have admittances ``-0.5j`` and ``+0.5j``:
    the reduced phase system is the 1x1 matrix ``[0]``, exactly singular for
    LAPACK and for SuperLU alike.
    """
    net = gi.create_network(name="lc", frequencies=[50.0])
    for k in range(2):
        gi.create_bus(name=f"B{k}", type=BT, network=net)
    for k, x in enumerate((2.0, -2.0)):
        gi.create_branch(name=f"L{k}", type=CABLE, from_bus="B0", to_bus="B1",
                         length=1.0, network=net)
        net.branches[f"L{k}"].phase_impedance = {
            50.0: gi.ComplexNumber(real=0.0, imag=x)
        }
    gi.create_source(name="S", bus="B0", values={50.0: 100.0}, network=net)
    gi.create_fault(name="F", bus="B1", scalings={50.0: 1.0}, network=net)
    with pytest.raises(RuntimeError, match="phase-conductor network around fault bus"):
        gi.run_fault(net, "F")


# ---------------------------------------------------------------------------
# 3. P13: large networks against exact and refined references
# ---------------------------------------------------------------------------


def _phase_matrix(net):
    en = net.electrical_network
    return np.array(
        [[en.phase_currents[f][b] for f in net.frequencies] for b in net.branches]
    )


def _relative(got, want):
    return float(np.abs(got - want).max() / np.abs(want).max())


def _exact_tree_currents(net) -> np.ndarray:
    """Phase currents of a radial network by KCL alone (topology, no solve)."""
    from collections import deque

    fault_bus = net.faults["F"].bus
    adjacency: Dict[str, list] = {}
    for b in net.branches.values():
        adjacency.setdefault(b.from_bus, []).append((b.to_bus, b.name, +1))
        adjacency.setdefault(b.to_bus, []).append((b.from_bus, b.name, -1))
    parent = {fault_bus: None}
    queue = deque([fault_bus])
    while queue:
        bus = queue.popleft()
        for nb, name, sign in adjacency.get(bus, []):
            if nb not in parent:
                parent[nb] = (bus, name, sign)
                queue.append(nb)
    names = list(net.branches)
    out = np.zeros((len(names), len(net.frequencies)), dtype=complex)
    pos = {name: i for i, name in enumerate(names)}
    for j, f in enumerate(net.frequencies):
        scaling = net.faults["F"].scalings.get(f, 1)
        for source in net.sources.values():
            current = scaling * complex(source.values[f].real, source.values[f].imag)
            bus = source.bus
            while parent[bus] is not None:
                up, name, sign = parent[bus]
                out[pos[name], j] += -sign * current
                bus = up
    return out


def _ring_closed_form(net) -> np.ndarray:
    """Phase split of a uniform ring: ``I*(n-q)/n`` one way, ``I*q/n`` back."""
    n = len(net.buses)
    q = int(net.faults["F"].bus[1:])
    out = np.zeros((n, len(net.frequencies)), dtype=complex)
    for j, f in enumerate(net.frequencies):
        total = sum(
            complex(s.values[f].real, s.values[f].imag) for s in net.sources.values()
        )
        assert len(net.sources) == 1 and net.sources["S0"].bus == "B0"
        out[:q, j] = total * (n - q) / n
        out[q:, j] = -total * q / n
    return out


def test_a_radial_feeder_gets_the_exact_kcl_currents():
    """P13, tree arbiter: in a tree the phase currents follow from KCL alone, so
    the rounding error of every kernel is visible directly. The sparse kernel
    has to be at least as good as the dense LAPACK solve it replaces."""
    net = radial(1000, F=3, n_sources=3)
    gi.run_fault(net, "F")
    exact = _exact_tree_currents(net)
    err_kernel = _relative(_phase_matrix(net), exact)
    dense = oracle.solve(net, "F")
    err_dense = _relative(dense.i_phase, exact)
    assert err_kernel <= max(2.0 * err_dense, 1e-12), (err_kernel, err_dense)
    assert err_kernel <= 1e-10


def test_the_refined_reference_reproduces_the_closed_form_ring_split():
    """The checker is checked: the clongdouble-refined oracle is exact on a
    uniform ring, where the split is known in closed form."""
    net = ring(1000, F=2)
    gi.run_fault(net, "F")
    refined = oracle.solve(net, "F", refine=3, division="numpy", sparse=True)
    closed = _ring_closed_form(net)
    assert _relative(refined.i_phase, closed) <= 1e-15


@pytest.mark.parametrize(
    "name,builder",
    [
        ("ring1000", lambda: ring(1000, F=7)),
        ("radial1000_3src", lambda: radial(1000, F=3, n_sources=3)),
        ("mesh30_4src", lambda: mesh(30, F=3, n_sources=4)),
    ],
)
def test_large_networks_against_a_refined_reference(name, builder):
    """P13: on large networks the 1e-12 bound is condition-limited for any
    float64 kernel. The reference is the clongdouble-refined solution of the
    bit-identical stored system; the sparse kernel may be at most twice as far
    from it as the dense LAPACK solve of the same system."""
    net = builder()
    gi.run_fault(net, "F")
    refined = oracle.solve(net, "F", refine=3, division="numpy", sparse=True)
    dense = oracle.solve(net, "F", division="numpy")
    pkg = oracle.package_arrays(net, "F")
    for key in ("i_phase", "u", "i_shield", "ia"):
        err_kernel = _relative(pkg[key], getattr(refined, key))
        err_dense = _relative(getattr(dense, key), getattr(refined, key))
        assert err_kernel <= max(2.0 * err_dense, 1e-13), (name, key, err_kernel, err_dense)


# ---------------------------------------------------------------------------
# 4. P2/P6: the assembled matrices
# ---------------------------------------------------------------------------


def _assembly_cases():
    yield from ((f"rnd{s}", lambda s=s: random_network(s)) for s in range(0, 150, 5))
    yield from STRUCTURED.items()


@pytest.mark.parametrize("name,builder", list(_assembly_cases()))
def test_sparse_assembly_equals_the_dense_reference(name, builder):
    """``Y_sparse`` is the matrix the historic dense loops would have built.

    Two references: the oracle with Python division (independent rounding,
    normwise 1e-15) and the oracle with NumPy division and program-order
    accumulation, which must be reproduced bit for bit -- that identity is what
    makes the refined references of P13 references for *this* kernel.
    """
    net = builder()
    net.set_active_fault("F")
    # The assembly needs no solve: some of these networks have no solution
    # (a floating island), but their matrices are still well defined.
    en = en_mod.ElectricalNetwork(net, auto_phase_currents=True)
    python_div = oracle.assemble(net, "F")
    numpy_div = oracle.assemble(net, "F", division="numpy")
    for freq in net.frequencies:
        sparse_y = en.Y_sparse[freq]
        assert sp.issparse(sparse_y) and sparse_y.format == "csc"
        dense = sparse_y.toarray()
        scale = np.abs(python_div[freq]).max()
        assert np.abs(dense - python_div[freq]).max() <= 1e-15 * scale, name
        assert np.array_equal(dense, numpy_div[freq]), name
        # compatibility view: a dense copy of the same matrix, on demand
        assert np.array_equal(en.Y_matrices[freq], dense)
    assert set(en.Y_matrices) == set(net.frequencies)


class _DenseGuard:
    """Make every dense ``n x n`` allocation with ``n >= limit`` fail loudly."""

    CREATORS = ("zeros", "empty", "ones", "full", "zeros_like", "empty_like")

    def __init__(self, monkeypatch, limit: int = 500):
        self.limit = limit
        self.hits: List[str] = []
        for name in self.CREATORS:
            monkeypatch.setattr(np, name, self._wrap(getattr(np, name), name))
        monkeypatch.setattr(np, "eye", self._wrap_eye(np.eye))
        monkeypatch.setattr(np, "identity", self._wrap_eye(np.identity))
        monkeypatch.setattr(np.linalg, "solve", self._wrap_solve(np.linalg.solve))
        for cls in (sp.csc_matrix, sp.csr_matrix, sp.coo_matrix,
                    sp.csc_array, sp.csr_array, sp.coo_array):
            monkeypatch.setattr(cls, "toarray", self._wrap_method(cls.toarray))
            monkeypatch.setattr(cls, "todense", self._wrap_method(cls.todense))

    def _too_big(self, shape) -> bool:
        if isinstance(shape, (int, np.integer)):
            return False
        dims = tuple(int(d) for d in shape)
        return len(dims) >= 2 and min(dims[-2:]) >= self.limit

    def _fail(self, what):
        self.hits.append(what)
        raise AssertionError(f"dense square allocation on the default path: {what}")

    def _wrap(self, func, name):
        def wrapped(shape, *args, **kwargs):
            probe = getattr(shape, "shape", shape)
            if self._too_big(probe):
                self._fail(f"np.{name}{tuple(probe)}")
            return func(shape, *args, **kwargs)
        return wrapped

    def _wrap_eye(self, func):
        def wrapped(N, *args, **kwargs):
            if int(N) >= self.limit:
                self._fail(f"np.eye({N})")
            return func(N, *args, **kwargs)
        return wrapped

    def _wrap_solve(self, func):
        def wrapped(a, b, *args, **kwargs):
            if self._too_big(np.shape(a)):
                self._fail(f"np.linalg.solve{np.shape(a)}")
            return func(a, b, *args, **kwargs)
        return wrapped

    def _wrap_method(self, func):
        guard = self

        def wrapped(matrix, *args, **kwargs):
            if guard._too_big(matrix.shape):
                guard._fail(f"{type(matrix).__name__}.toarray{matrix.shape}")
            return func(matrix, *args, **kwargs)
        return wrapped


@pytest.mark.parametrize(
    "builder",
    [lambda: ring(600, F=3, n_sources=2), lambda: thevenin_ring(600, F=3)],
    ids=["current sources", "Thevenin sources"],
)
def test_no_dense_square_matrix_on_the_default_path(monkeypatch, builder):
    """P2: the default ``run_fault`` never materialises a dense ``n x n`` --
    with Thevenin sources neither, where ``Z_E`` is taken from the earthing
    matrix without the loop closures (PH4) and needs a factorisation of its
    own."""
    net = builder()
    guard = _DenseGuard(monkeypatch)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # THEVENIN_COUPLING, PHASE_PROXY
        gi.run_fault(net, "F")
    assert guard.hits == []
    assert net.results["F"].buses
    z_e = net.results["F"].reduction_factor.z_earthing
    assert all(z_e[f] is not None for f in net.frequencies)


def test_the_earthing_matrix_is_the_solved_one_without_the_loop_closures():
    """PH4 on the sparse kernel. ``thevenin_loop_admittances`` returns the
    values stamped into ``Y_sparse`` bit for bit; ``earthing_admittance_matrix``
    equals, bit for bit, ``Y - loops`` built densely here the way the dense
    kernel defined it; and ``Z_E`` from the sparse factorisation agrees with a
    dense solve of that matrix and with the oracle's driving point."""
    net = thevenin_ring(40, F=3)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gi.run_fault(net, "F")
    en = net.electrical_network
    k = en.bus_indices[net.faults["F"].bus]
    reference = oracle.solve(net, "F", refine=2, division="numpy")
    for j, f in enumerate(net.frequencies):
        stamps = en.thevenin_loop_admittances(f)
        assert [s for s, _, _ in stamps] == [en.bus_indices["B10"], en.bus_indices["B30"]]
        solved = en.Y_matrices[f]
        expected = solved.copy()
        for s, fault_row, y in stamps:
            assert fault_row == k
            # no branch joins a source bus and the fault bus: the entry is the stamp
            assert solved[s, k] == -y
            expected[s, s] -= y
            expected[k, k] -= y
            expected[s, k] += y
            expected[k, s] += y
        assert np.array_equal(en.earthing_admittance_matrix(f), expected)
        unit = np.zeros(expected.shape[0], dtype=complex)
        unit[k] = 1.0
        dense_z = complex(np.linalg.solve(expected, unit)[k])
        z_e = complex(net.results["F"].reduction_factor.z_earthing[f])
        assert abs(z_e - dense_z) <= 1e-13 * abs(dense_z)
        assert abs(z_e - reference.z_dp[j]) <= 1e-12 * abs(reference.z_dp[j])


def _peak_bytes(func) -> int:
    gc.collect()
    tracemalloc.start()
    try:
        func()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_peak_memory_is_linear_in_the_network_size():
    """P2: no ``n**2 * F`` term left in the memory of ``run_fault``.

    The dense kernel held one complex ``n x n`` matrix per frequency plus two
    copies of the phase network: 150 MB at a 1000-bus ring with seven
    frequencies, four times that at 2000 buses.
    """
    small, large = ring(1000, F=7), ring(2000, F=7)
    peak_small = _peak_bytes(lambda: gi.run_fault(small, "F"))
    peak_large = _peak_bytes(lambda: gi.run_fault(large, "F"))
    assert peak_small < 60e6, f"{peak_small / 1e6:.0f} MB"
    assert peak_large < 2.6 * peak_small, (peak_small, peak_large)


# --- the fingerprint (P6) ----------------------------------------------------


def _fingerprint_cases():
    """Mutations that change the assembled ``Y`` and must be detected."""

    def rho(net):
        bus = net.buses["B3"]
        bus.specific_earth_resistance = 500.0
        bus.calculate_impedance(net.frequencies)

    def bus_value(net):
        z = net.buses["B4"].impedance[50.0]
        net.buses["B4"].impedance[50.0] = gi.ComplexNumber(real=z.real * 2, imag=z.imag)

    def bus_inactive(net):
        net.buses["B5"].active = False

    def branch_inactive(net):
        net.branches["L2"].active = False

    def rewire(net):
        net.branches["L2"].to_bus = "B7"

    def shield_value(net):
        z = net.branches["L6"].self_impedance[100.0]
        net.branches["L6"].self_impedance[100.0] = gi.ComplexNumber(
            real=z.real, imag=z.imag * 1.5
        )

    def extra_key(net):
        net.buses["B1"].impedance[999.0] = gi.ComplexNumber(real=1.0, imag=0.0)

    def other_fault(net):
        gi.create_fault(
            name="F2", bus="B2", scalings={f: 1.0 for f in net.frequencies},
            network=net,
        )
        net.set_active_fault("F2")

    def thevenin(net):
        source = net.sources["V0"]
        z = source.source_impedance[50.0]
        source.source_impedance[50.0] = gi.ComplexNumber(real=z.real + 1, imag=z.imag)

    return {
        "rho": rho, "bus_value": bus_value, "bus_inactive": bus_inactive,
        "branch_inactive": branch_inactive, "rewire": rewire,
        "shield_value": shield_value, "extra_key": extra_key,
        "other_fault": other_fault, "thevenin": thevenin,
    }


@pytest.mark.parametrize("mutation", sorted(_fingerprint_cases()))
def test_the_fingerprint_detects_every_change_of_the_assembled_system(mutation):
    """Equal fingerprint <=> equal matrix, for each input of the assembly."""
    net = thevenin_ring(12, F=2)
    gi.run_fault(net, "F")
    en = net.electrical_network
    assert en.matches_network_state()
    assert en.state_fingerprint() == en.state_fingerprint()
    _fingerprint_cases()[mutation](net)
    assert not en.matches_network_state()


def test_the_build_fingerprint_reuses_the_assembly_tables(monkeypatch):
    """P6: building the network takes no second pass over the model.

    The fingerprint of a build is digested from the tables the assembly has
    gathered; ``state_fingerprint`` -- which reads the model afresh -- is only
    for the later staleness check. The two must agree.
    """
    net = thevenin_ring(12, F=2)
    calls = []
    real = en_mod.ElectricalNetwork.state_fingerprint

    def counting(self):
        calls.append(self)
        return real(self)

    monkeypatch.setattr(en_mod.ElectricalNetwork, "state_fingerprint", counting)
    gi.run_fault(net, "F")
    assert calls == []
    en = net.electrical_network
    assert en._build_fingerprint == real(en)


@pytest.mark.parametrize("how", ["deepcopy", "pickle"])
def test_the_sparse_matrices_and_their_dense_view_travel_together(how):
    """P2 with GUI2: a copied solved network keeps a consistent kernel.

    ``Y_matrices`` is a view holding a reference to ``Y_sparse``; in a copy it
    has to read the *copy's* matrices, and the copy has to answer
    ``bus_response`` -- which reads the sparse matrices and the stamped bus
    admittances -- exactly like the original.
    """
    import copy
    import pickle

    net = thevenin_ring(16, F=2)
    gi.run_fault(net, "F")
    clone = copy.deepcopy(net) if how == "deepcopy" else pickle.loads(pickle.dumps(net))
    en, en_c = net.electrical_network, clone.electrical_network
    assert en_c is not en and en_c.network is clone
    for freq in net.frequencies:
        assert en_c.Y_sparse[freq] is not en.Y_sparse[freq]
        assert np.array_equal(en_c.Y_matrices[freq], en_c.Y_sparse[freq].toarray())
        assert np.array_equal(en_c.Y_sparse[freq].toarray(), en.Y_sparse[freq].toarray())
    # a change to the copy's matrix is seen by the copy's view only
    first = net.frequencies[0]
    en_c.Y_sparse[first] = en_c.Y_sparse[first] * 2.0
    assert np.array_equal(en_c.Y_matrices[first], en_c.Y_sparse[first].toarray())
    assert not np.array_equal(en.Y_matrices[first], en_c.Y_matrices[first])
    clone = copy.deepcopy(net) if how == "deepcopy" else pickle.loads(pickle.dumps(net))
    a = gi.bus_response(net, fault="F", bus="B3")
    b = gi.bus_response(clone, fault="F", bus="B3")
    for freq in net.frequencies:
        assert b.z_network[freq] == a.z_network[freq]


def test_the_fingerprint_ignores_what_the_assembly_does_not_read():
    """Source values and mutual impedances never enter ``Y``."""
    net = thevenin_ring(12, F=2)
    gi.run_fault(net, "F")
    en = net.electrical_network
    net.sources["S0"].values[50.0] = gi.ComplexNumber(real=1.0, imag=0.0)
    z = net.branches["L1"].mutual_impedance[50.0]
    net.branches["L1"].mutual_impedance[50.0] = gi.ComplexNumber(
        real=z.real, imag=z.imag * 3
    )
    assert en.matches_network_state()


# ---------------------------------------------------------------------------
# 5. P5a: the garbage collector is paused while result rows are built
# ---------------------------------------------------------------------------


def _record_gc_state(monkeypatch, raise_on_first: bool = False):
    states: List[bool] = []
    for name in ("ResultBus", "ResultBranch"):
        real = getattr(en_mod, name)

        def recording(*args, _real=real, **kwargs):
            states.append(gc.isenabled())
            if raise_on_first:
                raise RuntimeError("injected failure while building results")
            return _real(*args, **kwargs)

        monkeypatch.setattr(en_mod, name, recording)
    return states


def test_result_rows_are_built_with_the_collector_paused(monkeypatch):
    net = ring(30, F=3)
    states = _record_gc_state(monkeypatch)
    assert gc.isenabled()
    gi.run_fault(net, "F")
    assert states and not any(states)
    assert gc.isenabled()


def test_a_disabled_collector_stays_disabled():
    net = ring(30, F=2)
    gc.disable()
    try:
        gi.run_fault(net, "F")
        assert not gc.isenabled()
    finally:
        gc.enable()


def test_the_collector_is_restored_when_result_building_fails(monkeypatch):
    net = ring(30, F=2)
    _record_gc_state(monkeypatch, raise_on_first=True)
    with pytest.raises(RuntimeError, match="injected failure"):
        gi.run_fault(net, "F")
    assert gc.isenabled()


def test_the_gc_pause_nests_and_is_shared_between_threads():
    """Nested and concurrent pauses: the collector comes back exactly once, when
    the last pause ends, and only if it was enabled when the first began."""
    pause = en_mod._gc_paused
    assert gc.isenabled()
    with pause():
        assert not gc.isenabled()
        with pause():
            assert not gc.isenabled()
        assert not gc.isenabled()
    assert gc.isenabled()

    inside = threading.Event()
    release = threading.Event()

    def worker():
        with pause():
            inside.set()
            release.wait(5)

    thread = threading.Thread(target=worker)
    thread.start()
    inside.wait(5)
    with pause():
        pass
    assert not gc.isenabled()  # the worker still holds its pause
    release.set()
    thread.join(5)
    assert gc.isenabled()


# ---------------------------------------------------------------------------
# 6. P7: bus_response and analyze_cuts on the sparse matrices
# ---------------------------------------------------------------------------


def _resolved_bus_impedance(net, name, freq, ref):
    z = net.buses[name].impedance.get(freq)
    if z is None:
        return None
    value = complex(z.real, z.imag)
    if freq == 0.0:
        sub = oracle._dc_substitute(net, {b: i for i, b in enumerate(ref.bus_names)})
        if sub is not None and oracle.is_short_circuit(value):
            value = complex(sub, 0.0)
    return value


@pytest.mark.parametrize(
    "name,builder,bus",
    [
        ("ring60", lambda: ring(60, F=3, n_sources=2), "B20"),
        ("mesh8_fault", lambda: mesh(8, F=2, n_sources=2), None),
        ("thevenin", lambda: thevenin_ring(40, F=2), "B5"),
        ("dc_ring", lambda: dc_ring(30), "B3"),
    ],
)
def test_bus_response_matches_a_dense_solve(name, builder, bus):
    """P7: the closed-form response from the sparse factorisation equals a
    dense solve of the independently assembled system without the shunt --
    and the earthing columns behind ``fault_driving_point`` (PH4) equal a
    dense solve of the independently assembled earthing network (no Thevenin
    loop closures) without the shunt."""
    net = builder()
    reference = _solve_both(net)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        response = gi.bus_response(net, fault="F", bus=bus)
    target = response.bus
    k = reference.bus_names.index(target)
    k_fault = reference.bus_names.index(net.faults["F"].bus)
    model = oracle._Model(net, "F")
    for j, f in enumerate(reference.frequencies):
        Y = reference.Y[f].copy()
        z = _resolved_bus_impedance(net, target, f, reference)
        if z is not None and np.isfinite(z) and z != 0:
            Y[k, k] -= 1.0 / z
        unit = np.zeros(len(reference.bus_names), dtype=complex)
        unit[k] = 1.0
        rhs = np.column_stack(
            [reference.i_vector[:, j], unit, reference.i_inj[:, j]]
        )
        solved = np.linalg.solve(Y, rhs)
        # 1e-12, or the forward-error bound where the system is condition-
        # limited (the 0 Hz bin with a DC stand-in, kappa ~ 1e10, see P13)
        tol = max(TOL, float(np.linalg.cond(Y)) * np.finfo(float).eps)
        u_scale = np.abs(solved[:, 0]).max()
        z_scale = np.abs(solved[:, 1]).max()
        got_u = np.array([response.u_open[f][b] for b in reference.bus_names])
        got_z = np.array([response.z_column[f][b] for b in reference.bus_names])
        got_nm = np.array(
            [response.u_open_no_mutual[f][b] for b in reference.bus_names]
        )
        assert np.abs(got_u - solved[:, 0]).max() <= tol * u_scale, (f, tol)
        assert np.abs(got_z - solved[:, 1]).max() <= tol * z_scale, (f, tol)
        assert np.abs(got_nm - solved[:, 2]).max() <= tol * np.abs(solved[:, 2]).max()
        assert abs(response.z_network[f] - solved[k, 1]) <= tol * abs(solved[k, 1])

        earthing = model.nodal(f, "python")[3].dense()
        if z is not None and np.isfinite(z) and z != 0:
            earthing[k, k] -= 1.0 / z
        units = np.zeros((len(reference.bus_names), 2), dtype=complex)
        units[k, 0] = 1.0
        units[k_fault, 1] = 1.0
        columns = np.linalg.solve(earthing, units)
        tol = max(TOL, float(np.linalg.cond(earthing)) * np.finfo(float).eps)
        for got, want in (
            (response.z_fault_open[f], columns[k_fault, 1]),
            (response.z_fault_transfer[f], columns[k_fault, 0]),
            (response.z_bus_open[f], columns[k, 0]),
        ):
            assert abs(got - want) <= tol * abs(want), (name, f, got, want)


@pytest.mark.parametrize("solved", [True, False])
def test_analyze_cuts_matches_a_dense_solve(solved):
    """P7: driving point and side impedances from the sparse unit-injection
    solve -- for the solver's matrix and for the fallback assembly."""
    net = ring(50, F=3, n_sources=1)
    fault_bus = net.faults["F"].bus
    incident = sorted(
        b.name for b in net.branches.values() if fault_bus in (b.from_bus, b.to_bus)
    )
    cuts = [gi.Cut(name="left", branches=[incident[0]]),
            gi.Cut(name="right", branches=[incident[1]])]
    reference = oracle.solve(net, "F")
    if solved:
        gi.run_fault(net, "F")
    analysis = gi.analyze_cuts(net, fault="F", cuts=cuts)
    k = reference.bus_names.index(fault_bus)
    for f in reference.frequencies:
        unit = np.zeros(len(reference.bus_names), dtype=complex)
        unit[k] = 1.0
        u = np.linalg.solve(reference.Y[f], unit)
        z_dp = u[k]
        assert abs(analysis.z_driving_point[f] - z_dp) <= TOL * abs(z_dp)
        for cut in cuts:
            branch = net.branches[cut.branches[0]]
            other = branch.to_bus if branch.from_bus == fault_bus else branch.from_bus
            z_self = branch.self_impedance[f]
            i_cut = (z_dp - u[reference.bus_names.index(other)]) / complex(
                z_self.real, z_self.imag
            )
            assert abs(analysis.z_side[cut.name][f] - z_dp / i_cut) <= TOL * abs(
                z_dp / i_cut
            )


@pytest.mark.parametrize(
    "builder",
    [lambda: ring(600, F=2, n_sources=1), lambda: thevenin_ring(600, F=2)],
    ids=["current sources", "Thevenin sources"],
)
def test_bus_response_and_analyze_cuts_build_no_dense_square_matrix(
    monkeypatch, builder
):
    """P7: neither analysis densifies -- with Thevenin sources neither, where
    the earthing columns of ``bus_response`` need the matrix without the loop
    closures (PH4)."""
    net = builder()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gi.run_fault(net, "F")
    fault_bus = net.faults["F"].bus
    incident = sorted(
        b.name for b in net.branches.values() if fault_bus in (b.from_bus, b.to_bus)
    )
    guard = _DenseGuard(monkeypatch)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        response = gi.bus_response(net, fault="F", bus="B10")
        gi.analyze_cuts(
            net, fault="F", cuts=[gi.Cut(name="left", branches=[incident[0]])]
        )
    assert guard.hits == []
    assert set(response.z_fault_open) == set(net.frequencies)


# ---------------------------------------------------------------------------
# 7. performance acceptance (slow)
# ---------------------------------------------------------------------------


def _cpu_seconds(func) -> float:
    start = time.process_time()
    func()
    return time.process_time() - start


@pytest.fixture(scope="module")
def ring4000():
    return ring(4000, F=7)


@pytest.mark.slow
def test_ring_4000_at_seven_frequencies_runs_in_seconds(ring4000):
    """P1/P2: the dense kernel needs 20-28 s here (extrapolated from 1000 to
    2500 buses / measured in the audit); see below for the memory."""
    seconds = _cpu_seconds(lambda: gi.run_fault(ring4000, "F"))
    assert seconds < 3.0, f"{seconds:.2f} s"


@pytest.mark.slow
def test_ring_4000_at_seven_frequencies_stays_below_400_mb(ring4000):
    """P2: the dense kernel peaks at 2.3 GB here (seven n x n matrices plus
    two copies of the phase network; the fit over 1000 to 2500 buses and the
    audit agree); what is left is linear in n."""
    peak = _peak_bytes(lambda: gi.run_fault(ring4000, "F"))
    assert peak < 400e6, f"{peak / 1e6:.0f} MB"


@pytest.mark.slow
def test_radial_4000_with_three_sources_runs_in_seconds():
    """P1: 143-200 s with the dense kernel, which repeated the phase solve
    per source (audit / extrapolated from 1000 and 2000 buses)."""
    net = radial(4000, F=7, n_sources=3)
    seconds = _cpu_seconds(lambda: gi.run_fault(net, "F"))
    assert seconds < 3.0, f"{seconds:.2f} s"


@pytest.mark.slow
def test_mesh_50x50_with_four_sources_runs_in_seconds():
    """P1/P2: 20-27 s with the dense kernel (extrapolated / audit)."""
    net = mesh(50, F=7, n_sources=4)
    seconds = _cpu_seconds(lambda: gi.run_fault(net, "F"))
    assert seconds < 3.0, f"{seconds:.2f} s"


@pytest.mark.slow
def test_bus_response_and_cuts_on_large_rings_run_in_fractions_of_a_second():
    """P7: 5.6 s (bus_response, with its dense earthing columns) and 2.8 s
    (analyze_cuts) on a 2000-bus ring with the dense solves, measured
    interleaved against the base implementation."""
    net = ring(2000, F=7)
    gi.run_fault(net, "F")
    fault_bus = net.faults["F"].bus
    incident = sorted(
        b.name for b in net.branches.values() if fault_bus in (b.from_bus, b.to_bus)
    )
    cuts = [gi.Cut(name="left", branches=[incident[0]]),
            gi.Cut(name="right", branches=[incident[1]])]
    t_cuts = _cpu_seconds(lambda: gi.analyze_cuts(net, fault="F", cuts=cuts))
    t_resp = _cpu_seconds(lambda: gi.bus_response(net, fault="F", bus="B100"))
    assert t_cuts < 0.5, f"analyze_cuts {t_cuts:.2f} s"
    assert t_resp < 1.0, f"bus_response {t_resp:.2f} s"
