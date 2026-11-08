# docs/snippets/types_from_datasheets.py
"""
Bus and branch types from data sheets: the worked examples of the how-to.

The single-core cable NA2XS(F)2Y 1x300 RM/25 12/20 kV in soil of
100 Ohm*m, at 50 Hz and at the 5th harmonic. The shield-earth loop impedance
``Z_s'``, the phase-shield mutual impedance ``Z_m'`` and the reduction factor
``r = |1 - Z_m/Z_s|`` are computed three ways:

(a) the closed form with the equivalent earth-return depth
    ``D_E = 658*sqrt(rho/f)``;
(b) Pollaczek's earth-return integral for a cable buried at depth ``h``,
    evaluated numerically with ``scipy.integrate.quad`` -- this shows how good
    the ``D_E`` approximation is;
(c) a one-branch groundinsight network solved with ``run_fault``, whose
    EPR-based reduction factor ``ResultReductionFactor.value`` reproduces (a)
    to rounding.

The same file builds the type of the three-phase cable system in trefoil
(the other two screens), an overhead line with earth wire (Carson's integral
as method (b)) and three earth-electrode types. Run it as a script to print
the tables shown on the page::

    python docs/snippets/types_from_datasheets.py

Every input that does not come from a data sheet is marked "estimate".
"""

import cmath
import math

import numpy as np
from scipy import integrate, special

import groundinsight as gi

# --8<-- [start:datasheet]
# NA2XS(F)2Y 1x300 RM/25 12/20 kV. Sources: Prysmian data sheet
# "NA2XS(F)2Y 12/20KV CPR F" (row 1x300RM/25) and EN 60228 (DC resistance
# of a class-2 conductor of the nominal cross-section, at 20 °C).
R_CORE = 0.100  # Ohm/km, Al conductor 300 mm², data sheet
R_SCREEN = 0.727  # Ohm/km, Cu wire screen 25 mm², EN 60228 value for 25 mm²
D_OVER_INSULATION = 32.9e-3  # m, nominal diameter over insulation, data sheet
T_INSULATION = 5.5e-3  # m, nominal insulation thickness, data sheet
D_OUTER = 41.9e-3  # m, nominal outer diameter, data sheet
DEPTH = 0.7  # m, laying depth of the data sheet's current ratings

# Build-up between the insulation and the screen wires: estimates.
T_SEMICON = 0.6e-3  # m, inner and outer semiconductive layer, each
T_BEDDING = 0.4e-3  # m, semiconductive swelling tape under the wires
D_WIRE = 0.75e-3  # m, diameter of one screen wire

R_SCREEN_MEAN = round(D_OVER_INSULATION / 2 + T_SEMICON + T_BEDDING + D_WIRE / 2, 8)
R_CONDUCTOR = round(D_OVER_INSULATION / 2 - T_INSULATION - T_SEMICON, 8)
GMR_CONDUCTOR = round(math.exp(-0.25) * R_CONDUCTOR, 8)  # solid round conductor

RHO = 100.0  # Ohm*m
FREQUENCIES = [50.0, 250.0]  # Hz
# --8<-- [end:datasheet]


# --8<-- [start:closed_form]
def earth_return_depth(rho, f):
    """Equivalent earth-return depth ``D_E = 658*sqrt(rho/f)`` in m."""
    return 658.0 * math.sqrt(rho / f)


def loop_impedance(r_dc, radius, rho, f):
    """
    Conductor-earth loop impedance per km after Carson, in Ohm/km.

    ``r_dc + pi²·1e-4·f + j·4·pi·1e-4·f·ln(D_E/radius)``. With ``r_dc = 0``
    and the screen radius it is the mutual impedance of a conductor and its
    concentric screen, both with earth return.
    """
    return complex(
        r_dc + math.pi**2 * 1e-4 * f,
        4 * math.pi * 1e-4 * f * math.log(earth_return_depth(rho, f) / radius),
    )


def loop_expression(r_dc, radius, parallel=1):
    """
    The same loop impedance as a formula expression in ``f`` and ``rho``.

    ``parallel`` conductors of resistance ``r_dc`` each are written as
    ``r_dc/parallel``; ``radius`` is then their geometric mean radius.
    """
    if not r_dc:
        resistance = ""
    elif parallel == 1:
        resistance = f"{r_dc!r} + "
    else:
        resistance = f"{r_dc!r}/{parallel} + "
    return (
        f"({resistance}pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/{radius!r}))"
    )


def reduced_phase_expression(z_pp, z_m, z_s):
    """``Z_red = Z_pp - Z_m²/Z_s`` as a formula expression."""
    return f"({z_pp} - {z_m}**2/{z_s})"
# --8<-- [end:closed_form]


# --8<-- [start:types]
z_s_expr = loop_expression(R_SCREEN, R_SCREEN_MEAN)
z_m_expr = loop_expression(0.0, R_SCREEN_MEAN)
z_pp_expr = loop_expression(R_CORE, GMR_CONDUCTOR)

cable = gi.BranchType(
    name="NA2XS(F)2Y 1x300RM/25 12/20 kV, own screen",
    grounding_conductor=True,
    self_impedance_formula=f"{z_s_expr}*l",
    mutual_impedance_formula=f"{z_m_expr}*l",
    phase_impedance_formula=f"{reduced_phase_expression(z_pp_expr, z_m_expr, z_s_expr)}*l",
    conductor_material="Cu",
    cross_section_mm2=25.0,
)
# --8<-- [end:types]


# --8<-- [start:pollaczek]
MU0 = 4e-7 * math.pi  # H/m


def pollaczek_earth_impedance(radius, depth, rho, f):
    """
    Earth-return part of a conductor-earth loop, conductor buried at ``depth``.

    Pollaczek's integral for homogeneous soil, displacement currents
    neglected, in Ohm/km:
    ``jωμ0/(2π)·[K0(m·r) − K0(m·2h) + 2∫ exp(−2h·√(λ²+m²))/(λ+√(λ²+m²)) dλ]``
    with ``m = √(jωμ0/ρ)``.
    """
    omega = 2 * math.pi * f
    m = cmath.sqrt(1j * omega * MU0 / rho)

    def integrand(lam):
        s = cmath.sqrt(lam * lam + m * m)
        return cmath.exp(-2 * depth * s) / (lam + s)

    upper = 40.0 / depth  # the integrand is below exp(-80) beyond
    points = sorted({abs(m), 10 * abs(m), 1 / (2 * depth)})
    parts = [
        integrate.quad(
            lambda lam, part=part: getattr(integrand(lam), part),
            0.0, upper, points=points, limit=400, epsabs=0.0, epsrel=1e-11,
        )[0]
        for part in ("real", "imag")
    ]
    bracket = (
        special.kv(0, m * radius) - special.kv(0, m * 2 * depth) + 2 * complex(*parts)
    )
    return 1j * omega * MU0 / (2 * math.pi) * bracket * 1e3


def internal_reactance(f):
    """Internal reactance of a solid round conductor, ``ωμ0/(8π)``, Ohm/km."""
    return 1j * 2 * math.pi * f * MU0 / (8 * math.pi) * 1e3
# --8<-- [end:pollaczek]


# --8<-- [start:network]
station = gi.BusType(
    name="station", system_type="Substation", voltage_level=20.0,
    impedance_formula="2.0",
)
net = gi.create_network(name="one_branch", frequencies=FREQUENCIES)
gi.create_bus(name="A", type=station, specific_earth_resistance=RHO, network=net)
gi.create_bus(name="B", type=station, specific_earth_resistance=RHO, network=net)
gi.create_branch(
    name="K", type=cable, from_bus="A", to_bus="B", length=1.0,
    specific_earth_resistance=RHO, network=net,
)
gi.create_source(name="infeed", bus="A", values={50.0: 1000.0, 250.0: 100.0}, network=net)
gi.create_fault(name="F", bus="B", scalings={50.0: 1.0, 250.0: 1.0}, network=net)
result = gi.run_fault(net, "F")
r_model = result.reduction_factor.value  # {50.0: ..., 250.0: ...}
# --8<-- [end:network]


def _complex(value):
    """A ``ComplexNumber`` (or number) as a Python complex."""
    return complex(getattr(value, "real", value), getattr(value, "imag", 0.0))


ROWS = []
for f in FREQUENCIES:
    zs_a = loop_impedance(R_SCREEN, R_SCREEN_MEAN, RHO, f)
    zm_a = loop_impedance(0.0, R_SCREEN_MEAN, RHO, f)
    zpp_a = loop_impedance(R_CORE, GMR_CONDUCTOR, RHO, f)
    zm_b = pollaczek_earth_impedance(R_SCREEN_MEAN, DEPTH, RHO, f)
    zs_b = R_SCREEN + zm_b
    zpp_b = (
        R_CORE + internal_reactance(f)
        + pollaczek_earth_impedance(R_CONDUCTOR, DEPTH, RHO, f)
    )
    branch = net.branches["K"]
    ROWS.append({
        "f": f,
        "zs_a": zs_a, "zs_b": zs_b, "zs_c": _complex(branch.self_impedance[f]),
        "zm_a": zm_a, "zm_b": zm_b, "zm_c": _complex(branch.mutual_impedance[f]),
        "zred_a": zpp_a - zm_a**2 / zs_a,
        "zred_b": zpp_b - zm_b**2 / zs_b,
        "zred_c": _complex(branch.phase_impedance[f]),
        "r_a": abs(1 - zm_a / zs_a),
        "r_b": abs(1 - zm_b / zs_b),
        "r_c": r_model[f],
    })


# --8<-- [start:trefoil]
def bundle_equivalent(positions, faulted, r_screen, r_dc, rho, f):
    """
    Equivalent screen-bundle impedances seen by one faulted core, Ohm/km.

    ``positions`` are the cable axes (x, y) in m; the screens are bonded at
    both ends, so they share one voltage drop. Eliminating their individual
    currents from ``Z_ss·I = U·1 − z_c·I_core`` gives
    ``Z_s,eq = 1/(1ᵀ Z_ss⁻¹ 1)`` and ``Z_m,eq = Z_s,eq · 1ᵀ Z_ss⁻¹ z_c``.
    """
    n = len(positions)
    z_ss = np.empty((n, n), dtype=complex)
    z_c = np.empty(n, dtype=complex)
    for i in range(n):
        for k in range(n):
            d = r_screen if i == k else math.dist(positions[i], positions[k])
            z_ss[i, k] = loop_impedance(r_dc if i == k else 0.0, d, rho, f)
        d = r_screen if i == faulted else math.dist(positions[i], positions[faulted])
        z_c[i] = loop_impedance(0.0, d, rho, f)
    ones = np.ones(n)
    z_s_eq = 1 / (ones @ np.linalg.solve(z_ss, ones))
    z_m_eq = z_s_eq * (ones @ np.linalg.solve(z_ss, z_c))
    return complex(z_s_eq), complex(z_m_eq)


def screen_current_shares(positions, faulted, r_screen, r_dc, rho, f):
    """
    Share of each screen in the bundle current, screens ideally bonded.

    With no voltage drop along the screens the coupling alone drives them:
    ``I = −Z_ss⁻¹ z_c`` per ampere in the faulted core. Returns
    ``|I_k| / |Σ I|`` for every screen.
    """
    n = len(positions)
    z_ss = np.empty((n, n), dtype=complex)
    z_c = np.empty(n, dtype=complex)
    for i in range(n):
        for k in range(n):
            d = r_screen if i == k else math.dist(positions[i], positions[k])
            z_ss[i, k] = loop_impedance(r_dc if i == k else 0.0, d, rho, f)
        d = r_screen if i == faulted else math.dist(positions[i], positions[faulted])
        z_c[i] = loop_impedance(0.0, d, rho, f)
    currents = -np.linalg.solve(z_ss, z_c)
    return np.abs(currents) / abs(currents.sum())


TREFOIL = [(0.0, 0.0), (D_OUTER, 0.0), (D_OUTER / 2, D_OUTER * math.sqrt(3) / 2)]
GMR_TREFOIL = round((R_SCREEN_MEAN * D_OUTER**2) ** (1 / 3), 8)  # bundle GMR = core-bundle GMD
# The thermal check compares the bundle current with cross_section_mm2: give the
# cross-section that covers the screen of the faulted core at 50 Hz.
SHARE_FAULTED_SCREEN = screen_current_shares(TREFOIL, 0, R_SCREEN_MEAN, R_SCREEN, RHO, 50.0)[0]
S_BUNDLE_MM2 = round(25.0 / SHARE_FAULTED_SCREEN, 1)

z_s3_expr = loop_expression(R_SCREEN, GMR_TREFOIL, parallel=3)
z_m3_expr = loop_expression(0.0, GMR_TREFOIL)
cable_system = gi.BranchType(
    name="3x NA2XS(F)2Y 1x300RM/25 12/20 kV, trefoil, screens bonded both ends",
    grounding_conductor=True,
    self_impedance_formula=f"{z_s3_expr}*l",
    mutual_impedance_formula=f"{z_m3_expr}*l",
    phase_impedance_formula=f"{reduced_phase_expression(z_pp_expr, z_m3_expr, z_s3_expr)}*l",
    conductor_material="Cu",
    cross_section_mm2=S_BUNDLE_MM2,
)
# --8<-- [end:trefoil]

net3 = gi.create_network(name="one_branch_trefoil", frequencies=FREQUENCIES)
gi.create_bus(name="A", type=station, specific_earth_resistance=RHO, network=net3)
gi.create_bus(name="B", type=station, specific_earth_resistance=RHO, network=net3)
gi.create_branch(
    name="K", type=cable_system, from_bus="A", to_bus="B", length=1.0,
    specific_earth_resistance=RHO, network=net3,
)
gi.create_source(name="infeed", bus="A", values={50.0: 1000.0, 250.0: 100.0}, network=net3)
gi.create_fault(name="F", bus="B", scalings={50.0: 1.0, 250.0: 1.0}, network=net3)
r_model_trefoil = gi.run_fault(net3, "F").reduction_factor.value

TREFOIL_ROWS = []
gmr_exact = (R_SCREEN_MEAN * D_OUTER**2) ** (1 / 3)  # unrounded, for the identity
for f in FREQUENCIES:
    zs_gmd = loop_impedance(R_SCREEN / 3, gmr_exact, RHO, f)
    zm_gmd = loop_impedance(0.0, gmr_exact, RHO, f)
    zs_formula = loop_impedance(R_SCREEN / 3, GMR_TREFOIL, RHO, f)
    zm_formula = loop_impedance(0.0, GMR_TREFOIL, RHO, f)
    zs_mat, zm_mat = bundle_equivalent(TREFOIL, 0, R_SCREEN_MEAN, R_SCREEN, RHO, f)
    TREFOIL_ROWS.append({
        "f": f,
        "zs_gmd": zs_gmd, "zm_gmd": zm_gmd, "zs_matrix": zs_mat, "zm_matrix": zm_mat,
        "r_gmd": abs(1 - zm_gmd / zs_gmd), "r_matrix": abs(1 - zm_mat / zs_mat),
        "r_formula": abs(1 - zm_formula / zs_formula),
        "r_model": r_model_trefoil[f],
    })


# --8<-- [start:overhead]
# 20 kV overhead line: phase conductor 94-AL1/15-ST1A (Al/St 95/15), earth wire
# 47-AL1/8-ST1A (Al/St 50/8), EN 50182 values; geometry: estimate.
R_EARTH_WIRE = 0.6129  # Ohm/km at 20 °C
GMR_EARTH_WIRE = round(math.exp(-0.25) * 9.45e-3 / 2, 8)  # m, solid-conductor approximation
R_PHASE_OHL = 0.3060  # Ohm/km at 20 °C
GMR_PHASE_OHL = round(math.exp(-0.25) * 13.6e-3 / 2, 8)  # m, solid-conductor approximation
H_PHASE, H_EARTH_WIRE = 10.0, 12.0  # m above ground, earth wire above the faulted phase
D_PHASE_EARTH_WIRE = H_EARTH_WIRE - H_PHASE  # m

z_ew_expr = loop_expression(R_EARTH_WIRE, GMR_EARTH_WIRE)
z_pe_expr = loop_expression(0.0, D_PHASE_EARTH_WIRE)
overhead_line = gi.BranchType(
    name="20 kV OHL 94-AL1/15-ST1A with earth wire 47-AL1/8-ST1A",
    grounding_conductor=True,
    self_impedance_formula=f"{z_ew_expr}*l",
    mutual_impedance_formula=f"{z_pe_expr}*l",
    phase_impedance_formula=(
        f"{reduced_phase_expression(loop_expression(R_PHASE_OHL, GMR_PHASE_OHL), z_pe_expr, z_ew_expr)}*l"
    ),
)


def carson_earth_impedance(h_i, h_j, x, d, rho, f):
    """
    Carson's loop or mutual impedance of overhead conductors with earth return,
    without the internal impedance, in Ohm/km.

    ``jωμ0/(2π)·ln(D/d) + jωμ0/π·∫ exp(−(h_i+h_j)λ)·cos(xλ)/(λ+√(λ²+m²)) dλ``,
    ``D`` the distance to the image conductor.
    """
    omega = 2 * math.pi * f
    m = cmath.sqrt(1j * omega * MU0 / rho)
    height = h_i + h_j

    def integrand(lam):
        return cmath.exp(-height * lam) * math.cos(x * lam) / (lam + cmath.sqrt(lam * lam + m * m))

    upper = 80.0 / height
    points = sorted({abs(m), 10 * abs(m), 1 / height})
    parts = [
        integrate.quad(
            lambda lam, part=part: getattr(integrand(lam), part),
            0.0, upper, points=points, limit=400, epsabs=0.0, epsrel=1e-11,
        )[0]
        for part in ("real", "imag")
    ]
    image = math.hypot(x, height)
    z = 1j * omega * MU0 / (2 * math.pi) * math.log(image / d)
    return (z + 1j * omega * MU0 / math.pi * complex(*parts)) * 1e3
# --8<-- [end:overhead]

net_ohl = gi.create_network(name="one_span", frequencies=FREQUENCIES)
gi.create_bus(name="A", type=station, specific_earth_resistance=RHO, network=net_ohl)
gi.create_bus(name="B", type=station, specific_earth_resistance=RHO, network=net_ohl)
gi.create_branch(
    name="K", type=overhead_line, from_bus="A", to_bus="B", length=1.0,
    specific_earth_resistance=RHO, network=net_ohl,
)
gi.create_source(name="infeed", bus="A", values={50.0: 1000.0, 250.0: 100.0}, network=net_ohl)
gi.create_fault(name="F", bus="B", scalings={50.0: 1.0, 250.0: 1.0}, network=net_ohl)
r_model_ohl = gi.run_fault(net_ohl, "F").reduction_factor.value

OHL_ROWS = []
for f in FREQUENCIES:
    zs_a = loop_impedance(R_EARTH_WIRE, GMR_EARTH_WIRE, RHO, f)
    zm_a = loop_impedance(0.0, D_PHASE_EARTH_WIRE, RHO, f)
    zs_b = R_EARTH_WIRE + carson_earth_impedance(H_EARTH_WIRE, H_EARTH_WIRE, 0.0, GMR_EARTH_WIRE, RHO, f)
    zm_b = carson_earth_impedance(H_PHASE, H_EARTH_WIRE, 0.0, D_PHASE_EARTH_WIRE, RHO, f)
    OHL_ROWS.append({
        "f": f, "zs_a": zs_a, "zs_b": zs_b, "zm_a": zm_a, "zm_b": zm_b,
        "r_a": abs(1 - zm_a / zs_a), "r_b": abs(1 - zm_b / zs_b), "r_c": r_model_ohl[f],
    })


# --8<-- [start:electrodes]
# Earth electrodes as BusType formulas in rho (Ohm*m); dimensions in m.
rod = gi.BusType(  # one rod, L = 3 m, diameter 20 mm (Dwight 1936)
    name="rod 3 m", system_type="Station", voltage_level=20.0,
    impedance_formula="rho/(2*pi*3.0)*(log(4*3.0/0.01) - 1)",
)
ring = gi.BusType(  # ring around 10 m x 10 m, D = 1.13*sqrt(A)
    name="ring 10 m x 10 m", system_type="Station", voltage_level=20.0,
    impedance_formula="2*rho/(3*1.13*sqrt(100.0))",
)
mesh = gi.BusType(  # mesh 40 m x 40 m, 400 m of conductor at 0.8 m (IEEE 80, Sverak)
    name="mesh 40 m x 40 m", system_type="Substation", voltage_level=110.0,
    impedance_formula=(
        "rho*(1/400.0 + 1/sqrt(20*1600.0)*(1 + 1/(1 + 0.8*sqrt(20/1600.0))))"
    ),
)
measured = gi.BusType(  # a measured 50 Hz impedance, inductive part scaled with f
    name="station, measured", system_type="Station", voltage_level=20.0,
    impedance_formula="0.42 + j*0.08*f/50",
)
# --8<-- [end:electrodes]

ELECTRODE_ROWS = []
for bus_type, second_name, second in (
    (rod, "rough rule rho/L", lambda rho: rho / 3.0),
    (ring, "Dwight, buried ring", lambda rho: rho / (2 * math.pi**2 * 11.3)
        * (math.log(8 * 11.3 / 0.01) + math.log(4 * 11.3 / 1.0))),
    (mesh, "Laurent-Niemann", lambda rho: rho / 4 * math.sqrt(math.pi / 1600.0) + rho / 400.0),
):
    probe = gi.create_network(name="probe", frequencies=[50.0])
    bus = gi.create_bus(name="E", type=bus_type, specific_earth_resistance=RHO, network=probe)
    ELECTRODE_ROWS.append({
        "name": bus_type.name,
        "formula": bus_type.impedance_formula,
        "z_model": _complex(bus.impedance[50.0]),
        "second_name": second_name,
        "second": second(RHO),
    })


if __name__ == "__main__":
    print(f"r_s = {R_SCREEN_MEAN*1e3:.3f} mm, r_c = {R_CONDUCTOR*1e3:.3f} mm, "
          f"GMR_c = {GMR_CONDUCTOR*1e3:.4f} mm, D_E(50 Hz) = {earth_return_depth(RHO, 50.0):.1f} m")
    print("\nSingle cable, own screen: (a) closed form, (b) Pollaczek, (c) groundinsight")
    for row in ROWS:
        print(f"  f = {row['f']:5.0f} Hz  Z_s' (a) {row['zs_a']:.5f}  (b) {row['zs_b']:.5f}"
              f"  Z_m' (a) {row['zm_a']:.5f}  (b) {row['zm_b']:.5f}")
        print(f"               Z_red' (a) {row['zred_a']:.5f}  (b) {row['zred_b']:.5f}  (c) {row['zred_c']:.5f}")
        print(f"               r (a) {row['r_a']:.6f}  (b) {row['r_b']:.6f}  (c) {row['r_c']:.6f}"
              f"  |a-c| = {abs(row['r_a'] - row['r_c']):.1e}  |a-b| = {abs(row['r_a'] - row['r_b']):.1e}")
    print("\nThree screens in trefoil: GMD closed form, exact reduction, groundinsight")
    for row in TREFOIL_ROWS:
        print(f"  f = {row['f']:5.0f} Hz  Z_s,eq {row['zs_gmd']:.5f} / {row['zs_matrix']:.5f}"
              f"  Z_m,eq {row['zm_gmd']:.5f} / {row['zm_matrix']:.5f}"
              f"  r {row['r_gmd']:.6f} / {row['r_matrix']:.6f} / {row['r_model']:.6f}")
    print("\nOverhead line with earth wire: (a) closed form, (b) Carson, (c) groundinsight")
    for row in OHL_ROWS:
        print(f"  f = {row['f']:5.0f} Hz  Z_s' (a) {row['zs_a']:.5f}  (b) {row['zs_b']:.5f}"
              f"  Z_m' (a) {row['zm_a']:.5f}  (b) {row['zm_b']:.5f}"
              f"  r {row['r_a']:.6f} / {row['r_b']:.6f} / {row['r_c']:.6f}")
    print("\nEarth electrodes at rho = 100 Ohm*m: BusType formula and a second published formula")
    for row in ELECTRODE_ROWS:
        print(f"  {row['name']:18s} {row['z_model'].real:7.3f} Ohm   {row['second_name']}: {row['second']:7.3f} Ohm")
    print("\nFormula strings:")
    for t in (cable, cable_system, overhead_line):
        print(f"  {t.name}")
        print(f"    self_impedance_formula   = {t.self_impedance_formula!r}")
        print(f"    mutual_impedance_formula = {t.mutual_impedance_formula!r}")
        print(f"    phase_impedance_formula  = {t.phase_impedance_formula!r}")
