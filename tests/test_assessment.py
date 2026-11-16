# tests/test_assessment.py
"""
Assessment criteria of a project and the utilisation of a fault's results.

The criteria are the user's limits -- earth potential rise, touch voltage,
admissible short-time currents -- kept with the project (section
``criteria`` of the project file) and set with an undoable command. The
assessment holds a stored result to them in percent; every number is checked
a second way against the library's own checks and the result tables.
"""

import json
import warnings

import polars as pl
import pytest

import groundinsight as gi
from groundinsight import api

RING = "Substation ring 2.5 m, 4 rods 2.5 m"
CABLE = "NA2XS(F)2Y 1x150RM/25 12/20 kV"


def _session():
    net = gi.create_network(name="assess", frequencies=[50.0])
    ring = gi.standard_bus_types()[RING]
    cable = gi.standard_branch_types()[CABLE]
    for name in ("A", "B", "C"):
        gi.create_bus(name=name, type=ring, specific_earth_resistance=100.0, network=net)
    gi.create_branch(name="K1", type=cable, from_bus="A", to_bus="B", length=1.0, network=net)
    gi.create_branch(name="K2", type=cable, from_bus="B", to_bus="C", length=1.5, network=net)
    gi.create_source(name="Q", bus="A", values={50.0: 4000.0}, network=net)
    gi.create_fault(name="F", bus="C", t_k_s=0.5, network=net)
    session = api.ProjectSession(net)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", gi.GroundinsightWarning)
        session.solve("F")
    return session


CRITERIA = {"u_e_limit_V": 500.0, "u_touch_limit": [[0.1, 600.0], [0.5, 220.0], [1.0, 117.0]],
            "r_to_x": 0.1}


def test_the_criteria_are_kept_with_the_project_and_undone(tmp_path):
    session = _session()
    session.apply(api.SetCriteria(criteria=CRITERIA))
    path = tmp_path / "p.json"
    session.save(str(path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["criteria"]["u_e_limit_V"] == 500.0
    again = api.ProjectSession.from_file(str(path))
    assert again.criteria() == session.criteria()
    session.undo()
    assert session.criteria() == api.CriteriaDTO()
    with pytest.raises(api.CommandError):
        session.apply(api.SetCriteria(criteria={"branch_current_limits_A": {CABLE: -1.0}}))


def test_a_file_without_criteria_has_none():
    net = gi.create_network(name="n", frequencies=[50.0])
    text = gi.project_to_json(net)
    payload = json.loads(text)
    del payload["criteria"]
    assert gi.project_from_json(json.dumps(payload)).criteria == {}
    payload["criteria"] = []
    with pytest.raises(ValueError, match="'criteria' must be an object"):
        gi.project_from_json(json.dumps(payload))


def test_the_utilisation_is_the_library_checks_in_percent():
    session = _session()
    session.apply(api.SetCriteria(criteria=CRITERIA))
    dto = session.assessment("F")
    net = session.network
    buses = {r["bus_name"]: r for r in dto.buses.rows}
    # EPR: the RMS row of res_buses against the limit.
    rms = net.res_buses("F").filter(pl.col("is_rms"))
    for row in rms.iter_rows(named=True):
        assert buses[row["bus_name"]]["epr_utilisation_pct"] == pytest.approx(100 * row["EPR_V"] / 500.0)
    # Touch: check_touch_voltages with the curve at the fault's 0.5 s.
    touch = gi.check_touch_voltages(net, "F", [(0.1, 600.0), (0.5, 220.0), (1.0, 117.0)])
    for row in touch.iter_rows(named=True):
        assert buses[row["bus_name"]]["U_T_limit_V"] == pytest.approx(220.0)
        assert buses[row["bus_name"]]["touch_utilisation_pct"] == pytest.approx(100 * row["utilisation"])
    # Thermal: check_conductor_limits of the standard cable (material and cross-section).
    conductors = gi.check_conductor_limits(net, "F", 0.5, r_to_x=0.1)
    branches = {r["branch_name"]: r for r in dto.branches.rows}
    for row in conductors.iter_rows(named=True):
        assert branches[row["branch_name"]]["limit_source"] == "thermal"
        assert branches[row["branch_name"]]["thermal_utilisation_pct"] == pytest.approx(100 * row["utilization"])


def test_a_current_limit_of_the_type_replaces_the_computed_one():
    session = _session()
    session.apply(api.SetCriteria(criteria=dict(CRITERIA, branch_current_limits_A={CABLE: 2000.0})))
    branches = {r["branch_name"]: r for r in session.assessment("F").branches.rows}
    for row in branches.values():
        assert row["limit_source"] == "criteria" and row["I_limit_A"] == 2000.0
        assert row["thermal_utilisation_pct"] == pytest.approx(100 * row["I_th_A"] / 2000.0)


def test_missing_limits_are_named_not_guessed():
    session = _session()
    dto = session.assessment("F")
    assert all(r["epr_utilisation_pct"] is None for r in dto.buses.rows)
    assert any("no limit given" in note for note in dto.notes)
    with pytest.raises(api.ProjectError):
        api.ProjectSession.from_example("minimal").assessment("fault")
