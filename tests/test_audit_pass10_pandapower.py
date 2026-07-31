# tests/test_audit_pass10_pandapower.py

"""
Regression tests for the tenth audit-pass bug-fix batch (2026-07-28).

Covers the three confirmed defects of the pandapower interoperability layer:

A. ``preview_pandapower_import`` crashed with a ``polars.ComputeError`` on
   every net with >= 100 buses. The row list starts with the bus rows,
   whose ``from_bus`` / ``to_bus`` / ``length_km`` are all ``None``, so
   polars' default ``infer_schema_length=100`` inferred ``Null`` for those
   columns and the first line row could no longer be appended. All three
   ``pl.DataFrame(list_of_dicts)`` call sites of the two modules now pass an
   explicit schema (``_PREVIEW_SCHEMA``, ``_SC_SCHEMA``, ``_REPORT_SCHEMA``).
B. ``apply_shortcircuit_characteristics`` overwrote ``Fault.t_k_s`` with
   pandapower's placeholder ``net._options["tk_s"] == 1.0`` and reset
   ``Fault.n_factor`` to ``1.0``, silently. Since ``I_adm = k * S / sqrt(t_k)``
   (IEC 60949), 3.0 s -> 1.0 s inflates the admissible current by
   ``sqrt(3) ~ 1.73`` — the non-conservative direction. A pandapower value is
   now adopted only when the caller genuinely asked for a thermal-equivalent
   current, and every overwrite is logged.
C. ``length_km`` of ``0.0`` or ``NaN`` silently became ``length = 1.0``.
   A missing length now warns loudly before falling back, and a zero or
   negative length is rejected with a clear ``ValueError`` (the preview lists
   the offending rows instead of raising).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import polars as pl
import pytest

import groundinsight as gi
from groundinsight.io.defaults import ImportDefaults
from groundinsight.io.pandapower_import import _PREVIEW_SCHEMA
from groundinsight.models.core_models import BranchType, BusType


IMPORT_LOGGER = "groundinsight.io.pandapower_import"
SC_LOGGER = "groundinsight.io.pandapower_sc"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pp_module():
    return pytest.importorskip("pandapower")


@pytest.fixture(scope="module")
def sc_module():
    pytest.importorskip("pandapower")
    return pytest.importorskip("pandapower.shortcircuit")


def _defaults_20kV() -> ImportDefaults:
    """Minimal :class:`ImportDefaults` for a 20 kV import at 50 Hz."""
    bt = BusType(
        name="BT",
        description="",
        system_type="Grounded",
        voltage_level=20.0,
        impedance_formula="rho * 0 + 1.0",
    )
    brt = BranchType(
        name="BRT",
        description="",
        grounding_conductor=True,
        self_impedance_formula="(rho * 0 + 0.25 + I * f * 0.012)*l",
        mutual_impedance_formula="(rho * 0 + 0.05 + I * f * 0.010)*l",
    )
    return ImportDefaults(
        default_bus_type=bt,
        default_branch_type=brt,
        frequencies=[50.0],
        rho=100.0,
    )


def _chain_net(pp, n_bus: int, *, length_km: float = 2.5):
    """Build a pandapower chain of ``n_bus`` 20 kV buses and ``n_bus - 1`` lines."""
    net = pp.create_empty_network()
    for i in range(n_bus):
        pp.create_bus(net, vn_kv=20.0, name=f"B{i}")
    for i in range(n_bus - 1):
        pp.create_line_from_parameters(
            net,
            from_bus=i,
            to_bus=i + 1,
            length_km=length_km,
            r_ohm_per_km=0.25,
            x_ohm_per_km=0.6,
            c_nf_per_km=0.0,
            max_i_ka=0.4,
            name=f"L{i}",
        )
    return net


def _two_bus_net(pp, length_km: Any):
    """Build a 2-bus / 1-line 20 kV net whose single line has ``length_km``."""
    net = pp.create_empty_network(name="jumper")
    pp.create_bus(net, vn_kv=20.0, name="A")
    pp.create_bus(net, vn_kv=20.0, name="B")
    pp.create_line_from_parameters(
        net,
        from_bus=0,
        to_bus=1,
        length_km=1.0,
        r_ohm_per_km=0.25,
        x_ohm_per_km=0.6,
        c_nf_per_km=0.0,
        max_i_ka=0.4,
        name="jumper",
    )
    # Written after creation: pandapower rejects some of these at create time.
    net.line.at[0, "length_km"] = length_km
    return net


def _sc_pp_net(pp):
    """Two 110 kV buses fed by an ext_grid, ready for ``calc_sc``."""
    net = pp.create_empty_network(sn_mva=100.0)
    hv = pp.create_bus(net, vn_kv=110.0, name="HV")
    mv = pp.create_bus(net, vn_kv=110.0, name="MV2")
    pp.create_ext_grid(
        net, hv, s_sc_max_mva=1000.0, rx_max=0.1, x0x_max=3.0, r0x0_max=0.1
    )
    pp.create_line_from_parameters(
        net,
        from_bus=hv,
        to_bus=mv,
        length_km=5.0,
        r_ohm_per_km=0.1,
        x_ohm_per_km=0.4,
        c_nf_per_km=0.0,
        max_i_ka=1.0,
        r0_ohm_per_km=0.3,
        x0_ohm_per_km=1.2,
        c0_nf_per_km=0.0,
    )
    return net


def _sc_gi_net(*, t_k_s: Optional[float] = 3.0, n_factor: float = 0.85):
    """A groundinsight twin of :func:`_sc_pp_net` with a pre-set fault."""
    net = gi.create_network(name="pass10_sc", frequencies=[50.0])
    bt = gi.BusType(
        name="b",
        system_type="s",
        voltage_level=110.0,
        impedance_formula="rho*0 + 0.5",
    )
    ct = gi.BranchType(
        name="c",
        grounding_conductor=True,
        self_impedance_formula="(rho*0 + 0.25)*l",
        mutual_impedance_formula="(rho*0 + 0.05)*l",
    )
    for name in ("HV", "MV2"):
        gi.create_bus(name=name, type=bt, network=net)
    gi.create_branch(
        name="HV-MV2", type=ct, from_bus="HV", to_bus="MV2", length=1.0, network=net
    )
    gi.create_source(name="src", bus="HV", values={50.0: 1000.0}, network=net)
    gi.create_fault(name="F", bus="MV2", scalings={50.0: 1.0}, network=net)
    net.faults["F"].t_k_s = t_k_s
    net.faults["F"].n_factor = n_factor
    return net


# ---------------------------------------------------------------------------
# A. preview_pandapower_import survives the 100-row schema-inference boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n_bus", [99, 100, 101])
def test_preview_survives_schema_inference_boundary(pp_module, n_bus):
    """Preview works on both sides of polars' ``infer_schema_length=100``.

    The bug was a hard ``ComputeError`` as soon as 100 bus rows preceded the
    first line row, i.e. from ``n_bus == 100`` upwards.
    """
    net = _chain_net(pp_module, n_bus)
    frame = gi.preview_pandapower_import(net, voltage_level_kV=20.0)

    # n_bus bus rows + (n_bus - 1) line rows.
    assert frame.height == 2 * n_bus - 1
    assert frame.width == len(_PREVIEW_SCHEMA)
    assert (frame["status"] == "keep").all()

    lines = frame.filter(pl.col("kind") == "line")
    assert lines.height == n_bus - 1
    assert lines["from_bus"].null_count() == 0
    assert lines["length_km"].to_list() == [2.5] * (n_bus - 1)


def test_preview_schema_is_pinned_and_size_independent(pp_module):
    """The dtypes are identical for a small and a large net, and never ``Null``.

    Inference used to make ``from_bus`` / ``to_bus`` / ``length_km``
    ``pl.Null`` on a bus-only net, so a downstream ``filter``/``join`` on the
    preview behaved differently depending on the net size.
    """
    small = gi.preview_pandapower_import(
        _chain_net(pp_module, 5), voltage_level_kV=20.0
    )
    large = gi.preview_pandapower_import(
        _chain_net(pp_module, 150), voltage_level_kV=20.0
    )

    assert dict(small.schema) == dict(_PREVIEW_SCHEMA)
    assert dict(large.schema) == dict(_PREVIEW_SCHEMA)
    assert pl.Null not in set(small.schema.values())


def test_preview_bus_only_net_keeps_line_columns_typed(pp_module):
    """A net without a single line still yields typed ``length_km``/``from_bus``.

    With inferred schemas these all-``None`` columns collapsed to ``pl.Null``.
    """
    net = pp_module.create_empty_network()
    for i in range(3):
        pp_module.create_bus(net, vn_kv=20.0, name=f"B{i}")

    frame = gi.preview_pandapower_import(net, voltage_level_kV=20.0)

    assert frame.height == 3
    assert frame.schema["length_km"] == pl.Float64
    assert frame.schema["from_bus"] == pl.Utf8
    assert frame["length_km"].null_count() == 3


def test_preview_large_reference_net_matches_import(pp_module):
    """One representative large reference net previews and imports consistently.

    ``case118`` has 118 buses, i.e. it sits above the inference boundary and
    used to fail outright.
    """
    networks = pytest.importorskip("pandapower.networks")
    net = networks.case118()

    frame = gi.preview_pandapower_import(net, voltage_level_kV=138.0)
    assert dict(frame.schema) == dict(_PREVIEW_SCHEMA)

    kept_buses = frame.filter(
        (pl.col("kind") == "bus") & (pl.col("status") == "keep")
    ).height
    kept_lines = frame.filter(
        (pl.col("kind") == "line") & (pl.col("status") == "keep")
    ).height
    assert kept_buses > 0 and kept_lines > 0

    imported = gi.from_pandapower(
        net, defaults=_defaults_20kV(), voltage_level_kV=138.0
    )
    assert len(imported.buses) == kept_buses
    assert len(imported.branches) == kept_lines


def test_shortcircuit_frame_schema_is_pinned(pp_module, sc_module):
    """``read_shortcircuit_results`` keeps unfilled columns numeric.

    A ``3ph`` case leaves ``r0_ohm`` / ``x0_ohm`` empty; without a pinned
    schema they came back as ``pl.Null`` and could not be arithmetically
    combined with the other columns.
    """
    net = _sc_pp_net(pp_module)
    sc_module.calc_sc(net, fault="3ph", case="max")

    frame = gi.read_shortcircuit_results(net)

    assert frame.schema["r0_ohm"] == pl.Float64
    assert frame.schema["x0_ohm"] == pl.Float64
    assert frame.schema["kappa_origin"] == pl.Utf8
    assert pl.Null not in set(frame.schema.values())


# ---------------------------------------------------------------------------
# B. Fault.t_k_s / Fault.n_factor are no longer silently overwritten
# ---------------------------------------------------------------------------


def test_placeholder_tk_s_does_not_overwrite_fault(pp_module, sc_module):
    """A ``calc_sc`` without ``ith``/``tk_s`` must leave the Fault untouched.

    pandapower always stores its signature default ``tk_s == 1.0`` in
    ``net._options``; adopting it turned a 3.0 s clearing time into 1.0 s and
    inflated the IEC 60949 admissible current by ``sqrt(3)``.
    """
    pp_net = _sc_pp_net(pp_module)
    sc_module.calc_sc(pp_net, fault="1ph", case="max")
    assert pp_net._options["tk_s"] == 1.0  # the placeholder is really there
    assert not pp_net._options.get("ith", False)

    net = _sc_gi_net(t_k_s=3.0, n_factor=0.85)
    report = gi.apply_shortcircuit_characteristics(net, pp_net, "F")

    assert net.faults["F"].t_k_s == pytest.approx(3.0)
    assert net.faults["F"].n_factor == pytest.approx(0.85)
    # The report shows what is in effect, not what pandapower offered.
    assert report["t_k_s"].to_list() == [pytest.approx(3.0)]
    assert report["n_factor"].to_list() == [pytest.approx(0.85)]


def test_placeholder_tk_s_is_not_reported_as_a_clearing_time(
    pp_module, sc_module
):
    """The result frame reports no ``t_k_s`` / ``I_th`` for a non-thermal run."""
    net = _sc_pp_net(pp_module)
    sc_module.calc_sc(net, fault="1ph", case="max")

    frame = gi.read_shortcircuit_results(net)

    assert frame["t_k_s"].to_list() == [None] * frame.height
    assert frame["m"].to_list() == [None] * frame.height
    assert frame["i_th_a"].to_list() == [None] * frame.height


def test_requested_tk_s_is_adopted_with_a_warning(pp_module, sc_module, caplog):
    """A genuine ``ith=True, tk_s=0.4`` run wins — loudly."""
    pp_net = _sc_pp_net(pp_module)
    sc_module.calc_sc(
        pp_net, fault="1ph", case="max", ip=True, ith=True, tk_s=0.4
    )

    net = _sc_gi_net(t_k_s=3.0, n_factor=0.85)
    with caplog.at_level(logging.WARNING, logger=SC_LOGGER):
        gi.apply_shortcircuit_characteristics(net, pp_net, "F")

    assert net.faults["F"].t_k_s == pytest.approx(0.4)

    messages = [rec.getMessage() for rec in caplog.records]
    hits = [m for m in messages if "t_k_s" in m and "3.0" in m and "0.4" in m]
    assert hits, f"no warning naming both values, got: {messages}"


def test_explicit_tk_s_argument_still_wins(pp_module, sc_module, caplog):
    """An explicit ``t_k_s=`` / ``n_factor=`` overrides both other sources."""
    pp_net = _sc_pp_net(pp_module)
    sc_module.calc_sc(
        pp_net, fault="1ph", case="max", ip=True, ith=True, tk_s=0.4
    )

    net = _sc_gi_net(t_k_s=3.0, n_factor=0.85)
    with caplog.at_level(logging.WARNING, logger=SC_LOGGER):
        report = gi.apply_shortcircuit_characteristics(
            net, pp_net, "F", t_k_s=0.2, n_factor=0.9
        )

    assert net.faults["F"].t_k_s == pytest.approx(0.2)
    assert net.faults["F"].n_factor == pytest.approx(0.9)
    assert report["t_k_s"].to_list() == [pytest.approx(0.2)]
    assert report["n_factor"].to_list() == [pytest.approx(0.9)]

    messages = [rec.getMessage() for rec in caplog.records]
    assert any("n_factor" in m and "0.85" in m and "0.9" in m for m in messages)


def test_n_factor_survives_a_thermal_run_without_warning_noise(
    pp_module, sc_module, caplog
):
    """``n_factor`` is not a pandapower quantity and must never be clobbered.

    ``read_shortcircuit_results`` only echoes its own ``n_factor`` argument
    default of ``1.0``; adopting that reset a user-set near-to-generator
    factor of 0.85. Here the clearing time genuinely matches, so a correct
    implementation writes nothing and therefore warns about nothing.
    """
    pp_net = _sc_pp_net(pp_module)
    sc_module.calc_sc(
        pp_net, fault="1ph", case="max", ip=True, ith=True, tk_s=0.4
    )

    net = _sc_gi_net(t_k_s=0.4, n_factor=0.85)
    with caplog.at_level(logging.WARNING, logger=SC_LOGGER):
        gi.apply_shortcircuit_characteristics(net, pp_net, "F")

    assert net.faults["F"].t_k_s == pytest.approx(0.4)
    assert net.faults["F"].n_factor == pytest.approx(0.85)

    messages = [rec.getMessage() for rec in caplog.records]
    assert not [m for m in messages if "replaced by the short-circuit" in m]


# ---------------------------------------------------------------------------
# C. length_km = 0 / NaN / negative
# ---------------------------------------------------------------------------


def test_positive_length_is_preserved_verbatim(pp_module):
    """The documented ``length = length_km`` contract holds exactly."""
    net = _two_bus_net(pp_module, 2.5)
    imported = gi.from_pandapower(
        net, defaults=_defaults_20kV(), voltage_level_kV=20.0
    )

    branch = imported.branches["jumper"]
    assert branch.length == pytest.approx(2.5)
    impedance = branch.self_impedance[50.0]
    assert impedance.real == pytest.approx(0.25 * 2.5)
    assert impedance.imag == pytest.approx(0.012 * 50.0 * 2.5)


def test_zero_length_line_is_rejected(pp_module):
    """A zero-length line aborts the import instead of becoming a 1 km branch.

    ``ElectricalNetwork._build_admittance_matrices`` drops a branch whose
    self impedance is exactly zero, so a preserved 0 km jumper would silently
    act as an open circuit rather than as the short it represents.
    """
    net = _two_bus_net(pp_module, 0.0)

    with pytest.raises(ValueError) as excinfo:
        gi.from_pandapower(net, defaults=_defaults_20kV(), voltage_level_kV=20.0)

    message = str(excinfo.value)
    assert "length_km" in message
    assert "jumper" in message


def test_negative_length_line_is_rejected(pp_module):
    """A negative length would flip the sign of the branch impedance."""
    net = _two_bus_net(pp_module, -2.5)

    with pytest.raises(ValueError) as excinfo:
        gi.from_pandapower(net, defaults=_defaults_20kV(), voltage_level_kV=20.0)

    assert "length_km" in str(excinfo.value)


@pytest.mark.parametrize(
    "length_km, reason",
    [(0.0, "zero_length"), (-2.5, "negative_length")],
)
def test_preview_reports_unusable_lengths_without_raising(
    pp_module, length_km, reason
):
    """The preview stays total: it enumerates the offenders, it does not raise."""
    net = _two_bus_net(pp_module, length_km)

    frame = gi.preview_pandapower_import(net, voltage_level_kV=20.0)

    line = frame.filter(pl.col("kind") == "line")
    assert line.height == 1
    assert line["status"].to_list() == ["skip"]
    assert line["reason"].to_list() == [reason]
    assert line["length_km"].to_list() == [pytest.approx(length_km)]


def test_missing_length_warns_before_falling_back(pp_module, caplog):
    """A ``NaN`` length still imports, but only with a loud warning.

    It used to be substituted by 1.0 km without a trace, which silently
    rescales every self and mutual impedance of the branch.
    """
    net = _two_bus_net(pp_module, float("nan"))

    with caplog.at_level(logging.WARNING, logger=IMPORT_LOGGER):
        imported = gi.from_pandapower(
            net, defaults=_defaults_20kV(), voltage_level_kV=20.0
        )

    assert imported.branches["jumper"].length == pytest.approx(1.0)

    messages = [rec.getMessage() for rec in caplog.records]
    hits = [m for m in messages if "length_km" in m and "jumper" in m]
    assert hits, f"no fallback warning emitted, got: {messages}"


def test_preview_agrees_with_the_commit_on_the_fallback(pp_module, caplog):
    """For a kept line the preview shows the length the import will use.

    Preview and commit must never disagree, so the ``NaN`` fallback appears
    in the preview as well — and warns there too.
    """
    net = _two_bus_net(pp_module, float("nan"))

    with caplog.at_level(logging.WARNING, logger=IMPORT_LOGGER):
        frame = gi.preview_pandapower_import(net, voltage_level_kV=20.0)

    line = frame.filter(pl.col("kind") == "line")
    assert line["status"].to_list() == ["keep"]
    assert line["length_km"].to_list() == [pytest.approx(1.0)]

    imported = gi.from_pandapower(
        net, defaults=_defaults_20kV(), voltage_level_kV=20.0
    )
    assert imported.branches["jumper"].length == pytest.approx(
        line["length_km"][0]
    )
    assert any("length_km" in rec.getMessage() for rec in caplog.records)
