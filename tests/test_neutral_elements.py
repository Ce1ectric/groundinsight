# tests/test_neutral_elements.py
"""
The model of the capacitive currents: capacitance and voltage level of a
branch type, the neutral-point elements, and how a network keeps them.

Nothing is solved here (see ``test_capacitive_currents.py``); the tests pin
the fields, their validation, the edit invariants of the network and that a
network without any of the new data keeps the digest it had before.
"""

import math

import pytest

import groundinsight as gi

#: Digest of the fault "F" of :func:`_plain` as the released version stamps it.
PINNED_DIGEST = "gi1:33a04214cbdcdd6697665eab7b4f7f2f"


def _station() -> gi.BusType:
    return gi.BusType(name="station", system_type="MV", voltage_level=20.0, impedance_formula="rho/50")


def _plain() -> gi.Network:
    """Three stations, two cables, one source: nothing of the new model."""
    cable = gi.standard_branch_types()["NA2XS(F)2Y 1x150RM/25 12/20 kV"].model_copy(
        update={"c_shield_nf_per_km": None, "c_earth_nf_per_km": None, "voltage_level": None})
    net = gi.create_network(name="pinned", frequencies=[50.0, 250.0])
    for bus in ("A", "B", "C"):
        gi.create_bus(name=bus, type=_station(), network=net)
    gi.create_branch(name="K1", type=cable, from_bus="A", to_bus="B", length=2.0, network=net)
    gi.create_branch(name="K2", type=cable, from_bus="B", to_bus="C", length=3.0, network=net)
    gi.create_source(name="Q", bus="A", values={50.0: 1000.0, 250.0: 20.0}, network=net)
    gi.create_fault(name="F", bus="C", scalings={50.0: 0.9}, network=net)
    return net


def _branch_type(**fields) -> gi.BranchType:
    return gi.BranchType(name="cable", grounding_conductor=True, self_impedance_formula="(0.3 + j*f/50*0.7)*l",
                         mutual_impedance_formula="(j*f/50*0.6)*l", **fields)


class TestBranchTypeFields:
    def test_default_is_no_capacitance_and_no_level(self):
        cable = _branch_type()
        assert (cable.c_shield_nf_per_km, cable.c_earth_nf_per_km, cable.voltage_level) == (None, None, None)

    def test_values_are_kept(self):
        cable = _branch_type(c_shield_nf_per_km=250.0, c_earth_nf_per_km=5.5, voltage_level=20.0)
        assert (cable.c_shield_nf_per_km, cable.c_earth_nf_per_km, cable.voltage_level) == (250.0, 5.5, 20.0)

    @pytest.mark.parametrize("field", ["c_shield_nf_per_km", "c_earth_nf_per_km"])
    @pytest.mark.parametrize("value", [-1.0, math.nan, math.inf])
    def test_a_capacitance_is_finite_and_not_negative(self, field, value):
        with pytest.raises(ValueError, match=field):
            _branch_type(**{field: value})

    @pytest.mark.parametrize("value", [0.0, -20.0, math.nan])
    def test_a_voltage_level_is_positive(self, value):
        with pytest.raises(ValueError, match="voltage_level"):
            _branch_type(voltage_level=value)


class TestNeutralClasses:
    def test_isolated(self):
        neutral = gi.IsolatedNeutral(name="N", bus="A", voltage_level=20.0)
        assert neutral.kind == "isolated"
        assert neutral.remaining_capacitive_current == 0.0
        assert neutral.harmonic_currents == {}
        assert neutral.nominal_frequency == 50.0
        assert neutral.active is True

    def test_resonant(self):
        neutral = gi.ResonantNeutral(name="N", bus="A", voltage_level=20.0, coil_current=28.0,
                                     active_residual_current=0.8, harmonic_currents={250.0: 3.0})
        assert neutral.kind == "resonant"
        assert neutral.coil_current == 28.0
        assert complex(neutral.harmonic_currents[250.0]) == 3.0

    def test_low_impedance(self):
        neutral = gi.LowImpedanceNeutral(name="N", bus="A", voltage_level=20.0, current=1000.0)
        assert neutral.kind == "low_impedance"
        assert complex(neutral.current) == 1000.0

    @pytest.mark.parametrize("cls,extra", [
        (gi.IsolatedNeutral, {}), (gi.ResonantNeutral, {"coil_current": 10.0}),
        (gi.LowImpedanceNeutral, {"current": 1000.0})])
    def test_the_voltage_level_is_positive(self, cls, extra):
        with pytest.raises(ValueError, match="voltage_level"):
            cls(name="N", bus="A", voltage_level=0.0, **extra)

    @pytest.mark.parametrize("field", ["coil_current", "active_residual_current", "remaining_capacitive_current"])
    def test_currents_of_a_coil_are_not_negative(self, field):
        fields = {"coil_current": 10.0, field: -1.0}
        with pytest.raises(ValueError, match=field):
            gi.ResonantNeutral(name="N", bus="A", voltage_level=20.0, **fields)

    @pytest.mark.parametrize("cls,extra", [(gi.IsolatedNeutral, {}), (gi.ResonantNeutral, {"coil_current": 10.0})])
    def test_the_fundamental_is_not_entered_as_a_harmonic(self, cls, extra):
        # isolated and resonant: the current at the nominal frequency follows from capacitances and coil
        with pytest.raises(ValueError, match="nominal frequency"):
            cls(name="N", bus="A", voltage_level=20.0, harmonic_currents={50.0: 5.0}, **extra)

    def test_harmonic_keys_are_frequencies(self):
        neutral = gi.IsolatedNeutral(name="N", bus="A", voltage_level=20.0, harmonic_currents={"250": 2.0})
        assert list(neutral.harmonic_currents) == [250.0]
        with pytest.raises(ValueError):
            gi.IsolatedNeutral(name="N", bus="A", voltage_level=20.0, harmonic_currents={-50.0: 2.0})


class TestNetworkKeepsNeutrals:
    def test_add_and_remove(self):
        net = _plain()
        neutral = gi.create_resonant_neutral(name="coil", bus="A", voltage_level=20.0, coil_current=28.0, network=net)
        assert net.neutrals["coil"] is neutral
        assert net.remove_neutral("coil") is neutral
        assert net.neutrals == {}

    def test_the_three_factories(self):
        net = _plain()
        gi.create_isolated_neutral(name="i", bus="A", voltage_level=20.0, network=net)
        gi.create_resonant_neutral(name="r", bus="B", voltage_level=20.0, coil_current=10.0, network=net)
        gi.create_low_impedance_neutral(name="l", bus="C", voltage_level=20.0, current=1000.0, network=net)
        assert [n.kind for n in net.neutrals.values()] == ["isolated", "resonant", "low_impedance"]

    def test_the_bus_must_exist(self):
        net = _plain()
        with pytest.raises(ValueError, match="'X' is not in the network"):
            gi.create_isolated_neutral(name="i", bus="X", voltage_level=20.0, network=net)

    def test_a_name_is_taken_once(self):
        net = _plain()
        gi.create_isolated_neutral(name="i", bus="A", voltage_level=20.0, network=net)
        with pytest.raises(ValueError, match="already exists"):
            gi.create_isolated_neutral(name="i", bus="B", voltage_level=20.0, network=net)
        net.add_neutral(gi.IsolatedNeutral(name="i", bus="B", voltage_level=20.0), overwrite=True)
        assert net.neutrals["i"].bus == "B"

    def test_a_bus_with_a_neutral_is_not_removed(self):
        net = _plain()
        gi.create_bus(name="D", type=_station(), network=net)
        gi.create_isolated_neutral(name="i", bus="D", voltage_level=20.0, network=net)
        with pytest.raises(ValueError, match="neutral 'i'"):
            net.remove_bus("D")

    def test_a_neutral_in_a_network_cannot_move_to_a_missing_bus_or_be_renamed(self):
        net = _plain()
        neutral = gi.create_isolated_neutral(name="i", bus="A", voltage_level=20.0, network=net)
        with pytest.raises(ValueError):
            neutral.bus = "X"
        with pytest.raises(ValueError):
            neutral.name = "j"
        neutral.bus = "B"
        assert net.neutrals["i"].bus == "B"

    def test_unknown_neutral(self):
        with pytest.raises(ValueError, match="Neutral 'x' does not exist"):
            _plain().remove_neutral("x")

    def test_json_keeps_the_classes(self):
        net = _plain()
        gi.create_isolated_neutral(name="i", bus="A", voltage_level=20.0, network=net)
        gi.create_resonant_neutral(name="r", bus="B", voltage_level=20.0, coil_current=10.0,
                                   harmonic_currents={250.0: 1 + 2j}, network=net)
        gi.create_low_impedance_neutral(name="l", bus="C", voltage_level=20.0, current=1000.0, network=net)
        again = gi.Network.model_validate_json(net.model_dump_json())
        assert [type(n).__name__ for n in again.neutrals.values()] == [
            "IsolatedNeutral", "ResonantNeutral", "LowImpedanceNeutral"]
        assert complex(again.neutrals["r"].harmonic_currents[250.0]) == 1 + 2j
        assert again.neutrals["r"]._owning_network() is again


class TestDigest:
    def test_a_network_without_the_new_data_keeps_its_digest(self):
        assert _plain().input_digest("F") == PINNED_DIGEST

    def test_every_new_input_changes_the_digest(self):
        seen = {PINNED_DIGEST}

        def distinct(net):
            digest = net.input_digest("F")
            assert digest not in seen
            seen.add(digest)

        net = _plain()
        gi.create_isolated_neutral(name="i", bus="A", voltage_level=20.0, network=net)
        distinct(net)
        net.neutrals["i"].remaining_capacitive_current = 12.0
        distinct(net)
        net.neutrals["i"].harmonic_currents = {250.0: 1.0}
        distinct(net)
        net.neutrals["i"].active = False
        distinct(net)

        net = _plain()
        net.update_branch("K1", type=net.branches["K1"].type.model_copy(update={"c_shield_nf_per_km": 250.0}))
        distinct(net)
        net.update_branch("K1", type=net.branches["K1"].type.model_copy(update={"voltage_level": 20.0}))
        distinct(net)

        net = _plain()
        net.sources["Q"].voltage_level = 20.0
        distinct(net)
        net = _plain()
        net.faults["F"].voltage_level = 20.0
        distinct(net)


class TestTables:
    def _network(self):
        net = _plain()
        net.update_branch("K1", type=net.branches["K1"].type.model_copy(
            update={"name": "with capacitance", "c_shield_nf_per_km": 250.0, "c_earth_nf_per_km": 1.5,
                    "voltage_level": 20.0}))
        gi.create_isolated_neutral(name="i", bus="A", voltage_level=20.0, remaining_capacitive_current=12.0,
                                   harmonic_currents={250.0: 1 + 2j}, network=net)
        gi.create_resonant_neutral(name="r", bus="B", voltage_level=20.0, coil_current=28.0,
                                   active_residual_current=0.8, nominal_frequency=50.0, network=net)
        gi.create_low_impedance_neutral(name="l", bus="C", voltage_level=20.0, current=900 - 300j,
                                        description="resistor", network=net)
        net.neutrals["l"].active = False
        net.sources["Q"].voltage_level = 20.0
        net.faults["F"].voltage_level = 20.0
        return net

    def test_round_trip_through_the_tables(self):
        net = self._network()
        tables = net.to_tables()
        assert tables["neutrals"]["kind"].to_list() == ["isolated", "resonant", "low_impedance"]
        assert tables["neutral_harmonics"].to_dicts() == [
            {"neutral": "i", "frequency_Hz": 250.0, "real": 1.0, "imag": 2.0}]
        again = gi.network_from_tables(tables)
        assert again.model_dump_json() == net.model_dump_json()

    def test_round_trip_through_csv(self, tmp_path):
        net = self._network()
        for table, frame in net.to_tables().items():
            frame.write_csv(tmp_path / f"{table}.csv")
        assert gi.network_from_tables(tmp_path).model_dump_json() == net.model_dump_json()

    def test_tables_without_neutrals_still_load(self):
        tables = _plain().to_tables()
        del tables["neutrals"], tables["neutral_harmonics"]
        assert gi.network_from_tables(tables).neutrals == {}

    def test_problems_are_reported_with_table_and_row(self):
        import polars as pl

        tables = self._network().to_tables()
        tables["neutrals"] = tables["neutrals"].with_columns(
            pl.when(pl.col("name") == "r").then(pl.lit("petersen")).otherwise(pl.col("kind")).alias("kind"),
            pl.when(pl.col("name") == "i").then(pl.lit("X")).otherwise(pl.col("bus")).alias("bus"),
            pl.when(pl.col("name") == "l").then(None).otherwise(pl.col("current_real")).alias("current_real"))
        with pytest.raises(ValueError) as error:
            gi.network_from_tables(tables)
        message = str(error.value)
        assert "petersen" in message and "'X' is not in buses" in message and "neutrals" in message
        assert "current" in message


class TestDatabase:
    def test_round_trip_through_sqlite(self, tmp_path):
        net = TestTables()._network()
        database = gi.Database(str(tmp_path / "net.db"))
        database.save_network(net)
        again = database.load_network(net.name)
        assert again.model_dump_json() == net.model_dump_json()
        assert again.neutrals["r"]._owning_network() is again
        database.close()

    def test_a_database_of_the_released_schema_is_migrated_in_place(self, tmp_path):
        import sqlite3

        path = str(tmp_path / "old.db")
        database = gi.Database(path)
        database.save_network(_plain())
        database.close()
        connection = sqlite3.connect(path)
        try:
            for table, column in (("networks", "neutrals"), ("branch_types", "c_shield_nf_per_km"),
                                  ("sources", "voltage_level"), ("faults", "voltage_level")):
                connection.execute(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
            connection.commit()
        finally:
            connection.close()
        assert gi.needs_migration(path) == "add_columns"
        report = gi.migrate_database(path)
        assert "neutrals" in report.added_columns.get("networks", [])
        database = gi.Database(path)
        loaded = database.load_network("pinned")
        assert loaded.neutrals == {} and loaded.input_digest("F") == PINNED_DIGEST
        database.close()
