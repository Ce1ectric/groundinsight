# gui/tests/test_colouring.py
"""
The colouring of the plan: pure functions of DTOs, checked against the library.

The numerical modes are checked a second way against ``res_buses`` /
``res_branches`` of a twin of the network solved with ``groundinsight.run_fault``
(the end colours sit at the minimum and maximum of the frame), the earthing
groups against a breadth-first search written here.
"""

import warnings
from collections import deque

import pytest

import groundinsight as gi
from groundinsight.api import EXAMPLES, ProjectSession, UpdateBranch

from groundinsight_gui.colouring import MODES, NEUTRAL, PALETTE, SCALE, colour, scale_colour


def _session(example="mv_ring"):
    return ProjectSession.from_example(example)


def _solved(session, fault):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return session.solve(fault)


@pytest.mark.parametrize("mode", MODES)
def test_every_mode_colours_every_element(mode):
    session = _session()
    result = _solved(session, "F1")
    out = colour(mode, session.diagram(), result=result, check=session.check())
    assert set(out.nodes) == {f"b{i:02d}" for i in range(1, 21)}
    assert set(out.edges) == set(session.network.branches)
    assert all(c.startswith("#") and len(c) == 7 for c in [*out.nodes.values(), *out.edges.values()])


def test_the_scale_runs_from_the_smallest_to_the_largest_epr():
    session = _session()
    result = _solved(session, "F1")
    out = colour("epr", session.diagram(), result=result)
    twin = EXAMPLES["mv_ring"]()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gi.run_fault(twin, "F1")
    frame = twin.res_buses("F1").filter(twin.res_buses("F1")["frequency_Hz"].is_null())
    epr = dict(zip(frame["bus_name"], frame["EPR_V"], strict=True))
    low, high, unit = out.scale
    assert (low, high, unit) == (min(epr.values()), max(epr.values()), "V")
    assert out.nodes[max(epr, key=epr.get)] == SCALE[-1]
    assert out.nodes[min(epr, key=epr.get)] == SCALE[0]


def test_branch_currents_colour_the_lines():
    session = _session("minimal")
    result = _solved(session, "fault")
    out = colour("i_branch", session.diagram(), result=result)
    rms = {r["branch_name"]: r["I_branch_A"] for r in result.branches.rows if r["is_rms"]}
    assert out.scale[:2] == (min(rms.values()), max(rms.values()))
    assert set(out.nodes.values()) == {NEUTRAL}


def test_without_a_result_or_touch_factors_everything_is_neutral():
    session = _session("minimal")
    assert colour("epr", session.diagram()).scale is None
    result = _solved(session, "fault")
    out = colour("u_touch", session.diagram(), result=result)  # no characteristic: no U_vT
    assert out.scale is None and set(out.nodes.values()) == {NEUTRAL}


def _bfs_groups(session):
    """Second way: connected parts over active branches with an earth conductor."""
    net = session.network
    neighbours = {b: [] for b, bus in net.buses.items() if bus.active}
    for branch in net.branches.values():
        if branch.active and branch.type.grounding_conductor:
            neighbours[branch.from_bus].append(branch.to_bus)
            neighbours[branch.to_bus].append(branch.from_bus)
    groups, seen = [], set()
    for start in neighbours:
        if start in seen:
            continue
        part, queue = {start}, deque([start])
        while queue:
            for other in neighbours[queue.popleft()]:
                if other not in part:
                    part.add(other)
                    queue.append(other)
        seen |= part
        groups.append(part)
    return groups


def test_earthing_groups_follow_the_open_branches():
    session = _session()
    assert len(set(colour("group", session.diagram()).nodes.values())) == 1  # the closed ring
    session.apply(UpdateBranch(name="L_05_06", fields={"active": False}))
    session.apply(UpdateBranch(name="L_15_16", fields={"active": False}))
    out = colour("group", session.diagram())
    groups = _bfs_groups(session)
    assert len(groups) == 2 and [e.label for e in out.entries] == ["#1", "#2"]
    for part in groups:
        assert len({out.nodes[b] for b in part}) == 1
    assert out.nodes["b01"] != out.nodes["b10"]


def test_categorical_modes_and_their_legends():
    session = _session()
    out = colour("infeed_fault", session.diagram())
    assert out.nodes["b01"] == PALETTE[0] and out.nodes["b10"] == PALETTE[5]
    assert [e.label for e in out.entries] == ["legend_infeed", "legend_fault", "legend_other"]
    out = colour("type", session.diagram())
    labels = [e.label for e in out.entries]
    assert labels[0] == session.network.buses["b01"].type.name
    assert session.network.branches["L_01_02"].type.name in labels
    out = colour("voltage_level", session.diagram())
    assert [e.label for e in out.entries] == ["20 kV"]


def test_unknown_modes_are_refused_and_the_scale_has_its_ends():
    with pytest.raises(ValueError, match="unknown colouring"):
        colour("rainbow", _session("minimal").diagram())
    assert scale_colour(0.0) == SCALE[0] and scale_colour(1.0) == SCALE[-1]
    assert scale_colour(-1.0) == SCALE[0] and scale_colour(2.0) == SCALE[-1]
