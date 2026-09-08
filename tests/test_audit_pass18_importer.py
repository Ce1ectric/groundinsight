# tests/test_audit_pass18_importer.py

"""
Regression tests for audit pass 18, findings M10 and M20.

M10 -- the pandapower importer dropped ``net.switch``, ``net.trafo`` and
``net.impedance`` without a count, a log line or a preview row. A bus bonded
to the rest of the system only through a **closed** bus-to-bus switch then
appeared galvanically isolated and the solver reported 0 V for it, which
reads as "this station is safe" -- the one answer a grounding study must
never produce by accident. The line data (``r_ohm_per_km`` /
``x_ohm_per_km``) was ignored as well, so no imported branch carried a
``phase_impedance_formula`` and every imported mesh fell back to the
path-based proxy instead of the default ``phase_current_mode="auto"`` solve.
The bus-level filter compared ``vn_kv`` exactly, so rounding noise from a CSV
or DGS export reported the whole target level as a mismatch.

M20 -- ``ImportDefaults`` validated nothing: ``frequencies`` defaulted to the
empty list and ``rho`` accepted ``nan``, ``0`` and negative values.
"""

from __future__ import annotations

import math

import polars as pl
import pytest

import groundinsight as gi
from groundinsight.models.core_models import BranchType, BusType


pp = pytest.importorskip("pandapower")


def _bus_type(voltage_level: float = 20.0) -> BusType:
    return BusType(
        name="ImportedBus",
        system_type="Grounded",
        voltage_level=voltage_level,
        impedance_formula="rho * 0 + 1.0 + I * f * 0",
    )


def _branch_type() -> BranchType:
    return BranchType(
        name="ImportedCable",
        grounding_conductor=True,
        self_impedance_formula="(0.25 + I * f * 0.012) * l",
        mutual_impedance_formula="(0.05 + I * f * 0.010) * l",
    )


def _switch_type() -> BranchType:
    return BranchType(
        name="ImportedBond",
        description="closed bus-to-bus switch",
        grounding_conductor=True,
        self_impedance_formula="(0.01 + I * f * 1e-5) * l",
        mutual_impedance_formula="(0.0 + I * f * 0) * l",
    )


def _defaults(**overrides) -> gi.ImportDefaults:
    kwargs = dict(
        rho=100.0,
        frequencies=[50.0],
        default_bus_type=_bus_type(),
        default_branch_type=_branch_type(),
    )
    kwargs.update(overrides)
    return gi.ImportDefaults(**kwargs)


def _net_with_a_switch_only_bus():
    """The review repro: bus D hangs off bus C through a closed switch.

    ``A -- B -- C`` are lines; ``C == D`` is a closed bus-to-bus switch.
    """
    net = pp.create_empty_network(name="switch_repro")
    a = pp.create_bus(net, vn_kv=20.0, name="A")
    b = pp.create_bus(net, vn_kv=20.0, name="B")
    c = pp.create_bus(net, vn_kv=20.0, name="C")
    d = pp.create_bus(net, vn_kv=20.0, name="D")
    for from_bus, to_bus, label in ((a, b, "line_AB"), (b, c, "line_BC")):
        pp.create_line_from_parameters(
            net,
            from_bus=from_bus,
            to_bus=to_bus,
            length_km=1.0,
            r_ohm_per_km=0.16,
            x_ohm_per_km=0.11,
            c_nf_per_km=0.0,
            max_i_ka=0.4,
            name=label,
        )
    pp.create_switch(net, bus=c, element=d, et="b", closed=True, name="cd_bond")
    return net


# ---------------------------------------------------------------------------
# M10 -- switches
# ---------------------------------------------------------------------------


def test_a_closed_switch_is_reported_when_it_cannot_be_imported(caplog):
    net = _net_with_a_switch_only_bus()
    with caplog.at_level("WARNING", logger="groundinsight.io.pandapower_import"):
        network = gi.from_pandapower(
            net, defaults=_defaults(), voltage_level_kV=20.0
        )
    assert "closed bus-to-bus switch" in caplog.text
    assert "switch_branch_type" in caplog.text
    # Historical behaviour is preserved: the bond is still not invented.
    assert "D" in network.buses
    assert all(
        "D" not in (br.from_bus, br.to_bus) for br in network.branches.values()
    )


def test_a_closed_switch_becomes_a_branch_when_a_type_is_given():
    net = _net_with_a_switch_only_bus()
    defaults = _defaults(switch_branch_type=_switch_type(), switch_length_km=0.002)
    network = gi.from_pandapower(net, defaults=defaults, voltage_level_kV=20.0)

    bonds = [
        br
        for br in network.branches.values()
        if {br.from_bus, br.to_bus} == {"C", "D"}
    ]
    assert len(bonds) == 1
    assert bonds[0].type.name == "ImportedBond"
    assert bonds[0].length == pytest.approx(0.002)

    # And the bus is no longer a 0 V island.
    gi.create_source(
        name="s", bus="A", values={50.0: 1000.0 + 0.0j}, network=network
    )
    gi.create_fault(name="F", bus="C", scalings={50.0: 1.0}, network=network)
    gi.run_fault(network, "F")
    epr = {b.name: b.uepr for b in network.results["F"].buses}
    assert epr["D"] > 0.0


def test_an_open_switch_stays_out_and_is_named_as_such():
    net = _net_with_a_switch_only_bus()
    net.switch.loc[0, "closed"] = False
    defaults = _defaults(switch_branch_type=_switch_type())
    network = gi.from_pandapower(net, defaults=defaults, voltage_level_kV=20.0)
    assert not any(
        {br.from_bus, br.to_bus} == {"C", "D"} for br in network.branches.values()
    )

    preview = gi.preview_pandapower_import(
        net, voltage_level_kV=20.0, defaults=defaults
    )
    row = preview.filter(pl.col("kind") == "switch")
    assert row["status"].to_list() == ["skip"]
    assert row["reason"].to_list() == ["switch_open"]


def test_preview_lists_switches_trafos_and_impedances():
    net = _net_with_a_switch_only_bus()
    hv = pp.create_bus(net, vn_kv=110.0, name="HV")
    pp.create_transformer_from_parameters(
        net,
        hv_bus=hv,
        lv_bus=int(net.bus.index[0]),
        sn_mva=25.0,
        vn_hv_kv=110.0,
        vn_lv_kv=20.0,
        vkr_percent=0.5,
        vk_percent=12.0,
        pfe_kw=0.0,
        i0_percent=0.0,
        name="trafo_hv_mv",
    )

    preview = gi.preview_pandapower_import(net, voltage_level_kV=20.0)
    kinds = set(preview["kind"].to_list())
    assert {"bus", "line", "switch", "trafo"} <= kinds

    switch_rows = preview.filter(pl.col("kind") == "switch")
    assert switch_rows["status"].to_list() == ["skip"]
    assert switch_rows["reason"].to_list() == ["no_switch_branch_type"]
    assert switch_rows["from_bus"].to_list() == ["C"]
    assert switch_rows["to_bus"].to_list() == ["D"]

    trafo_rows = preview.filter(pl.col("kind") == "trafo")
    assert trafo_rows.height == 1
    assert trafo_rows["reason"][0] == "trafos_are_not_modelled"


def test_preview_marks_an_importable_switch_as_kept():
    net = _net_with_a_switch_only_bus()
    defaults = _defaults(switch_branch_type=_switch_type())
    preview = gi.preview_pandapower_import(
        net, voltage_level_kV=20.0, defaults=defaults
    )
    row = preview.filter(pl.col("kind") == "switch")
    assert row["status"].to_list() == ["keep"]
    assert row["length_km"][0] == pytest.approx(defaults.switch_length_km)


# ---------------------------------------------------------------------------
# M10 -- phase impedance and the voltage-level check
# ---------------------------------------------------------------------------


def test_phase_impedance_is_derived_from_the_line_data():
    net = _net_with_a_switch_only_bus()
    plain = gi.from_pandapower(net, defaults=_defaults(), voltage_level_kV=20.0)
    assert all(
        br.type.phase_impedance_formula is None for br in plain.branches.values()
    )

    derived = gi.from_pandapower(
        net,
        defaults=_defaults(derive_phase_impedance=True),
        voltage_level_kV=20.0,
    )
    formulas = {
        br.type.phase_impedance_formula for br in derived.branches.values()
    }
    assert len(formulas) == 1
    formula = formulas.pop()
    assert formula is not None
    # Both lines share r/x, so exactly one derived type is created.
    assert len({br.type.name for br in derived.branches.values()}) == 1
    # And the values are the pandapower ones, evaluated at the branch length.
    branch = derived.branches["line_AB"]
    assert branch.phase_impedance[50.0].real == pytest.approx(0.16 * branch.length)
    assert branch.phase_impedance[50.0].imag == pytest.approx(0.11 * branch.length)
    # The earth-return formulas are not invented.
    assert (
        branch.type.self_impedance_formula
        == _branch_type().self_impedance_formula
    )


def test_a_voltage_level_mismatch_between_type_and_argument_is_reported(caplog):
    net = _net_with_a_switch_only_bus()
    defaults = _defaults(default_bus_type=_bus_type(voltage_level=0.4))
    with caplog.at_level("WARNING", logger="groundinsight.io.pandapower_import"):
        gi.from_pandapower(net, defaults=defaults, voltage_level_kV=20.0)
    assert "voltage_level" in caplog.text
    assert "0.4" in caplog.text and "20" in caplog.text


def test_rounding_noise_in_vn_kv_no_longer_empties_the_import():
    """A CSV/DGS export rarely stores exactly 20.0."""
    net = _net_with_a_switch_only_bus()
    net.bus.loc[:, "vn_kv"] = 20.0 + 4e-15
    network = gi.from_pandapower(net, defaults=_defaults(), voltage_level_kV=20.0)
    assert set(network.buses) == {"A", "B", "C", "D"}


# ---------------------------------------------------------------------------
# M20 -- ImportDefaults validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_rho", [0.0, -1.0, float("nan"), float("inf")])
def test_import_defaults_reject_an_unusable_rho(bad_rho):
    with pytest.raises(ValueError, match="rho"):
        _defaults(rho=bad_rho)


@pytest.mark.parametrize(
    "bad_frequencies", [[], [50.0, 50.0], [-1.0], [float("nan")]]
)
def test_import_defaults_reject_an_unusable_frequency_list(bad_frequencies):
    with pytest.raises(ValueError, match="frequencies"):
        _defaults(frequencies=bad_frequencies)


def test_import_defaults_reject_a_zero_switch_length():
    with pytest.raises(ValueError, match="switch_length_km"):
        _defaults(switch_branch_type=_switch_type(), switch_length_km=0.0)


def test_import_defaults_still_accept_the_documented_shape():
    defaults = _defaults()
    assert defaults.frequencies == [50.0]
    assert defaults.switch_branch_type is None
    assert defaults.derive_phase_impedance is False
    assert math.isclose(defaults.switch_length_km, 0.001)
