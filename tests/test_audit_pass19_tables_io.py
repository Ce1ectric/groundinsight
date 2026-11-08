# tests/test_audit_pass19_tables_io.py
"""
Audit pass 19 -- building a network from tables and exporting it as tables (USE6).

The people who use groundinsight keep their networks in spreadsheets and in
exports of network-planning tools. Until this pass the only way in was one
``create_*`` call per element, and the only way out was the model's JSON.
``gi.network_from_tables(...)`` and ``Network.to_tables()`` close that gap
with one pinned schema per table -- the same schema a GUI table editor uses.

Pinned here:

* the schema of every table (names, column order, dtypes);
* the round trip network -> tables -> network is identical (``model_dump_json``
  without results), through Polars frames and through CSV files, for every
  example network -- and, as a second, independent route, the rebuilt
  network solves to the same numbers;
* formulas from tables pass the same formula guard as every other formula;
* malformed tables raise one ``ValueError`` that names table, row and column
  of every problem.
"""

from __future__ import annotations

import warnings

import polars as pl
import pytest

import groundinsight as gi

FREQ = [50.0, 250.0]


# --------------------------------------------------------------------------
# example networks
# --------------------------------------------------------------------------


def _chain() -> gi.Network:
    """Two faults, partial scalings, IEC 60909 metadata, descriptions."""
    bus_type = gi.BusType(name="bt", system_type="MV", voltage_level=20.0,
                          impedance_formula="0.1*rho", description="rod")
    branch_type = gi.BranchType(
        name="brt", grounding_conductor=True,
        self_impedance_formula="(0.1+0.2*j*f/50)*l",
        mutual_impedance_formula="(0.05+0.1*j*f/50)*l",
    )
    net = gi.create_network("chain", frequencies=FREQ, description="demo chain")
    for name in ("S", "A", "F"):
        gi.create_bus(name, bus_type, 100.0, network=net)
    gi.create_branch("SA", branch_type, "S", "A", 1.0, network=net,
                     description="feeder")
    gi.create_branch("AF", branch_type, "A", "F", 2.5, specific_earth_resistance=50.0,
                     parallel_coefficient=0.5, network=net)
    gi.create_fault("F", "F", {50.0: 1.0, 250.0: 0.2}, network=net, t_k_s=0.5,
                    n_factor=0.9, description="end of line")
    gi.create_fault("FA", "A", {50.0: 0.5}, network=net)
    gi.create_source("SRC", "S", {50.0: 1000.0 + 50.0j, 250.0: 200.0}, network=net,
                     i_k_a=1000.0, r_to_x=0.1)
    return net


def _ring_with_everything() -> gi.Network:
    """RLC and thermal type data, inactive elements, an active fault."""
    bus_type = gi.BusType(
        name="station", system_type="MV", voltage_level=20.0,
        impedance_formula="0.1*rho + 0.5 + j*f*1e-3",
        R_formula="0.1*rho + 0.5", L_formula="1e-3/(2*pi)",
        earthing_conductor_material="Cu", earthing_conductor_cross_section_mm2=95.0,
        electrode_material="Steel", electrode_cross_section_mm2=50.0,
        electrode_current_split=0.5,
    )
    cable = gi.BranchType(
        name="NA2XS2Y", grounding_conductor=True,
        self_impedance_formula="(0.25 + j*f*0.012)*l",
        mutual_impedance_formula="(0.05 + j*f*0.010)*l",
        phase_impedance_formula="(0.1 + j*f*0.004)*l",
        R_self_formula="0.25*l", L_self_formula="0.012/(2*pi)*l",
        conductor_material="Cu", cross_section_mm2=35.0, theta_final_C=250.0,
    )
    overhead = gi.BranchType(
        name="overhead", grounding_conductor=False,
        self_impedance_formula="(0.3 + j*f*0.016)*l",
        mutual_impedance_formula="(0.05 + j*f*0.008)*l",
    )
    net = gi.create_network("ring", frequencies=[50.0, 150.0, 250.0])
    names = [f"R{i}" for i in range(5)]
    for i, name in enumerate(names):
        gi.create_bus(name, bus_type, 50.0 + 25.0 * i, network=net)
    for i in range(4):
        gi.create_branch(f"K{i}", cable, names[i], names[(i + 1) % 4],
                         0.8 + 0.3 * i, network=net)
    gi.create_branch("OHL", overhead, "R3", "R4", 4.0, network=net)
    net.branches["K2"].active = False
    gi.create_fault("F2", "R2", {50.0: 1.0, 150.0: 0.3, 250.0: 0.1}, network=net)
    gi.create_fault("F4", "R4", {}, network=net)
    gi.create_source("SRC", "R0", {50.0: 800.0, 150.0: 50.0, 250.0: 20.0},
                     network=net)
    net.set_active_fault("F4")
    return net


def _thevenin() -> gi.Network:
    bus_type = gi.BusType(name="bt", system_type="MV", voltage_level=20.0,
                          impedance_formula="0.1*rho")
    branch_type = gi.BranchType(name="brt", grounding_conductor=True,
                                self_impedance_formula="(0.1+0.2*j*f/50)*l",
                                mutual_impedance_formula="(0.05+0.1*j*f/50)*l")
    net = gi.create_network("thevenin", frequencies=FREQ)
    for name in ("S", "F"):
        gi.create_bus(name, bus_type, 100.0, network=net)
    gi.create_branch("SF", branch_type, "S", "F", 1.0, network=net)
    gi.create_fault("F", "F", {50.0: 1.0, 250.0: 1.0}, network=net)
    gi.create_voltage_source(
        "V", "S", voltage={50.0: 11547.0, 250.0: 500.0 + 10.0j},
        source_impedance={50.0: 0.5 + 2.0j, 250.0: 0.5 + 10.0j}, network=net,
    )
    return net


def _unicode() -> gi.Network:
    """Names and formulas that a CSV writer has to quote and encode."""
    bus_type = gi.BusType(name="Erder, Typ \"A\"", system_type="Umspannwerk",
                          voltage_level=110.0, impedance_formula="rho*0.01 + 1")
    branch_type = gi.BranchType(name="Kabel", grounding_conductor=True,
                                self_impedance_formula="(0.25 + j*f*0.012)*l",
                                mutual_impedance_formula="(0.0 + j*f*0.012)*l")
    net = gi.create_network("Umspannwerk Süd Ω", frequencies=[50.0])
    gi.create_bus("UW Süd", bus_type, 120.0, network=net, description="Ω, µ, ä")
    gi.create_bus("Station 7", bus_type, 80.0, network=net, description="")
    gi.create_branch("UW Süd – Station 7", branch_type, "UW Süd", "Station 7",
                     3.2, network=net)
    gi.create_fault("Fehler 7", "Station 7", {50.0: 1.0}, network=net)
    gi.create_source("Einspeisung", "UW Süd", {50.0: 1500.0}, network=net)
    return net


NETWORKS = {
    "chain": _chain,
    "ring_with_everything": _ring_with_everything,
    "thevenin": _thevenin,
    "unicode": _unicode,
}


def _json(net, exclude=("results",)):
    return net.model_dump_json(exclude=set(exclude))


# --------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------


def test_to_tables_returns_every_table_with_its_pinned_schema():
    from groundinsight.io.tables import TABLE_SCHEMAS

    tables = _chain().to_tables()
    assert list(tables) == list(TABLE_SCHEMAS)
    for name, frame in tables.items():
        assert dict(frame.schema) == TABLE_SCHEMAS[name], name


def test_the_schemas_cover_every_input_field_of_the_models():
    """A field added to a model must not be lost on the way through the
    tables. Derived fields (impedance dictionaries, RLC values, the results
    and paths, the ``active`` flag of a fault) are recomputed, not stored."""
    from groundinsight.io.tables import TABLE_SCHEMAS

    expected = {
        "bus_types": set(gi.BusType.model_fields),
        "branch_types": set(gi.BranchType.model_fields),
        "buses": set(gi.Bus.model_fields) - {"impedance", "R", "L", "C"},
        "branches": set(gi.Branch.model_fields) - {
            "self_impedance", "mutual_impedance", "phase_impedance",
            "R_self", "L_self", "C_self", "R_mutual", "M_mutual",
        },
        "sources": set(gi.Source.model_fields) - {"values", "voltage", "source_impedance"},
        "faults": set(gi.Fault.model_fields) - {"scalings"},
    }
    for table, fields in expected.items():
        assert set(TABLE_SCHEMAS[table]) == fields, table


def test_the_exports():
    assert "network_from_tables" in gi.__all__
    assert gi.network_from_tables.__module__ == "groundinsight.io.tables"
    assert callable(gi.Network.to_tables)


# --------------------------------------------------------------------------
# USE6 -- the round trip
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(NETWORKS))
def test_round_trip_through_frames_is_identical(kind):
    original = NETWORKS[kind]()
    rebuilt = gi.network_from_tables(original.to_tables())
    assert _json(rebuilt) == _json(original)


@pytest.mark.parametrize("kind", sorted(NETWORKS))
def test_round_trip_through_csv_files_is_identical(kind, tmp_path):
    original = NETWORKS[kind]()
    for name, frame in original.to_tables().items():
        frame.write_csv(tmp_path / f"{name}.csv")
    from_directory = gi.network_from_tables(tmp_path)
    from_paths = gi.network_from_tables(
        {path.stem: path for path in tmp_path.glob("*.csv")}
    )
    assert _json(from_directory) == _json(original)
    assert _json(from_paths) == _json(original)


@pytest.mark.parametrize("kind", sorted(NETWORKS))
def test_the_rebuilt_network_solves_to_the_same_numbers(kind):
    """Second route: not the serialised model but what it computes."""
    original = NETWORKS[kind]()
    rebuilt = gi.network_from_tables(original.to_tables())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for fault in original.faults:
            assert (
                gi.solve(rebuilt, fault).model_dump_json()
                == gi.solve(original, fault).model_dump_json()
            )


def test_round_trip_of_the_shared_fixture_and_a_solved_network(simple_network):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gi.run_fault(simple_network, "F")
    rebuilt = gi.network_from_tables(simple_network.to_tables())
    assert _json(rebuilt) == _json(simple_network)
    assert rebuilt.results == {}


def test_enumerated_paths_are_derived_data_and_not_part_of_the_tables():
    original = _chain()
    gi.create_paths(original)
    assert original.paths
    rebuilt = gi.network_from_tables(original.to_tables())
    assert rebuilt.paths == {}
    assert _json(rebuilt, ("results", "paths")) == _json(original, ("results", "paths"))


def test_tables_can_be_passed_by_keyword_and_from_a_spreadsheet():
    """``pl.read_excel(path, sheet_id=0)`` returns a dict of sheet frames --
    the same shape as ``to_tables``; integer columns, as a spreadsheet
    delivers them, are accepted where floats are expected."""
    tables = _chain().to_tables()
    rebuilt = gi.network_from_tables(**tables)
    assert _json(rebuilt) == _json(_chain())

    sheets = dict(tables)
    sheets["frequencies"] = pl.DataFrame({"frequency_Hz": [50, 250]})
    sheets["bus_types"] = sheets["bus_types"].with_columns(pl.col("voltage_level").cast(pl.Int64))
    assert _json(gi.network_from_tables(sheets)) == _json(_chain())


def test_only_frequencies_bus_types_and_buses_are_required():
    tables = {
        "frequencies": pl.DataFrame({"frequency_Hz": [50.0]}),
        "bus_types": pl.DataFrame({"name": ["bt"], "system_type": ["MV"],
                                   "voltage_level": [20.0],
                                   "impedance_formula": ["0.1*rho"]}),
        "buses": pl.DataFrame({"name": ["B1"], "type": ["bt"]}),
    }
    net = gi.network_from_tables(tables, name="minimal")
    assert net.name == "minimal"
    assert net.buses["B1"].specific_earth_resistance == 100.0  # model default
    assert net.buses["B1"].impedance[50.0] == gi.ComplexNumber(real=10.0, imag=0.0)
    assert gi.check_network(net)[0].code == "NO_SOURCE"


def test_to_tables_refuses_two_different_types_under_one_name():
    net = _chain()
    other = gi.BusType(name="bt", system_type="MV", voltage_level=20.0,
                       impedance_formula="0.2*rho")
    gi.create_bus("X", other, 100.0, network=net)
    with pytest.raises(ValueError, match="'bt'"):
        net.to_tables()


# --------------------------------------------------------------------------
# security -- the formula guard applies to formulas from tables
# --------------------------------------------------------------------------


def test_a_formula_from_a_table_passes_the_formula_guard(tmp_path):
    marker = tmp_path / "pwned"
    payload = f"__import__('os').system('touch {marker}')"
    tables = _chain().to_tables()
    tables["bus_types"] = tables["bus_types"].with_columns(
        pl.lit(payload).alias("impedance_formula")
    )
    for name, frame in tables.items():
        frame.write_csv(tmp_path / f"{name}.csv")
    with pytest.raises(ValueError) as caught:
        gi.network_from_tables(tmp_path)
    message = str(caught.value)
    assert "bus_types" in message and "impedance_formula" in message
    assert not marker.exists()


# --------------------------------------------------------------------------
# malformed tables -- one ValueError, every problem with its place
# --------------------------------------------------------------------------


def _tables():
    return _chain().to_tables()


def _with(tables, name, frame):
    tables = dict(tables)
    tables[name] = frame
    return tables


def test_a_missing_required_table_is_named():
    tables = _tables()
    del tables["buses"]
    with pytest.raises(ValueError, match="buses"):
        gi.network_from_tables(tables)


def test_a_missing_required_column_is_named():
    tables = _tables()
    tables = _with(tables, "buses", tables["buses"].drop("type"))
    with pytest.raises(ValueError, match=r"buses.*'type'"):
        gi.network_from_tables(tables)


def test_an_unknown_column_is_refused_unless_extras_are_allowed():
    tables = _tables()
    tables = _with(tables, "branches",
                   tables["branches"].rename({"length": "lenght"}))
    with pytest.raises(ValueError, match="lenght"):
        gi.network_from_tables(tables)
    tables = _tables()
    tables = _with(tables, "buses",
                   tables["buses"].with_columns(pl.lit("x").alias("comment")))
    assert _json(gi.network_from_tables(tables, ignore_extra_columns=True)) == _json(_chain())


def test_an_unparsable_number_is_reported_with_table_row_column_and_value(tmp_path):
    tables = _tables()
    branches = tables["branches"].with_columns(pl.col("length").cast(pl.Utf8))
    branches = branches.with_columns(
        pl.when(pl.col("name") == "AF").then(pl.lit("2,5")).otherwise(pl.col("length"))
        .alias("length")
    )
    with pytest.raises(ValueError) as caught:
        gi.network_from_tables(_with(tables, "branches", branches))
    message = str(caught.value)
    assert "branches" in message and "row 2" in message
    assert "length" in message and "'2,5'" in message


def test_dangling_references_are_reported_with_their_rows():
    tables = _tables()
    buses = tables["buses"].with_columns(
        pl.when(pl.col("name") == "A").then(pl.lit("nope")).otherwise(pl.col("type"))
        .alias("type")
    )
    branches = tables["branches"].with_columns(
        pl.when(pl.col("name") == "SA").then(pl.lit("ghost")).otherwise(pl.col("from_bus"))
        .alias("from_bus")
    )
    scalings = tables["fault_scalings"].with_columns(pl.lit("FX").alias("fault"))
    with pytest.raises(ValueError) as caught:
        gi.network_from_tables(
            _with(_with(_with(tables, "buses", buses), "branches", branches),
                  "fault_scalings", scalings)
        )
    message = str(caught.value)
    assert "buses, row 2, column 'type'" in message and "'nope'" in message
    assert "branches, row 1, column 'from_bus'" in message and "'ghost'" in message
    assert "fault_scalings" in message and "'FX'" in message


def test_duplicate_names_are_reported():
    tables = _tables()
    buses = pl.concat([tables["buses"], tables["buses"].head(1)])
    with pytest.raises(ValueError, match=r"buses.*'S'.*rows 1 and 4"):
        gi.network_from_tables(_with(tables, "buses", buses))


def test_a_model_validation_error_carries_table_row_and_column():
    tables = _tables()
    faults = tables["faults"].with_columns(pl.lit(1.5).alias("n_factor"))
    with pytest.raises(ValueError, match=r"faults, row 1.*n_factor"):
        gi.network_from_tables(_with(tables, "faults", faults))


def test_source_values_must_fit_the_source_type():
    tables = _tables()
    values = tables["source_values"].with_columns(pl.lit("voltage").alias("quantity"))
    with pytest.raises(ValueError, match=r"source_values.*'voltage'.*current"):
        gi.network_from_tables(_with(tables, "source_values", values))


def test_a_bad_boolean_is_reported():
    tables = _tables()
    buses = tables["buses"].with_columns(pl.lit("maybe").alias("active"))
    with pytest.raises(ValueError, match=r"buses, row 1, column 'active'.*'maybe'"):
        gi.network_from_tables(_with(tables, "buses", buses))


def test_all_problems_come_in_one_error():
    tables = _tables()
    buses = tables["buses"].with_columns(pl.lit("maybe").alias("active"))
    branches = tables["branches"].rename({"length": "lenght"})
    faults = tables["faults"].with_columns(pl.lit(1.5).alias("n_factor"))
    with pytest.raises(ValueError) as caught:
        gi.network_from_tables(
            _with(_with(_with(tables, "buses", buses), "branches", branches),
                  "faults", faults)
        )
    message = str(caught.value)
    assert "'maybe'" in message and "lenght" in message and "n_factor" in message


def test_the_documented_schema_matches_the_pinned_one():
    """``docs/api/io.md`` lists every table with its columns, required ones
    in bold; it must not drift from ``TABLE_SCHEMAS`` / ``REQUIRED_COLUMNS``."""
    import pathlib
    import re

    from groundinsight.io.tables import REQUIRED_COLUMNS, TABLE_SCHEMAS

    page = pathlib.Path(__file__).resolve().parents[1] / "docs" / "api" / "io.md"
    text = page.read_text(encoding="utf-8")
    lines = {
        match.group(1): match.group(2)
        for match in re.finditer(r"^- `([a-z_]+)`: (.*)$", text, re.M)
    }
    for table, schema in TABLE_SCHEMAS.items():
        line = lines[table]
        columns = re.findall(r"`([A-Za-z0-9_]+)`", line.split(" — ")[0])
        documented = [c for c in columns if c in schema]
        assert documented == list(schema), table
        bold = re.findall(r"\*\*`([A-Za-z0-9_]+)`\*\*", line)
        assert bold == list(REQUIRED_COLUMNS[table]), table


def test_a_bad_cell_is_reported_once_and_an_empty_required_cell_is_named():
    """A cell that cannot be converted is one problem, not two (the model
    would add "Field required" for the value that never arrived); an empty
    required cell says so instead of passing pydantic's generic text."""
    tables = _tables()
    branches = tables["branches"].with_columns(pl.col("length").cast(pl.Utf8))
    branches = branches.with_columns(
        pl.when(pl.col("name") == "SA").then(pl.lit("1,0")).otherwise(pl.col("length"))
        .alias("length"),
        pl.when(pl.col("name") == "AF").then(pl.lit(None)).otherwise(pl.col("to_bus"))
        .alias("to_bus"),
    )
    with pytest.raises(ValueError) as caught:
        gi.network_from_tables(_with(tables, "branches", branches))
    lines = [line for line in str(caught.value).splitlines() if line.startswith("  - ")]
    assert lines == [
        "  - branches, row 1, column 'length': '1,0' is not a number (use a decimal point).",
        "  - branches, row 2, column 'to_bus': the value is empty; this column is required.",
    ]
