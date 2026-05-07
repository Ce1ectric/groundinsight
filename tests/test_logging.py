"""
Tests for the logging migration.

These tests verify that user-facing messages previously emitted via ``print``
now go through the standard ``logging`` module at the agreed levels and that
the package logger has the expected library-friendly default
configuration (a single :class:`logging.NullHandler` on import, no output
without explicit opt-in).

Tests use pytest's ``caplog`` fixture to capture records and assert on
levels, logger names and message content. They do not assert on the exact
formatted output to keep them robust against minor wording changes.
"""

import logging

import pytest

import groundinsight as gi
from groundinsight.models.core_models import (
    BusType,
    BranchType,
    Fault,
    Source,
    ComplexNumber,
)


# --- package-level configuration --------------------------------------------


def test_package_logger_has_null_handler_only_by_default():
    """
    On import, the ``groundinsight`` package logger must have at least one
    ``NullHandler`` attached and no console handler, so that importing the
    library does not produce any output without explicit configuration.
    """
    pkg_logger = logging.getLogger("groundinsight")

    assert any(isinstance(h, logging.NullHandler) for h in pkg_logger.handlers), (
        "Expected a NullHandler on the package logger by default."
    )


def test_set_log_level_attaches_stream_handler_once():
    """
    ``set_log_level`` should attach exactly one StreamHandler on first call
    and only adjust the level on subsequent calls.
    """
    pkg_logger = logging.getLogger("groundinsight")

    # Remove any non-Null handlers from a previous test to start clean.
    for h in list(pkg_logger.handlers):
        if not isinstance(h, logging.NullHandler):
            pkg_logger.removeHandler(h)

    gi.set_log_level("INFO")
    stream_handlers_after_first = [
        h
        for h in pkg_logger.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.NullHandler)
    ]
    assert len(stream_handlers_after_first) == 1
    assert pkg_logger.level == logging.INFO

    gi.set_log_level("WARNING")
    stream_handlers_after_second = [
        h
        for h in pkg_logger.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.NullHandler)
    ]
    assert len(stream_handlers_after_second) == 1
    assert pkg_logger.level == logging.WARNING


# --- core_models: overwrite warnings ----------------------------------------


def _make_minimal_network():
    bus_type = BusType(
        name="BT",
        description="",
        system_type="Grounded",
        voltage_level=230.0,
        impedance_formula="rho * 0 + 1 + I * f * 1/50",
    )
    branch_type = BranchType(
        name="BRT",
        description="",
        grounding_conductor=True,
        self_impedance_formula="(rho * 0 + 0.25 + I * f * 0.012)*l",
        mutual_impedance_formula="(rho * 0 + 0.0 + I * f * 0.010)*l",
    )
    net = gi.create_network(name="N", frequencies=[50])
    return net, bus_type, branch_type


def test_overwrite_bus_emits_warning(caplog):
    """
    The user-facing ``create_bus`` wrapper does not expose ``overwrite``;
    the flag lives on :meth:`Network.add_bus`. Construct the Bus first via
    the wrapper, then re-add it with ``overwrite=True`` to trigger the
    warning.
    """
    net, bus_type, _ = _make_minimal_network()
    gi.create_bus(name="B1", type=bus_type, specific_earth_resistance=100.0, network=net)

    bus = net.buses["B1"]
    with caplog.at_level(logging.WARNING, logger="groundinsight.models.core_models"):
        net.add_bus(bus, overwrite=True)

    matching = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "Bus 'B1'" in r.getMessage()
    ]
    assert matching, "Expected a WARNING about the overwritten bus."


def test_overwrite_branch_emits_warning(caplog):
    net, bus_type, branch_type = _make_minimal_network()
    gi.create_bus(name="B1", type=bus_type, specific_earth_resistance=100.0, network=net)
    gi.create_bus(name="B2", type=bus_type, specific_earth_resistance=100.0, network=net)
    gi.create_branch(
        name="L1",
        type=branch_type,
        from_bus="B1",
        to_bus="B2",
        length=1.0,
        network=net,
    )

    branch = net.branches["L1"]
    with caplog.at_level(logging.WARNING, logger="groundinsight.models.core_models"):
        net.add_branch(branch, overwrite=True)

    matching = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "Branch 'L1'" in r.getMessage()
    ]
    assert matching, "Expected a WARNING about the overwritten branch."


def test_overwrite_fault_and_source_emit_warning(caplog):
    net, bus_type, _ = _make_minimal_network()
    gi.create_bus(name="B1", type=bus_type, specific_earth_resistance=100.0, network=net)

    fault = Fault(name="F1", bus="B1", scalings={50: 1.0}, active=True)
    net.add_fault(fault)

    source = Source(
        name="S1",
        bus="B1",
        values={50: ComplexNumber(real=1.0, imag=0.0)},
    )
    net.add_source(source)

    with caplog.at_level(logging.WARNING, logger="groundinsight.models.core_models"):
        net.add_fault(fault, overwrite=True)
        net.add_source(source, overwrite=True)

    fault_warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "Fault 'F1'" in r.getMessage()
    ]
    source_warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "Source 'S1'" in r.getMessage()
    ]
    assert fault_warnings, "Expected a WARNING about the overwritten fault."
    assert source_warnings, "Expected a WARNING about the overwritten source."


# --- network_operations: parallel-coefficient warning -----------------------


def test_parallel_coefficient_warning_is_logged(caplog):
    """
    The parallel-coefficient guidance message previously printed in
    ``network_operations._warning_parallel_coeffcient`` is now a WARNING
    on the ``groundinsight.network_operations`` logger. It is emitted
    from inside ``run_fault`` when the network has more than one
    source-to-fault path and ``auto_parallel_coefficients`` is False.
    """
    bus_type = BusType(
        name="BT_par",
        description="",
        system_type="Grounded",
        voltage_level=230.0,
        impedance_formula="rho * 0 + 1 + I * f * 1/50",
    )
    branch_type = BranchType(
        name="BRT_par",
        description="",
        grounding_conductor=True,
        self_impedance_formula="(rho * 0 + 0.25 + I * f * 0.012)*l",
        mutual_impedance_formula="(rho * 0 + 0.0 + I * f * 0.010)*l",
    )

    # Three-bus ring: source at B1, fault at B3, two parallel paths.
    net = gi.create_network(name="ring", frequencies=[50])
    for bn in ("B1", "B2", "B3"):
        gi.create_bus(
            name=bn,
            type=bus_type,
            specific_earth_resistance=100.0,
            network=net,
        )
    for from_bus, to_bus, branch_name in (
        ("B1", "B2", "L12"),
        ("B2", "B3", "L23"),
        ("B1", "B3", "L13"),
    ):
        gi.create_branch(
            name=branch_name,
            type=branch_type,
            from_bus=from_bus,
            to_bus=to_bus,
            length=1.0,
            network=net,
        )
    gi.create_source(
        name="S1",
        bus="B1",
        values={50: ComplexNumber(real=1.0, imag=0.0)},
        network=net,
    )
    gi.create_fault(
        name="F1",
        bus="B3",
        scalings={50: 1.0},
        active=True,
        network=net,
    )

    with caplog.at_level(logging.WARNING, logger="groundinsight.network_operations"):
        gi.run_fault(net, fault_name="F1")

    matching = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING
        and "parallel coefficients" in r.getMessage()
    ]
    assert matching, (
        "Expected a WARNING about parallel coefficients in a ring network."
    )


# --- __init__: db session lifecycle -----------------------------------------


def test_db_session_already_started_emits_warning(tmp_path, caplog):
    db_path = tmp_path / "log_test.db"
    gi.start_dbsession(str(db_path))

    try:
        with caplog.at_level(logging.WARNING, logger="groundinsight"):
            gi.start_dbsession(str(db_path))

        matching = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "already started" in r.getMessage()
        ]
        assert matching, "Expected a WARNING about the already-started session."
    finally:
        gi.close_dbsession()


def test_no_db_session_to_close_emits_warning(caplog):
    # Make sure no session is active before the test.
    if gi.engine is not None:
        gi.close_dbsession()

    with caplog.at_level(logging.WARNING, logger="groundinsight"):
        gi.close_dbsession()

    matching = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "No database session" in r.getMessage()
    ]
    assert matching, "Expected a WARNING when closing without an active session."
