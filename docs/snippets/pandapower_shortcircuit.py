# docs/snippets/pandapower_shortcircuit.py
"""
Short-circuit characteristics from pandapower: the second example of the how-to
"Import from pandapower" and of the I/O page.

A 20 kV feeder -- infeed ``UW`` and three stations on 1.2 km cable sections --
is built in pandapower with the minimum data an earth-fault calculation
(``fault="1ph"``) needs: the short-circuit power and the positive- and
zero-sequence ratios of the external grid, the positive- and zero-sequence
data of the lines. The values are illustrative: an MV network earthed through
a low resistance. ``calc_sc`` solves the case; ``groundinsight`` reads it,
writes the characteristics of the fault at ``S2`` onto the grounding model and
checks the cable screens thermally.

Needs the optional extra: ``pip install "groundinsight[pandapower]"``.
Run it as a script: ``python docs/snippets/pandapower_shortcircuit.py``.
"""

import polars as pl
import pandapower as pp
import pandapower.shortcircuit as sc

import groundinsight as gi
from groundinsight.diagnostics import collecting

# --8<-- [start:case]
net_pp = pp.create_empty_network(f_hz=50.0)
names = ["UW", "S1", "S2", "S3"]
index = {name: pp.create_bus(net_pp, vn_kv=20.0, name=name) for name in names}
pp.create_ext_grid(net_pp, index["UW"],
                   s_sc_max_mva=300.0,   # short-circuit power of the infeed
                   rx_max=0.1,           # R/X, positive sequence
                   x0x_max=1.0,          # X0/X1
                   r0x0_max=11.0)        # R0/X0 -- low-resistance earthing
for i, (a, b) in enumerate(zip(names[:-1], names[1:]), start=1):
    pp.create_line_from_parameters(
        net_pp, index[a], index[b], length_km=1.2, name=f"K{i}",
        r_ohm_per_km=0.100, x_ohm_per_km=0.110, c_nf_per_km=400.0, max_i_ka=0.5,
        r0_ohm_per_km=0.400, x0_ohm_per_km=0.350, c0_nf_per_km=400.0)
sc.calc_sc(net_pp, fault="1ph", case="max", ip=True, ith=True, tk_s=0.5)
# --8<-- [end:case]

# --8<-- [start:read]
sc_results = gi.read_shortcircuit_results(net_pp)  # amperes, one row per bus
# --8<-- [end:read]

# --8<-- [start:apply]
cable_system = gi.BranchType(
    name="3x NA2XS(F)2Y 1x300RM/25 12/20 kV, trefoil",
    grounding_conductor=True,
    self_impedance_formula=(
        "(0.727/3 + pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/0.03151272))*l"
    ),
    mutual_impedance_formula=(
        "(pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/0.03151272))*l"
    ),
    conductor_material="Cu", cross_section_mm2=69.7,  # three 25 mm2 screens, see the data-sheet how-to
    theta_final_C=gi.final_temperature("Cu", "XLPE"),
)
station = gi.BusType(name="station", system_type="Station", voltage_level=20.0,
                     impedance_formula="2*rho/(3*1.13*sqrt(100.0))")
defaults = gi.ImportDefaults(rho=100.0, frequencies=[50.0], default_bus_type=station,
                             default_branch_type=cable_system)
net = gi.from_pandapower(net_pp, defaults=defaults, voltage_level_kV=20.0)
gi.create_source(name="infeed", bus="UW", values={50.0: 1000.0}, network=net)
gi.create_fault(name="F", bus="S2", network=net)

with collecting() as records:
    report = gi.apply_shortcircuit_characteristics(net, net_pp, "F", set_source_values=True)
gi.run_fault(net, "F")
limits = gi.check_conductor_limits(net, "F")
# --8<-- [end:apply]

sc = sc_results  # short name for the checks of the test suite

if __name__ == "__main__":
    row = sc_results.filter(pl.col("bus_name") == "S2").row(0, named=True)
    print(f"S2: I_k'' = {row['i_k_a']:.1f} A, R/X = {row['r_to_x']:.3f}, "
          f"kappa = {row['kappa']:.4f} ({row['kappa_origin']})")
    print(f"S2: i_p = {row['i_p_a']:.1f} A, m = {row['m']:.5f}, I_th = {row['i_th_a']:.1f} A, "
          f"t_k = {row['t_k_s']:g} s")
    applied = report.row(0, named=True)
    print(f"source 'infeed': {applied['i_k_previous_a']:.1f} A -> {applied['i_k_a']:.1f} A "
          f"(share {applied['share']:g}), kappa {applied['kappa']:.4f}")
    print(f"fault 'F': t_k_s = {net.faults['F'].t_k_s:g} s, n_factor = {net.faults['F'].n_factor:g}")
    print("apply diagnostics:", [r.code for r in records])
    for limit in limits.iter_rows(named=True):
        print(f"{limit['branch_name']}: I_th = {limit['I_th_A']:.1f} A, "
              f"I_adm = {limit['I_admissible_A']:.1f} A, utilization = {limit['utilization']:.3f}")
