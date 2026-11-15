# tests/test_characteristic_network.py
"""
Earthing characteristics inside a network: resolution, derivation, results.

Each quantity is checked on two ways:

* the bus impedance from a table at grid points against the table itself,
  and a table sampled from a formula against that formula (exact at the grid
  points, within the interpolation bound between them, and the EPR follows);
* ``U_vT`` in ``res_buses`` against ``k_touch`` from
  :meth:`EarthingCharacteristic.evaluate` times ``|EPR|`` of the same row,
  and the maxima of ``res_fault`` against the maxima of ``res_buses``.
"""

import math
import warnings

import polars as pl
import pytest

import groundinsight as gi
from groundinsight.diagnostics import collecting
from tests._characteristic_cases import F_GRID, FREQS, RHO_GRID, _formula, _network, _table

# --- resolution and derivation -------------------------------------------------


def test_without_characteristic_nothing_changes():
    net = _network()
    bus = net.buses["S1"]
    assert bus.resolved_characteristic() is None
    assert bus.k_touch is None and bus.k_transfer is None
    gi.run_fault(net, "F")
    frame = net.res_buses("F")
    assert frame["U_vT_V"].null_count() == frame.height
    row = net.res_fault("F").row(0, named=True)
    assert row["U_vT_max_V"] is None and row["U_vT_max_bus"] is None


def test_table_at_grid_points_gives_the_table_and_the_bus_overrides_the_type():
    table = _table()
    net = _network(station_characteristic=table, rho=200.0)
    for name, rho in (("S1", 200.0), ("S2", 100.0)):
        bus = net.buses[name]
        assert bus.resolved_characteristic() is table
        i = RHO_GRID.index(rho)
        for j, f in enumerate(F_GRID[:3]):
            assert complex(bus.impedance[f]) == table.impedance_Ohm[i][j]
            assert bus.k_touch[f] == table.k_touch[i][j]
            assert bus.k_transfer[f] == table.k_transfer[i][j]
    # A bus characteristic wins over the type's.
    own = _table(name="own", k_touch=[[0.9] * 4] * 5)
    net.update_bus("S1", characteristic=own)
    assert net.buses["S1"].resolved_characteristic() is own
    assert set(net.buses["S1"].k_touch.values()) == {0.9}
    assert net.buses["S2"].resolved_characteristic() is table


def test_a_table_sampled_from_the_formula_reproduces_the_formula():
    """Second way for the impedance: grid points exactly, between them within
    the interpolation error, and the EPR follows."""
    by_formula = _network(rho=200.0)
    by_table = _network(station_characteristic=_table(), rho=200.0)
    for f in FREQS:
        assert complex(by_table.buses["S1"].impedance[f]) == _formula(200.0, f)
        # SymPy and math may differ in the last bit of the formula itself.
        assert complex(by_table.buses["S1"].impedance[f]) == pytest.approx(
            complex(by_formula.buses["S1"].impedance[f]), rel=1e-15)
    gi.run_fault(by_formula, "F")
    gi.run_fault(by_table, "F")
    a = by_formula.res_buses("F")["EPR_V"].to_list()
    b = by_table.res_buses("F")["EPR_V"].to_list()
    assert a == pytest.approx(b, rel=1e-12)
    # Off the grid: rho = 300 lies between 200 and 400.
    off = _network(station_characteristic=_table(), rho=300.0)
    for f in FREQS:
        z = complex(off.buses["S1"].impedance[f])
        assert z == pytest.approx(_formula(300.0, f), rel=5e-3)


def test_formula_source_takes_the_characteristic_formula_or_the_type_formula():
    k_only = _table(impedance_Ohm=None, impedance_source="formula")
    net = _network(station_characteristic=k_only)
    reference = _network()
    assert net.buses["S1"].impedance == reference.buses["S1"].impedance  # the type formula
    assert net.buses["S1"].k_touch is not None
    own_formula = _table(impedance_source="formula", impedance_formula="rho / 100")
    net.update_bus("S1", characteristic=own_formula)
    assert {complex(z) for z in net.buses["S1"].impedance.values()} == {2.0 + 0j}


def test_assigning_a_characteristic_rederives_and_makes_results_stale():
    net = _network()
    gi.run_fault(net, "F")
    assert net.stale_results() == []
    net.buses["S1"].characteristic = _table()
    assert net.buses["S1"].k_touch is not None
    assert net.stale_results() == ["F"]
    net.buses["S1"].characteristic = None
    assert net.buses["S1"].k_touch is None
    assert net.stale_results() == []  # back to the inputs of the result


def test_a_characteristic_with_an_active_impedance_is_refused_atomically():
    net = _network()
    before = dict(net.buses["S1"].impedance)
    # Extrapolating far beyond the grid turns the angle past 90 degrees.
    steep = gi.EarthingCharacteristic(rho_Ohm_m=(100.0, 101.0), frequency_Hz=(50.0,),
                                      impedance_Ohm=[[1.0], [1j]])
    with pytest.raises(ValueError):
        net.buses["S1"].characteristic = steep
    assert net.buses["S1"].characteristic is None
    assert net.buses["S1"].impedance == before


# --- results ---------------------------------------------------------------


def test_touch_voltages_are_the_factors_times_the_epr():
    table = _table()
    net = _network(station_characteristic=table, rho=300.0)
    gi.run_fault(net, "F")
    frame = net.res_buses("F")
    for row in frame.filter(~pl.col("is_rms")).iter_rows(named=True):
        if row["bus_name"] == "Q":
            assert row["U_vT_V"] is None and row["U_transfer_V"] is None
            continue
        rho = net.buses[row["bus_name"]].specific_earth_resistance
        k = table.evaluate(rho, [row["frequency_Hz"]])
        assert row["U_vT_V"] == pytest.approx(k.k_touch[0] * row["EPR_V"], rel=1e-14)
        assert row["U_transfer_V"] == pytest.approx(k.k_transfer[0] * row["EPR_V"], rel=1e-14)
    for name in ("S1", "S2"):
        per_f = frame.filter((pl.col("bus_name") == name) & ~pl.col("is_rms"))["U_vT_V"].to_list()
        rms = frame.filter((pl.col("bus_name") == name) & pl.col("is_rms"))["U_vT_V"].item()
        assert rms == pytest.approx(math.sqrt(sum(v * v for v in per_f)), rel=1e-14)


def test_res_fault_reports_the_largest_touch_voltage_and_its_bus():
    net = _network(station_characteristic=_table(), rho=300.0)
    gi.run_fault(net, "F")
    buses = net.res_buses("F")
    for row in net.res_fault("F").iter_rows(named=True):
        selection = buses.filter(pl.col("is_rms")) if row["is_rms"] else buses.filter(
            pl.col("frequency_Hz") == row["frequency_Hz"])
        for column, top_column, bus_column in (
            ("U_vT_V", "U_vT_max_V", "U_vT_max_bus"),
            ("U_transfer_V", "U_transfer_max_V", "U_transfer_max_bus"),
        ):
            best = selection.drop_nulls(column).sort(column, descending=True).row(0, named=True)
            assert row[top_column] == best[column]
            assert row[bus_column] == best["bus_name"]


def test_the_key_figure_dto_carries_the_touch_columns():
    from groundinsight.api import ProjectSession

    session = ProjectSession(_network(station_characteristic=_table(), rho=300.0))
    result = session.solve("F")
    rms = [row for row in result.key_figures.rows if row.is_rms][0]
    assert rms.U_vT_max_V == session.network.res_fault("F").filter(pl.col("is_rms"))["U_vT_max_V"].item()


def test_extrapolation_is_a_diagnostic_of_the_run_and_of_the_check():
    net = _network(station_characteristic=_table(), rho=1200.0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", gi.ModelAssumptionWarning)
        result = gi.run_fault(net, "F")
    codes = [(d.code, tuple(map(tuple, d.elements))) for d in result.diagnostics]
    assert ("CHARACTERISTIC_EXTRAPOLATED", (("bus", "S1"),)) in codes
    assert all(code != "CHARACTERISTIC_EXTRAPOLATED" or elements == (("bus", "S1"),)
               for code, elements in codes)
    message = next(d.message for d in result.diagnostics if d.code == "CHARACTERISTIC_EXTRAPOLATED")
    assert "rho = 1200" in message and "station A" in message
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # a finding of the check, not a failed check
        checked = [d.code for d in gi.check_network(net)]
    assert "CHARACTERISTIC_EXTRAPOLATED" in checked and "CHECK_FAILED" not in checked
    inside = _network(station_characteristic=_table(), rho=300.0)
    with collecting() as records:
        gi.run_fault(inside, "F")
    assert "CHARACTERISTIC_EXTRAPOLATED" not in [d.code for d in records]


# --- persistence -----------------------------------------------------------


def test_json_round_trip_keeps_characteristics_and_touch_results():
    net = _network(station_characteristic=_table(), bus_characteristic=_table(name="own"), rho=300.0)
    gi.run_fault(net, "F")
    again = gi.Network.model_validate_json(net.model_dump_json())
    assert again.buses["S1"].characteristic == net.buses["S1"].characteristic
    assert again.buses["S2"].type.characteristic == net.buses["S2"].type.characteristic
    assert again.res_buses("F").equals(net.res_buses("F"))
    assert again.stale_results() == []


def test_tables_round_trip_keeps_characteristics():
    net = _network(station_characteristic=_table(), bus_characteristic=_table(name="own"), rho=300.0)
    tables = net.to_tables()
    assert tables["buses"].filter(pl.col("name") == "S2")["characteristic"].item() is None
    copy = gi.network_from_tables(tables)
    for name in net.buses:
        assert copy.buses[name].characteristic == net.buses[name].characteristic
        assert copy.buses[name].type.characteristic == net.buses[name].type.characteristic
        assert copy.buses[name].impedance == net.buses[name].impedance
        assert copy.buses[name].k_touch == net.buses[name].k_touch


def test_tables_report_an_unreadable_characteristic_cell():
    tables = _network().to_tables()
    tables["buses"] = tables["buses"].with_columns(
        pl.when(pl.col("name") == "S1").then(pl.lit('{"rho_Ohm_m": []}'))
        .otherwise(pl.col("characteristic")).alias("characteristic"))
    with pytest.raises(ValueError, match="characteristic"):
        gi.network_from_tables(tables)


def test_database_round_trip_keeps_characteristics_and_touch_results(tmp_path):
    net = _network(station_characteristic=_table(), bus_characteristic=_table(name="own"), rho=300.0)
    gi.run_fault(net, "F")
    with gi.Database(tmp_path / "char.db") as database:
        database.save_network(net)
        again = database.load_network(net.name)
    for name in net.buses:
        assert again.buses[name].characteristic == net.buses[name].characteristic
        assert again.buses[name].type.characteristic == net.buses[name].type.characteristic
        assert again.buses[name].k_touch == net.buses[name].k_touch
        assert again.buses[name].k_transfer == net.buses[name].k_transfer
    assert again.res_buses("F").equals(net.res_buses("F"))
    assert again.stale_results() == []


def test_a_database_without_the_new_columns_is_migrated(tmp_path):
    """A file written before characteristics existed gains the columns on open."""
    import sqlite3

    path = tmp_path / "old.db"
    net = _network()
    with gi.Database(path) as database:
        database.save_network(net)
    with sqlite3.connect(path) as conn:  # the layout of 2026.11.1
        for table, column in (("buses", "characteristic"), ("buses", "k_touch"),
                              ("buses", "k_transfer"), ("bus_types", "characteristic")):
            conn.execute(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
    with gi.Database(path) as database:
        again = database.load_network(net.name)
    assert again.buses["S1"].impedance == net.buses["S1"].impedance
    assert again.buses["S1"].characteristic is None and again.buses["S1"].k_touch is None
    with sqlite3.connect(path) as conn:
        bus_columns = {row[1] for row in conn.execute("PRAGMA table_info(buses)")}
        type_columns = {row[1] for row in conn.execute("PRAGMA table_info(bus_types)")}
    assert {"characteristic", "k_touch", "k_transfer"} <= bus_columns
    assert "characteristic" in type_columns
