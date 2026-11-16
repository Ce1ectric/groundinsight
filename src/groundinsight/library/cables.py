# groundinsight/library/cables.py
r"""
Branch types of common cables, built from data-sheet values.

Two builders turn the numbers of a data sheet into a
:class:`~groundinsight.BranchType`, with the formulas derived on the page
"Types from data sheets" of the documentation (Carson's earth return with the
equivalent depth $D_E = 658\sqrt{\rho/f}$ m):

$$
E(r) = \pi^2 10^{-4} f + j\,4\pi 10^{-4} f \ln\frac{D_E}{r}
\quad [\Omega/\mathrm{km}].
$$

* :func:`cable_system_type` -- a **three-phase system of single-core MV
  cables in trefoil**, screens bonded at both ends (the usual case). The
  three screens return the current in parallel; for trefoil the matrix
  reduction of the three screens is exactly the bundle with resistance
  $R_s'/3$ and geometric mean radius $(r_s D^2)^{1/3}$ ($r_s$ mean screen
  radius, $D$ outer diameter = axis distance of touching cables):
  $Z_s' = R_s'/3 + E(\mathrm{GMR}_b)$, $Z_m' = E(\mathrm{GMR}_b)$. A single
  cable's own screen alone would give about twice the reduction factor; that
  variant is deliberately not offered, because a lone screen is the exception
  in a three-phase system.
* :func:`lv_cable_type` -- a **four-core LV cable** whose fourth core is the
  PEN, bonded to the buses: $Z_s' = R' + E(\mathrm{GMR})$ of the PEN, and the
  coupling of the faulted phase core to the PEN, $Z_m' = E(d_m)$, over the
  geometric mean of the three phase-PEN axis distances (two neighbours at
  $d$, the opposite core at $\sqrt2\,d$), $d_m = 2^{1/6} d$, so the type
  does not depend on which phase is faulted.

Both set ``phase_impedance_formula`` to the reduced phase impedance
$Z_\mathrm{red} = Z_{pp} - Z_m^2/Z_s$ (needed in rings and meshes), and the
material and cross-section of the earthing conductor for the thermal check.

Geometry a data sheet does not give is estimated and marked so: the build-up
between insulation and screen wires of an MV cable, and the core geometry of
a sector-shaped LV cable (round-equivalent radius $\sqrt{A/\pi}$, axis
distance $2(r + t)$ with the PVC insulation thickness $t$). These radii enter
only through a logarithm: 20 % on a distance moves $|Z_m|$ by about 2 % at
50 Hz. Resistances are the 20 °C values; at operating temperature the screen
resistance and with it the reduction factor are higher, so the 20 °C type
gives the lower bound of r and the lower earth potential rise -- see the
documentation page for the temperature effect.

The data rows of :data:`MV_CABLES` and :data:`LV_CABLES` cite their source;
:func:`standard_branch_types` builds a type for every row.
"""

import logging
import math
from dataclasses import dataclass
from typing import Dict, List

import numpy as np

from ..models.core_models import BranchType

logger = logging.getLogger(__name__)

__all__ = [
    "LV_CABLES",
    "LvCableData",
    "MV_CABLES",
    "MvCableData",
    "cable_system_type",
    "lv_cable_type",
    "standard_branch_types",
]

#: Build-up between the insulation and the middle of the screen wires (estimates,
#: as on the documentation page): outer semiconductive layer, swelling tape, half a wire.
SEMICON_MM = 0.6
BEDDING_MM = 0.4
SCREEN_WIRE_MM = 0.75

#: Soil and frequency at which the screen-current share of the thermal cross-section is taken.
SHARE_RHO = 100.0
SHARE_FREQUENCY = 50.0


@dataclass(frozen=True)
class MvCableData:
    """
    Data-sheet values of a single-core MV cable with a copper wire screen.

    Attributes
    ----------
    designation : str
        Type designation, e.g. ``"NA2XS(F)2Y 1x300RM/25 12/20 kV"``.
    conductor_material : {"Al", "Cu"}
        Material of the conductor.
    conductor_resistance : float
        DC resistance of the conductor at 20 °C, Ohm/km.
    screen_cross_section_mm2 : float
        Cross-section of the copper screen, mm².
    screen_resistance : float
        DC resistance of the screen at 20 °C, Ohm/km (EN 60228 value of the
        screen cross-section).
    diameter_over_insulation_mm : float
        Nominal diameter over the insulation, mm.
    insulation_thickness_mm : float
        Nominal insulation thickness, mm.
    outer_diameter_mm : float
        Nominal outer diameter, mm (the axis distance of touching cables).
    source : str
        Where the values come from.
    """

    designation: str
    conductor_material: str
    conductor_resistance: float
    screen_cross_section_mm2: float
    screen_resistance: float
    diameter_over_insulation_mm: float
    insulation_thickness_mm: float
    outer_diameter_mm: float
    source: str


@dataclass(frozen=True)
class LvCableData:
    """
    Data-sheet values of a four-core LV cable whose fourth core is the PEN.

    Attributes
    ----------
    designation : str
        Type designation, e.g. ``"NAYY 4x150SM 0.6/1 kV"``.
    conductor_material : {"Al", "Cu"}
        Material of the cores.
    cross_section_mm2 : float
        Cross-section of every core, the PEN included, mm².
    conductor_resistance : float
        DC resistance of a core at 20 °C, Ohm/km.
    insulation_thickness_mm : float
        Nominal thickness of the core insulation, mm.
    outer_diameter_mm : float
        Nominal outer diameter, mm (documentation only).
    source : str
        Where the values come from.
    """

    designation: str
    conductor_material: str
    cross_section_mm2: float
    conductor_resistance: float
    insulation_thickness_mm: float
    outer_diameter_mm: float
    source: str


_PRYSMIAN_AL = ('Prysmian data sheet "NA2XS(F)2Y 12/20KV CPR F" (RO00_NA2XS_F_2Y20KVCPRF_16/11/2026), '
                "first row of the construction; screen resistance EN 60228")
_PRYSMIAN_CU = ('Prysmian MKM product catalogue 2019, "N2XS(F)2Y 12/20 kV", p. 69; insulation 5.5 mm '
                "(DIN VDE 0276-620, not in the table); screen resistance EN 60228")
_PRYSMIAN_LV = ('Prysmian data sheet "NAYY 0,6/1KV CPR E" (RO00_NAYY1KVCPRE_16/11/2026); insulation '
                "thickness HD 603 / IEC 60502-1 (PVC); core geometry estimated")

#: The 20 kV cables of the standard library.
MV_CABLES: List[MvCableData] = [
    MvCableData("NA2XS(F)2Y 1x95RM/25 12/20 kV", "Al", 0.320, 25.0, 0.727, 23.3, 5.5, 32.3, _PRYSMIAN_AL),
    MvCableData("NA2XS(F)2Y 1x150RM/25 12/20 kV", "Al", 0.206, 25.0, 0.727, 26.0, 5.5, 34.8, _PRYSMIAN_AL),
    MvCableData("NA2XS(F)2Y 1x185RM/25 12/20 kV", "Al", 0.164, 25.0, 0.727, 27.8, 5.5, 36.8, _PRYSMIAN_AL),
    MvCableData("NA2XS(F)2Y 1x240RM/25 12/20 kV", "Al", 0.125, 25.0, 0.727, 30.2, 5.5, 39.2, _PRYSMIAN_AL),
    MvCableData("NA2XS(F)2Y 1x300RM/25 12/20 kV", "Al", 0.100, 25.0, 0.727, 32.9, 5.5, 41.9, _PRYSMIAN_AL),
    MvCableData("NA2XS(F)2Y 1x500RM/35 12/20 kV", "Al", 0.0605, 35.0, 0.524, 38.6, 5.5, 47.7, _PRYSMIAN_AL),
    MvCableData("N2XS(F)2Y 1x95RM/16 12/20 kV", "Cu", 0.193, 16.0, 1.15, 23.6, 5.5, 32.6, _PRYSMIAN_CU),
    MvCableData("N2XS(F)2Y 1x150RM/25 12/20 kV", "Cu", 0.124, 25.0, 0.727, 26.4, 5.5, 35.4, _PRYSMIAN_CU),
    MvCableData("N2XS(F)2Y 1x185RM/25 12/20 kV", "Cu", 0.0991, 25.0, 0.727, 28.0, 5.5, 37.0, _PRYSMIAN_CU),
    MvCableData("N2XS(F)2Y 1x240RM/25 12/20 kV", "Cu", 0.0754, 25.0, 0.727, 30.4, 5.5, 39.4, _PRYSMIAN_CU),
    MvCableData("N2XS(F)2Y 1x300RM/25 12/20 kV", "Cu", 0.0601, 25.0, 0.727, 32.8, 5.5, 41.9, _PRYSMIAN_CU),
]

#: The LV cables of the standard library (SE: sector solid, SM: sector stranded).
LV_CABLES: List[LvCableData] = [
    LvCableData("NAYY 4x50SE 0.6/1 kV", "Al", 50.0, 0.641, 1.4, 29.1, _PRYSMIAN_LV),
    LvCableData("NAYY 4x95SE 0.6/1 kV", "Al", 95.0, 0.320, 1.6, 37.5, _PRYSMIAN_LV),
    LvCableData("NAYY 4x150SM 0.6/1 kV", "Al", 150.0, 0.206, 1.8, 47.0, _PRYSMIAN_LV),
    LvCableData("NAYY 4x185SM 0.6/1 kV", "Al", 185.0, 0.164, 2.0, 51.9, _PRYSMIAN_LV),
    LvCableData("NAYY 4x240SE 0.6/1 kV", "Al", 240.0, 0.125, 2.2, 54.7, _PRYSMIAN_LV),
]


def _earth(radius_m: float) -> str:
    """Carson's earth-return term ``E(r)`` per km, as a formula in ``f`` and ``rho``."""
    return f"pi**2*1e-4*f + j*4*pi*1e-4*f*log(658*sqrt(rho/f)/{radius_m!r})"


def _loop(resistance: str, radius_m: float) -> str:
    """``(R + E(r))``, or ``(E(r))`` without resistance."""
    return f"({resistance} + {_earth(radius_m)})" if resistance else f"({_earth(radius_m)})"


def _earth_value(radius_m: float, rho: float, f: float) -> complex:
    """``E(r)`` per km as a number."""
    return complex(math.pi**2 * 1e-4 * f, 4 * math.pi * 1e-4 * f * math.log(658 * math.sqrt(rho / f) / radius_m))


def _faulted_screen_share(r_screen_m: float, axis_m: float, screen_resistance: float) -> float:
    """
    Share of the faulted core's own screen in the current of the trefoil bundle.

    The screens ideally bonded (no voltage drop along them), the coupling
    alone drives them: ``I = -Z_ss^-1 z_c`` per ampere in the faulted core
    (the method of ``screen_current_shares`` on the documentation page).
    """
    own = _earth_value(r_screen_m, SHARE_RHO, SHARE_FREQUENCY)
    other = _earth_value(axis_m, SHARE_RHO, SHARE_FREQUENCY)
    z_ss = np.full((3, 3), other, dtype=complex)
    np.fill_diagonal(z_ss, screen_resistance + own)
    z_c = np.array([own, other, other])
    currents = -np.linalg.solve(z_ss, z_c)
    return float(abs(currents[0]) / abs(currents.sum()))


def cable_system_type(data: MvCableData, *, name: str = "") -> BranchType:
    r"""
    The branch type of a three-phase system of single-core cables in trefoil.

    Screens bonded at both ends, 20 °C. See the module docstring for the
    formulas; every radius is computed from ``data`` as on the documentation
    page "Types from data sheets".

    Parameters
    ----------
    data : MvCableData
        The data-sheet values of one cable.
    name : str, optional
        Name of the type; default ``data.designation``.

    Returns
    -------
    BranchType
        ``grounding_conductor=True`` with self, mutual and reduced phase
        impedance; ``conductor_material="Cu"`` and as ``cross_section_mm2``
        the screen cross-section divided by the share of the faulted core's
        screen at 50 Hz (the thermal check compares the bundle current with
        it).

    Examples
    --------
    >>> from groundinsight.library import MV_CABLES, cable_system_type
    >>> cable = cable_system_type(MV_CABLES[4])
    >>> cable.name, cable.cross_section_mm2
    ('NA2XS(F)2Y 1x300RM/25 12/20 kV', 69.7)
    """
    r_screen = (data.diameter_over_insulation_mm / 2 + SEMICON_MM + BEDDING_MM + SCREEN_WIRE_MM / 2) * 1e-3
    r_core = (data.diameter_over_insulation_mm / 2 - data.insulation_thickness_mm - SEMICON_MM) * 1e-3
    gmr_core = round(math.exp(-0.25) * r_core, 8)
    axis = data.outer_diameter_mm * 1e-3
    gmr_bundle = round((r_screen * axis**2) ** (1 / 3), 8)
    z_s = _loop(f"{data.screen_resistance!r}/3", gmr_bundle)
    z_m = _loop("", gmr_bundle)
    z_pp = _loop(repr(data.conductor_resistance), gmr_core)
    share = _faulted_screen_share(round(r_screen, 8), axis, data.screen_resistance)
    return BranchType(
        name=name or data.designation,
        description=(f"3 x {data.designation}, trefoil, screens bonded at both ends, 20 °C. "
                     f"Source: {data.source}."),
        grounding_conductor=True,
        self_impedance_formula=f"{z_s}*l",
        mutual_impedance_formula=f"{z_m}*l",
        phase_impedance_formula=f"({z_pp} - {z_m}**2/{z_s})*l",
        conductor_material="Cu",
        cross_section_mm2=round(data.screen_cross_section_mm2 / share, 1),
    )


def lv_cable_type(data: LvCableData, *, name: str = "") -> BranchType:
    r"""
    The branch type of a four-core LV cable whose fourth core is the PEN.

    See the module docstring for the formulas: the PEN-earth loop, the
    phase-PEN coupling over the geometric mean axis distance, the reduced
    phase impedance; 20 °C.

    Parameters
    ----------
    data : LvCableData
        The data-sheet values of the cable.
    name : str, optional
        Name of the type; default ``data.designation``.

    Returns
    -------
    BranchType
        ``grounding_conductor=True``; material and cross-section of the PEN.

    Examples
    --------
    >>> from groundinsight.library import LV_CABLES, lv_cable_type
    >>> lv_cable_type(LV_CABLES[2]).name
    'NAYY 4x150SM 0.6/1 kV'
    """
    r_core = math.sqrt(data.cross_section_mm2 / math.pi) * 1e-3  # round-equivalent radius (estimate)
    gmr = round(math.exp(-0.25) * r_core, 8)
    axis = 2 * (r_core + data.insulation_thickness_mm * 1e-3)  # neighbouring cores touching (estimate)
    d_mean = round(2 ** (1 / 6) * axis, 8)  # geometric mean of d, d, sqrt(2)*d
    z_s = _loop(repr(data.conductor_resistance), gmr)  # PEN-earth loop = phase-earth loop (same core)
    z_m = _loop("", d_mean)
    return BranchType(
        name=name or data.designation,
        description=f"{data.designation}, PEN as earthing conductor, 20 °C. Source: {data.source}.",
        grounding_conductor=True,
        self_impedance_formula=f"{z_s}*l",
        mutual_impedance_formula=f"{z_m}*l",
        phase_impedance_formula=f"({z_s} - {z_m}**2/{z_s})*l",
        conductor_material=data.conductor_material,
        cross_section_mm2=data.cross_section_mm2,
    )


def standard_branch_types() -> Dict[str, BranchType]:
    """
    Every cable of the standard library as a branch type, by name.

    Returns
    -------
    dict of str to BranchType
        The MV cable systems (:data:`MV_CABLES`, trefoil) followed by the LV
        cables (:data:`LV_CABLES`); a fresh dictionary on every call.

    Examples
    --------
    >>> import groundinsight as gi
    >>> types = gi.standard_branch_types()
    >>> "NAYY 4x150SM 0.6/1 kV" in types, len(types)
    (True, 16)
    """
    types = [cable_system_type(row) for row in MV_CABLES] + [lv_cable_type(row) for row in LV_CABLES]
    return {t.name: t for t in types}
