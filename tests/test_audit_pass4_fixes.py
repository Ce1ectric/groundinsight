# tests/test_audit_pass4_fixes.py

"""
Regression tests for the fourth audit-pass bug-fix batch.

This file collects pytest cases for every bug reported in
``applications/audit-report-changelogs-2026-05-12-pass4.md`` (and the two
preceding passes that re-confirmed the same items) that has been resolved
in this branch. Each test pins a single audit bullet so the suite reports
clearly which fix has regressed if any.

The tests deliberately avoid network solves where the fix can be probed
in isolation -- the heavy notebooks under ``notebooks/14_audit_pass4_*``
exercise the end-to-end behaviour and are part of the regression CI.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pytest

import groundinsight as gi
from groundinsight.models.core_models import (
    BranchType,
    BusType,
    ComplexNumber,
    Fault,
    Source,
)
from groundinsight.pathfinder import (
    PathFinder,
    _FIND_PATHS_CACHE,
    _GRAPH_CACHE,
    clear_pathfinder_cache,
)
from groundinsight.simulation import waveforms


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ring_network():
    """A 3-bus ring used by the PathFinder cache test."""
    bt = BusType(
        name="BT_ring",
        description="",
        system_type="Grounded",
        voltage_level=20.0,
        impedance_formula="rho * 0 + 1 + I * f * 1/50",
    )
    brt = BranchType(
        name="BRT_ring",
        description="",
        grounding_conductor=True,
        self_impedance_formula="(rho * 0 + 0.25 + I * f * 0.012)*l",
        mutual_impedance_formula="(rho * 0 + 0.0 + I * f * 0.010)*l",
    )
    net = gi.create_network(name="ring", frequencies=[50])
    for bn in ("B1", "B2", "B3"):
        gi.create_bus(
            name=bn,
            type=bt,
            specific_earth_resistance=100.0,
            network=net,
        )
    for from_bus, to_bus, bn in (
        ("B1", "B2", "L12"),
        ("B2", "B3", "L23"),
        ("B1", "B3", "L13"),
    ):
        gi.create_branch(
            name=bn,
            type=brt,
            from_bus=from_bus,
            to_bus=to_bus,
            length=1.0,
            network=net,
        )
    return net


# ---------------------------------------------------------------------------
# 1. mkdocs.yml polyfill.io entry removed (pass 1..4 confirmed)
# ---------------------------------------------------------------------------


def test_mkdocs_no_polyfill_io_script():
    """The security-relevant polyfill.io CDN entry must no longer ship.

    Scans the ``extra_javascript`` block and ensures no live ``- ...``
    list entry references polyfill.io. Lines that are *comments*
    (start with ``#``) are allowed: they document why the entry was
    removed and help future audits not re-flag the gap.
    """
    mkdocs_path = Path(__file__).resolve().parent.parent / "mkdocs.yml"
    in_extra_js = False
    offending: list[str] = []
    for raw in mkdocs_path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped:
            in_extra_js = False
            continue
        if not raw.startswith(" ") and not raw.startswith("\t"):
            in_extra_js = stripped.startswith("extra_javascript")
            continue
        if not in_extra_js:
            continue
        if stripped.startswith("#"):
            continue
        if "polyfill.io" in stripped:
            offending.append(raw)
    assert not offending, (
        "extra_javascript still includes a polyfill.io entry: "
        f"{offending!r}"
    )


# ---------------------------------------------------------------------------
# 2. ``__all__`` carries the persistence helpers and ``set_log_level``
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "set_log_level",
        "start_dbsession",
        "close_dbsession",
        "save_bustype_to_db",
        "load_bustypes_from_db",
        "save_branchtype_to_db",
        "load_branchtypes_from_db",
        "save_network_to_db",
        "load_network_from_db",
        "save_network_to_json",
        "load_network_from_json",
    ],
)
def test_init_all_lists_public_helpers(name):
    assert name in gi.__all__, (
        f"groundinsight.__all__ is missing {name!r} — `from groundinsight "
        "import *` would silently skip it."
    )


# ---------------------------------------------------------------------------
# 3. ``set_log_level`` is handler-idempotent across alternating levels.
# ---------------------------------------------------------------------------


def test_set_log_level_is_idempotent_across_toggles():
    """
    Repeated calls to ``set_log_level`` with alternating levels must leave
    the package logger with **exactly one** non-Null StreamHandler.
    """
    pkg_logger = logging.getLogger("groundinsight")
    # Reset to a clean baseline.
    for h in list(pkg_logger.handlers):
        if not isinstance(h, logging.NullHandler):
            pkg_logger.removeHandler(h)

    for lvl in ("DEBUG", "INFO", "WARNING", "DEBUG", "INFO"):
        gi.set_log_level(lvl)

    stream_handlers = [
        h
        for h in pkg_logger.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.NullHandler)
    ]
    assert len(stream_handlers) == 1, (
        f"Expected exactly one console handler after the toggle dance, "
        f"got {len(stream_handlers)}: {stream_handlers!r}"
    )
    # Final level is INFO.
    assert pkg_logger.level == logging.INFO


# ---------------------------------------------------------------------------
# 4. ``start_dbsession`` rejects a silent re-bind to a different DB.
# ---------------------------------------------------------------------------


def test_start_dbsession_rejects_silent_rebind(tmp_path):
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    gi.start_dbsession(str(db_a))
    try:
        with pytest.raises(RuntimeError, match="already active"):
            gi.start_dbsession(str(db_b))
    finally:
        gi.close_dbsession()


def test_start_dbsession_same_path_warns_and_reuses(tmp_path, caplog):
    db_a = tmp_path / "a.db"
    gi.start_dbsession(str(db_a))
    try:
        with caplog.at_level(logging.WARNING, logger="groundinsight"):
            gi.start_dbsession(str(db_a))
        assert any(
            "re-using existing engine" in r.getMessage()
            for r in caplog.records
        )
    finally:
        gi.close_dbsession()


def test_start_dbsession_force_disposes_old_engine(tmp_path):
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    gi.start_dbsession(str(db_a))
    try:
        gi.start_dbsession(str(db_b), force=True)
        assert "b.db" in str(gi.engine.url)
    finally:
        gi.close_dbsession()


# ---------------------------------------------------------------------------
# 5. ``Fault.scalings`` int / float key coercion
# ---------------------------------------------------------------------------


def test_fault_scalings_int_keys_coerced_to_float():
    fault = Fault(name="F", bus="B1", scalings={50: 1.0})
    assert list(fault.scalings.keys()) == [50.0]
    assert all(isinstance(k, float) for k in fault.scalings.keys())


def test_fault_scalings_mixed_int_and_float_keys_yield_float_keys_only():
    """
    Python deduplicates ``{50: 1.0, 50.0: 0.5}`` at literal-construction
    time (``50`` and ``50.0`` share a hash) so the audit's exact
    sequence collapses *before* pydantic sees it. The fix guarantees
    that any int key surviving validation is coerced to float — which
    is what the per-frequency lookup against ``network.frequencies``
    relies on. This test pins the coercion direction.
    """
    fault = Fault(
        name="F",
        bus="B1",
        scalings={50: 1.0, 100: 0.5},
    )
    assert sorted(fault.scalings.keys()) == [50.0, 100.0]
    assert all(isinstance(k, float) for k in fault.scalings.keys())


def test_fault_scalings_unparsable_key_rejected():
    """Non-numeric keys are caught loudly rather than silently dropped."""
    with pytest.raises(ValueError, match="not convertible to float"):
        Fault(name="F", bus="B1", scalings={"50hz": 1.0})


# ---------------------------------------------------------------------------
# 6. ``PathFinder`` reuses the adjacency graph on the same topology.
# ---------------------------------------------------------------------------


def test_pathfinder_reuses_graph_cache_across_instances():
    clear_pathfinder_cache()
    net = _ring_network()

    finder_a = PathFinder(net)
    cache_size_after_first = len(_GRAPH_CACHE)
    finder_b = PathFinder(net)
    cache_size_after_second = len(_GRAPH_CACHE)

    # Second instance must hit the cache — no new graph entry.
    assert cache_size_after_second == cache_size_after_first
    # And it must really be the *same* graph object, not a fresh copy.
    assert finder_b.graph is finder_a.graph


def test_pathfinder_find_paths_memoised():
    clear_pathfinder_cache()
    net = _ring_network()
    finder = PathFinder(net)

    paths_first = finder.find_paths("B1", "B3")
    # The Pass-5 cache key layout is
    # (id, name, n_buses, n_branches, active_buses, active_branches,
    #  source_bus_name, fault_bus_name) — the source/fault bus names
    # are the *last* two components. Test by membership instead of
    # by fixed index so the assertion survives further key changes.
    cache_entries = sum(
        1
        for key in _FIND_PATHS_CACHE
        if "B1" in key and "B3" in key
    )
    assert cache_entries == 1
    paths_second = finder.find_paths("B1", "B3")
    # Same count of unique paths.
    assert len(paths_first) == len(paths_second) > 0


def test_invalidate_paths_clears_cache():
    clear_pathfinder_cache()
    net = _ring_network()
    PathFinder(net).find_paths("B1", "B3")
    assert _GRAPH_CACHE
    net.invalidate_paths()
    assert not _GRAPH_CACHE
    assert not _FIND_PATHS_CACHE


# ---------------------------------------------------------------------------
# 7. ``waveforms`` validation: ``decay_tau``, ``frequency_hz``, ``t_off``
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_tau", [0.0, -1e-3])
def test_damped_oscillation_rejects_nonpositive_tau(bad_tau):
    with pytest.raises(ValueError, match="decay_tau"):
        waveforms.damped_oscillation(
            amplitude=1.0, frequency_hz=50.0, decay_tau=bad_tau
        )


@pytest.mark.parametrize("bad_freq", [0.0, -50.0])
def test_sinusoidal_rejects_nonpositive_frequency(bad_freq):
    with pytest.raises(ValueError, match="frequency_hz"):
        waveforms.sinusoidal_with_dc_offset(
            amplitude=1.0, frequency_hz=bad_freq
        )


def test_sinusoidal_rejects_zero_dc_decay_tau():
    with pytest.raises(ValueError, match="dc_decay_tau"):
        waveforms.sinusoidal_with_dc_offset(
            amplitude=1.0,
            frequency_hz=50.0,
            dc_amplitude=10.0,
            dc_decay_tau=0.0,
        )


@pytest.mark.parametrize(
    "factory_kwargs",
    [
        dict(amplitude=1.0, t_on=0.1, t_off=0.05),
        dict(amplitude=1.0, t_on=0.05, t_off=0.05),  # equal -> still empty
    ],
)
def test_step_rejects_inverted_window(factory_kwargs):
    with pytest.raises(ValueError, match="t_off must be strictly greater"):
        waveforms.step(**factory_kwargs)


def test_damped_oscillation_window_validates():
    with pytest.raises(ValueError, match="t_off must be strictly greater"):
        waveforms.damped_oscillation(
            amplitude=1.0,
            frequency_hz=50.0,
            decay_tau=1e-3,
            t_on=0.1,
            t_off=0.05,
        )


def test_waveforms_still_callable_on_valid_input():
    w = waveforms.sinusoidal_with_dc_offset(
        amplitude=1.0,
        frequency_hz=50.0,
        t_on=0.0,
        t_off=0.2,
        dc_amplitude=0.5,
        dc_decay_tau=0.05,
    )
    t = np.linspace(0.0, 0.2, 11)
    values = w(t)
    assert values.shape == t.shape


# ---------------------------------------------------------------------------
# 8. Pandapower importer fixes (require the pandapower extra).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pp_module():
    return pytest.importorskip("pandapower")


def _minimal_pp_net_two_buses_one_line(pp, vn_kv_b: float = 20.0):
    """Build a 2-bus pandapower net with one line at the requested vn_kv."""
    net = pp.create_empty_network()
    pp.create_bus(net, vn_kv=20.0, name="B0")
    pp.create_bus(net, vn_kv=vn_kv_b, name="B1")
    pp.create_line_from_parameters(
        net,
        from_bus=0,
        to_bus=1,
        length_km=1.0,
        r_ohm_per_km=0.1,
        x_ohm_per_km=0.1,
        c_nf_per_km=0.0,
        max_i_ka=1.0,
        name="L0",
    )
    return net


def _defaults_20kV():
    from groundinsight.io.defaults import ImportDefaults

    bt = BusType(
        name="BT",
        description="",
        system_type="Grounded",
        voltage_level=20.0,
        impedance_formula="rho * 0 + 1 + I * f * 1/50",
    )
    brt = BranchType(
        name="BRT",
        description="",
        grounding_conductor=True,
        self_impedance_formula="(rho * 0 + 0.25 + I * f * 0.012)*l",
        mutual_impedance_formula="(rho * 0 + 0.0 + I * f * 0.010)*l",
    )
    return ImportDefaults(
        default_bus_type=bt,
        default_branch_type=brt,
        frequencies=[50.0],
        rho=100.0,
    )


def test_pandapower_empty_network_emits_warning(pp_module, caplog):
    """Mis-set voltage_level_kV produces an empty Network with a warning."""
    net = _minimal_pp_net_two_buses_one_line(pp_module, vn_kv_b=20.0)
    defaults = _defaults_20kV()
    with caplog.at_level(
        logging.WARNING, logger="groundinsight.io.pandapower_import"
    ):
        result = gi.from_pandapower(
            net, defaults=defaults, voltage_level_kV=0.4
        )
    assert len(result.buses) == 0
    assert any(
        "empty or branch-less" in r.getMessage() for r in caplog.records
    )


def test_pandapower_self_loop_skipped_with_warning(pp_module, caplog):
    """Pandapower line with from_bus == to_bus must be skipped, not imported."""
    pp = pp_module
    net = pp.create_empty_network()
    pp.create_bus(net, vn_kv=20.0, name="B0")
    pp.create_line_from_parameters(
        net,
        from_bus=0,
        to_bus=0,
        length_km=1.0,
        r_ohm_per_km=0.1,
        x_ohm_per_km=0.1,
        c_nf_per_km=0.0,
        max_i_ka=1.0,
        name="self",
    )
    defaults = _defaults_20kV()
    with caplog.at_level(
        logging.WARNING, logger="groundinsight.io.pandapower_import"
    ):
        result = gi.from_pandapower(
            net, defaults=defaults, voltage_level_kV=20.0
        )
    assert len(result.branches) == 0
    assert any("self-loop" in r.getMessage() for r in caplog.records)


def test_pandapower_vn_kv_none_skipped_with_warning(pp_module, caplog):
    """Bus with vn_kv=None must be skipped explicitly, not silently zeroed."""
    pp = pp_module
    net = pp.create_empty_network()
    pp.create_bus(net, vn_kv=20.0, name="B0")
    pp.create_bus(net, vn_kv=20.0, name="B1")
    # Force a None on the second bus — pandapower stores numbers, but the
    # importer reads via row.get() and must cope with corrupted nets.
    net.bus.at[1, "vn_kv"] = None
    defaults = _defaults_20kV()
    with caplog.at_level(
        logging.WARNING, logger="groundinsight.io.pandapower_import"
    ):
        result = gi.from_pandapower(
            net, defaults=defaults, voltage_level_kV=20.0
        )
    assert "B0" in result.buses
    assert "B1" not in result.buses
    assert any("unparsable vn_kv" in r.getMessage() for r in caplog.records)


def test_preview_pandapower_import_has_include_trafos(pp_module):
    """Preview API mirrors from_pandapower's include_trafos contract."""
    net = _minimal_pp_net_two_buses_one_line(pp_module)
    with pytest.raises(NotImplementedError):
        gi.preview_pandapower_import(
            net, voltage_level_kV=20.0, include_trafos=True
        )
