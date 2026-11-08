# tests/test_audit_pass6_fixes.py

"""
Regression tests for the sixth audit-pass bug-fix batch.

Pins every code-level finding reported in
``applications/Claude Audits/audit-report-changelogs-2026-05-14-pass6.md``
under the *sixth 2026-05-14 review pass* / *seventh 2026-05-18 review
pass* heading for ``groundinsight``. Each test pins one finding so
the suite reports clearly which fix has regressed if any.

Pass-6 / Pass-7 findings covered here:

1. ``pathfinder._GRAPH_CACHE`` / ``_FIND_PATHS_CACHE`` carry an LRU
   eviction policy via ``OrderedDict`` and a configurable cap
   (``set_pathfinder_cache_size``).
2. ``outage_context`` invalidates the module-level pathfinder cache
   on exit.

Audit pass 19 (ARC2) moved the memo from module-level dictionaries into the
network (``network.path_cache()``) and the cap into
``network.pathfinder_settings``; ``set_pathfinder_cache_size`` is a
deprecated switch for the default. Findings 1 and 2 are pinned per network.
3. ``outage_context`` is re-entrant: nested ``with`` blocks restore
   the outermost baseline correctly.
4. ``Network._validate_frequencies`` rejects negative input but
   accepts ``f == 0`` (DC); the docstring matches the implementation.
5. ``Network.frequencies`` emits ``NetworkFrequencyOrderWarning`` on
   a non-strictly-increasing input.
6. ``Network.invalidate_paths()`` rebinds ``self.paths`` atomically
   so an external ``dict(network.paths)`` snapshot survives.
7. ``gi.set_active_fault(net, fault_name, keep_results=)`` factory
   is reachable from the public top-level surface.
8. ``__version__`` agrees with the version declared in
   ``pyproject.toml`` and in ``CITATION.cff``. The expectation is read
   from the project metadata rather than pinned to a literal, because
   ``scripts/release.py`` rewrites all three in one step and a literal
   would fail the CI on every release commit (Pass-17 finding).
"""

from __future__ import annotations

import tomllib
import warnings
from pathlib import Path
from typing import Optional

import pytest

import groundinsight as gi
from groundinsight.models.core_models import (
    BranchType,
    BusType,
    NetworkFrequencyOrderWarning,
)
from groundinsight.pathfinder import (
    PathFinder,
    PathfinderSettings,
    clear_pathfinder_cache,
    get_pathfinder_cache_size,
    set_pathfinder_cache_size,
)
from groundinsight.simulation.outage import (
    Outage,
    outage_context,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bus_type() -> BusType:
    return BusType(
        name="BT",
        description="",
        system_type="Grounded",
        voltage_level=20.0,
        impedance_formula="rho * 0 + 1 + I * f * 1/50",
    )


def _branch_type() -> BranchType:
    return BranchType(
        name="BRT",
        description="",
        grounding_conductor=True,
        self_impedance_formula="(rho * 0 + 0.25 + I * f * 0.012)*l",
        mutual_impedance_formula="(rho * 0 + 0.0 + I * f * 0.010)*l",
    )


def _two_bus_network(name: str = "net"):
    """A minimal 2-bus / 1-branch network used by the cache tests."""
    bt = _bus_type()
    brt = _branch_type()
    net = gi.create_network(name=name, frequencies=[50.0])
    gi.create_bus(name="B1", type=bt, specific_earth_resistance=100.0, network=net)
    gi.create_bus(name="B2", type=bt, specific_earth_resistance=100.0, network=net)
    gi.create_branch(
        name="L12",
        type=brt,
        from_bus="B1",
        to_bus="B2",
        length=1.0,
        network=net,
    )
    gi.create_source(
        name="S1",
        bus="B1",
        values={50.0: 1.0 + 0.0j},
        network=net,
    )
    gi.create_fault(
        name="F1",
        bus="B2",
        scalings={50.0: 1.0},
        network=net,
        active=True,
    )
    return net


def _chain_network(name: str = "chain"):
    """Four buses in a chain: deactivating a branch gives a new topology."""
    bt = _bus_type()
    brt = _branch_type()
    net = gi.create_network(name=name, frequencies=[50.0])
    for bus in ("B1", "B2", "B3", "B4"):
        gi.create_bus(name=bus, type=bt, specific_earth_resistance=100.0, network=net)
    for a, b in (("B1", "B2"), ("B2", "B3"), ("B3", "B4")):
        gi.create_branch(name=f"L{a[1]}{b[1]}", type=brt, from_bus=a, to_bus=b,
                         length=1.0, network=net)
    return net


def _search_without(net, branch: str):
    """Run a path search with ``branch`` switched off; return its topology key."""
    net.branches[branch].active = False
    try:
        finder = PathFinder(net)
        finder.find_paths("B1", "B4")
        return finder._topology_key
    finally:
        net.branches[branch].active = True


@pytest.fixture(autouse=True)
def _reset_caches_and_cap():
    """Each test starts with a clean cache and the default cap restored."""
    clear_pathfinder_cache()
    with pytest.deprecated_call():
        previous = set_pathfinder_cache_size(256)
    yield
    clear_pathfinder_cache()
    with pytest.deprecated_call():
        set_pathfinder_cache_size(previous if previous > 0 else 256)


# ---------------------------------------------------------------------------
# Finding 1 — Pathfinder LRU cache with configurable cap.
# ---------------------------------------------------------------------------


def test_set_pathfinder_cache_size_returns_previous_and_caps_immediately():
    """``set_pathfinder_cache_size`` returns the prior cap and caps the cache.

    Deprecated since audit pass 19: it sets the default of networks without
    settings of their own and trims their memo at once.
    """
    net = _chain_network()
    for branch in ("L12", "L23", "L34"):
        _search_without(net, branch)
    assert net.path_cache().sizes()["graph"] == 3
    with pytest.deprecated_call():
        previous = set_pathfinder_cache_size(2)
    assert previous == 256
    with pytest.deprecated_call():
        assert get_pathfinder_cache_size() == 2
    assert net.path_cache().sizes()["graph"] == 2


def test_set_pathfinder_cache_size_rejects_non_positive_input():
    for bad in (0, -1, 1.5):
        with pytest.deprecated_call(), pytest.raises(ValueError):
            set_pathfinder_cache_size(bad)  # type: ignore[arg-type]


def test_lru_eviction_drops_oldest_entry():
    """More topologies than the cap evict the least recently used one."""
    net = _chain_network()
    net.pathfinder_settings = PathfinderSettings(cache_size=2)
    keys = [_search_without(net, branch) for branch in ("L12", "L23", "L34")]
    # After three insertions with cap=2, only two entries remain per table.
    assert net.path_cache().sizes()["graph"] == 2
    assert net.path_cache().sizes()["paths"] == 2
    # The first topology was evicted.
    assert net.path_cache().keys("graph") == keys[1:]


def test_lru_recency_bump_on_lookup():
    """A re-hit moves the entry to the MRU end and protects it from eviction."""
    net = _chain_network()
    net.pathfinder_settings = PathfinderSettings(cache_size=2)
    first = _search_without(net, "L12")
    second = _search_without(net, "L23")
    # Touch the first topology again so the second becomes the LRU entry.
    assert _search_without(net, "L12") == first
    third = _search_without(net, "L34")

    remaining = net.path_cache().keys("graph")
    assert second not in remaining
    assert remaining == [first, third]


# ---------------------------------------------------------------------------
# Finding 2 — ``outage_context`` clears the pathfinder cache on exit.
# ---------------------------------------------------------------------------


def test_outage_context_clears_pathfinder_cache_on_exit():
    """Cache entries built inside the ``with`` block must be evicted."""
    net = _two_bus_network(name="net")
    # Prime the cache with the unaltered topology.
    PathFinder(net).find_paths("B1", "B2")
    base_keys = set(net.path_cache().keys("graph"))

    with outage_context(net, Outage(name="o1", disabled_branches=["L12"])):
        # The inner solve would build a new entry under the active-subset
        # fingerprint with L12 inactive.
        PathFinder(net).find_paths("B1", "B2")
        inner_keys = set(net.path_cache().keys("graph"))
        assert inner_keys != base_keys, "inner topology key should differ"

    # On exit, the inner cache entries must be gone — the network-scoped
    # ``clear_pathfinder_cache`` is invoked in the ``finally`` branch.
    after_keys = set(net.path_cache().keys("graph"))
    assert all(k not in inner_keys for k in after_keys)


# ---------------------------------------------------------------------------
# Finding 3 — ``outage_context`` is re-entrant safe.
# ---------------------------------------------------------------------------


def test_outage_context_nested_restores_outer_baseline():
    """Nested ``outage_context`` blocks restore the outer-block baseline."""
    bt = _bus_type()
    brt = _branch_type()
    net = gi.create_network(name="net", frequencies=[50.0])
    gi.create_bus(name="B1", type=bt, specific_earth_resistance=100.0, network=net)
    gi.create_bus(name="B2", type=bt, specific_earth_resistance=100.0, network=net)
    gi.create_bus(name="B3", type=bt, specific_earth_resistance=100.0, network=net)
    gi.create_branch(
        name="L12", type=brt, from_bus="B1", to_bus="B2", length=1.0, network=net
    )
    gi.create_branch(
        name="L23", type=brt, from_bus="B2", to_bus="B3", length=1.0, network=net
    )

    outer = Outage(name="outer", disabled_branches=["L12"])
    inner = Outage(name="inner", disabled_branches=["L23"])

    assert net.branches["L12"].active is True
    assert net.branches["L23"].active is True

    with outage_context(net, outer):
        assert net.branches["L12"].active is False
        assert net.branches["L23"].active is True
        with outage_context(net, inner):
            # Both branches inactive inside the nested block.
            assert net.branches["L12"].active is False
            assert net.branches["L23"].active is False
        # After the inner block, L23 must be re-activated even though
        # the outer block is still around L12.
        assert net.branches["L12"].active is False
        assert net.branches["L23"].active is True

    # After the outer block, both branches must be back to their
    # original state.
    assert net.branches["L12"].active is True
    assert net.branches["L23"].active is True
    # Audit pass 18 (S15): the nesting used to also push onto a
    # module-level ``_OUTAGE_BASELINE_STACK`` keyed by ``id(network)``
    # that nothing ever read back. It has been removed -- the per-level
    # baseline lives in each generator frame, which is what the assertions
    # above actually exercise. Nothing to check here any more.


def test_outage_context_nested_uses_outer_baseline_not_inner_state():
    """Inner block must not capture outer-modified state as its baseline."""
    bt = _bus_type()
    brt = _branch_type()
    net = gi.create_network(name="net", frequencies=[50.0])
    gi.create_bus(name="B1", type=bt, specific_earth_resistance=100.0, network=net)
    gi.create_bus(name="B2", type=bt, specific_earth_resistance=100.0, network=net)
    gi.create_branch(
        name="L12", type=brt, from_bus="B1", to_bus="B2", length=1.0, network=net
    )

    # Outer disables L12; inner *also* references L12. The inner block
    # must not commit the already-False state as its baseline — on
    # outer-exit the original True must come back.
    with outage_context(net, Outage(name="o1", disabled_branches=["L12"])):
        with outage_context(net, Outage(name="o2", disabled_branches=["L12"])):
            assert net.branches["L12"].active is False
        # Inner exit alone does not re-activate (outer still holds).
        assert net.branches["L12"].active is False

    assert net.branches["L12"].active is True


# ---------------------------------------------------------------------------
# Finding 4 — Frequencies validator: DC permitted, negative rejected.
# ---------------------------------------------------------------------------


def test_dc_frequency_is_accepted():
    net = gi.create_network(name="net", frequencies=[0.0, 50.0])
    assert net.frequencies == [0.0, 50.0]


def test_negative_frequency_is_rejected():
    with pytest.raises(ValueError, match="must be >= 0"):
        gi.create_network(name="net", frequencies=[-1.0, 50.0])


# ---------------------------------------------------------------------------
# Finding 5 — Frequency-order warning.
# ---------------------------------------------------------------------------


def test_non_monotone_frequencies_emit_order_warning():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        gi.create_network(name="net", frequencies=[100.0, 50.0])
    categories = [w.category for w in caught]
    assert NetworkFrequencyOrderWarning in categories


def test_strictly_increasing_frequencies_emit_no_warning():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        gi.create_network(name="net", frequencies=[50.0, 100.0, 200.0])
    categories = [w.category for w in caught]
    assert NetworkFrequencyOrderWarning not in categories


def test_warning_class_exposed_at_top_level():
    assert gi.NetworkFrequencyOrderWarning is NetworkFrequencyOrderWarning
    assert issubclass(NetworkFrequencyOrderWarning, UserWarning)


# ---------------------------------------------------------------------------
# Finding 6 — invalidate_paths uses atomic rebind.
# ---------------------------------------------------------------------------


def test_invalidate_paths_preserves_snapshot():
    """An external ``dict(network.paths)`` snapshot survives the call."""
    net = _two_bus_network(name="net")
    net.define_paths()
    assert net.paths, "precondition: paths populated"

    snapshot = dict(net.paths)
    snapshot_names = sorted(snapshot)

    net.invalidate_paths()

    # network.paths itself is now empty.
    assert net.paths == {}
    # The external snapshot must still hold its entries.
    assert sorted(snapshot) == snapshot_names
    assert all(isinstance(p, type(next(iter(snapshot.values())))) for p in snapshot.values())


def test_invalidate_paths_rebinds_to_new_dict_object():
    """Atomic rebind: ``self.paths`` is a new dict instance after the call."""
    net = _two_bus_network(name="net")
    net.define_paths()
    paths_before = net.paths
    net.invalidate_paths()
    assert net.paths is not paths_before
    assert net.paths == {}


# ---------------------------------------------------------------------------
# Finding 7 — top-level set_active_fault factory accepts keep_results=.
# ---------------------------------------------------------------------------


def test_top_level_set_active_fault_propagates_keep_results():
    """``gi.set_active_fault(..., keep_results=True)`` keeps cached Result."""
    from groundinsight.models.core_models import Result

    net = _two_bus_network(name="net")
    # Inject a synthetic cached Result so we do not need to actually solve.
    net.results["F1"] = Result(fault="F1")
    assert "F1" in net.results

    # Default: cached result is dropped.
    gi.set_active_fault(net, "F1")
    assert "F1" not in net.results

    # Re-inject and call with keep_results=True.
    net.results["F1"] = Result(fault="F1")
    gi.set_active_fault(net, "F1", keep_results=True)
    assert "F1" in net.results


def test_top_level_set_active_fault_is_callable_at_module_root():
    """The factory must be reachable as ``gi.set_active_fault``."""
    assert callable(gi.set_active_fault)
    assert "set_active_fault" in gi.__all__


# ---------------------------------------------------------------------------
# Finding 8 — version bump.
# ---------------------------------------------------------------------------


def _declared_pyproject_version() -> Optional[str]:
    """Return the version declared in ``pyproject.toml``, if reachable.

    Returns
    -------
    str or None
        The ``tool.poetry.version`` string, or ``None`` when the package is
        installed without its source tree next to the tests.
    """
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    if not pyproject.is_file():
        return None
    with pyproject.open("rb") as handle:
        return tomllib.load(handle)["tool"]["poetry"]["version"]


def test_version_matches_pyproject():
    """``gi.__version__`` must equal the version in ``pyproject.toml``.

    Deliberately *not* pinned to a literal. ``scripts/release.py`` rewrites
    the version in ``pyproject.toml``, in ``src/groundinsight/__init__.py``
    and in ``CITATION.cff`` in a single step and then commits; a hard-coded
    expectation here would turn every future release commit into a CI
    failure, which is what the Pass-17 release-readiness sweep found.
    """
    declared = _declared_pyproject_version()
    if declared is None:
        pytest.skip("pyproject.toml is not part of the installation")
    assert gi.__version__ == declared


def test_version_matches_citation_cff():
    """``CITATION.cff`` must carry the same version as the package.

    ``scripts/release.py`` reads the current version from all three
    locations and aborts with "version drift detected" when they disagree,
    so a stale ``CITATION.cff`` blocks the release. This test turns that
    blocker into a test failure at development time instead.
    """
    citation = Path(__file__).resolve().parents[1] / "CITATION.cff"
    if not citation.is_file():
        pytest.skip("CITATION.cff is not part of the installation")

    lines = citation.read_text(encoding="utf-8").splitlines()
    version_line = next((ln for ln in lines if ln.startswith("version:")), None)
    assert version_line is not None, "CITATION.cff has no top-level 'version:' key"

    declared = version_line.split(":", 1)[1].strip().strip("\"'")
    assert declared == gi.__version__


def test_pathfinder_cache_helpers_exposed_at_top_level():
    assert callable(gi.set_pathfinder_cache_size)
    assert callable(gi.get_pathfinder_cache_size)
    assert callable(gi.clear_pathfinder_cache)
    for name in (
        "set_pathfinder_cache_size",
        "get_pathfinder_cache_size",
        "clear_pathfinder_cache",
        "NetworkFrequencyOrderWarning",
    ):
        assert name in gi.__all__, f"{name!r} missing from gi.__all__"
