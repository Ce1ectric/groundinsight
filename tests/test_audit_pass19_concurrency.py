# tests/test_audit_pass19_concurrency.py
"""
Concurrent solves of *different* networks in one process (audit pass 19,
found while building the multi-user service layer ``groundinsight.api``;
related to ARC1, ARC2 and GUI5).

The service layer runs jobs in worker threads: a server solves two users'
projects at once, a GUI's editor changes a network while a worker solves
another. :func:`groundinsight.solve` documents that the *same* network must
not be solved from two threads at once; two *different* networks must be
independent.

They were not while the pathfinder kept module-level LRU caches shared by
every network: :func:`~groundinsight.pathfinder.clear_pathfinder_cache`
iterated a cache to drop one network's entries while another thread inserted
into it, and the iteration failed with ``RuntimeError: OrderedDict mutated
during iteration`` -- measured with four threads solving four rings, 150
solves each, ``sys.setswitchinterval(1e-6)``: 7 of 600 solves raised. Since
ARC2 every network owns its memo behind its own lock, and clearing "every
memo" walks a registry of live caches; these tests keep both properties
under thread pressure.

The race needs a thread switch inside a few microseconds, so the tests shrink
the switch interval to make it reproducible; the property they check does not
depend on it.

Second way: every concurrent answer is compared with the answer of the same
search or solve run alone (the same path sets, bit-identical serialised
results), so a lock cannot hide a wrong number behind an absent exception.
"""

from __future__ import annotations

import sys
import threading
import time
import warnings
from typing import Dict, List

import pytest

import groundinsight as gi
from groundinsight import pathfinder


def _ring(n: int) -> gi.Network:
    """A cable ring of ``n`` stations with one infeed and one fault.

    Parameters
    ----------
    n : int
        Number of buses (>= 3).

    Returns
    -------
    Network
        The unsolved network; source ``src`` at ``b0``, fault ``F`` opposite.
    """
    net = gi.create_network(name=f"ring_{n}", frequencies=[50.0, 250.0])
    bus_type = gi.BusType(
        name="station", system_type="MV", voltage_level=20.0,
        impedance_formula="2.0 + rho * 0",
    )
    cable = gi.BranchType(
        name="cable", grounding_conductor=True,
        self_impedance_formula="(0.25 + j * f * 0.0019 * 2 * pi) * l",
        mutual_impedance_formula="(0.05 + j * f * 0.00185 * 2 * pi) * l",
    )
    for k in range(n):
        gi.create_bus(name=f"b{k}", type=bus_type, network=net)
    for k in range(n):
        gi.create_branch(
            name=f"L{k}", type=cable, from_bus=f"b{k}", to_bus=f"b{(k + 1) % n}",
            length=0.5, network=net,
        )
    gi.create_source(name="src", bus="b0", values={50.0: 1000.0, 250.0: 50.0}, network=net)
    gi.create_fault(name="F", bus=f"b{n // 2}", network=net)
    return net


def _key_figures(result: gi.Result) -> str:
    """A serialised fingerprint of every number of a result.

    Parameters
    ----------
    result : Result
        A solved result.

    Returns
    -------
    str
        The JSON of the bus, branch and reduction-factor results.
    """
    return result.model_dump_json(include={"buses", "branches", "reduction_factor"})


def _path_set(net: gi.Network, source: str, target: str) -> List[tuple]:
    """The paths between two buses as sorted branch-name tuples."""
    found = pathfinder.PathFinder(net).find_paths(source, target)
    return sorted(tuple(branch.name for branch in path.segments) for path in found)


@pytest.fixture
def fast_thread_switches():
    """Shrink the interpreter's thread switch interval for the test."""
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(previous)


def test_clearing_every_memo_while_another_thread_searches_does_not_race(fast_thread_switches):
    """One thread drops the memo of every live network, and of one other
    network, while another thread searches on its own network.

    On the former shared caches this failed with ``OrderedDict mutated during
    iteration`` within well under a second.
    """
    searched, other = _ring(8), _ring(5)
    expected = _path_set(searched, "b0", "b4")
    assert expected == [("L0", "L1", "L2", "L3"), ("L7", "L6", "L5", "L4")]
    stop = threading.Event()
    errors: List[str] = []
    wrong: List[List[tuple]] = []

    def search() -> None:
        while not stop.is_set():
            try:
                found = _path_set(searched, "b0", "b4")
                reached = pathfinder.PathFinder(searched).reaches("b0", "b4")
            except Exception as exc:  # pragma: no cover - the defect
                errors.append(f"search: {type(exc).__name__}: {exc}")
                return
            if found != expected or not reached:
                wrong.append(found)

    worker = threading.Thread(target=search)
    worker.start()
    deadline = time.monotonic() + 1.0
    rounds = 0
    try:
        while time.monotonic() < deadline and not errors:
            try:
                pathfinder.clear_pathfinder_cache()
                pathfinder.clear_pathfinder_cache(other)
            except Exception as exc:  # pragma: no cover - the defect
                errors.append(f"clear: {type(exc).__name__}: {exc}")
            rounds += 1
    finally:
        stop.set()
        worker.join()
    assert errors == [] and wrong == []
    assert rounds > 0


def test_concurrent_solves_of_different_networks_agree_with_solo_solves(fast_thread_switches):
    """End to end: four threads solve four rings; every result equals its solo solve."""
    sizes = (6, 7, 8, 9)
    networks = {n: _ring(n) for n in sizes}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        solo = {n: _key_figures(gi.solve(networks[n], "F", phase_current_mode="paths")) for n in sizes}
    errors: List[str] = []
    mismatches: Dict[int, int] = {n: 0 for n in sizes}

    def worker(n: int) -> None:
        for _ in range(60):
            try:
                result = gi.solve(networks[n], "F", phase_current_mode="paths")
            except Exception as exc:
                errors.append(f"ring {n}: {type(exc).__name__}: {exc}")
                return
            if _key_figures(result) != solo[n]:
                mismatches[n] += 1

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        threads = [threading.Thread(target=worker, args=(n,)) for n in sizes]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert errors == []
    assert mismatches == {n: 0 for n in sizes}
