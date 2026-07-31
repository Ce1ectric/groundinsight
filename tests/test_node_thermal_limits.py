# tests/test_node_thermal_limits.py

"""
Tests for the node thermal-limit check (F4).

Covers the three layers the feature is built from:

* ``ResultBus.i_inj`` — the source-only nodal injection, i.e. the current
  the **earthing conductor** (*Erdungsleiter*) carries into the grounding
  system, as opposed to ``ResultBus.ia = u_EPR / Z_B``, the share the
  **earth electrode** (*Erder*) dissipates into the soil. EN 50522 /
  IEC 61936-1 size the two for different currents, so the solver has to
  keep them apart.
* The thermal fields on :class:`~groundinsight.models.core_models.BusType`
  for both elements, including their validation and their round-trip
  through JSON and SQLite.
* :func:`~groundinsight.analysis.thermal.check_node_limits`, which applies
  ``I_th = I_rms * current_split * sqrt(m + n)`` (IEC 60909-0) and compares
  it against ``I_adm = k * S / sqrt(t_k)`` (IEC 60949), one row per bus and
  element.
"""

from __future__ import annotations

import math

import pytest

import groundinsight as gi
from groundinsight.analysis.thermal import (
    CABLE_INSULATION_LIMITS,
    FINAL_TEMPERATURES,
    IEC60949_MATERIALS,
    final_temperature,
    iec60909_m,
    iec60949_k,
    kappa_from_r_to_x,
)
from groundinsight.models.core_models import ResultBus


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


def _bus_type(**overrides):
    """A bus type with both grounding elements declared."""
    params = dict(
        name="bt",
        system_type="Tower",
        voltage_level=20.0,
        impedance_formula="rho * 0.1",
        earthing_conductor_material="Cu",
        earthing_conductor_cross_section_mm2=50.0,
        earthing_conductor_theta_final_C=405.0,
        earthing_conductor_current_split=1.0,
        electrode_material="Steel",
        electrode_cross_section_mm2=95.0,
        electrode_current_split=0.5,
    )
    params.update(overrides)
    return gi.BusType(**params)


def _branch_type():
    return gi.BranchType(
        name="brt",
        grounding_conductor=True,
        self_impedance_formula="(0.25 + j*f*0.012) * l",
        mutual_impedance_formula="(0.0 + j*f*0.012) * l",
    )


def _chain(bus_type=None, name="chain"):
    """Three buses in a row, 1000 A fed in at b1, fault at b3."""
    bt = bus_type if bus_type is not None else _bus_type()
    net = gi.create_network(name=name, frequencies=[50.0])
    for b in ("b1", "b2", "b3"):
        gi.create_bus(name=b, type=bt, specific_earth_resistance=100, network=net)
    gi.create_branch(name="l12", type=_branch_type(), from_bus="b1", to_bus="b2",
                     length=1.0, network=net)
    gi.create_branch(name="l23", type=_branch_type(), from_bus="b2", to_bus="b3",
                     length=1.0, network=net)
    gi.create_source(name="src", bus="b1", values={50.0: 1000.0}, network=net)
    gi.create_fault(name="F", bus="b3", scalings={50.0: 1.0}, network=net)
    gi.run_fault(net, "F")
    return net


# ---------------------------------------------------------------------------
# ResultBus.i_inj — the earthing-conductor current
# ---------------------------------------------------------------------------


def test_i_inj_is_the_source_injection_not_the_electrode_current():
    net = _chain()
    by = {rb.name: rb for rb in net.results["F"].buses}

    # Source bus and fault bus carry the full 1000 A, with opposite sign.
    assert by["b1"].i_inj == pytest.approx(1000.0)
    assert by["b3"].i_inj == pytest.approx(1000.0)
    b1 = by["b1"].i_inj_freq[50.0]
    b3 = by["b3"].i_inj_freq[50.0]
    assert complex(b1.real, b1.imag) == pytest.approx(1000.0 + 0j)
    assert complex(b3.real, b3.imag) == pytest.approx(-1000.0 + 0j)

    # Every bus that is neither source nor fault carries nothing.
    assert by["b2"].i_inj == pytest.approx(0.0, abs=1e-9)

    # The electrode current is a small fraction of it — that is the whole
    # point of separating the two, and the classic sizing error.
    assert by["b3"].ia < by["b3"].i_inj / 5.0


def test_i_inj_excludes_the_mutual_norton_injections():
    """The mutual terms model a distributed EMF, not a lumped infeed."""
    import numpy as np

    from groundinsight.electrical_network import ElectricalNetwork

    net = _chain()
    en = ElectricalNetwork(net)
    i_vector = en.i_vectors[50.0]
    source_only = en.source_injections[50.0]

    # Same shape, materially different content: if the mutual terms leaked
    # into i_inj the earthing conductor would be sized for a current no
    # lumped conductor ever carries.
    assert i_vector.shape == source_only.shape
    assert np.max(np.abs(i_vector - source_only)) > 1.0


def test_i_inj_survives_the_result_json_roundtrip():
    net = _chain()
    reloaded = gi.Network.model_validate_json(net.model_dump_json())
    by = {rb.name: rb for rb in reloaded.results["F"].buses}
    assert by["b1"].i_inj == pytest.approx(1000.0)
    assert by["b1"].i_inj_freq[50.0].real == pytest.approx(1000.0)


def test_resultbus_defaults_keep_pre_f4_results_loadable():
    """Results stored before ``i_inj`` existed must still validate."""
    legacy = ResultBus.model_validate(
        {"name": "b1", "uepr": 1.0, "ia": 2.0, "uepr_freq": {}, "ia_freq": {}}
    )
    assert legacy.i_inj == 0.0
    assert legacy.i_inj_freq == {}


# ---------------------------------------------------------------------------
# BusType thermal fields: validation and persistence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["earthing_conductor_cross_section_mm2", "electrode_cross_section_mm2"],
)
@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_bustype_rejects_nonpositive_cross_section(field, bad):
    with pytest.raises(ValueError):
        _bus_type(**{field: bad})


@pytest.mark.parametrize(
    "field", ["earthing_conductor_current_split", "electrode_current_split"]
)
@pytest.mark.parametrize("bad", [0.0, -0.1, 1.5])
def test_bustype_rejects_current_split_outside_unit_interval(field, bad):
    """A split above 1 is not a split but an error; 0 or less is unphysical."""
    with pytest.raises(ValueError):
        _bus_type(**{field: bad})


@pytest.mark.parametrize("good", [0.25, 0.5, 1.0])
def test_bustype_accepts_current_split_inside_unit_interval(good):
    bt = _bus_type(electrode_current_split=good)
    assert bt.electrode_current_split == pytest.approx(good)


def test_bustype_thermal_fields_survive_json_roundtrip():
    net = _chain()
    reloaded = gi.Network.model_validate_json(net.model_dump_json())
    bt = reloaded.buses["b1"].type
    assert bt.earthing_conductor_material == "Cu"
    assert bt.earthing_conductor_cross_section_mm2 == 50.0
    assert bt.earthing_conductor_theta_final_C == 405.0
    assert bt.earthing_conductor_theta_initial_C == 20.0
    assert bt.electrode_material == "Steel"
    assert bt.electrode_cross_section_mm2 == 95.0
    assert bt.electrode_current_split == 0.5


def test_bustype_thermal_fields_survive_sqlite_roundtrip(tmp_path):
    bt = _bus_type()
    gi.start_dbsession(str(tmp_path / "node_thermal.db"))
    try:
        gi.save_bustype_to_db(bt, overwrite=True)
        loaded = gi.load_bustypes_from_db()["bt"]
    finally:
        gi.close_dbsession()
    assert loaded == bt


def test_legacy_bustype_row_without_thermal_columns_loads_with_defaults(tmp_path):
    """A row written before F4 has NULL in the new columns."""
    from groundinsight.models.database_models import BusTypeDB

    row = BusTypeDB(
        name="legacy",
        description=None,
        system_type="Tower",
        voltage_level=20.0,
        impedance_formula="rho * 0.1",
    )
    bt = row.to_pydantic()
    # NULL must map back onto the pydantic default, not be passed through.
    assert bt.earthing_conductor_theta_initial_C == 20.0
    assert bt.earthing_conductor_current_split == 1.0
    assert bt.electrode_theta_initial_C == 20.0
    assert bt.electrode_current_split == 1.0
    assert bt.earthing_conductor_material is None
    assert bt.electrode_material is None


# ---------------------------------------------------------------------------
# Final-temperature catalog
# ---------------------------------------------------------------------------


def test_final_temperature_returns_catalog_values():
    assert final_temperature("Cu", "bare") == FINAL_TEMPERATURES["Cu"]["bare"]
    assert final_temperature("Steel", "galvanized") == pytest.approx(300.0)


def test_uninsulated_entries_follow_en50522_table_2():
    """EN 50522 Table 2: 300 C bare or galvanised, 150 C for tinned copper.

    Pinning the uninsulated block as a whole -- exact equality, not a sample
    -- is deliberate, and it pins the *set of keys* as much as the values.
    ``theta_f`` enters ``k`` under a logarithm and ``k`` scales the admissible
    current directly, so an entry that drifts *upwards*, or a new uninsulated
    covering that appears with a guessed number, silently permits more current
    -- the unsafe direction, and invisible in an end-to-end limit check that
    only ever asserts "no violation".
    """
    uninsulated = {
        (material, covering): theta_f
        for material, coverings in FINAL_TEMPERATURES.items()
        for covering, theta_f in coverings.items()
        if covering not in CABLE_INSULATION_LIMITS
    }
    assert uninsulated == {
        ("Cu", "bare"): 300.0,
        ("Cu", "tinned"): 150.0,
        ("Al", "bare"): 300.0,
        ("Steel", "bare"): 300.0,
        ("Steel", "galvanized"): 300.0,
    }


def test_insulated_entries_use_the_cable_caps_and_not_the_bare_value():
    """EN 50522 Table 2 speaks about the *uninsulated* conductor only.

    A PVC- or XLPE-insulated conductor loses its insulation long before the
    metal approaches 300 C, so the IEC 60364-5-54 caps bind instead. Serving
    the bare-conductor value here would over-estimate ``theta_f`` by up to
    140 K. The caps do not depend on the material, so every material must
    return the same number -- a per-material copy is exactly how such a table
    drifts.
    """
    assert CABLE_INSULATION_LIMITS == {"PVC": 160.0, "XLPE": 250.0, "EPR": 250.0}
    for material, coverings in FINAL_TEMPERATURES.items():
        for covering, cap in CABLE_INSULATION_LIMITS.items():
            assert covering in coverings, (material, covering)
            assert final_temperature(material, covering) == pytest.approx(cap)
            assert cap < coverings["bare"], (material, covering)


def test_pe_is_deliberately_not_tabulated():
    """``PE`` has no value this package can cite, so it must raise.

    Both neighbouring answers are tempting and both are unsourced: 300 C from
    the EN 50522 side, or the 160 C of the PVC row. Refusing keeps
    ``theta_final_C`` an explicit, reviewable choice by the caller instead of
    a number the docstring cannot point at a table for.
    """
    for material in FINAL_TEMPERATURES:
        with pytest.raises(ValueError, match="'PE'"):
            final_temperature(material, "PE")


def test_material_defaults_match_en50522_bare():
    """The per-material default must not exceed the tabulated bare value.

    ``Steel`` defaulted to 400 C until EN 50522 Table 2 settled the question at
    300 C. A default *above* the standard is the dangerous kind of wrong: it
    needs no user action to take effect.
    """
    for material, data in IEC60949_MATERIALS.items():
        assert data["theta_final_default_C"] == pytest.approx(
            FINAL_TEMPERATURES[material]["bare"]
        ), material
    assert IEC60949_MATERIALS["Steel"]["theta_final_default_C"] == pytest.approx(300.0)


def test_pvc_is_the_cable_cap_and_not_the_en50522_bare_value():
    """Regression guard for the entry that changed meaning.

    An earlier revision read EN 50522 Table 2 as covering PVC- and PE-covered
    conductors as well and returned 300 C for them. Table 2 speaks about the
    uninsulated conductor; an insulated one is capped by its insulation. The
    difference is not cosmetic: ``k`` grows with ``theta_f`` and scales
    ``I_adm`` directly, so 300 C would wave through a current that 160 C
    rejects.
    """
    assert final_temperature("Cu", "PVC") == pytest.approx(160.0)
    assert final_temperature("Steel", "PVC") == pytest.approx(160.0)
    assert final_temperature("Al", "XLPE") == pytest.approx(250.0)
    assert iec60949_k("Cu", theta_final_C=300.0) > iec60949_k(
        "Cu", theta_final_C=final_temperature("Cu", "PVC")
    )


def test_final_temperature_rejects_unknown_entries():
    with pytest.raises(ValueError):
        final_temperature("Gold", "bare")
    with pytest.raises(ValueError):
        final_temperature("Cu", "asbestos")


def test_final_temperature_rejects_physically_impossible_pairings():
    """There is no tinned aluminium and no galvanised copper.

    Returning an invented number for these would be worse than raising: the
    caller gets a plausible 300 C for a combination the standard never
    tabulated.
    """
    with pytest.raises(ValueError):
        final_temperature("Al", "tinned")
    with pytest.raises(ValueError):
        final_temperature("Cu", "galvanized")


# ---------------------------------------------------------------------------
# check_node_limits
# ---------------------------------------------------------------------------


def test_check_node_limits_applies_the_iec_formulas():
    net = _chain()
    df = gi.check_node_limits(net, "F", t_k=1.0, kappa=1.8, n=1.0, f=50.0)
    by = {rb.name: rb for rb in net.results["F"].buses}
    rows = {(r["bus_name"], r["element"]): r for r in df.to_dicts()}

    m = iec60909_m(1.8, 50.0, 1.0)
    factor = math.sqrt(m + 1.0)
    k_ec = iec60949_k("Cu", 20.0, 405.0)
    k_el = iec60949_k("Steel", 20.0)  # material default

    ec = rows[("b3", "earthing_conductor")]
    assert ec["I_rms_A"] == pytest.approx(by["b3"].i_inj)
    assert ec["current_split"] == pytest.approx(1.0)
    assert ec["I_conductor_A"] == pytest.approx(by["b3"].i_inj)
    assert ec["I_th_factor"] == pytest.approx(factor)
    assert ec["I_th_A"] == pytest.approx(by["b3"].i_inj * factor)
    assert ec["i_p_A"] == pytest.approx(1.8 * math.sqrt(2) * by["b3"].i_inj)
    assert ec["k"] == pytest.approx(k_ec)
    assert ec["I_admissible_A"] == pytest.approx(k_ec * 50.0)
    assert ec["utilization"] == pytest.approx(ec["I_th_A"] / ec["I_admissible_A"])
    assert ec["within_limit"] is True

    el = rows[("b3", "electrode")]
    assert el["I_rms_A"] == pytest.approx(by["b3"].ia)
    assert el["I_conductor_A"] == pytest.approx(by["b3"].ia * 0.5)
    assert el["k"] == pytest.approx(k_el)
    assert el["I_admissible_A"] == pytest.approx(k_el * 95.0)


def test_check_node_limits_separates_earthing_conductor_from_electrode():
    """The feature exists because these two differ by an order of magnitude."""
    net = _chain()
    df = gi.check_node_limits(net, "F", t_k=1.0, kappa=1.8)
    rows = {(r["bus_name"], r["element"]): r for r in df.to_dicts()}
    ec = rows[("b3", "earthing_conductor")]["I_th_A"]
    el = rows[("b3", "electrode")]["I_th_A"]
    assert ec > 5.0 * el


def test_check_node_limits_reports_every_bus_and_element():
    net = _chain()
    df = gi.check_node_limits(net, "F", t_k=1.0, kappa=1.8)
    assert df.height == 6  # 3 buses x 2 elements
    assert set(df["element"].to_list()) == {"earthing_conductor", "electrode"}
    assert set(df["bus_name"].to_list()) == {"b1", "b2", "b3"}


def test_check_node_limits_element_selection():
    net = _chain()
    df = gi.check_node_limits(
        net, "F", t_k=1.0, kappa=1.8, elements=("electrode",)
    )
    assert df.height == 3
    assert set(df["element"].to_list()) == {"electrode"}


def test_check_node_limits_rejects_unknown_element():
    net = _chain()
    with pytest.raises(ValueError):
        gi.check_node_limits(net, "F", t_k=1.0, kappa=1.8, elements=("shield",))


def test_check_node_limits_current_split_scales_the_stress():
    full = _chain(_bus_type(electrode_current_split=1.0), name="full")
    half = _chain(_bus_type(electrode_current_split=0.5), name="half")
    df_full = gi.check_node_limits(full, "F", t_k=1.0, kappa=1.8,
                                   elements=("electrode",))
    df_half = gi.check_node_limits(half, "F", t_k=1.0, kappa=1.8,
                                   elements=("electrode",))
    a = df_full.filter(df_full["bus_name"] == "b3")["I_th_A"][0]
    b = df_half.filter(df_half["bus_name"] == "b3")["I_th_A"][0]
    assert b == pytest.approx(a / 2.0)


def test_check_node_limits_undeclared_element_is_reported_but_not_judged():
    bare = gi.BusType(name="bare", system_type="Tower", voltage_level=20.0,
                      impedance_formula="rho * 0.1")
    net = _chain(bare, name="bare_chain")
    df = gi.check_node_limits(net, "F", t_k=1.0, kappa=1.8)
    assert all(v is None for v in df["within_limit"].to_list())
    assert all(v is None for v in df["material"].to_list())
    # The currents are still reported so the user can size by hand.
    ec = df.filter(
        (df["bus_name"] == "b3") & (df["element"] == "earthing_conductor")
    ).to_dicts()[0]
    assert ec["I_th_A"] > 0.0


def test_check_node_limits_flags_an_undersized_electrode():
    small = _chain(
        _bus_type(electrode_cross_section_mm2=0.05, electrode_current_split=1.0),
        name="small",
    )
    df = gi.check_node_limits(small, "F", t_k=1.0, kappa=1.8,
                              elements=("electrode",))
    row = df.filter(df["bus_name"] == "b3").to_dicts()[0]
    assert row["within_limit"] is False
    assert row["utilization"] > 1.0


def test_check_node_limits_accepts_r_to_x_instead_of_kappa():
    net = _chain()
    df = gi.check_node_limits(net, "F", t_k=1.0, r_to_x=0.1)
    assert df["kappa"][0] == pytest.approx(kappa_from_r_to_x(0.1))


def test_check_node_limits_input_validation():
    net = _chain()
    with pytest.raises(ValueError):
        gi.check_node_limits(net, "F", t_k=0.0, kappa=1.8)       # bad t_k
    with pytest.raises(ValueError):
        gi.check_node_limits(net, "F", t_k=1.0)                  # no DC info
    with pytest.raises(ValueError):
        gi.check_node_limits(net, "F", t_k=1.0, kappa=1.8, r_to_x=0.1)  # both
    with pytest.raises(ValueError):
        gi.check_node_limits(net, "nope", t_k=1.0, kappa=1.8)    # no results


def test_check_node_limits_matches_the_branch_check_excitation():
    """Both checks must derive m, n and sqrt(m+n) from the same resolver."""
    net = _chain()
    node = gi.check_node_limits(net, "F", t_k=0.5, r_to_x=0.1)
    branch = gi.check_conductor_limits(net, "F", t_k=0.5, r_to_x=0.1)
    assert node["kappa"][0] == pytest.approx(branch["kappa"][0])
    assert node["m"][0] == pytest.approx(branch["m"][0])
    assert node["I_th_factor"][0] == pytest.approx(branch["I_th_factor"][0])
