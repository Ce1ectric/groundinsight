# tests/test_audit_pass19_diagnostics_inventory.py
"""
Audit pass 19 -- result-relevant log lines become structured diagnostics (GUI4, STD5).

The library's logging default is a ``NullHandler``, so a ``logger.warning`` reaches
nobody who has not configured logging -- and a GUI cannot attach it to an element
or tell a hint from a warning. Every ``logger.warning``/``logger.error`` of the
package was therefore classified:

* **result-relevant** -- the user must know it to read a result correctly: it is
  raised with :func:`groundinsight.diagnostics.emit` as an ``InputWarning``,
  ``ModelAssumptionWarning`` or ``TransientModelWarning`` with a stable code and
  the elements concerned (collected into ``Result.diagnostics`` when it happens
  inside ``run_fault``);
* **operational** -- it reports what the library does (persistence, sessions,
  performance) or summarises something the returned object already carries (a
  status field, a failures map, a table column): it stays a log line.

What is pinned here:

* the inventory itself: every remaining log site, with the reason it is
  operational. A new ``logger.warning`` fails this test until it is classified;
* every converted site: the condition raises the warning with its code, category
  and elements, independently of the logging configuration and of the warning
  filters, and the code is documented in the docstring of the function that
  raises it;
* the second way for the two solver-side conditions: the earth-group spread is
  recomputed from the independent reference solver.
"""

from __future__ import annotations

import ast
import logging
import math
import pathlib
import re
import warnings
from typing import Dict, List, Tuple

import polars as pl
import pytest

import groundinsight as gi
from groundinsight.diagnostics import (
    GroundinsightWarning,
    InputWarning,
    ModelAssumptionWarning,
    collecting,
)
from groundinsight.models.core_models import TransientModelWarning
from tests import _reference_mna as ref

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "groundinsight"


# --------------------------------------------------------------------------
# the inventory
# --------------------------------------------------------------------------

_STATUS = "the returned dict carries status, converged and the bracket"
_PERSISTENCE = "persistence / migration bookkeeping, not a result (details in the report object)"
_SESSION = "database session lifecycle"

#: Log sites that stay log lines: (module, enclosing function) -> (count, why).
OPERATIONAL: Dict[Tuple[str, str], Tuple[int, str]] = {
    ("analysis/inverse_rho.py", "_non_monotone_result"): (1, _STATUS),
    ("analysis/inverse_rho.py", "find_max_rho_scaling"): (1, _STATUS),
    ("analysis/inverse_rho_f.py", "find_max_rho_f_scaling"): (1, _STATUS),
    ("analysis/reference.py", "run_reference_cases"): (
        1, "summary of the returned table, which carries the pass/fail column"),
    ("analysis/thermal.py", "check_conductor_limits"): (
        1, "summary of violations, which are the within_limit column of the table"),
    ("analysis/thermal.py", "check_node_limits"): (
        1, "summary of violations, which are the within_limit column of the table"),
    ("database/crud.py", "_check_type_merge"): (1, _PERSISTENCE),
    ("database/migration.py", "_convert_pickled_frequencies"): (1, _PERSISTENCE),
    ("database/migration.py", "migrate_database"): (3, _PERSISTENCE),
    ("database/migration.py", "_log_report"): (6, _PERSISTENCE),
    ("database/store.py", "Database.close"): (2, _SESSION),
    ("database/store.py", "start_dbsession"): (2, _SESSION),
    ("database/store.py", "close_dbsession"): (2, _SESSION),
    ("electrical_network.py", "ElectricalNetwork._sources_with_path_to_active_fault"): (
        1, "paths rebuilt in 'paths' mode: a cost notice, the result is correct"),
    ("models/core_models.py", "Network.add_bus"): (1, "overwrite=True was requested"),
    ("models/core_models.py", "Network.add_branch"): (1, "overwrite=True was requested"),
    ("models/core_models.py", "Network.add_fault"): (1, "overwrite=True was requested"),
    ("models/core_models.py", "Network.add_source"): (1, "overwrite=True was requested"),
    ("models/core_models.py", "Network.res_all_impedances"): (
        3, "rows missing from a reporting table, visible in the table"),
    ("network_operations.py", "_resolve_phase_current_mode"): (
        1, "deprecated argument; a DeprecationWarning is raised alongside"),
    ("plotting.py", "_warn_about_log_scale_zeros"): (
        1, "presentation of a plot, not a result"),
    ("simulation/outage.py", "run_outage_study"): (
        2, "summary of the failures map of the returned study"),
    ("simulation/sweep.py", "run_sweep"): (
        2, "summary of the failures map of the returned study"),
    ("simulation/transient.py", "TransientStudy._solve_fft"): (
        1, "memory budget: a cost notice, the result does not depend on it"),
}

#: The converted sites: code -> (category, module that raises it).
CONVERTED: Dict[str, Tuple[type, str]] = {
    "PARALLEL_COEFFICIENT_DEFAULT": (ModelAssumptionWarning, "network_operations.py"),
    "EARTH_GROUP_SPREAD": (ModelAssumptionWarning, "electrical_network.py"),
    "REDUCTION_FACTOR_UNDEFINED": (ModelAssumptionWarning, "electrical_network.py"),
    "SC_KAPPA_MIXED": (ModelAssumptionWarning, "analysis/shortcircuit.py"),
    "SC_DATA_INCOMPLETE": (InputWarning, "analysis/shortcircuit.py"),
    "THERMAL_DATA_INCOMPLETE": (InputWarning, "analysis/thermal.py"),
    "SC_VALUE_REPLACED": (InputWarning, "io/pandapower_sc.py"),
    "SC_KAPPA_OUT_OF_RANGE": (InputWarning, "io/pandapower_sc.py"),
    "SC_KAPPA_MISSING": (InputWarning, "io/pandapower_sc.py"),
    "SC_ROW_AMBIGUOUS": (InputWarning, "io/pandapower_sc.py"),
    "SC_SPLIT_EQUAL": (InputWarning, "io/pandapower_sc.py"),
    "SC_VOLTAGE_SOURCE_SKIPPED": (InputWarning, "io/pandapower_sc.py"),
    "IMPORT_LENGTH_DEFAULT": (InputWarning, "io/pandapower_import.py"),
    "IMPORT_BUS_SKIPPED": (InputWarning, "io/pandapower_import.py"),
    "IMPORT_LINE_SKIPPED": (InputWarning, "io/pandapower_import.py"),
    "IMPORT_SWITCH_NOT_IMPORTED": (InputWarning, "io/pandapower_import.py"),
    "IMPORT_ELEMENT_NOT_IMPORTED": (InputWarning, "io/pandapower_import.py"),
    "IMPORT_VOLTAGE_LEVEL_MISMATCH": (InputWarning, "io/pandapower_import.py"),
    "IMPORT_EMPTY": (InputWarning, "io/pandapower_import.py"),
    "TRANSIENT_SOURCE_NOT_CONNECTED": (InputWarning, "simulation/transient.py"),
    "TRANSIENT_COUPLING_SKIPPED": (TransientModelWarning, "simulation/transient.py"),
    "TRANSIENT_BRANCH_SKIPPED": (InputWarning, "simulation/transient.py"),
    "TRANSIENT_RESISTIVE_COUPLING_IGNORED": (TransientModelWarning, "simulation/transient.py"),
}

_LEVELS = {"warning", "error", "exception", "critical"}


def _log_sites() -> Dict[Tuple[str, str], int]:
    """Every ``logger.<warning|error|exception|critical>(...)`` call in the package."""
    found: Dict[Tuple[str, str], int] = {}

    class Visitor(ast.NodeVisitor):
        def __init__(self, module: str) -> None:
            self.module = module
            self.stack: List[str] = []

        def visit_FunctionDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef

        def visit_Call(self, node):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in _LEVELS
                and isinstance(func.value, ast.Name)
                and func.value.id in ("logger", "log", "_log", "_logger")
            ):
                key = (self.module, ".".join(self.stack))
                found[key] = found.get(key, 0) + 1
            self.generic_visit(node)

    for path in sorted(SRC.rglob("*.py")):
        module = path.relative_to(SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        Visitor(module).visit(tree)
    return found


def _emitted_codes() -> Dict[str, List[Tuple[str, str]]]:
    """``code="..."`` of every ``emit(...)`` call: code -> [(module, function)]."""
    found: Dict[str, List[Tuple[str, str]]] = {}
    for path in sorted(SRC.rglob("*.py")):
        module = path.relative_to(SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "emit"
                ):
                    for keyword in node.keywords:
                        if keyword.arg == "code" and isinstance(keyword.value, ast.Constant):
                            found.setdefault(keyword.value.value, []).append(
                                (module, function.name)
                            )
    return found


def test_every_remaining_log_site_is_classified_as_operational():
    """A new ``logger.warning`` fails here until it is classified: emit it as
    a structured warning if the user needs it to read a result, or add it to
    ``OPERATIONAL`` with the reason it is not."""
    expected = {key: count for key, (count, _) in OPERATIONAL.items()}
    assert _log_sites() == expected


def test_the_inventory_counts_sixty_two_sites_before_and_thirty_eight_after():
    """62 sites were inventoried; 24 became structured warnings."""
    assert sum(count for count, _ in OPERATIONAL.values()) == 38
    converted_sites = sum(
        len([site for site in sites if site[0] == CONVERTED[code][1]])
        for code, sites in _emitted_codes().items()
        if code in CONVERTED and code != "REDUCTION_FACTOR_UNDEFINED"
    )
    # REDUCTION_FACTOR_UNDEFINED existed before (wp1b) and gained one site.
    assert converted_sites + 1 == 24


@pytest.mark.parametrize("code", sorted(CONVERTED))
def test_every_converted_code_is_raised_in_its_module_and_documented_there(code):
    category, module = CONVERTED[code]
    sites = [site for site in _emitted_codes().get(code, []) if site[0] == module]
    assert sites, f"{code} is not raised in {module}"
    if code == "REDUCTION_FACTOR_UNDEFINED":
        # Existing code (wp1b); the site converted here is the failed solve.
        sites = [site for site in sites if site[1] == "compute_reduction_factors"]
        assert sites
    source = (SRC / module).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for _, function_name in sites:
        function = next(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        )
        assert code in (ast.get_docstring(function) or ""), (code, function_name)
    assert issubclass(category, GroundinsightWarning)


def test_codes_are_upper_snake_and_converted_codes_live_in_one_module():
    codes = _emitted_codes()
    for code in codes:
        assert re.fullmatch(r"[A-Z][A-Z0-9]*(_[A-Z0-9]+)*", code), code
    for code, (_, module) in CONVERTED.items():
        if code == "REDUCTION_FACTOR_UNDEFINED":
            continue
        assert {site[0] for site in codes[code]} == {module}, code


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _silenced():
    """Logging switched off and every warning ignored: the records must still
    reach the collection sink and ``Result.diagnostics``."""
    logger = logging.getLogger("groundinsight")
    previous = (logger.disabled, logger.level)
    logger.disabled = True
    context = warnings.catch_warnings()
    context.__enter__()
    warnings.simplefilter("ignore")

    def restore():
        context.__exit__(None, None, None)
        logger.disabled, logger.level = previous[0], previous[1]

    return restore


def _records(code, records):
    return [record for record in records if record.code == code]


def _bus_type(name, formula, **extra):
    return gi.BusType(name=name, system_type="MV", voltage_level=20.0,
                      impedance_formula=formula, **extra)


def _cable(name="cable", **extra):
    return gi.BranchType(
        name=name, grounding_conductor=True,
        self_impedance_formula="(0.3+0.6*j*f/50)*l",
        mutual_impedance_formula="(0.05+0.55*j*f/50)*l", **extra,
    )


# --------------------------------------------------------------------------
# run_fault: the records land in Result.diagnostics
# --------------------------------------------------------------------------


def _ring_in_paths_mode():
    net = gi.create_network("ring", frequencies=[50.0])
    for bus in ("B1", "B2", "B3"):
        gi.create_bus(bus, _bus_type(f"bt{bus}", "1 + 0.02*j*f"), 100.0, network=net)
    for a, b in (("B1", "B2"), ("B2", "B3"), ("B1", "B3")):
        gi.create_branch(f"L{a}{b}", _cable(), a, b, 1.0, network=net)
    gi.create_source("src", "B1", {50.0: 1000.0}, network=net)
    gi.create_fault("F1", "B3", {50.0: 1.0}, network=net)
    return net


def test_parallel_coefficient_default_is_a_structured_diagnostic():
    """GUI4: the legacy path mode on a ring with default coefficients."""
    net = _ring_in_paths_mode()
    restore = _silenced()
    try:
        result = gi.run_fault(net, "F1", phase_current_mode="paths")
    finally:
        restore()
    records = _records("PARALLEL_COEFFICIENT_DEFAULT", result.diagnostics)
    assert len(records) == 1
    assert records[0].elements == [("source", "src")]
    assert records[0].category == "ModelAssumptionWarning"
    with pytest.warns(ModelAssumptionWarning, match="parallel_coefficient"):
        gi.run_fault(_ring_in_paths_mode(), "F1", phase_current_mode="paths")
    # The default mode splits the ring from the topology: nothing to say.
    clean = gi.run_fault(_ring_in_paths_mode(), "F1")
    assert not _records("PARALLEL_COEFFICIENT_DEFAULT", clean.diagnostics)


def _spread_chain():
    """Three electrodes of very different impedance angle on a short chain:
    the electrode currents spread in angle (found by a seeded search)."""
    net = gi.create_network("spread", frequencies=[50.0])
    for bus, z in (("N0", "(20.0-20.0*j)"), ("N1", "(20.0+1.0*j)"), ("N2", "(5.0-20.0*j)")):
        gi.create_bus(bus, _bus_type(f"bt{bus}", z), 100.0, network=net)
    cable = gi.BranchType(name="c", grounding_conductor=True,
                          self_impedance_formula="(0.3+0.6*j)*l",
                          mutual_impedance_formula="(0.05+0.55*j)*l")
    gi.create_branch("L0", cable, "N0", "N1", 0.3, network=net)
    gi.create_branch("L1", cable, "N1", "N2", 0.3, network=net)
    gi.create_source("Q", "N0", {50.0: 1000.0}, network=net)
    gi.create_fault("F", "N1", {50.0: 1.0}, network=net)
    return net


def test_a_spread_earth_group_is_a_structured_diagnostic():
    net = _spread_chain()
    restore = _silenced()
    try:
        result = gi.run_fault(net, "F")
    finally:
        restore()
    records = _records("EARTH_GROUP_SPREAD", result.diagnostics)
    assert len(records) == 1
    assert records[0].elements == [("fault", "F"), ("bus", "N1")]
    assert "50 Hz" in records[0].message
    # Second way: the separation from the independent reference solver --
    # |I_E| over half the summed electrode-current magnitudes -- is below the
    # threshold of 0.9 the package applies.
    rd = ref.reference_network(net, 50.0, "F")
    mapped = ref.to_package_frame(ref.solve_oneway(rd, thevenin_phase="norton"))
    i_earth, _ = ref.half_plane_split(mapped["ia"], rd["fault_bus"])
    separation = abs(i_earth) / (0.5 * sum(abs(v) for v in mapped["ia"].values()))
    assert separation < 0.9
    assert f"separation {separation:.3f}" in records[0].message


def test_a_well_separated_network_raises_no_spread_diagnostic():
    net = _ring_in_paths_mode()
    result = gi.run_fault(net, "F1")
    assert not _records("EARTH_GROUP_SPREAD", result.diagnostics)


def test_a_failed_solve_without_coupling_reports_the_undefined_factor(monkeypatch):
    """The no-coupling solve shares the factorisation of the solve with
    coupling and does not fail in practice; forced to fail, the EPR-based
    factor of that frequency is None and the result says why."""
    from groundinsight import electrical_network as en

    original = en.ElectricalNetwork._construct_vectors_no_mutual

    class Failing:
        def solve(self, rhs):
            raise RuntimeError("factor lost")

    def broken(self):
        original(self)
        self._lu_factors = {freq: Failing() for freq in self._lu_factors}

    monkeypatch.setattr(en.ElectricalNetwork, "_construct_vectors_no_mutual", broken)
    net = _ring_in_paths_mode()
    restore = _silenced()
    try:
        result = gi.run_fault(net, "F1")
    finally:
        restore()
    assert result.reduction_factor.value[50.0] is None
    records = _records("REDUCTION_FACTOR_UNDEFINED", result.diagnostics)
    assert records and "failed" in records[0].message
    assert records[0].elements == [("fault", "F1"), ("bus", "B3")]
    # The EPR is not affected: same as an unbroken run.
    monkeypatch.undo()
    reference = gi.run_fault(_ring_in_paths_mode(), "F1")
    assert [b.uepr for b in result.buses] == [b.uepr for b in reference.buses]


# --------------------------------------------------------------------------
# short-circuit characteristics and thermal checks
# --------------------------------------------------------------------------


def _sc_network():
    net = gi.create_network("sc", frequencies=[50.0])
    bus_type = _bus_type("bt", "0.5", earthing_conductor_material="Cu",
                         earthing_conductor_cross_section_mm2=50.0,
                         electrode_material="Cu")  # cross-section missing
    for bus in ("S", "A", "F"):
        gi.create_bus(bus, bus_type, 100.0, network=net)
    half = _cable("half", conductor_material="Cu")  # cross-section missing
    gi.create_branch("SA", half, "S", "A", 1.0, network=net)
    gi.create_branch("AF", _cable(), "A", "F", 1.0, network=net)
    gi.create_source("Q1", "S", {50.0: 1000.0}, network=net, r_to_x=0.1)
    gi.create_source("Q2", "S", {50.0: 1000.0}, network=net, r_to_x=0.3)
    gi.create_source("Q3", "A", {50.0: 2000.0}, network=net)  # no IEC data
    gi.create_fault("F1", "F", {50.0: 1.0}, network=net, t_k_s=0.5)
    gi.run_fault(net, "F1")
    return net


def test_short_circuit_aggregation_reports_mixed_and_missing_data():
    net = _sc_network()
    with collecting() as records:
        data = gi.resolve_fault_sc_characteristics(net, "F1")
    mixed = _records("SC_KAPPA_MIXED", records)
    assert len(mixed) == 1 and mixed[0].category == "ModelAssumptionWarning"
    assert mixed[0].elements == [("source", "Q1"), ("source", "Q2"), ("fault", "F1")]
    missing = _records("SC_DATA_INCOMPLETE", records)
    assert len(missing) == 1 and missing[0].category == "InputWarning"
    assert missing[0].elements == [("source", "Q3"), ("fault", "F1")]
    # Second way for the share named in the message: 2000 A of 4000 A.
    assert "50 %" in missing[0].message
    # And the kappa it is about: the current-weighted mean of the closed forms.
    kappas = [1.02 + 0.98 * math.exp(-3 * rx) for rx in (0.1, 0.3)]
    assert data.kappa == pytest.approx(sum(kappas) / 2, rel=1e-12)


def test_half_declared_thermal_data_is_a_structured_diagnostic():
    net = _sc_network()
    with collecting() as records:
        rows = gi.check_conductor_limits(net, "F1", kappa=1.8)
    branch_records = _records("THERMAL_DATA_INCOMPLETE", records)
    assert len(branch_records) == 1
    assert branch_records[0].elements == [("branch", "SA"), ("fault", "F1")]
    assert rows.filter(pl.col("branch_name") == "SA")["within_limit"].to_list() == [None]
    with collecting() as records:
        gi.check_node_limits(net, "F1", kappa=1.8)
    node_records = _records("THERMAL_DATA_INCOMPLETE", records)
    assert len(node_records) == 1
    assert node_records[0].elements == [("bus", "S"), ("bus", "A"), ("bus", "F"),
                                        ("fault", "F1")]


# --------------------------------------------------------------------------
# pandapower importer and short-circuit reader
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pp():
    return pytest.importorskip("pandapower")


def _defaults(voltage_level=20.0, **overrides):
    values = dict(
        default_bus_type=_bus_type("BT", "1.0").model_copy(
            update={"voltage_level": voltage_level}
        ),
        default_branch_type=_cable("BRT"),
        frequencies=[50.0],
        rho=100.0,
    )
    values.update(overrides)
    return gi.ImportDefaults(**values)


def _pp_chain(pp, names=("A", "B"), length=1.0, vn_kv=20.0):
    net = pp.create_empty_network()
    for name in names:
        pp.create_bus(net, vn_kv=vn_kv, name=name)
    for i in range(len(names) - 1):
        pp.create_line_from_parameters(
            net, from_bus=i, to_bus=i + 1, length_km=length, r_ohm_per_km=0.25,
            x_ohm_per_km=0.6, c_nf_per_km=0.0, max_i_ka=0.4, name=f"L{i}",
        )
    return net


def test_import_length_fallback_and_skipped_rows_are_structured(pp):
    net = _pp_chain(pp, names=("A", "B", "C"))
    net.line.at[0, "length_km"] = float("nan")
    net.bus.at[2, "vn_kv"] = None
    pp.create_line_from_parameters(net, from_bus=1, to_bus=1, length_km=1.0,
                                   r_ohm_per_km=0.1, x_ohm_per_km=0.1,
                                   c_nf_per_km=0.0, max_i_ka=1.0, name="loop")
    with collecting() as records:
        network = gi.from_pandapower(net, defaults=_defaults(), voltage_level_kV=20.0)
    assert [r.elements for r in _records("IMPORT_LENGTH_DEFAULT", records)] == [
        [("branch", "L0")]
    ]
    assert network.branches["L0"].length == pytest.approx(1.0)
    assert [r.elements for r in _records("IMPORT_BUS_SKIPPED", records)] == [[("bus", "C")]]
    assert "C" not in network.buses
    loops = _records("IMPORT_LINE_SKIPPED", records)
    assert [r.elements for r in loops] == [[("branch", "loop")]]
    assert all(r.category == "InputWarning" for r in records)


def test_import_preview_names_a_line_of_zero_length(pp):
    net = _pp_chain(pp)
    net.line.at[0, "length_km"] = 0.0
    with collecting() as records:
        frame = gi.preview_pandapower_import(net, voltage_level_kV=20.0)
    assert [r.elements for r in _records("IMPORT_LINE_SKIPPED", records)] == [
        [("branch", "L0")]
    ]
    assert frame.filter(pl.col("kind") == "line")["status"].to_list() == ["skip"]


def test_import_reports_switches_transformers_level_and_emptiness(pp):
    net = _pp_chain(pp, names=("A", "B", "C"))
    pp.create_bus(net, vn_kv=20.0, name="D")
    pp.create_switch(net, bus=2, element=3, et="b", closed=True, name="cd_bond")
    hv = pp.create_bus(net, vn_kv=110.0, name="HV")
    pp.create_transformer_from_parameters(
        net, hv, 0, sn_mva=40, vn_hv_kv=110, vn_lv_kv=20, vkr_percent=0.5,
        vk_percent=10, pfe_kw=0, i0_percent=0,
    )
    with collecting() as records:
        gi.from_pandapower(net, defaults=_defaults(voltage_level=0.4),
                           voltage_level_kV=20.0)
    switch = _records("IMPORT_SWITCH_NOT_IMPORTED", records)
    assert [r.elements for r in switch] == [[("bus", "C"), ("bus", "D")]]
    assert len(_records("IMPORT_ELEMENT_NOT_IMPORTED", records)) == 1
    level = _records("IMPORT_VOLTAGE_LEVEL_MISMATCH", records)
    assert [r.elements for r in level] == [[("bus_type", "BT")]]
    with collecting() as records:
        empty = gi.from_pandapower(net, defaults=_defaults(), voltage_level_kV=0.4)
    assert len(empty.buses) == 0
    assert [r.elements for r in _records("IMPORT_EMPTY", records)] == [
        [("network", empty.name)]
    ]


def _sc_frame(rows):
    """A short-circuit result frame in the schema of read_shortcircuit_results."""
    from groundinsight.io.pandapower_sc import _SC_SCHEMA

    full = []
    for row in rows:
        record = {column: None for column in _SC_SCHEMA}
        record.update(row)
        full.append(record)
    return pl.DataFrame(full, schema=_SC_SCHEMA)


def _sc_target():
    net = gi.create_network("target", frequencies=[50.0])
    for bus in ("HV", "MV"):
        gi.create_bus(bus, _bus_type(f"bt{bus}", "0.5"), 100.0, network=net)
    gi.create_branch("L", _cable(), "HV", "MV", 1.0, network=net)
    gi.create_source("Q1", "HV", {50.0: 0.0}, network=net)
    gi.create_source("Q2", "HV", {50.0: 0.0}, network=net)
    gi.create_voltage_source("V", "HV", voltage={50.0: 1000.0},
                             source_impedance={50.0: 1 + 2j}, network=net)
    gi.create_fault("F1", "MV", {50.0: 1.0}, network=net, t_k_s=3.0)
    return net


def test_applying_short_circuit_results_reports_every_decision():
    net = _sc_target()
    frame = _sc_frame([
        {"bus_name": "MV", "pp_bus_index": 1, "i_k_a": 4000.0, "kappa": 1.7,
         "r_to_x": 0.1, "t_k_s": 0.4},
        {"bus_name": "MV", "pp_bus_index": 1, "i_k_a": 3000.0, "kappa": 1.6,
         "r_to_x": 0.2, "t_k_s": 0.4},
    ])
    with collecting() as records:
        gi.apply_shortcircuit_characteristics(net, frame, "F1", set_source_values=True)
    assert [r.elements for r in _records("SC_ROW_AMBIGUOUS", records)] == [
        [("fault", "F1"), ("bus", "MV")]
    ]
    split = _records("SC_SPLIT_EQUAL", records)
    assert len(split) == 1 and ("fault", "F1") in split[0].elements
    assert [r.elements for r in _records("SC_VOLTAGE_SOURCE_SKIPPED", records)] == [
        [("source", "V")]
    ]
    replaced = _records("SC_VALUE_REPLACED", records)
    assert [r.elements for r in replaced] == [[("fault", "F1")]]
    assert "3.0" in replaced[0].message and "0.4" in replaced[0].message
    assert net.faults["F1"].t_k_s == pytest.approx(0.4)
    assert all(r.category == "InputWarning" for r in records)


def test_reading_short_circuit_results_reports_kappa_problems():
    from groundinsight.io.pandapower_sc import _kappa_for_row

    with collecting() as records:
        kappa, origin = _kappa_for_row(1000.0, 3.0, 0.1, "MV")  # ip/ikss -> kappa 2.12
    assert origin == "iec_closed_form"
    # Second way: the closed form for R/X = 0.1.
    assert kappa == pytest.approx(1.02 + 0.98 * math.exp(-0.3), rel=1e-12)
    assert [r.elements for r in _records("SC_KAPPA_OUT_OF_RANGE", records)] == [
        [("bus", "MV")]
    ]


def test_a_bus_without_any_kappa_data_is_reported(pp):
    """A result row with I_k'' but neither ip nor a usable R/X."""
    import pandas as pd

    net = pp.create_empty_network()
    pp.create_bus(net, vn_kv=20.0, name="A")
    pp.create_bus(net, vn_kv=20.0, name="B")
    net.res_bus_sc = pd.DataFrame(
        {"ikss_ka": [4.0, 3.0], "rk_ohm": [0.5, float("nan")],
         "xk_ohm": [2.0, float("nan")]},
        index=[0, 1],
    )
    with collecting() as records:
        frame = gi.read_shortcircuit_results(net)
    unavailable = frame.filter(pl.col("kappa_origin") == "unavailable")["bus_name"]
    assert unavailable.to_list() == ["B"]
    assert [r.elements for r in _records("SC_KAPPA_MISSING", records)] == [[("bus", "B")]]


# --------------------------------------------------------------------------
# transient solvers
# --------------------------------------------------------------------------


def _transient_network(*, rlc_branch=True, resistive_with_mutual=False,
                       isolated_source=False, voltage_source=False):
    net = gi.create_network("tr", frequencies=[50.0])
    bus_type = _bus_type("bt", "0.5", R_formula="0.5", L_formula="0", C_formula="0")
    for bus in ("S", "A", "F"):
        gi.create_bus(bus, bus_type, 100.0, network=net)
    with_rl = _cable("rl", R_self_formula="0.3*l", L_self_formula="0.002*l",
                     R_mutual_formula="0.05*l", M_mutual_formula="0.0017*l")
    gi.create_branch("SA", with_rl, "S", "A", 1.0, network=net)
    if rlc_branch:
        gi.create_branch("AF", with_rl, "A", "F", 1.0, network=net)
    elif resistive_with_mutual:
        resistive = _cable("r", R_self_formula="0.3*l", L_self_formula="0*l",
                           R_mutual_formula="0.05*l", M_mutual_formula="0.0017*l")
        gi.create_branch("AF", resistive, "A", "F", 1.0, network=net)
    else:
        gi.create_branch("AF", _cable("plain"), "A", "F", 1.0, network=net)
    gi.create_source("Q", "S", {50.0: 1000.0}, network=net)
    if isolated_source:
        gi.create_bus("X", bus_type, 100.0, network=net)
        gi.create_source("QX", "X", {50.0: 100.0}, network=net)
    if voltage_source:
        gi.create_voltage_source("V", "S", voltage={50.0: 1000.0},
                                 source_impedance={50.0: 1 + 2j}, network=net)
    gi.create_fault("F1", "F", {50.0: 1.0}, network=net)
    return net


def _study(net, sources):
    study = gi.TransientStudy(net, "F1")
    for name in sources:
        study.set_source_waveform(
            name, gi.waveforms.sinusoidal_with_dc_offset(1000.0, 50.0)
        )
    study.set_observation(buses=["F"])
    return study


def test_an_unconnected_transient_source_is_reported():
    net = _transient_network(isolated_source=True)
    with collecting() as records, warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _study(net, ["Q", "QX"]).solve(t_end=0.04, dt=1e-4)
    assert [r.elements for r in _records("TRANSIENT_SOURCE_NOT_CONNECTED", records)] == [
        [("source", "QX")]
    ]


def test_state_space_reports_skipped_branches_and_ignored_coupling():
    with collecting() as records, warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _study(_transient_network(rlc_branch=False), ["Q"]).solve(
            t_end=0.02, dt=1e-4, solver="state_space")
    skipped = _records("TRANSIENT_BRANCH_SKIPPED", records)
    assert [r.elements for r in skipped] == [[("branch", "AF")]]
    with collecting() as records, warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _study(_transient_network(rlc_branch=False, resistive_with_mutual=True),
               ["Q"]).solve(t_end=0.02, dt=1e-4, solver="state_space")
    ignored = _records("TRANSIENT_RESISTIVE_COUPLING_IGNORED", records)
    assert [r.elements for r in ignored] == [[("branch", "AF")]]
    assert ignored[0].category == "TransientModelWarning"


def test_state_space_reports_the_coupling_it_skips_for_voltage_sources():
    net = _transient_network(voltage_source=True)
    with collecting() as records, warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _study(net, ["Q", "V"]).solve(t_end=0.02, dt=1e-4, solver="state_space")
    skipped = _records("TRANSIENT_COUPLING_SKIPPED", records)
    assert [r.elements for r in skipped] == [[("source", "V")]]


def test_the_service_page_lists_every_code_the_package_raises():
    """The registry on ``docs/api/service.md`` is complete and has no extras:
    every ``emit(..., code=...)`` of the package plus the default codes of the
    three warning classes that predate the structured diagnostics."""
    page = SRC.parents[1] / "docs" / "api" / "service.md"
    text = page.read_text(encoding="utf-8")
    section = text.split("## Diagnostic codes", 1)[1].split("\n## ", 1)[0]
    documented = re.findall(r"^\| `([A-Z_]+)` \|", section, re.M)
    expected = set(_emitted_codes()) | {"DC_LIMIT", "FREQUENCY_ORDER", "TRANSIENT_MODEL"}
    assert sorted(documented) == sorted(expected)
    assert len(documented) == len(set(documented))
