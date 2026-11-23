# tests/test_capacitive_currents.py
r"""
Charging currents of the branches and the currents of the neutral-point
elements in the solve.

Every number is checked a second way: against a closed form, against the same
network with the currents written out as plain sources (the construction of
the study notebook, which was itself checked against a nodal system set up by
hand, the cut cable and the independent solver ``_reference_mna``), and
against that independent solver here.
"""

import cmath
import math

import pytest

import groundinsight as gi

from tests import _reference_mna as oracle

F = 50.0
U_N = 20e3
C_NF = 250.0  # nF/km, NKT data sheet NA2XS(F)2Y 1x150/25 12/20 kV
UW = gi.BusType(name="UW", system_type="Substation", voltage_level=20.0, impedance_formula="0.5")
ONS = gi.BusType(name="ONS", system_type="Substation", voltage_level=20.0, impedance_formula="rho/50")
PLAIN = gi.standard_branch_types()["NA2XS(F)2Y 1x150RM/25 12/20 kV"].model_copy(
    update={"c_shield_nf_per_km": None, "c_earth_nf_per_km": None, "voltage_level": None})
CABLE = PLAIN.model_copy(update={"c_shield_nf_per_km": C_NF, "voltage_level": 20.0})
FEEDER = (("UW", "S1", 3.0), ("S1", "S2", 3.0), ("S2", "S3", 4.0))


def charging(km: float, f: float = F, nf: float = C_NF, u: float = U_N) -> complex:
    """``j*sqrt(3)*omega*C'*l*U_n`` in A."""
    return 1j * math.sqrt(3) * 2 * math.pi * f * nf * 1e-9 * km * u


def feeder(cable=CABLE, frequencies=(F,), sections=FEEDER) -> gi.Network:
    net = gi.create_network(name="feeder", frequencies=list(frequencies))
    gi.create_bus(name="UW", type=UW, network=net)
    for k, (a, b, km) in enumerate(sections, start=1):
        gi.create_bus(name=b, type=ONS, network=net)
        gi.create_branch(name=f"K{k}", type=cable, from_bus=a, to_bus=b, length=km, network=net)
    return net


def written_out(frequencies=(F,), sections=FEEDER, unit=None) -> gi.Network:
    """The feeder without capacitances, the half charging currents as plain sources at the branch ends."""
    net = feeder(PLAIN, frequencies, sections)
    for k, (a, b, km) in enumerate(sections, start=1):
        for end in (a, b):
            values = {f: (unit or {}).get(f, 1.0 if f == F else 0.0) * abs(charging(km)) * 1j / 2
                      for f in frequencies}
            gi.create_source(name=f"IC_K{k}_{end}", bus=end, values=values, network=net)
    return net


def potentials(net: gi.Network, fault: str = "F", f: float = F):
    result = gi.run_fault(net, fault)
    assert result is net.results[fault]
    return {row["bus_name"]: cmath.rect(row["EPR_V"], math.radians(row["EPR_degree"]))
            for row in net.res_buses(fault).iter_rows(named=True) if row["frequency_Hz"] == f}


def key(net: gi.Network, fault: str = "F", f: float = F):
    return next(row for row in net.res_fault(fault).iter_rows(named=True) if row["frequency_Hz"] == f)


def same(a, b, rel=1e-12):
    scale = max(abs(v) for v in b.values())
    assert set(a) == set(b)
    assert max(abs(a[n] - b[n]) for n in b) <= rel * scale


class TestIsolated:
    def test_the_fault_current_is_the_closed_form_and_needs_no_source(self):
        net = feeder()
        gi.create_isolated_neutral(name="N", bus="UW", voltage_level=20.0, network=net)
        gi.create_fault(name="F", bus="S3", network=net)
        gi.run_fault(net, "F")
        expected = math.sqrt(3) * 2 * math.pi * F * C_NF * 1e-9 * 10.0 * U_N
        assert key(net)["I_F_A"] == pytest.approx(expected, rel=1e-13)
        assert expected == pytest.approx(27.206990, abs=1e-6)

    def test_phase_currents_follow_kirchhoff(self):
        net = feeder()
        gi.create_isolated_neutral(name="N", bus="UW", voltage_level=20.0, network=net)
        gi.create_fault(name="F", bus="S3", network=net)
        gi.run_fault(net, "F")
        ic = [abs(charging(km)) for _, _, km in FEEDER]
        by_hand = {"K1": ic[0] / 2, "K2": ic[0] + ic[1] / 2, "K3": ic[0] + ic[1] + ic[2] / 2}
        solved = net.electrical_network.phase_currents[F]
        for name, value in by_hand.items():
            assert abs(solved[name]) == pytest.approx(value, rel=1e-12)

    @pytest.mark.parametrize("fault_bus", ["S3", "S1", "UW"])
    def test_equal_to_the_currents_written_out_as_sources(self, fault_bus):
        net = feeder()
        gi.create_isolated_neutral(name="N", bus="UW", voltage_level=20.0, network=net)
        gi.create_fault(name="F", bus=fault_bus, network=net)
        reference = written_out()
        gi.create_fault(name="F", bus=fault_bus, network=reference)
        same(potentials(net), potentials(reference))
        for column in ("I_F_A", "U_E_V", "I_E_A", "r", "Z_E_Ohm"):
            assert key(net)[column] == pytest.approx(key(reference)[column], rel=1e-12)

    def test_the_written_out_network_agrees_with_the_independent_solver(self):
        reference = written_out()
        gi.create_fault(name="F", bus="S3", network=reference)
        solved = potentials(reference)
        independent = oracle.to_package_frame(oracle.solve_package(oracle.reference_network(reference, F, "F")))["u"]
        same(solved, independent, rel=1e-11)

    def test_two_stations_closed_form(self):
        # U_E = I_C/2 * Z_B (Z_s - Z_m) / (Z_s + Z_A + Z_B): the half at B does nothing
        net = feeder(sections=(("UW", "B", 5.0),))
        gi.create_isolated_neutral(name="N", bus="UW", voltage_level=20.0, network=net)
        gi.create_fault(name="F", bus="B", network=net)
        gi.run_fault(net, "F")
        branch = net.branches["K1"]
        zs, zm = complex(branch.self_impedance[F]), complex(branch.mutual_impedance[F])
        za, zb = complex(net.buses["UW"].impedance[F]), complex(net.buses["B"].impedance[F])
        expected = abs(charging(5.0) / 2 * zb * (zs - zm) / (zs + za + zb))
        assert key(net)["U_E_V"] == pytest.approx(expected, rel=1e-12)
        assert expected == pytest.approx(3.2003, abs=5e-5)

    def test_the_part_that_is_not_modelled_is_taken_at_the_bus_of_the_element(self):
        net = feeder()
        gi.create_isolated_neutral(name="N", bus="UW", voltage_level=20.0, remaining_capacitive_current=40.0,
                                   network=net)
        gi.create_fault(name="F", bus="S3", network=net)
        reference = written_out()
        gi.create_source(name="rest", bus="UW", values={F: 40j}, network=reference)
        gi.create_fault(name="F", bus="S3", network=reference)
        same(potentials(net), potentials(reference))

    def test_an_inactive_element_feeds_nothing(self):
        net = feeder()
        neutral = gi.create_isolated_neutral(name="N", bus="UW", voltage_level=20.0, network=net)
        gi.create_fault(name="F", bus="S3", network=net)
        neutral.active = False
        with pytest.raises(ValueError, match="no sources"):
            gi.run_fault(net, "F")

    def test_the_path_mode_is_refused(self):
        net = feeder()
        gi.create_isolated_neutral(name="N", bus="UW", voltage_level=20.0, network=net)
        gi.create_fault(name="F", bus="S3", network=net)
        with pytest.raises(ValueError, match="phase_current_mode='auto'"):
            gi.run_fault(net, "F", phase_current_mode="paths")


class TestResonantAndLowImpedance:
    COIL = 0.03 * abs(charging(10.0)) - 1.05j * abs(charging(10.0))

    def test_resonant_equals_the_written_out_network(self):
        net = feeder()
        gi.create_resonant_neutral(name="coil", bus="UW", voltage_level=20.0, coil_current=-self.COIL.imag,
                                   active_residual_current=self.COIL.real, network=net)
        gi.create_fault(name="F", bus="S3", network=net)
        reference = written_out()
        gi.create_source(name="coil", bus="UW", values={F: self.COIL}, network=reference)
        gi.create_fault(name="F", bus="S3", network=reference)
        same(potentials(net), potentials(reference))
        # the fault carries the residual current, the earthing network far more
        assert key(net)["I_F_A"] == pytest.approx(abs(self.COIL + charging(10.0)), rel=1e-12)
        assert key(net)["I_F_A"] == pytest.approx(1.5864, abs=5e-5)
        assert key(net)["U_E_V"] == pytest.approx(5.1208, abs=5e-5)
        assert key(net)["r"] == pytest.approx(2.2181, abs=5e-5)

    def test_a_fault_at_the_bus_of_the_coil_sees_the_capacitive_currents_only(self):
        isolated = feeder()
        gi.create_isolated_neutral(name="N", bus="UW", voltage_level=20.0, network=isolated)
        gi.create_fault(name="F", bus="UW", network=isolated)
        resonant = feeder()
        gi.create_resonant_neutral(name="coil", bus="UW", voltage_level=20.0, coil_current=28.0, network=resonant)
        gi.create_fault(name="F", bus="UW", network=resonant)
        same(potentials(resonant), potentials(isolated))

    def test_the_scaling_acts_on_the_neutral_current_not_on_the_charging_currents(self):
        net = feeder()
        gi.create_low_impedance_neutral(name="R", bus="UW", voltage_level=20.0, current=1000.0, network=net)
        gi.create_fault(name="F", bus="S3", scalings={F: 0.6}, network=net)
        reference = written_out()
        gi.create_source(name="R", bus="UW", values={F: 600.0}, network=reference)
        gi.create_fault(name="F", bus="S3", network=reference)
        same(potentials(net), potentials(reference))
        assert key(net)["I_F_A"] == pytest.approx(abs(600.0 + charging(10.0)), rel=1e-12)

    def test_the_scaling_does_not_act_on_a_coil(self):
        net = feeder()
        gi.create_resonant_neutral(name="coil", bus="UW", voltage_level=20.0, coil_current=28.0, network=net)
        gi.create_fault(name="F", bus="S3", scalings={F: 0.5}, network=net)
        other = feeder()
        gi.create_resonant_neutral(name="coil", bus="UW", voltage_level=20.0, coil_current=28.0, network=other)
        gi.create_fault(name="F", bus="S3", network=other)
        same(potentials(net), potentials(other))

    def test_a_source_next_to_a_neutral_keeps_its_scaling(self):
        net = feeder()
        gi.create_isolated_neutral(name="N", bus="UW", voltage_level=20.0, network=net)
        gi.create_source(name="Q", bus="UW", values={F: 100.0}, network=net)
        gi.create_fault(name="F", bus="S3", scalings={F: 0.5}, network=net)
        reference = written_out()
        gi.create_source(name="Q", bus="UW", values={F: 50.0}, network=reference)
        gi.create_fault(name="F", bus="S3", network=reference)
        same(potentials(net), potentials(reference))


class TestOverheadLine:
    LINE = gi.BranchType(name="line", grounding_conductor=False, self_impedance_formula="(0.3 + j*f/50*0.35)*l",
                         mutual_impedance_formula="0*l", phase_impedance_formula="(0.3 + j*f/50*0.35)*l",
                         c_earth_nf_per_km=5.5, voltage_level=20.0)

    def test_the_current_to_earth_flows_through_the_electrode_of_the_fault_station(self):
        net = feeder(self.LINE, sections=(("UW", "S1", 5.0), ("S1", "S2", 5.0)))
        gi.create_isolated_neutral(name="N", bus="UW", voltage_level=20.0, network=net)
        gi.create_fault(name="F", bus="S2", network=net)
        gi.run_fault(net, "F")
        current = abs(charging(10.0, nf=5.5))
        assert key(net)["I_F_A"] == pytest.approx(current, rel=1e-12)
        # no earthing conductor joins the stations: U_E = I_C * Z_S2, r = 1
        assert key(net)["U_E_V"] == pytest.approx(current * 2.0, rel=1e-12)
        assert key(net)["r"] == pytest.approx(1.0, rel=1e-12)
        epr = potentials(net)
        assert abs(epr["UW"]) == pytest.approx(0.0, abs=1e-12)

    def test_nothing_is_taken_at_the_stations(self):
        net = feeder(self.LINE, sections=(("UW", "S1", 5.0), ("S1", "S2", 5.0)))
        gi.create_isolated_neutral(name="N", bus="UW", voltage_level=20.0, network=net)
        gi.create_fault(name="F", bus="S2", network=net)
        gi.run_fault(net, "F")
        rows = {row["bus_name"]: row for row in net.res_buses("F").iter_rows(named=True) if row["frequency_Hz"] == F}
        assert rows["UW"]["I_inj_A"] == pytest.approx(0.0, abs=1e-12)
        assert rows["S1"]["I_inj_A"] == pytest.approx(0.0, abs=1e-12)
        assert rows["S2"]["I_inj_A"] == pytest.approx(abs(charging(10.0, nf=5.5)), rel=1e-12)


class TestHarmonics:
    def test_isolated_the_entered_current_is_the_fault_current_and_is_distributed_like_the_fundamental(self):
        net = feeder(frequencies=(F, 250.0))
        gi.create_isolated_neutral(name="N", bus="UW", voltage_level=20.0, harmonic_currents={250.0: 4.0},
                                   network=net)
        gi.create_fault(name="F", bus="S3", network=net)
        gi.run_fault(net, "F")
        assert key(net, f=250.0)["I_F_A"] == pytest.approx(4.0, rel=1e-12)
        share = 4.0 / abs(charging(10.0))
        reference = written_out((F, 250.0), unit={F: 1.0, 250.0: -1j * share})
        gi.create_fault(name="F", bus="S3", network=reference)
        same(potentials(net, f=250.0), potentials(reference, f=250.0))

    def test_resonant_the_coil_takes_its_share(self):
        coil = 1.05 * abs(charging(10.0))
        net = feeder(frequencies=(F, 250.0))
        gi.create_resonant_neutral(name="coil", bus="UW", voltage_level=20.0, coil_current=coil,
                                   harmonic_currents={250.0: 4j}, network=net)
        gi.create_fault(name="F", bus="S3", network=net)
        gi.run_fault(net, "F")
        assert key(net, f=250.0)["I_F_A"] == pytest.approx(4.0, rel=1e-12)
        capacitive = 4j / (1 - 1.05 / 25)                    # I_C(h) - I_C(h) k / h^2 = I_res(h)
        reference = written_out((F, 250.0), unit={F: 1.0, 250.0: capacitive / 1j / abs(charging(10.0))})
        gi.create_source(name="coil", bus="UW", values={F: -1j * coil, 250.0: -capacitive * 1.05 / 25},
                         network=reference)
        gi.create_fault(name="F", bus="S3", network=reference)
        same(potentials(net, f=250.0), potentials(reference, f=250.0))
        same(potentials(net), potentials(reference))

    def test_low_impedance_the_harmonic_flows_through_the_neutral(self):
        net = feeder(frequencies=(F, 250.0))
        gi.create_low_impedance_neutral(name="R", bus="UW", voltage_level=20.0, current=1000.0,
                                        harmonic_currents={250.0: 10.0}, network=net)
        gi.create_fault(name="F", bus="S3", network=net)
        reference = written_out((F, 250.0))
        gi.create_source(name="R", bus="UW", values={F: 1000.0, 250.0: 10.0}, network=reference)
        gi.create_fault(name="F", bus="S3", network=reference)
        same(potentials(net, f=250.0), potentials(reference, f=250.0))

    def test_without_capacitance_the_harmonic_stays_at_the_bus_of_the_element(self):
        net = feeder(PLAIN, frequencies=(F, 250.0))
        gi.create_isolated_neutral(name="N", bus="UW", voltage_level=20.0, harmonic_currents={250.0: 4.0},
                                   network=net)
        gi.create_fault(name="F", bus="S3", network=net)
        reference = feeder(PLAIN, frequencies=(F, 250.0))
        gi.create_source(name="Q", bus="UW", values={F: 0.0, 250.0: 4.0}, network=reference)
        gi.create_fault(name="F", bus="S3", network=reference)
        same(potentials(net, f=250.0), potentials(reference, f=250.0))

    def test_a_harmonic_on_the_resonance_is_refused(self):
        net = feeder(frequencies=(F, 100.0))
        gi.create_resonant_neutral(name="coil", bus="UW", voltage_level=20.0, coil_current=4 * abs(charging(10.0)),
                                   harmonic_currents={100.0: 1.0}, network=net)
        gi.create_fault(name="F", bus="S3", network=net)
        with pytest.raises(ValueError, match="resonance"):
            gi.run_fault(net, "F")


class TestUnchanged:
    def test_a_network_without_the_new_data_gives_the_released_numbers(self):
        station = gi.BusType(name="station", system_type="MV", voltage_level=20.0, impedance_formula="rho/50")
        net = gi.create_network(name="pinned", frequencies=[50.0, 250.0])
        for bus in ("A", "B", "C"):
            gi.create_bus(name=bus, type=station, network=net)
        gi.create_branch(name="K1", type=PLAIN, from_bus="A", to_bus="B", length=2.0, network=net)
        gi.create_branch(name="K2", type=PLAIN, from_bus="B", to_bus="C", length=3.0, network=net)
        gi.create_source(name="Q", bus="A", values={50.0: 1000.0, 250.0: 20.0}, network=net)
        gi.create_fault(name="F", bus="C", scalings={50.0: 0.9}, network=net)
        with pytest.warns(gi.InputWarning, match="no scaling at 250 Hz"):
            result = gi.run_fault(net, "F")
        assert result.input_digest == "gi1:33a04214cbdcdd6697665eab7b4f7f2f"
        rows = net.res_fault("F").select("U_E_V", "r", "I_E_A").to_dicts()
        assert rows[0] == {"U_E_V": 359.25024088244555, "r": 0.3053173200938621, "I_E_A": 274.7855880844759}
        assert rows[1] == {"U_E_V": 2.9965544705001035, "r": 0.08085562836455726, "I_E_A": 1.617112567291145}
        assert net.electrical_network.sources is net.sources


class TestTransient:
    def test_a_transient_study_says_that_it_leaves_the_neutral_out(self):
        from groundinsight.simulation import waveforms

        resistive = gi.BranchType(name="resistive", grounding_conductor=True, self_impedance_formula="0.5*l",
                                  mutual_impedance_formula="0*l")       # nothing else for the solver to remark on
        net = feeder(resistive)
        gi.create_source(name="Q", bus="UW", values={F: 100.0}, network=net)
        neutral = gi.create_isolated_neutral(name="N", bus="UW", voltage_level=20.0, network=net)
        gi.create_fault(name="F", bus="S3", network=net)
        study = gi.TransientStudy(net, "F")
        study.set_source_waveform("Q", waveforms.step(amplitude=100.0))
        with pytest.warns(gi.ModelAssumptionWarning, match="take no part") as caught:
            study.solve(t_end=0.02, dt=1e-3)
        assert [w.message.code for w in caught if hasattr(w.message, "code")] == ["TRANSIENT_WITHOUT_NEUTRALS"]
        neutral.active = False
        study = gi.TransientStudy(net, "F")
        study.set_source_waveform("Q", waveforms.step(amplitude=100.0))
        study.solve(t_end=0.02, dt=1e-3)            # silent: warnings are errors in this suite
