# tests/test_thermal_optional.py

"""
The thermal assessment is optional — the calculation runs without it.

A grounding study is useful long before anybody has decided on conductor
materials and cross-sections. Declaring the thermal data must therefore be
*opt-in*: a network that carries none of it has to solve exactly as it did
before the feature existed, and the limit checks have to degrade into a pure
current report instead of failing.

The tests below pin that contract on every layer it could break:

* **The solve.** ``run_fault`` on a network whose ``BusType`` / ``BranchType``
  declare no thermal fields at all, and whose ``Source`` / ``Fault`` carry no
  IEC 60909 metadata either.
* **Inertness.** Declaring the thermal fields must not move a single number of
  the electrical result — they are annotations, not model parameters.
* **The checks.** Every branch and every active bus is still reported, with the
  currents filled in and ``within_limit = None``, so the frame can be used to
  size by hand. The all-``None`` columns keep their declared dtypes, so the
  usual ``pl.col("within_limit") == False`` gate stays valid on a frame in
  which nothing was assessable.
* **Mixed networks.** Declared and undeclared elements coexist in one frame.
* **The excitation.** ``t_k`` and ``kappa``/``r_to_x`` are the one thing the
  checks genuinely cannot default: they raise, with a message naming the
  remedy. That is the documented boundary between "the calculation" (never
  needs them) and "the thermal assessment" (always does).
* **Persistence.** Types without thermal fields survive the JSON and SQLite
  round-trips, and the reloaded network still checks.
"""

from __future__ import annotations

import os
import tempfile

import polars as pl
import pytest

import groundinsight as gi
from groundinsight.models.core_models import (
    ComplexNumber,
    ResultGroundingImpedance,
    ResultReductionFactor,
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

_BUS_KW = dict(system_type="Tower", voltage_level=110.0, impedance_formula="rho * 0.15")
_BRANCH_KW = dict(
    grounding_conductor=True,
    self_impedance_formula="(0.30 + j*f*0.0025) * l",
    mutual_impedance_formula="(0.05 + j*f*0.0020) * l",
)


def _bare_bus_type(name="plain_bus"):
    """A bus type that declares neither element — the opt-out case."""
    return gi.BusType(name=name, **_BUS_KW)


def _bare_branch_type(name="plain_wire"):
    """A branch type without conductor material or cross-section."""
    return gi.BranchType(name=name, **_BRANCH_KW)


def _full_bus_type(name="full_bus"):
    return gi.BusType(
        name=name,
        earthing_conductor_material="Cu",
        earthing_conductor_cross_section_mm2=50.0,
        earthing_conductor_theta_final_C=405.0,
        electrode_material="Steel",
        electrode_cross_section_mm2=95.0,
        electrode_current_split=0.25,
        **_BUS_KW,
    )


def _full_branch_type(name="full_wire"):
    return gi.BranchType(
        name=name, conductor_material="Steel", cross_section_mm2=50.0, **_BRANCH_KW
    )


def _chain(bus_type, branch_type, *, name="net", sc_metadata=True, solve=True):
    """
    A three-bus chain A-B-C, infeed at A, earth fault at C.

    ``sc_metadata=False`` also strips ``Source.r_to_x`` and ``Fault.t_k_s``,
    i.e. the network carries no IEC 60909 data whatsoever.
    """
    net = gi.create_network(name=name, frequencies=[50.0], description=name)
    for bus in ("A", "B", "C"):
        gi.create_bus(
            name=bus, type=bus_type, specific_earth_resistance=100.0, network=net
        )
    for a, b in (("A", "B"), ("B", "C")):
        gi.create_branch(
            name=f"{a}-{b}", type=branch_type, from_bus=a, to_bus=b,
            length=0.4, network=net,
        )
    src_kw = {"r_to_x": 0.1} if sc_metadata else {}
    flt_kw = {"t_k_s": 0.5} if sc_metadata else {}
    gi.create_source(
        name="infeed", bus="A", values={50.0: 5000.0}, network=net, **src_kw
    )
    gi.create_fault(
        name="F", bus="C", scalings={50.0: 1.0}, network=net, **flt_kw
    )
    if solve:
        gi.run_fault(net, "F")
    return net


# ---------------------------------------------------------------------------
# 1 — the solve itself never needs thermal data
# ---------------------------------------------------------------------------


def test_run_fault_without_any_thermal_or_sc_metadata():
    """
    The core requirement: no thermal fields, no IEC 60909 fields, full result.

    This is the state every network is in before anybody starts sizing
    conductors, and it has to stay a first-class case.
    """
    net = _chain(_bare_bus_type(), _bare_branch_type(), sc_metadata=False)

    result = net.results["F"]
    assert len(result.buses) == 3
    assert len(result.branches) == 2
    # The physics is fully there.
    assert result.buses[0].uepr > 0.0
    assert result.grounding_impedance.value[50.0] is not None
    assert result.reduction_factor.value[50.0] is not None
    assert any(rb.i_s > 0.0 for rb in result.branches)


def test_the_three_node_currents_are_reported_without_thermal_data():
    """``i_inj`` and ``ia`` are solver outputs, not thermal ones."""
    net = _chain(_bare_bus_type(), _bare_branch_type(), sc_metadata=False)
    by_name = {rb.name: rb for rb in net.results["F"].buses}

    # The infeed bus and the fault bus carry the lumped injection ...
    assert by_name["A"].i_inj == pytest.approx(5000.0, rel=1e-9)
    assert by_name["C"].i_inj == pytest.approx(5000.0, rel=1e-9)
    # ... the tower in between conducts through its shield wire only.
    assert by_name["B"].i_inj == pytest.approx(0.0, abs=1e-9)
    # And the electrode current is a different, much smaller quantity.
    assert 0.0 < by_name["A"].ia < by_name["A"].i_inj


def test_thermal_fields_do_not_change_the_electrical_result():
    """
    Inertness: the thermal fields are annotations, not model parameters.

    Declaring material, cross-section and current split must not move a single
    number of the solve. Anything else would mean a user who documents their
    conductors silently gets a different study.
    """
    bare = _chain(_bare_bus_type(), _bare_branch_type(), name="bare")
    full = _chain(_full_bus_type(), _full_branch_type(), name="full")

    for rb_bare, rb_full in zip(bare.results["F"].buses, full.results["F"].buses):
        assert rb_bare.name == rb_full.name
        assert rb_bare.uepr == rb_full.uepr
        assert rb_bare.ia == rb_full.ia
        assert rb_bare.i_inj == rb_full.i_inj
    for br_bare, br_full in zip(bare.results["F"].branches, full.results["F"].branches):
        assert br_bare.name == br_full.name
        assert br_bare.i_s == br_full.i_s

    z_bare = bare.results["F"].grounding_impedance.value[50.0]
    z_full = full.results["F"].grounding_impedance.value[50.0]
    assert (z_bare.real, z_bare.imag) == (z_full.real, z_full.imag)
    assert (
        bare.results["F"].reduction_factor.value[50.0]
        == full.results["F"].reduction_factor.value[50.0]
    )


# ---------------------------------------------------------------------------
# 2 — the checks degrade into a current report
# ---------------------------------------------------------------------------


def test_branch_check_reports_every_branch_without_judging_it():
    net = _chain(_bare_bus_type(), _bare_branch_type())
    df = gi.check_conductor_limits(net, "F")

    assert df.height == len(net.branches)
    assert df["within_limit"].to_list() == [None] * df.height
    assert df["material"].to_list() == [None] * df.height
    # The currents are still there, so the frame can be used to size by hand.
    assert all(v > 0.0 for v in df["I_th_A"].to_list())
    assert all(v > 0.0 for v in df["i_p_A"].to_list())


def test_node_check_reports_every_bus_without_judging_it():
    net = _chain(_bare_bus_type(), _bare_branch_type())
    df = gi.check_node_limits(net, "F")

    assert df.height == 2 * len(net.buses)          # two elements per bus
    assert df["within_limit"].to_list() == [None] * df.height
    assert set(df["element"].to_list()) == {"earthing_conductor", "electrode"}
    fault_row = df.filter(
        (pl.col("bus_name") == "C") & (pl.col("element") == "earthing_conductor")
    ).to_dicts()[0]
    assert fault_row["I_th_A"] > 0.0
    assert fault_row["I_admissible_A"] is None
    assert fault_row["utilization"] is None


def test_an_unassessable_frame_keeps_its_dtypes():
    """
    All-``None`` columns must not degenerate to ``Null``.

    Otherwise the usual ``pl.col("utilization") > 1`` style gate would raise on
    exactly the networks that declare nothing — the case this feature is meant
    to tolerate.
    """
    net = _chain(_bare_bus_type(), _bare_branch_type())
    for df in (gi.check_conductor_limits(net, "F"), gi.check_node_limits(net, "F")):
        schema = dict(zip(df.columns, df.dtypes))
        assert schema["within_limit"] == pl.Boolean
        assert schema["utilization"] == pl.Float64
        assert schema["I_admissible_A"] == pl.Float64
        assert schema["material"] == pl.Utf8


def test_the_usual_violation_gate_still_works_when_nothing_was_assessed():
    """A network that declares nothing reports no violations — and does not raise."""
    net = _chain(_bare_bus_type(), _bare_branch_type())
    for df in (gi.check_conductor_limits(net, "F"), gi.check_node_limits(net, "F")):
        assert df.filter(pl.col("within_limit") == False).height == 0   # noqa: E712
        assert df.filter(pl.col("utilization") > 1.0).height == 0
        # ... and the null-safe form agrees.
        assert df.filter(pl.col("within_limit").eq(False).fill_null(False)).height == 0


def test_no_warning_is_logged_when_nothing_is_declared(caplog):
    """
    Opting out is legitimate and must stay quiet.

    A warning here would train users to ignore the log, which is where the real
    limit violations are announced.
    """
    net = _chain(_bare_bus_type(), _bare_branch_type())
    with caplog.at_level("WARNING", logger="groundinsight"):
        gi.check_conductor_limits(net, "F")
        gi.check_node_limits(net, "F")
    assert "Thermal limit exceeded" not in caplog.text
    # not merely "no violation warning" — no warning at all.
    assert [r for r in caplog.records if r.levelname == "WARNING"] == []


# ---------------------------------------------------------------------------
# 3 — mixed networks
# ---------------------------------------------------------------------------


def test_declared_and_undeclared_branches_coexist_in_one_frame():
    net = _chain(_bare_bus_type(), _bare_branch_type())
    net.branches["A-B"].type = _full_branch_type()
    gi.run_fault(net, "F")

    df = gi.check_conductor_limits(net, "F")
    assessed = df.filter(pl.col("branch_name") == "A-B").to_dicts()[0]
    skipped = df.filter(pl.col("branch_name") == "B-C").to_dicts()[0]

    assert assessed["within_limit"] is not None
    assert assessed["utilization"] > 0.0
    assert skipped["within_limit"] is None
    # Both rows carry the same excitation — the frame stays internally consistent.
    assert assessed["kappa"] == skipped["kappa"]
    assert assessed["t_k_s"] == skipped["t_k_s"]


def test_one_bus_element_declared_and_the_other_not():
    """The two elements are independent — declaring one must not imply the other."""
    electrode_only = gi.BusType(
        name="electrode_only",
        electrode_material="Steel",
        electrode_cross_section_mm2=95.0,
        **_BUS_KW,
    )
    net = _chain(electrode_only, _bare_branch_type())
    df = gi.check_node_limits(net, "F")

    electrodes = df.filter(pl.col("element") == "electrode")
    conductors = df.filter(pl.col("element") == "earthing_conductor")
    assert all(v is not None for v in electrodes["within_limit"].to_list())
    assert conductors["within_limit"].to_list() == [None] * conductors.height


def test_elements_argument_can_restrict_the_frame():
    """Assessing only one element is a supported way of opting out of the other."""
    net = _chain(_full_bus_type(), _bare_branch_type())
    df = gi.check_node_limits(net, "F", elements=("electrode",))
    assert set(df["element"].to_list()) == {"electrode"}
    assert df.height == len(net.buses)


# ---------------------------------------------------------------------------
# 3b — half-declared elements are the one silence that is NOT acceptable
#
# Declaring nothing is a modelling decision. Declaring a material without a
# cross-section means the user began describing the conductor and believes it
# is being assessed. Such a row carries ``within_limit = None``, which is
# visually indistinguishable from a pass, so it must be announced.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, missing",
    [
        ({"conductor_material": "Steel"}, "cross_section_mm2"),
        ({"cross_section_mm2": 50.0}, "conductor_material"),
    ],
)
def test_a_half_declared_branch_is_not_assessed_but_is_announced(
    caplog, kwargs, missing
):
    net = _chain(_bare_bus_type(), _bare_branch_type())
    net.branches["A-B"].type = gi.BranchType(name="half", **kwargs, **_BRANCH_KW)
    gi.run_fault(net, "F")

    with caplog.at_level("WARNING", logger="groundinsight"):
        df = gi.check_conductor_limits(net, "F")

    row = df.filter(pl.col("branch_name") == "A-B").to_dicts()[0]
    assert row["within_limit"] is None, "half data must never produce a verdict"
    assert row["I_th_A"] > 0.0, "the current is still reported"

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "A-B" in warnings[0]
    assert missing in warnings[0], "the message names the field that is missing"
    assert "not assessed" in warnings[0].lower()


@pytest.mark.parametrize(
    "kwargs, missing",
    [
        ({"earthing_conductor_material": "Cu"}, "earthing_conductor_cross_section_mm2"),
        ({"earthing_conductor_cross_section_mm2": 50.0}, "earthing_conductor_material"),
        ({"electrode_material": "Steel"}, "electrode_cross_section_mm2"),
        ({"electrode_cross_section_mm2": 95.0}, "electrode_material"),
    ],
)
def test_a_half_declared_node_element_is_not_assessed_but_is_announced(
    caplog, kwargs, missing
):
    net = _chain(_bare_bus_type(), _bare_branch_type())
    net.buses["B"].type = gi.BusType(name="half", **kwargs, **_BUS_KW)
    gi.run_fault(net, "F")

    with caplog.at_level("WARNING", logger="groundinsight"):
        df = gi.check_node_limits(net, "F")

    element = "earthing_conductor" if "earthing" in missing else "electrode"
    row = df.filter(
        (pl.col("bus_name") == "B") & (pl.col("element") == element)
    ).to_dicts()[0]
    assert row["within_limit"] is None

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert f"B/{element}" in warnings[0]
    assert missing in warnings[0]


def test_the_half_declared_warning_does_not_fire_on_complete_data(caplog):
    """The warning must stay specific, or it becomes noise that gets filtered out."""
    net = _chain(_full_bus_type(), _full_branch_type())
    with caplog.at_level("WARNING", logger="groundinsight"):
        gi.check_conductor_limits(net, "F")
        gi.check_node_limits(net, "F")
    assert "half of their thermal data" not in caplog.text


def test_the_half_declared_warning_lists_every_offender_once(caplog):
    """Two broken branches produce one warning naming both, not two warnings."""
    net = _chain(_bare_bus_type(), _bare_branch_type())
    for branch in ("A-B", "B-C"):
        net.branches[branch].type = gi.BranchType(
            name=f"half_{branch}", conductor_material="Steel", **_BRANCH_KW
        )
    gi.run_fault(net, "F")

    with caplog.at_level("WARNING", logger="groundinsight"):
        gi.check_conductor_limits(net, "F")

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "A-B" in warnings[0] and "B-C" in warnings[0]
    assert warnings[0].startswith("2 branch(es)")


def test_a_half_declared_element_does_not_disturb_its_neighbours(caplog):
    """The complete elements around a broken one are still assessed normally."""
    net = _chain(_full_bus_type(), _bare_branch_type())
    net.buses["B"].type = gi.BusType(
        name="half", earthing_conductor_material="Cu", **_BUS_KW
    )
    gi.run_fault(net, "F")

    with caplog.at_level("WARNING", logger="groundinsight"):
        df = gi.check_node_limits(net, "F")

    assessed = df.filter(pl.col("bus_name") != "B")
    assert all(v is not None for v in assessed["within_limit"].to_list())
    unassessed = df.filter(pl.col("bus_name") == "B")
    assert unassessed["within_limit"].to_list() == [None, None]


# ---------------------------------------------------------------------------
# 4 — the excitation is the documented boundary
# ---------------------------------------------------------------------------


def test_the_check_requires_a_fault_duration_and_says_so():
    """
    ``t_k`` cannot be defaulted — there is no safe value.

    The calculation never needs it; the thermal assessment always does. The
    error message has to name both ways out.
    """
    net = _chain(_bare_bus_type(), _bare_branch_type(), sc_metadata=False)
    with pytest.raises(ValueError, match="No fault duration"):
        gi.check_conductor_limits(net, "F")
    with pytest.raises(ValueError) as excinfo:
        gi.check_node_limits(net, "F")
    message = str(excinfo.value)
    assert "t_k=" in message and "t_k_s" in message


def test_the_check_requires_dc_information_and_says_so():
    net = _chain(_bare_bus_type(), _bare_branch_type(), sc_metadata=False)
    with pytest.raises(ValueError) as excinfo:
        gi.check_node_limits(net, "F", t_k=0.5)
    message = str(excinfo.value)
    assert "kappa" in message and "r_to_x" in message


def test_the_excitation_may_come_from_arguments_alone():
    """A network with no stored 60909 data is still fully assessable ad hoc."""
    net = _chain(_full_bus_type(), _full_branch_type(), sc_metadata=False)
    branch = gi.check_conductor_limits(net, "F", t_k=0.5, r_to_x=0.1)
    node = gi.check_node_limits(net, "F", t_k=0.5, r_to_x=0.1)
    assert all(v is not None for v in branch["within_limit"].to_list())
    assert all(v is not None for v in node["within_limit"].to_list())
    assert branch["kappa"][0] == pytest.approx(node["kappa"][0])


# ---------------------------------------------------------------------------
# 5 — persistence
# ---------------------------------------------------------------------------


def test_json_round_trip_of_types_without_thermal_fields():
    net = _chain(_bare_bus_type(), _bare_branch_type(), name="json_bare")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "net.json")
        gi.save_network_to_json(net, path)
        back = gi.load_network_from_json(path)

    bus_type = back.buses["A"].type
    branch_type = back.branches["A-B"].type
    assert bus_type.earthing_conductor_material is None
    assert bus_type.earthing_conductor_cross_section_mm2 is None
    assert bus_type.electrode_material is None
    assert branch_type.conductor_material is None
    assert branch_type.cross_section_mm2 is None

    # And the reloaded network still solves and still checks.
    gi.run_fault(back, "F")
    df = gi.check_node_limits(back, "F")
    assert df.height == 2 * len(back.buses)
    assert df["within_limit"].to_list() == [None] * df.height


def test_sqlite_round_trip_of_types_without_thermal_fields():
    net = _chain(_bare_bus_type(), _bare_branch_type(), name="db_bare")
    with tempfile.TemporaryDirectory() as tmp:
        gi.start_dbsession(os.path.join(tmp, "optional.db"))
        try:
            gi.save_network_to_db(net, overwrite=True)
            back = gi.load_network_from_db("db_bare")
        finally:
            gi.close_dbsession()

    bus_type = back.buses["A"].type
    assert bus_type.earthing_conductor_material is None
    assert bus_type.electrode_cross_section_mm2 is None
    # The split keeps its default rather than becoming None.
    assert bus_type.electrode_current_split == 1.0
    assert back.branches["A-B"].type.conductor_material is None


# ---------------------------------------------------------------------------
# 6 — result objects stay printable
# ---------------------------------------------------------------------------


def test_grounding_impedance_result_is_printable():
    """
    ``__str__`` referenced a field that does not exist.

    ``repr`` came from pydantic and kept working, so a bare notebook cell
    looked fine while ``print(...)`` and any f-string raised AttributeError.
    """
    obj = ResultGroundingImpedance(
        fault_bus="C", value={50.0: ComplexNumber(real=0.13, imag=0.012)}
    )
    text = str(obj)
    assert "ResultGroundingImpedance" in text
    assert "C" in text
    assert f"{obj}"  # the f-string path is the one that used to raise


def test_reduction_factor_result_is_printable():
    obj = ResultReductionFactor(fault_bus="C", value={50.0: 0.77})
    text = str(obj)
    assert "ResultReductionFactor" in text
    assert "0.77" in text


def test_every_result_object_of_a_solved_network_is_printable():
    """A sweep, so the next added ``__str__`` cannot reintroduce the defect."""
    net = _chain(_bare_bus_type(), _bare_branch_type(), sc_metadata=False)
    result = net.results["F"]
    for obj in (
        result,
        result.grounding_impedance,
        result.reduction_factor,
        *result.buses,
        *result.branches,
    ):
        assert str(obj)
