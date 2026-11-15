# tests/test_api_batch.py
"""
``Batch``: several commands as one undo step.

A front end action often is more than one edit -- drawing a bus with an infeed
places the bus and adds the source, deleting a bus removes its branches,
sources and faults first, the element dialog applies every changed field. A
``Batch`` applies its commands in order as one entry of the history: all or
nothing, one undo, one redo.
"""

import json

import pytest

import groundinsight.api as api
from tests._characteristic_cases import FREQS, _network


def _state(session):
    return (session.network.model_dump_json(exclude={"results", "paths"}),
            session.diagram_model.model_dump_json())


def test_a_batch_is_one_step_of_the_history():
    session = api.ProjectSession(_network())
    before = _state(session)
    batch = api.Batch(title="draw source bus", commands=[
        api.PlaceBus(name="Q2", type="remote", x=0, y=200, symbol="source_bus"),
        api.AddSource(name="I2", bus="Q2", values={f: 1.0 for f in FREQS}),
    ])
    report = session.apply(batch)
    assert report.label == "draw source bus" and session.history() == ["draw source bus"]
    assert "Q2" in session.network.buses and session.network.sources["I2"].bus == "Q2"
    after = _state(session)
    session.undo()
    assert _state(session) == before
    session.redo()
    assert _state(session) == after


def test_a_failing_command_reverts_the_ones_before_it():
    session = api.ProjectSession(_network())
    session.solve("F")
    before = (_state(session), session.revision, session.history())
    batch = api.Batch(commands=[
        api.RemoveFault(name="F"),
        api.MoveNodes(positions={"S2": (600, 600)}),
        api.RemoveBus(name="S1"),  # still referenced by K1 and K2: refused
    ])
    with pytest.raises(api.CommandError, match="remove bus 'S1'"):
        session.apply(batch)
    assert (_state(session), session.revision, session.history()) == before
    assert session.stale_results() == [] and "F" in session.network.results


def test_removing_a_bus_with_everything_attached_in_one_step():
    session = api.ProjectSession(_network())
    before = _state(session)
    session.apply(api.Batch(title="delete S1", commands=[
        api.RemoveFault(name="F"), api.RemoveBranch(name="K1"), api.RemoveBranch(name="K2"),
        api.RemoveBus(name="S1"),
    ]))
    assert "S1" not in session.network.buses and "S1" not in session.diagram_model.nodes
    session.undo()
    assert _state(session) == before


def test_a_batch_round_trips_through_json_and_refuses_to_be_empty():
    batch = api.Batch(commands=[{"op": "move_nodes", "positions": {"S1": [1, 2]}},
                                api.RemoveFault(name="F")])
    assert isinstance(batch.commands[0], api.MoveNodes)
    again = api.parse_command(json.loads(batch.model_dump_json()))
    assert again == batch and again.label == "move 1 node(s); remove fault 'F'"
    session = api.ProjectSession(_network())
    with pytest.raises(api.CommandError, match="no commands"):
        session.apply(api.Batch(commands=[]))
    with pytest.raises(ValueError, match="unknown command op"):
        api.Batch(commands=[{"op": "format_disk"}])
