# tests/_characteristic_cases.py
"""
Shared test networks with earthing characteristics.

A remote source bus ``Q`` feeds the stations ``S1`` (rho variable) and
``S2`` (100 Ohm*m) over two cables with a phase conductor; the fault ``F``
sits at ``S1``. :func:`_table` samples the station formula on a grid
together with smooth touch and transfer factors, so the table can be
checked against the formula.
"""

import math

import groundinsight as gi

FREQS = [50.0, 150.0, 250.0]
RHO_GRID = (50.0, 100.0, 200.0, 400.0, 800.0)
F_GRID = (50.0, 150.0, 250.0, 1000.0)
FORMULA = "rho * 0.004 + j * f * 0.0002 * sqrt(rho / 100)"


def _formula(rho, f):
    return complex(rho * 0.004, f * 0.0002 * math.sqrt(rho / 100.0))


def _k(rho, f, base):
    return base + 0.02 * math.log(rho / 50.0) + 0.01 * math.log(f / 50.0)


def _table(**extra):
    data = {
        "name": "station A",
        "rho_Ohm_m": RHO_GRID,
        "frequency_Hz": F_GRID,
        "impedance_Ohm": [[_formula(r, f) for f in F_GRID] for r in RHO_GRID],
        "k_touch": [[_k(r, f, 0.2) for f in F_GRID] for r in RHO_GRID],
        "k_transfer": [[_k(r, f, 0.5) for f in F_GRID] for r in RHO_GRID],
    }
    data.update(extra)
    return gi.EarthingCharacteristic(**data)


def _network(station_characteristic=None, bus_characteristic=None, rho=200.0):
    net = gi.create_network(name="char", frequencies=FREQS)
    station = gi.BusType(name="station", system_type="MV/LV", voltage_level=20.0,
                         impedance_formula=FORMULA, characteristic=station_characteristic)
    tower = gi.BusType(name="remote", system_type="HV/MV", voltage_level=110.0,
                       impedance_formula="0.2 + rho * 0")
    cable = gi.BranchType(name="cable", grounding_conductor=True,
                          self_impedance_formula="(0.2 + j * f / 50 * 0.6) * l",
                          mutual_impedance_formula="j * f / 50 * 0.5 * l",
                          phase_impedance_formula="(0.1 + j * f / 50 * 0.3) * l")
    gi.create_bus(name="Q", type=tower, network=net)
    gi.create_bus(name="S1", type=station, network=net, specific_earth_resistance=rho,
                  )
    gi.create_bus(name="S2", type=station, network=net, specific_earth_resistance=100.0)
    if bus_characteristic is not None:
        net.update_bus("S1", characteristic=bus_characteristic)
    gi.create_branch(name="K1", type=cable, from_bus="Q", to_bus="S1", length=2.0, network=net)
    gi.create_branch(name="K2", type=cable, from_bus="S1", to_bus="S2", length=1.0, network=net)
    gi.create_source(name="I", bus="Q", values={f: 1000.0 for f in FREQS}, network=net)
    gi.create_fault(name="F", bus="S1", scalings={50.0: 1.0, 150.0: 0.1, 250.0: 0.05}, network=net)
    return net
