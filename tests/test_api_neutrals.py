# tests/test_api_neutrals.py
"""
The service layer edits neutral-point elements: add, update, rename, remove.

Checked the strict way of the service tests: undo gives the serialised state
before, redo the state after, and a rejected command changes nothing. The
session's tables and its solve see the elements like the library does.
"""

import json
import math

import pytest

import groundinsight as gi
import groundinsight.api as api

F = 50.0
I_CE = math.sqrt(3) * 2 * math.pi * F * 250e-9 * 10.0 * 20e3


def _network() -> gi.Network:
    uw = gi.BusType(name="UW", system_type="Substation", voltage_level=20.0, impedance_formula="0.5")
    ons = gi.BusType(name="ONS", system_type="Substation", voltage_level=20.0, impedance_formula="rho/50")
    cable = gi.standard_branch_types()["NA2XS(F)2Y 1x150RM/25 12/20 kV"].model_copy(
        update={"c_shield_nf_per_km": 250.0, "c_earth_nf_per_km": None, "voltage_level": 20.0})
    net = gi.create_network(name="feeder", frequencies=[F, 250.0])
    gi.create_bus(name="UW", type=uw, network=net)
    for k, (a, b, km) in enumerate((("UW", "S1", 3.0), ("S1", "S2", 3.0), ("S2", "S3", 4.0)), start=1):
        gi.create_bus(name=b, type=ons, network=net)
        gi.create_branch(name=f"K{k}", type=cable, from_bus=a, to_bus=b, length=km, network=net)
    gi.create_fault(name="F", bus="S3", network=net)
    return net


def _session() -> api.ProjectSession:
    return api.ProjectSession(_network())


def _state(session: api.ProjectSession) -> str:
    return session.network.model_dump_json(exclude={"results", "paths"})


COMMANDS = [
    [api.AddNeutral(name="N", bus="UW", kind="isolated", voltage_level=20.0)],
    [api.AddNeutral(name="N", bus="UW", kind="resonant", voltage_level=20.0, coil_current=28.0,
                    active_residual_current=0.8, harmonic_currents={250.0: {"real": 0.0, "imag": 4.0}})],
    [api.AddNeutral(name="N", bus="UW", kind="low_impedance", voltage_level=20.0, current=1000.0)],
    [api.AddNeutral(name="N", bus="UW", kind="isolated", voltage_level=20.0),
     api.UpdateNeutral(name="N", fields={"remaining_capacitive_current": 40.0, "harmonic_currents": {"250": 2.0}})],
    [api.AddNeutral(name="N", bus="UW", kind="isolated", voltage_level=20.0),
     api.UpdateNeutral(name="N", fields={"kind": "resonant", "coil_current": 28.0})],
    [api.AddNeutral(name="N", bus="UW", kind="resonant", voltage_level=20.0, coil_current=28.0),
     api.UpdateNeutral(name="N", fields={"kind": "low_impedance", "current": {"real": 900.0, "imag": -300.0}})],
    [api.AddNeutral(name="N", bus="UW", kind="isolated", voltage_level=20.0),
     api.RenameNeutral(name="N", new_name="Sternpunkt 20 kV")],
    [api.AddNeutral(name="A", bus="UW", kind="isolated", voltage_level=20.0),
     api.AddNeutral(name="B", bus="S1", kind="resonant", voltage_level=20.0, coil_current=5.0),
     api.RemoveNeutral(name="A")],
    [api.AddNeutral(name="N", bus="UW", kind="isolated", voltage_level=20.0),
     api.UpdateFault(name="F", fields={"voltage_level": 20.0})],
    [api.AddSource(name="Q", bus="UW", values={F: 100.0}, voltage_level=20.0),
     api.UpdateSource(name="Q", fields={"voltage_level": None})],
    [api.AddFault(name="F2", bus="S1", voltage_level=20.0)],
]


@pytest.mark.parametrize("index", range(len(COMMANDS)))
def test_undo_and_redo_restore_the_exact_state(index):
    session = _session()
    states = [_state(session)]
    for command in COMMANDS[index]:
        session.apply(command)
        states.append(_state(session))
    assert len(set(states)) == len(states)
    for expected in reversed(states[:-1]):
        session.undo()
        assert _state(session) == expected
    for expected in states[1:]:
        session.redo()
        assert _state(session) == expected


def test_a_command_from_json():
    session = _session()
    session.apply({"op": "add_neutral", "name": "N", "bus": "UW", "kind": "resonant", "voltage_level": 20.0,
                   "coil_current": 28.0})
    neutral = session.network.neutrals["N"]
    assert isinstance(neutral, gi.ResonantNeutral) and neutral.coil_current == 28.0
    assert "add_neutral" in api.COMMAND_OPS and "rename_neutral" in api.COMMAND_OPS


def test_a_change_of_kind_drops_the_fields_of_the_former_kind():
    session = _session()
    session.apply(api.AddNeutral(name="N", bus="UW", kind="resonant", voltage_level=20.0, coil_current=28.0,
                                 remaining_capacitive_current=12.0))
    session.apply(api.UpdateNeutral(name="N", fields={"kind": "isolated"}))
    neutral = session.network.neutrals["N"]
    assert isinstance(neutral, gi.IsolatedNeutral)
    assert neutral.remaining_capacitive_current == 12.0 and not hasattr(neutral, "coil_current")


def _rejected():
    return [
        api.AddNeutral(name="N", bus="ghost", kind="isolated", voltage_level=20.0),
        api.AddNeutral(name="N", bus="UW", kind="resonant", voltage_level=20.0),          # no coil current
        api.AddNeutral(name="N", bus="UW", kind="low_impedance", voltage_level=20.0),     # no current
        api.AddNeutral(name="N", bus="UW", kind="isolated", voltage_level=-20.0),
        api.AddNeutral(name="N", bus="UW", kind="isolated", voltage_level=20.0, harmonic_currents={50.0: 1.0}),
        api.AddNeutral(name="E", bus="UW", kind="isolated", voltage_level=20.0),          # taken
        api.UpdateNeutral(name="ghost", fields={"bus": "S1"}),
        api.UpdateNeutral(name="E", fields={}),
        api.UpdateNeutral(name="E", fields={"bus": "ghost"}),
        api.UpdateNeutral(name="E", fields={"coil": 3.0}),
        api.UpdateNeutral(name="E", fields={"kind": "petersen"}),
        api.UpdateNeutral(name="E", fields={"kind": "resonant"}),                         # no coil current
        api.UpdateNeutral(name="E", fields={"name": "other"}),
        api.RenameNeutral(name="ghost", new_name="x"),
        api.RenameNeutral(name="E", new_name="E"),
        api.RenameNeutral(name="E", new_name=""),
        api.RemoveNeutral(name="ghost"),
        api.RemoveBus(name="UW"),                                                         # a neutral refers to it
    ]


@pytest.mark.parametrize("index", range(18))
def test_a_rejected_command_changes_nothing(index):
    commands = _rejected()
    assert len(commands) == 18
    session = _session()
    session.apply(api.AddNeutral(name="E", bus="UW", kind="isolated", voltage_level=20.0))
    before, revision, history = _state(session), session.revision, list(session.history())
    with pytest.raises(api.CommandError):
        session.apply(commands[index])
    assert (_state(session), session.revision, list(session.history())) == (before, revision, history)


def test_the_session_solves_and_tabulates_the_elements():
    session = _session()
    session.apply(api.AddNeutral(name="N", bus="UW", kind="resonant", voltage_level=20.0, coil_current=1.05 * I_CE,
                                 active_residual_current=0.03 * I_CE))
    table = session.table("neutrals")
    assert [row["kind"] for row in table.rows] == ["resonant"]
    result = session.solve("F")
    row = next(r for r in result.key_figures.rows if r.frequency_Hz == F)
    assert row.I_F_A == pytest.approx(1.5864, abs=5e-5)
    assert row.U_E_V == pytest.approx(5.1208, abs=5e-5)
    session.apply(api.UpdateNeutral(name="N", fields={"coil_current": 20.0}))
    assert session.is_stale("F")
    session.undo()
    assert not session.is_stale("F")


def test_the_project_file_keeps_the_elements(tmp_path):
    session = _session()
    session.apply(api.AddNeutral(name="N", bus="UW", kind="resonant", voltage_level=20.0, coil_current=28.0,
                                 harmonic_currents={250.0: 3.0}))
    path = session.save(str(tmp_path / "feeder.json"))
    again = api.ProjectSession.from_file(path)
    assert again.network.model_dump_json(exclude={"results", "paths"}) == _state(session)
    assert isinstance(again.network.neutrals["N"], gi.ResonantNeutral)
    session.close()
    again.close()


# --- edits of several elements, and the derived tables ------------------------


def test_removal_takes_what_refers_to_a_bus_and_is_one_undo_step():
    session = _session()
    session.apply(api.AddNeutral(name="N", bus="UW", kind="isolated", voltage_level=20.0))
    session.apply(api.AddSource(name="Q", bus="UW", values={F: 10.0}))
    session.apply(api.AddFault(name="G", bus="UW"))
    before = _state(session)
    command = api.removal(session, [("bus", "UW"), ("bus", "ghost"), ("branch", "K3")])
    assert [step.op for step in command.commands] == [
        "remove_fault", "remove_source", "remove_neutral", "remove_branch", "remove_branch", "remove_bus"]
    assert [step.name for step in command.commands] == ["G", "Q", "N", "K3", "K1", "UW"]
    session.remove_elements([("bus", "UW"), ("branch", "K3")])
    assert list(session.network.buses) == ["S1", "S2", "S3"] and list(session.network.branches) == ["K2"]
    assert session.network.neutrals == {} and "Q" not in session.network.sources
    session.undo()
    assert _state(session) == before


def test_removal_of_one_free_element_is_the_plain_command_and_of_nothing_is_none():
    session = _session()
    assert api.removal(session, [("branch", "K2")]).op == "remove_branch"
    assert api.removal(session, [("bus", "ghost")]) is None
    assert session.remove_elements([]) is None


def test_duplication_copies_buses_branches_and_sources_but_no_fault_and_no_neutral():
    session = _session()
    session.apply(api.AddNeutral(name="N", bus="UW", kind="isolated", voltage_level=20.0))
    session.apply(api.AddSource(name="Q", bus="S1", values={F: 10.0, 250.0: 1.0}, voltage_level=20.0))
    before = _state(session)
    created = session.duplicate_elements([("bus", "S1"), ("bus", "S3"), ("branch", "K2"), ("bus", "UW")])
    assert created == [("bus", "S1 copy"), ("bus", "S3 copy"), ("bus", "UW copy"), ("branch", "K2 copy")]
    net = session.network
    copy = net.branches["K2 copy"]
    assert (copy.from_bus, copy.to_bus, copy.length) == ("S1 copy", "S2", 3.0)     # S2 is not copied: it stays
    assert net.sources["Q copy"].bus == "S1 copy" and net.sources["Q copy"].voltage_level == 20.0
    assert net.sources["Q copy"].values == net.sources["Q"].values
    assert list(net.neutrals) == ["N"] and list(net.faults) == ["F"]
    again = session.duplicate_elements([("bus", "S1")], suffix="Kopie")
    assert again == [("bus", "S1 Kopie")]
    session.undo()
    session.undo()
    assert _state(session) == before


def test_a_copy_never_lands_on_another_symbol():
    session = _session()
    place = {node.name: (node.x, node.y) for node in session.diagram().nodes}
    session.duplicate_elements([("bus", "S1")], at=place["S2"])
    places = [(node.x, node.y) for node in session.diagram().nodes]
    assert len(set(places)) == len(places)


def test_the_tables_of_supply_areas_and_earth_current():
    session = _session()
    session.apply(api.AddNeutral(name="N", bus="UW", kind="resonant", voltage_level=20.0, coil_current=1.05 * I_CE,
                                 active_residual_current=0.03 * I_CE))
    areas = session.supply_areas()
    assert [column.name for column in areas.columns] == list(gi.SUPPLY_AREAS_SCHEMA)
    assert areas.rows[0]["detuning"] == pytest.approx(-0.05)
    assert json.loads(areas.rows[0]["branches"]) == ["K1", "K2", "K3"]
    assert json.loads(areas.rows[0]["feeding_elements"]) == ["N"]
    assert areas.rows[0]["I_CE_A"] == pytest.approx(I_CE)
    table = session.earth_current("F")
    assert [row["bus_name"] for row in table.rows if row["frequency_Hz"] == F] == ["UW", "S1", "S2", "S3"]
    assert json.loads(table.rows[0]["sources"]) == ["Charging current at UW", "Neutral N"]
    assert session.network.results == {}
    with pytest.raises(ValueError, match="does not exist"):
        session.earth_current("nope")
