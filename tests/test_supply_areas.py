# tests/test_supply_areas.py
r"""
Voltage levels and supply areas: several levels on one earthing network.

The reference for the solve is the same network built by hand for one level
at a time -- the branches of the other levels with an open phase conductor,
the charging currents and the coil written out as plain sources -- which is
what the model promises to be equivalent to.
"""

import cmath
import math

import pytest

import groundinsight as gi

F = 50.0
LIBRARY = gi.standard_branch_types()
CABLE = LIBRARY["NA2XS(F)2Y 1x150RM/25 12/20 kV"].model_copy(
    update={"c_shield_nf_per_km": 250.0, "c_earth_nf_per_km": None, "voltage_level": 20.0})
NAYY = LIBRARY["NAYY 4x150SM 0.6/1 kV"].model_copy(
    update={"c_shield_nf_per_km": None, "c_earth_nf_per_km": None, "voltage_level": 0.4})
LINE = gi.BranchType(name="110 kV line with earth wire", grounding_conductor=True, voltage_level=110.0,
                     self_impedance_formula="(0.25 + 0.05*f/50 + j*0.75*f/50)*l",
                     mutual_impedance_formula="(0.05*f/50 + j*0.40*f/50)*l",
                     phase_impedance_formula="(0.12 + 0.05*f/50 + j*0.70*f/50)*l")
BUSES = {"UW_N": "0.2", "M1": "3", "M2": "3", "UW": "0.5", "ONS1": "2", "ONS2": "2", "ONS3": "2", "H1": "8", "H2": "8"}
BRANCHES = [("L1", "UW_N", "M1", 4.0, LINE), ("L2", "M1", "M2", 4.0, LINE), ("L3", "M2", "UW", 4.0, LINE),
            ("K1", "UW", "ONS1", 3.0, CABLE), ("K2", "ONS1", "ONS2", 3.0, CABLE), ("K3", "ONS2", "ONS3", 4.0, CABLE),
            ("N1", "ONS2", "H1", 0.10, NAYY), ("N2", "H1", "H2", 0.05, NAYY)]
I_CE = math.sqrt(3) * 2 * math.pi * F * 250e-9 * 10.0 * 20e3
COIL = 1.05 * I_CE
WATT = 0.03 * I_CE


def skeleton(level=None) -> gi.Network:
    """Buses and branches; with ``level`` the branches of the other levels lose their phase conductor."""
    net = gi.create_network(name="levels", frequencies=[F])
    for bus, z in BUSES.items():
        gi.create_bus(name=bus, network=net, type=gi.BusType(
            name=f"T_{bus}", system_type="station", voltage_level=20.0, impedance_formula=z))
    for name, a, b, km, branch_type in BRANCHES:
        if level is not None:
            update = {"c_shield_nf_per_km": None, "voltage_level": None}
            if branch_type.voltage_level != level:
                update["phase_impedance_formula"] = "nan"
            branch_type = branch_type.model_copy(update=update)
        gi.create_branch(name=name, type=branch_type, from_bus=a, to_bus=b, length=km, network=net)
    return net


def modelled() -> gi.Network:
    net = skeleton()
    gi.create_source(name="Netz 110 kV", bus="UW_N", values={F: 8000.0}, voltage_level=110.0, network=net)
    gi.create_resonant_neutral(name="Sternpunkt 20 kV", bus="UW", voltage_level=20.0, coil_current=COIL,
                               active_residual_current=WATT, network=net)
    gi.create_source(name="Trafo ONS2", bus="ONS2", values={F: 1500.0}, voltage_level=0.4, network=net)
    return net


def by_hand(level: float) -> gi.Network:
    net = skeleton(level)
    if level == 110.0:
        gi.create_source(name="Netz 110 kV", bus="UW_N", values={F: 8000.0}, network=net)
    if level == 0.4:
        gi.create_source(name="Trafo ONS2", bus="ONS2", values={F: 1500.0}, network=net)
    if level == 20.0:
        for name, a, b, km, branch_type in BRANCHES:
            if branch_type is CABLE:
                for end in (a, b):
                    gi.create_source(name=f"IC_{name}_{end}", bus=end, values={F: 1j * I_CE * km / 10.0 / 2},
                                     network=net)
        gi.create_source(name="coil", bus="UW", values={F: complex(WATT, -COIL)}, network=net)
    return net


def potentials(net: gi.Network, fault: str = "F"):
    gi.run_fault(net, fault)
    return {row["bus_name"]: cmath.rect(row["EPR_V"], math.radians(row["EPR_degree"]))
            for row in net.res_buses(fault).iter_rows(named=True) if row["frequency_Hz"] == F}


def same(a, b, rel=1e-12):
    scale = max(abs(v) for v in b.values())
    assert max(abs(a[n] - b[n]) for n in b) <= rel * scale


class TestTable:
    def test_three_levels_three_areas(self):
        areas = gi.supply_areas(modelled())
        assert areas.schema == gi.SUPPLY_AREAS_SCHEMA
        rows = areas.to_dicts()
        assert [(r["area"], r["voltage_level_kV"], r["neutral"], r["branches"]) for r in rows] == [
            (1, 0.4, "source", ["N1", "N2"]),
            (2, 20.0, "resonant", ["K1", "K2", "K3"]),
            (3, 110.0, "source", ["L1", "L2", "L3"]),
        ]
        assert rows[1]["feeding_elements"] == ["Sternpunkt 20 kV"]
        assert rows[1]["buses"] == ["UW", "ONS1", "ONS2", "ONS3"]
        assert rows[1]["length_km"] == pytest.approx(10.0)
        assert rows[1]["I_CE_A"] == pytest.approx(I_CE, rel=1e-13)
        assert rows[1]["I_CE_A"] == pytest.approx(27.206990, abs=1e-6)
        assert rows[1]["I_coil_A"] == pytest.approx(COIL)
        assert rows[1]["detuning"] == pytest.approx(-0.05)
        assert rows[1]["I_residual_A"] == pytest.approx(math.hypot(I_CE - COIL, WATT))
        # a prescribed current charges nothing
        assert rows[0]["I_CE_A"] is None and rows[2]["I_CE_A"] is None and rows[2]["detuning"] is None

    def test_the_detuning_takes_every_coil_and_the_part_that_is_not_modelled(self):
        net = modelled()
        gi.create_resonant_neutral(name="dezentral", bus="ONS3", voltage_level=20.0, coil_current=10.0,
                                   remaining_capacitive_current=30.0, network=net)
        row = gi.supply_areas(net).to_dicts()[1]
        assert row["feeding_elements"] == ["Sternpunkt 20 kV", "dezentral"]
        assert row["I_C_remaining_A"] == pytest.approx(30.0)
        assert row["I_CE_A"] == pytest.approx(I_CE + 30.0)
        assert row["I_coil_A"] == pytest.approx(COIL + 10.0)
        assert row["detuning"] == pytest.approx((I_CE + 30.0 - COIL - 10.0) / (I_CE + 30.0))

    def test_an_open_phase_conductor_divides_an_area(self):
        net = modelled()
        open_cable = CABLE.model_copy(update={"name": "open", "phase_impedance_formula": "nan"})
        net.update_branch("K2", type=open_cable)
        rows = gi.supply_areas(net).to_dicts()
        assert rows[1]["branches"] == ["K1"]
        assert rows[1]["I_CE_A"] == pytest.approx(I_CE * 0.3)
        # the low-voltage transformer still feeds its two cables; K3 is fed by nobody
        assert all("K3" not in row["branches"] for row in rows)

    def test_a_network_without_feeding_elements_with_a_level_has_no_area(self):
        net = skeleton()
        gi.create_source(name="Q", bus="UW", values={F: 100.0}, network=net)
        assert gi.supply_areas(net).is_empty()

    def test_different_nominal_frequencies_in_one_area_are_refused(self):
        net = modelled()
        gi.create_isolated_neutral(name="bahn", bus="ONS1", voltage_level=20.0, nominal_frequency=16.7, network=net)
        with pytest.raises(ValueError, match="different nominal frequencies"):
            gi.supply_areas(net)


class TestOneFaultPerLevel:
    @pytest.mark.parametrize("bus,level", [("ONS3", 20.0), ("ONS1", 20.0), ("H2", 0.4), ("M1", 110.0)])
    def test_the_level_follows_from_the_fault_bus(self, bus, level):
        net = modelled()
        gi.create_fault(name="F", bus=bus, network=net)
        reference = by_hand(level)
        gi.create_fault(name="F", bus=bus, network=reference)
        same(potentials(net), potentials(reference))

    def test_only_the_cables_of_the_level_are_charged(self):
        net = modelled()
        gi.create_fault(name="F", bus="ONS3", network=net)
        gi.run_fault(net, "F")
        row = net.res_fault("F").to_dicts()[0]
        assert row["I_F_A"] == pytest.approx(abs(complex(WATT, I_CE - COIL)), rel=1e-12)
        phase = net.electrical_network.phase_currents[F]
        assert all(phase[name] == 0 for name in ("L1", "L2", "L3", "N1", "N2"))
        # ... but they carry the potentials on: the houses see the fault
        epr = {r["bus_name"]: r["EPR_V"] for r in net.res_buses("F").to_dicts() if r["frequency_Hz"] == F}
        assert epr["H2"] == pytest.approx(4.11, abs=5e-3)
        assert epr["ONS3"] == pytest.approx(4.60, abs=5e-3)

    @pytest.mark.parametrize("bus", ["UW", "ONS2"])
    def test_a_bus_with_two_fed_levels_needs_the_level_of_the_fault(self, bus):
        net = modelled()
        gi.create_fault(name="F", bus=bus, network=net)
        with pytest.raises(ValueError, match="several voltage levels"):
            gi.run_fault(net, "F")

    def test_the_fault_names_its_level(self):
        net = modelled()
        gi.create_fault(name="F", bus="UW", voltage_level=110.0, network=net)
        reference = by_hand(110.0)
        gi.create_fault(name="F", bus="UW", network=reference)
        epr = potentials(net)
        same(epr, potentials(reference))
        assert abs(epr["UW"]) == pytest.approx(1805.22, abs=5e-3)

        net = modelled()
        gi.create_fault(name="F", bus="ONS2", voltage_level=0.4, network=net)
        gi.run_fault(net, "F")
        # at the bus of the transformer that feeds it: nothing enters the earthing network
        assert net.res_fault("F").to_dicts()[0]["U_E_V"] == pytest.approx(0.0, abs=1e-9)


class TestLevelsAreOptIn:
    def test_levels_on_the_types_alone_change_nothing(self):
        # no feeding element carries a level: one system, as before
        net = skeleton()
        gi.create_source(name="Q", bus="UW_N", values={F: 8000.0}, network=net)
        gi.create_fault(name="F", bus="H2", network=net)
        plain = gi.create_network(name="levels", frequencies=[F])
        for bus, z in BUSES.items():
            gi.create_bus(name=bus, network=plain, type=gi.BusType(
                name=f"T_{bus}", system_type="station", voltage_level=20.0, impedance_formula=z))
        for name, a, b, km, branch_type in BRANCHES:
            gi.create_branch(name=name, from_bus=a, to_bus=b, length=km, network=plain, type=branch_type.model_copy(
                update={"c_shield_nf_per_km": None, "voltage_level": None}))
        gi.create_source(name="Q", bus="UW_N", values={F: 8000.0}, network=plain)
        gi.create_fault(name="F", bus="H2", network=plain)
        a, b = potentials(net), potentials(plain)
        assert a == b
        assert net.electrical_network.sources is net.sources

    def test_a_branch_without_level_belongs_to_every_level(self):
        net = skeleton()
        net.update_branch("K2", type=CABLE.model_copy(update={"name": "no level", "voltage_level": None}))
        gi.create_isolated_neutral(name="N", bus="UW", voltage_level=20.0, network=net)
        row = gi.supply_areas(net).to_dicts()[0]
        assert row["branches"] == ["K1", "K2", "K3"]
        assert row["I_CE_A"] == pytest.approx(I_CE)


class TestChecks:
    """check_network names what run_fault would refuse, and what it would pass over in silence."""

    def _codes(self, net, **options):
        return [(d.code, d.severity) for d in gi.check_network(net, **options)]

    def test_a_clean_network_with_neutrals_has_no_finding(self):
        net = modelled()
        gi.create_fault(name="F", bus="ONS3", network=net)
        assert self._codes(net) == []

    def test_an_ambiguous_fault_level_is_an_error_with_the_levels_in_the_message(self):
        net = modelled()
        gi.create_fault(name="F", bus="UW", network=net)
        gi.create_fault(name="G", bus="ONS3", network=net)
        findings = [d for d in gi.check_network(net) if d.code == "FAULT_VOLTAGE_LEVEL_AMBIGUOUS"]
        assert [(d.severity, d.elements) for d in findings] == [("error", [("fault", "F"), ("bus", "UW")])]
        assert "20 kV" in findings[0].message and "110 kV" in findings[0].message
        net.faults["F"].voltage_level = 110.0
        assert self._codes(net) == []

    def test_different_nominal_frequencies_in_one_area_are_an_error(self):
        net = modelled()
        gi.create_isolated_neutral(name="bahn", bus="ONS1", voltage_level=20.0, nominal_frequency=16.7, network=net)
        gi.create_fault(name="F", bus="ONS3", network=net)
        codes = self._codes(net)
        assert ("NEUTRAL_FREQUENCY_MISMATCH", "error") in codes
        with pytest.raises(ValueError, match="different nominal frequencies"):
            gi.run_fault(net, "F")

    def test_a_nominal_frequency_that_is_not_solved_is_a_warning(self):
        net = skeleton()
        gi.create_isolated_neutral(name="N", bus="UW", voltage_level=20.0, nominal_frequency=60.0,
                                   harmonic_currents={50.0: 3.0}, network=net)
        gi.create_fault(name="F", bus="ONS3", network=net)
        findings = [d for d in gi.check_network(net) if d.code == "NEUTRAL_FREQUENCY_NOT_SOLVED"]
        assert [(d.severity, d.elements) for d in findings] == [("warning", [("neutral", "N")])]
        assert "60 Hz" in findings[0].message

    def test_a_neutral_on_a_missing_bus_is_an_error(self):
        net = skeleton()
        gi.create_isolated_neutral(name="N", bus="UW", voltage_level=20.0, network=net)
        net.neutrals["N"].__dict__["bus"] = "ghost"       # past the edit invariant, as a damaged file would be
        gi.create_fault(name="F", bus="ONS3", network=net)
        assert ("NEUTRAL_UNKNOWN_BUS", "error") in self._codes(net)


class TestFactorAboveOne:
    def test_the_result_says_where_to_look_when_r_exceeds_one(self):
        net = modelled()
        gi.create_fault(name="F", bus="ONS3", network=net)
        result = gi.run_fault(net, "F")
        notes = [d for d in result.diagnostics if d.code == "EARTH_CURRENT_EXCEEDS_FAULT_CURRENT"]
        assert len(notes) == 1 and notes[0].severity == "info"
        assert "earth_current_contributions" in notes[0].message and "50 Hz" in notes[0].message
        assert net.res_fault("F").to_dicts()[0]["r"] > 1

    def test_no_note_below_one(self):
        net = skeleton()
        gi.create_isolated_neutral(name="N", bus="UW", voltage_level=20.0, network=net)
        gi.create_fault(name="F", bus="ONS3", network=net)
        result = gi.run_fault(net, "F")
        assert [d.code for d in result.diagnostics] == []
