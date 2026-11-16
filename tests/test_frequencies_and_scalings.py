# tests/test_frequencies_and_scalings.py
"""
Changing the network frequencies keeps the fault scalings complete.

A fault drawn in the GUI gets a scaling at every network frequency. When the
frequencies change afterwards (``SetFrequencies``), a new frequency used to
have no scaling: the solve took 1.0 there and said so with
``SCALINGS_INCOMPLETE``. The command now gives every new frequency the
scaling 1.0 and drops the scalings of frequencies that are gone; a fault
without scalings (1.0 everywhere) keeps none. Checked a second way: the
solve gives the same potentials as a network built with explicit scalings,
and undo restores the scalings the fault had.
"""

import warnings

import polars as pl
import pytest

import groundinsight as gi
from groundinsight import api


@pytest.fixture
def session():
    s = api.ProjectSession.from_example("minimal")
    yield s
    s.close()


def test_new_frequencies_get_the_scaling_one(session):
    session.apply(api.UpdateFault(name="fault", fields={"scalings": {50.0: 0.8}}))
    session.apply(api.SetFrequencies(frequencies=[50.0, 250.0, 350.0]))
    assert session.network.faults["fault"].scalings == {50.0: 0.8, 250.0: 1.0, 350.0: 1.0}
    assert "SCALINGS_INCOMPLETE" not in {d.code for d in session.check().diagnostics}


def test_removed_frequencies_lose_their_scaling(session):
    session.apply(api.SetFrequencies(frequencies=[50.0, 250.0]))
    session.apply(api.UpdateFault(name="fault", fields={"scalings": {50.0: 1.0, 250.0: 0.5}}))
    session.apply(api.SetFrequencies(frequencies=[250.0]))
    assert session.network.faults["fault"].scalings == {250.0: 0.5}


def test_a_fault_without_scalings_keeps_none(session):
    session.apply(api.UpdateFault(name="fault", fields={"scalings": {}}))
    session.apply(api.SetFrequencies(frequencies=[50.0, 150.0]))
    assert session.network.faults["fault"].scalings == {}


def test_undo_restores_the_scalings(session):
    session.apply(api.UpdateFault(name="fault", fields={"scalings": {50.0: 0.8}}))
    session.apply(api.SetFrequencies(frequencies=[50.0, 250.0]))
    session.undo()
    assert session.network.frequencies == [50.0]
    assert session.network.faults["fault"].scalings == {50.0: 0.8}
    session.redo()
    assert session.network.faults["fault"].scalings == {50.0: 0.8, 250.0: 1.0}


def test_the_solve_equals_explicit_scalings(session):
    session.apply(api.SetFrequencies(frequencies=[50.0, 250.0]))
    session.apply(api.UpdateSource(name="src", fields={"values": {50.0: 1000.0, 250.0: 100.0}}))
    net = session.network
    with warnings.catch_warnings():
        warnings.simplefilter("error", gi.GroundinsightWarning)
        gi.run_fault(net, "fault")
    # Second way: the same network built directly, with the scaling given.
    twin = api.EXAMPLES["minimal"]()
    twin.frequencies = [50.0, 250.0]
    twin.faults["fault"].scalings = {50.0: 1.0, 250.0: 1.0}
    for name, source in net.sources.items():
        twin.sources[name].values = dict(source.values)
    gi.run_fault(twin, "fault")
    columns = ["bus_name", "frequency_Hz", "EPR_V"]
    assert net.res_buses("fault").select(columns).equals(twin.res_buses("fault").select(columns))
    assert net.res_buses("fault").filter(pl.col("frequency_Hz") == 250.0).height == 3
