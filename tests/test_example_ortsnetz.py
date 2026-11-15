# tests/test_example_ortsnetz.py
"""
The example project ``docs/examples/ortsnetzstation_5_haeuser.json``.

A station and five houses on a PEN, drawn in the GUI, with earthing
characteristics computed by groundfield (two-layer soil). The file must keep
loading and solving to the same numbers; the earthing impedance is checked a
second way against the parallel connection of the file's own characteristics
(the PEN between them is a few milliohms). The comparison with groundfield's
full field solution of the coupled arrangement (3.324 Ohm, the node model
being 43 % low because it ignores the mutual resistance of the electrodes) is
the subject of the example, not of this test.
"""

import pathlib

import pytest

import groundinsight as gi

EXAMPLE = pathlib.Path(__file__).resolve().parents[1] / "docs" / "examples" / "ortsnetzstation_5_haeuser.json"


def test_the_example_project_solves_to_its_documented_numbers():
    project = gi.load_project(EXAMPLE)
    net = project.network
    assert list(net.buses) == ["UW", "ONS", "H1", "H2", "H3", "H4", "H5"]
    assert set(project.diagram.nodes) == set(net.buses) and project.diagram.color_scheme == "u_touch"
    gi.run_fault(net, "F")
    row = net.res_fault("F").row(0, named=True)  # 50 Hz
    assert row["Z_E_Ohm"] == pytest.approx(1.879387, rel=1e-6)
    assert (row["U_vT_max_bus"], row["U_vT_max_V"]) == ("ONS", pytest.approx(573.9245, rel=1e-6))
    station = net.buses["ONS"].resolved_characteristic()
    house = net.buses["H1"].resolved_characteristic()
    z_station = abs(station.evaluate(net.buses["ONS"].specific_earth_resistance, [50.0]).impedance[0])
    z_house = abs(house.evaluate(net.buses["H1"].specific_earth_resistance, [50.0]).impedance[0])
    assert row["Z_E_Ohm"] == pytest.approx(1 / (1 / z_station + 5 / z_house), rel=0.01)
    assert station.provenance.source == "groundfield" and station.provenance.solver == "mom"
