# tests/test_earth_current_contributions.py
r"""
The earth current of a fault as the sum over the buses that take current back.

``I_E = sum_S r_S * I_S``: every bus S at which a feeding element takes its
current out of the earthing network has a reduction factor ``r_S`` of its own
-- a property of the pair (S, fault bus) and the lines between them. The
factors are checked against closed forms and against a direct solve with one
ampere from that bus; the sum against the solved earth current.
"""

import cmath
import math

import pytest

import groundinsight as gi

F = 50.0
UW = gi.BusType(name="UW", system_type="Substation", voltage_level=20.0, impedance_formula="0.5")
ONS = gi.BusType(name="ONS", system_type="Substation", voltage_level=20.0, impedance_formula="rho/50")
PLAIN = gi.standard_branch_types()["NA2XS(F)2Y 1x150RM/25 12/20 kV"].model_copy(
    update={"c_shield_nf_per_km": None, "c_earth_nf_per_km": None, "voltage_level": None})
CABLE = PLAIN.model_copy(update={"c_shield_nf_per_km": 250.0, "voltage_level": 20.0})
FEEDER = (("UW", "S1", 3.0), ("S1", "S2", 3.0), ("S2", "S3", 4.0))
I_CE = math.sqrt(3) * 2 * math.pi * F * 250e-9 * 10.0 * 20e3


def feeder(cable=CABLE, sections=FEEDER) -> gi.Network:
    net = gi.create_network(name="feeder", frequencies=[F])
    gi.create_bus(name="UW", type=UW, network=net)
    for k, (a, b, km) in enumerate(sections, start=1):
        gi.create_bus(name=b, type=ONS, network=net)
        gi.create_branch(name=f"K{k}", type=cable, from_bus=a, to_bus=b, length=km, network=net)
    return net


def with_neutral(kind: str, fault_bus: str = "S3") -> gi.Network:
    net = feeder()
    if kind == "isolated":
        gi.create_isolated_neutral(name="N", bus="UW", voltage_level=20.0, network=net)
    elif kind == "resonant":
        gi.create_resonant_neutral(name="N", bus="UW", voltage_level=20.0, coil_current=1.05 * I_CE,
                                   active_residual_current=0.03 * I_CE, network=net)
    else:
        gi.create_low_impedance_neutral(name="N", bus="UW", voltage_level=20.0, current=1000.0, network=net)
    gi.create_fault(name="F", bus=fault_bus, network=net)
    return net


def rows(net: gi.Network, fault: str = "F"):
    table = gi.earth_current_contributions(net, fault)
    assert table.schema == gi.EARTH_CURRENT_SCHEMA
    return {(row["bus_name"], row["remote_earth"]): row for row in table.iter_rows(named=True)}


def phasor(row, quantity: str) -> complex:
    magnitude_key = quantity if quantity == "r_S" else f"{quantity}_A"
    return cmath.rect(row[magnitude_key], math.radians(row[f"{quantity}_degree"]))


def one_ampere_from(bus: str, fault_bus: str = "S3", sections=FEEDER) -> float:
    """r of a plain network with a single source of 1 A at ``bus``: the direct way."""
    net = feeder(PLAIN, sections)
    gi.create_source(name="unit", bus=bus, values={F: 1.0}, network=net)
    gi.create_fault(name="F", bus=fault_bus, network=net)
    gi.run_fault(net, "F")
    return net.res_fault("F").to_dicts()[0]["r"]


class TestFactors:
    def test_the_factor_of_a_bus_is_what_one_ampere_from_it_gives(self):
        table = rows(with_neutral("isolated"))
        for bus in ("UW", "S1", "S2"):
            assert table[(bus, False)]["r_S"] == pytest.approx(one_ampere_from(bus), rel=1e-11)
        assert table[("UW", False)]["r_S"] == pytest.approx(0.3401, abs=5e-5)
        assert table[("S1", False)]["r_S"] == pytest.approx(0.3154, abs=5e-5)
        assert table[("S2", False)]["r_S"] == pytest.approx(0.2461, abs=5e-5)

    def test_what_is_taken_at_the_fault_bus_does_nothing(self):
        row = rows(with_neutral("isolated"))[("S3", False)]
        assert row["r_S"] == pytest.approx(0.0, abs=1e-14)
        assert row["I_E_A"] == pytest.approx(0.0, abs=1e-12)
        assert row["I_S_A"] == pytest.approx(I_CE * 0.4 / 2, rel=1e-12)

    def test_two_stations_closed_form(self):
        net = feeder(sections=(("UW", "B", 5.0),))
        gi.create_isolated_neutral(name="N", bus="UW", voltage_level=20.0, network=net)
        gi.create_fault(name="F", bus="B", network=net)
        branch = net.branches["K1"]
        zs, zm = complex(branch.self_impedance[F]), complex(branch.mutual_impedance[F])
        za = complex(net.buses["UW"].impedance[F])
        expected = (zs - zm) / (zs + za)
        row = rows(net)[("UW", False)]
        assert phasor(row, "r_S") == pytest.approx(expected, rel=1e-12)
        assert row["r_S"] == pytest.approx(0.316102, abs=5e-7)

    def test_without_coupling_the_galvanic_part_remains(self):
        uncoupled = CABLE.model_copy(update={"name": "uncoupled", "mutual_impedance_formula": "0*l"})
        net = feeder(uncoupled, sections=(("UW", "B", 5.0),))
        gi.create_isolated_neutral(name="N", bus="UW", voltage_level=20.0, network=net)
        gi.create_fault(name="F", bus="B", network=net)
        zs = complex(net.branches["K1"].self_impedance[F])
        za = complex(net.buses["UW"].impedance[F])
        row = rows(net)[("UW", False)]
        assert phasor(row, "r_S") == pytest.approx(zs / (zs + za), rel=1e-12)
        assert row["r_S"] == pytest.approx(0.940067, abs=5e-7)

    def test_the_factors_do_not_depend_on_the_neutral_treatment(self):
        tables = {kind: rows(with_neutral(kind)) for kind in ("isolated", "resonant", "low_impedance")}
        for location in tables["isolated"]:
            for kind in ("resonant", "low_impedance"):
                assert phasor(tables[kind][location], "r_S") == pytest.approx(
                    phasor(tables["isolated"][location], "r_S"), rel=1e-11, abs=1e-14)
        # ... and neither do the currents at the cable ends
        for bus in ("S1", "S2", "S3"):
            for kind in ("resonant", "low_impedance"):
                assert phasor(tables[kind][(bus, False)], "I_S") == pytest.approx(
                    phasor(tables["isolated"][(bus, False)], "I_S"), rel=1e-12)


class TestSum:
    @pytest.mark.parametrize("kind", ["isolated", "resonant", "low_impedance"])
    @pytest.mark.parametrize("fault_bus", ["S3", "S1", "UW"])
    def test_the_contributions_add_up_to_the_earth_current(self, kind, fault_bus):
        net = with_neutral(kind, fault_bus)
        table = rows(net)
        gi.run_fault(net, "F")
        solved = net.res_fault("F").to_dicts()[0]
        total = sum(phasor(row, "I_E") for row in table.values())
        assert abs(total) == pytest.approx(solved["I_E_A"], rel=1e-10)
        assert abs(sum(phasor(row, "I_S") for row in table.values())) == pytest.approx(solved["I_F_A"], rel=1e-10)

    def test_resonant_the_fault_current_is_small_the_earth_current_is_not(self):
        table = rows(with_neutral("resonant"))
        assert table[("UW", False)]["I_S_A"] == pytest.approx(24.500, abs=5e-4)
        assert table[("UW", False)]["I_E_A"] == pytest.approx(8.333, abs=5e-4)
        assert sorted(table[("UW", False)]["sources"]) == ["Charging current at UW", "Neutral N"]
        fault_current = abs(sum(phasor(row, "I_S") for row in table.values()))
        earth_current = abs(sum(phasor(row, "I_E") for row in table.values()))
        assert fault_current == pytest.approx(1.5864, abs=5e-5)
        assert earth_current == pytest.approx(3.5189, abs=5e-5)
        assert earth_current / fault_current == pytest.approx(2.2181, abs=5e-5)

    def test_a_plain_source_has_the_factor_of_the_fault(self):
        net = feeder(PLAIN)
        gi.create_source(name="Q", bus="UW", values={F: 1000.0}, network=net)
        gi.create_fault(name="F", bus="S3", network=net)
        table = rows(net)
        gi.run_fault(net, "F")
        assert list(table) == [("UW", False)]
        assert table[("UW", False)]["r_S"] == pytest.approx(net.res_fault("F").to_dicts()[0]["r"], rel=1e-11)
        assert table[("UW", False)]["sources"] == ["Q"]

    def test_the_network_is_left_as_it_was(self):
        net = with_neutral("resonant")
        before = net.model_dump_json()
        gi.earth_current_contributions(net, "F")
        assert net.model_dump_json() == before
        assert net.results == {}


class TestRemoteEarthAndGuards:
    def test_the_current_of_an_overhead_line_comes_from_remote_earth_with_factor_one(self):
        line = gi.BranchType(name="line", grounding_conductor=False, self_impedance_formula="(0.3 + j*f/50*0.35)*l",
                             mutual_impedance_formula="0*l", phase_impedance_formula="(0.3 + j*f/50*0.35)*l",
                             c_earth_nf_per_km=5.5, voltage_level=20.0)
        net = feeder(line, sections=(("UW", "S1", 5.0), ("S1", "S2", 5.0)))
        gi.create_isolated_neutral(name="N", bus="UW", voltage_level=20.0, network=net)
        gi.create_fault(name="F", bus="S2", network=net)
        table = rows(net)
        assert set(table) == {("UW", True), ("S1", True), ("S2", True)}
        for row in table.values():
            assert phasor(row, "r_S") == pytest.approx(1.0, rel=1e-12)

    def test_a_voltage_source_is_refused(self):
        net = feeder(PLAIN)
        gi.create_voltage_source(name="E", bus="UW", voltage={F: 11547.0}, source_impedance={F: 11.5}, network=net)
        gi.create_fault(name="F", bus="S3", network=net)
        with pytest.raises(ValueError, match="voltage source"):
            gi.earth_current_contributions(net, "F")

    def test_an_unknown_fault_is_refused(self):
        with pytest.raises(ValueError, match="does not exist"):
            gi.earth_current_contributions(feeder(), "nope")
