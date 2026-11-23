# Neutral-point elements, supply areas and the earth current by source location

A system with an isolated, resonant-earthed or low-impedance-earthed neutral is
described by three things:

- the **capacitance per kilometre** of its branches
  (`BranchType.c_shield_nf_per_km` to the earthing conductor of the branch,
  `BranchType.c_earth_nf_per_km` to earth) and their **voltage level**
  (`BranchType.voltage_level`);
- a **neutral-point element** at the bus where the neutral is treated
  (`IsolatedNeutral`, `ResonantNeutral`, `LowImpedanceNeutral`, created with
  `create_isolated_neutral`, `create_resonant_neutral`,
  `create_low_impedance_neutral`), which gives the system its voltage;
- nothing else: the charging currents of the branches and what flows through
  the neutral are derived, and `run_fault` solves them with everything else.

`supply_areas` shows what follows from that before anything is solved: which
element feeds which branches, the capacitive earth-fault current of the area,
the sum of the coil currents and the detuning. `earth_current_contributions`
breaks the earth current of a solved fault down by the buses the current returns
to -- the table to read when the reduction factor of a fault exceeds 1. See
[Capacitive currents and the reduction factor](../explanation/capacitive-currents.md)
for the model and its derivation.

```python
import groundinsight as gi

station = gi.BusType(name="station", system_type="MV", voltage_level=20.0, impedance_formula="2")
cable = gi.standard_branch_types()["NA2XS(F)2Y 1x150RM/25 12/20 kV"]

net = gi.create_network(name="feeder", frequencies=[50.0])
for bus in ("UW", "S1", "S2"):
    gi.create_bus(name=bus, type=station, network=net)
gi.create_branch(name="K1", type=cable, from_bus="UW", to_bus="S1", length=5.0, network=net)
gi.create_branch(name="K2", type=cable, from_bus="S1", to_bus="S2", length=5.0, network=net)
gi.create_resonant_neutral(name="coil", bus="UW", voltage_level=20.0, coil_current=28.5,
                           active_residual_current=0.8, network=net)
gi.create_fault(name="F", bus="S2", network=net)

print(gi.supply_areas(net).select("neutral", "I_CE_A", "I_coil_A", "detuning", "I_residual_A"))
gi.run_fault(net, "F")
print(net.res_fault("F").select("frequency_Hz", "I_F_A", "I_E_A", "U_E_V", "r"))
print(gi.earth_current_contributions(net, "F").select("bus_name", "sources", "I_S_A", "r_S", "I_E_A"))
```

## Supply areas

::: groundinsight.supply
    options:
      members:
        - supply_areas
        - SUPPLY_AREAS_SCHEMA

## The earth current by source location

::: groundinsight.analysis.contributions
    options:
      members:
        - earth_current_contributions
        - EARTH_CURRENT_SCHEMA
