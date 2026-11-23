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
  does not depend on which phase is faulted. A **reduced PEN** (3x150+70)
  has its own resistance and radius, and the axis distance of unequal
  neighbours, $d = r + t + r_\mathrm{PEN} + t_\mathrm{PEN}$. In a
  **five-core cable** the protective conductor is the earthing conductor;
  with the cores in the order PE, N, L1, L2, L3 around the cable two phases
  are two steps and one is one step away from it, so
  $d_m = \varphi^{2/3} d$ with the golden ratio $\varphi$.

An MV row may carry the **capacitance** of a conductor to its screen
(the operating capacitance of the data sheet); the type then has
``c_shield_nf_per_km``, which a neutral-point element charges
(:mod:`groundinsight.supply`). Every type carries its ``voltage_level``. LV
cables get no capacitance: a TN system is solidly earthed.

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
from typing import Dict, List, Optional

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
    capacitance_nf_per_km : float, optional
        Capacitance of the conductor to the screen, nF/km (the operating
        capacitance of a cable with a radial field).
    capacitance_source : str, optional
        Where the capacitance comes from, if not from ``source``.
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
    capacitance_nf_per_km: Optional[float] = None
    capacitance_source: str = ""


@dataclass(frozen=True)
class LvCableData:
    """
    Data-sheet values of an LV cable with a PEN or a protective conductor.

    Attributes
    ----------
    designation : str
        Type designation, e.g. ``"NAYY 4x150SM 0.6/1 kV"``.
    conductor_material : {"Al", "Cu"}
        Material of the cores.
    cross_section_mm2 : float
        Cross-section of a phase core, mm².
    conductor_resistance : float
        DC resistance of a phase core at 20 °C, Ohm/km.
    insulation_thickness_mm : float
        Nominal thickness of the insulation of a phase core, mm.
    outer_diameter_mm : float
        Nominal outer diameter, mm (documentation only).
    source : str
        Where the values come from.
    pen_cross_section_mm2 : float, optional
        Cross-section of a reduced PEN, mm²; default the phase cross-section.
    pen_resistance : float, optional
        DC resistance of a reduced PEN at 20 °C, Ohm/km.
    pen_insulation_thickness_mm : float, optional
        Insulation thickness of a reduced PEN, mm.
    cores : {4, 5}
        Four cores: three phases and the PEN. Five: three phases, neutral and
        the protective conductor, which is the earthing conductor.
    """

    designation: str
    conductor_material: str
    cross_section_mm2: float
    conductor_resistance: float
    insulation_thickness_mm: float
    outer_diameter_mm: float
    source: str
    pen_cross_section_mm2: Optional[float] = None
    pen_resistance: Optional[float] = None
    pen_insulation_thickness_mm: Optional[float] = None
    cores: int = 4


_PRYSMIAN_AL = ('Prysmian data sheet "NA2XS(F)2Y 12/20KV CPR F" (RO00_NA2XS_F_2Y20KVCPRF_16/11/2026), '
                "first row of the construction; screen resistance EN 60228")
_PRYSMIAN_CU = ('Prysmian MKM product catalogue 2019, "N2XS(F)2Y 12/20 kV", p. 69; insulation 5.5 mm '
                "(DIN VDE 0276-620, not in the table); screen resistance EN 60228")
_PRYSMIAN_LV = ('Prysmian data sheet "NAYY 0,6/1KV CPR E" (RO00_NAYY1KVCPRE_16/11/2026); insulation '
                "thickness HD 603 / IEC 60502-1 (PVC); core geometry estimated")

_NKT_AL = ('NKT data sheet "NA2XS(F)2Y 12/20 kV" (CTL00205 rev. 2), DIN VDE 0276; screen resistance '
           "EN 60228")
_NKT_CU = ('NKT data sheet "N2XS(F)2Y 12/20" (HD 620 10C, DIN VDE 0276-620); screen resistance EN 60228')
_NKT_LV = ('NKT data sheet "NAYY Starkstromkabel 1 kV" (CTL00061 rev. 2), VDE 0276-603; core geometry '
           "estimated")
#: Where the capacitance of the rows comes from that take their other values from Prysmian: the
#: construction (conductor, 5.5 mm XLPE) is the standardised one, so the capacitance is the same.
_NKT_AL_C = 'NKT data sheet "NA2XS(F)2Y 12/20 kV" (CTL00205 rev. 2), same construction'
_NKT_CU_C = 'NKT data sheet "N2XS(F)2Y 12/20", same construction'
_R16, _R35 = 1.15, 0.524  # copper screen of 16, 25 and 35 mm², Ohm/km at 20 °C (EN 60228)

#: The 20 kV cables of the standard library.
MV_CABLES: List[MvCableData] = [
    MvCableData("NA2XS(F)2Y 1x95RM/25 12/20 kV", "Al", 0.320, 25.0, 0.727, 23.3, 5.5, 32.3, _PRYSMIAN_AL,
                210.0, _NKT_AL_C),
    MvCableData("NA2XS(F)2Y 1x150RM/25 12/20 kV", "Al", 0.206, 25.0, 0.727, 26.0, 5.5, 34.8, _PRYSMIAN_AL,
                250.0, _NKT_AL_C),
    MvCableData("NA2XS(F)2Y 1x185RM/25 12/20 kV", "Al", 0.164, 25.0, 0.727, 27.8, 5.5, 36.8, _PRYSMIAN_AL,
                270.0, _NKT_AL_C),
    MvCableData("NA2XS(F)2Y 1x240RM/25 12/20 kV", "Al", 0.125, 25.0, 0.727, 30.2, 5.5, 39.2, _PRYSMIAN_AL,
                300.0, _NKT_AL_C),
    MvCableData("NA2XS(F)2Y 1x300RM/25 12/20 kV", "Al", 0.100, 25.0, 0.727, 32.9, 5.5, 41.9, _PRYSMIAN_AL,
                320.0, _NKT_AL_C),
    MvCableData("NA2XS(F)2Y 1x500RM/35 12/20 kV", "Al", 0.0605, 35.0, 0.524, 38.6, 5.5, 47.7, _PRYSMIAN_AL,
                400.0, _NKT_AL_C),
    MvCableData("N2XS(F)2Y 1x95RM/16 12/20 kV", "Cu", 0.193, 16.0, 1.15, 23.6, 5.5, 32.6, _PRYSMIAN_CU,
                210.0, _NKT_CU_C),
    MvCableData("N2XS(F)2Y 1x150RM/25 12/20 kV", "Cu", 0.124, 25.0, 0.727, 26.4, 5.5, 35.4, _PRYSMIAN_CU,
                250.0, _NKT_CU_C),
    MvCableData("N2XS(F)2Y 1x185RM/25 12/20 kV", "Cu", 0.0991, 25.0, 0.727, 28.0, 5.5, 37.0, _PRYSMIAN_CU,
                270.0, _NKT_CU_C),
    MvCableData("N2XS(F)2Y 1x240RM/25 12/20 kV", "Cu", 0.0754, 25.0, 0.727, 30.4, 5.5, 39.4, _PRYSMIAN_CU,
                300.0, _NKT_CU_C),
    MvCableData("N2XS(F)2Y 1x300RM/25 12/20 kV", "Cu", 0.0601, 25.0, 0.727, 32.8, 5.5, 41.9, _PRYSMIAN_CU,
                330.0, _NKT_CU_C),
    # The other cross-sections of the two NKT data sheets, every value of a row from its sheet.
    MvCableData("NA2XS(F)2Y 1x50RM/16 12/20 kV", "Al", 0.641, 16.0, _R16, 20.5, 5.5, 29.0, _NKT_AL, 170.0),
    MvCableData("NA2XS(F)2Y 1x70RM/16 12/20 kV", "Al", 0.443, 16.0, _R16, 22.0, 5.5, 30.0, _NKT_AL, 190.0),
    MvCableData("NA2XS(F)2Y 1x95RM/16 12/20 kV", "Al", 0.320, 16.0, _R16, 23.5, 5.5, 32.0, _NKT_AL, 210.0),
    MvCableData("NA2XS(F)2Y 1x120RM/16 12/20 kV", "Al", 0.253, 16.0, _R16, 25.0, 5.5, 33.0, _NKT_AL, 230.0),
    MvCableData("NA2XS(F)2Y 1x150RM/16 12/20 kV", "Al", 0.206, 16.0, _R16, 26.4, 5.5, 35.0, _NKT_AL, 250.0),
    MvCableData("NA2XS(F)2Y 1x400RM/35 12/20 kV", "Al", 0.0778, 35.0, _R35, 35.5, 5.5, 44.0, _NKT_AL, 360.0),
    MvCableData("NA2XS(F)2Y 1x630RM/35 12/20 kV", "Al", 0.0469, 35.0, _R35, 42.1, 5.5, 51.0, _NKT_AL, 440.0),
    MvCableData("NA2XS(F)2Y 1x800RM/35 12/20 kV", "Al", 0.0367, 35.0, _R35, 46.4, 5.5, 56.0, _NKT_AL, 490.0),
    MvCableData("NA2XS(F)2Y 1x1000RM/35 12/20 kV", "Al", 0.0291, 35.0, _R35, 50.3, 5.5, 60.0, _NKT_AL, 510.0),
    MvCableData("N2XS(F)2Y 1x35RM/16 12/20 kV", "Cu", 0.524, 16.0, _R16, 19.5, 5.5, 28.0, _NKT_CU, 160.0),
    MvCableData("N2XS(F)2Y 1x50RM/16 12/20 kV", "Cu", 0.387, 16.0, _R16, 20.5, 5.5, 29.0, _NKT_CU, 170.0),
    MvCableData("N2XS(F)2Y 1x70RM/16 12/20 kV", "Cu", 0.268, 16.0, _R16, 22.1, 5.5, 31.0, _NKT_CU, 190.0),
    MvCableData("N2XS(F)2Y 1x120RM/16 12/20 kV", "Cu", 0.153, 16.0, _R16, 25.1, 5.5, 34.0, _NKT_CU, 230.0),
    MvCableData("N2XS(F)2Y 1x400RM/35 12/20 kV", "Cu", 0.047, 35.0, _R35, 35.6, 5.5, 44.0, _NKT_CU, 360.0),
    MvCableData("N2XS(F)2Y 1x500RM/35 12/20 kV", "Cu", 0.037, 35.0, _R35, 38.8, 5.5, 47.0, _NKT_CU, 400.0),
]


def _nayy(cores: str, shape: str, area: float, resistance: float, insulation: float, diameter: float,
          pen: Optional[tuple] = None, count: int = 4) -> LvCableData:
    """
    One row of the NKT data sheet "NAYY".

    Parameters
    ----------
    cores : str
        Number and cross-section as printed, e.g. ``"4x150"`` or ``"3x150+70"``.
    shape : str
        Conductor shape as printed (RE, SE, SM, RMV).
    area, resistance, insulation : float
        Cross-section (mm²), DC resistance at 20 °C (Ohm/km) and insulation
        thickness (mm) of a phase core.
    diameter : float
        Outer diameter, mm.
    pen : tuple of float, optional
        ``(cross-section, resistance, insulation thickness)`` of a reduced PEN.
    count : {4, 5}
        Number of cores.

    Returns
    -------
    LvCableData
        The row.
    """
    reduced = pen or (None, None, None)
    return LvCableData(f"NAYY {cores}{shape} 0.6/1 kV", "Al", area, resistance, insulation, diameter, _NKT_LV,
                       reduced[0], reduced[1], reduced[2], count)


#: The LV cables of the standard library (RE: round solid, SE: sector solid, SM: sector stranded,
#: RMV: round stranded compacted). Four-core, with reduced PEN, and five-core.
LV_CABLES: List[LvCableData] = [
    LvCableData("NAYY 4x50SE 0.6/1 kV", "Al", 50.0, 0.641, 1.4, 29.1, _PRYSMIAN_LV),
    LvCableData("NAYY 4x95SE 0.6/1 kV", "Al", 95.0, 0.320, 1.6, 37.5, _PRYSMIAN_LV),
    LvCableData("NAYY 4x150SM 0.6/1 kV", "Al", 150.0, 0.206, 1.8, 47.0, _PRYSMIAN_LV),
    LvCableData("NAYY 4x185SM 0.6/1 kV", "Al", 185.0, 0.164, 2.0, 51.9, _PRYSMIAN_LV),
    LvCableData("NAYY 4x240SE 0.6/1 kV", "Al", 240.0, 0.125, 2.2, 54.7, _PRYSMIAN_LV),
    # The other rows of the NKT data sheet with an earthing conductor.
    _nayy("4x16", "RE", 16.0, 1.91, 1.0, 21.0),
    _nayy("4x25", "RE", 25.0, 1.20, 1.2, 25.0),
    _nayy("4x35", "RE", 35.0, 0.868, 1.2, 28.0),
    _nayy("4x35", "SM", 35.0, 0.868, 1.2, 26.0),
    _nayy("4x50", "RE", 50.0, 0.641, 1.4, 32.0),
    _nayy("4x50", "SM", 50.0, 0.641, 1.4, 30.0),
    _nayy("4x70", "SE", 70.0, 0.443, 1.4, 33.0),
    _nayy("4x70", "SM", 70.0, 0.443, 1.4, 34.0),
    _nayy("4x95", "SM", 95.0, 0.320, 1.6, 39.0),
    _nayy("4x120", "SE", 120.0, 0.253, 1.6, 41.0),
    _nayy("4x120", "SM", 120.0, 0.253, 1.6, 42.0),
    _nayy("4x150", "SE", 150.0, 0.206, 1.8, 44.0),
    _nayy("4x185", "SE", 185.0, 0.164, 2.0, 50.0),
    _nayy("4x240", "SM", 240.0, 0.125, 2.2, 59.0),
    _nayy("4x300", "SE", 300.0, 0.100, 2.4, 62.0),
    _nayy("4x300", "SM", 300.0, 0.100, 2.4, 65.0),
    _nayy("3x35+16", "RE", 35.0, 0.868, 1.2, 26.0, pen=(16.0, 1.91, 1.0)),
    _nayy("3x50+25", "SM/RMV", 50.0, 0.641, 1.4, 29.0, pen=(25.0, 1.20, 1.2)),
    _nayy("3x70+35", "SM/RMV", 70.0, 0.443, 1.4, 34.0, pen=(35.0, 0.868, 1.2)),
    _nayy("3x95+50", "SM", 95.0, 0.320, 1.6, 38.0, pen=(50.0, 0.641, 1.4)),
    _nayy("3x120+70", "SM", 120.0, 0.253, 1.6, 41.0, pen=(70.0, 0.443, 1.4)),
    _nayy("3x150+70", "SM", 150.0, 0.206, 1.8, 46.0, pen=(70.0, 0.443, 1.4)),
    _nayy("3x185+95", "SM", 185.0, 0.164, 2.0, 51.0, pen=(95.0, 0.320, 1.6)),
    _nayy("3x240+120", "SM", 240.0, 0.125, 2.2, 58.0, pen=(120.0, 0.253, 1.6)),
    _nayy("5x16", "RE", 16.0, 1.91, 1.0, 23.0, count=5),
    _nayy("5x25", "RE", 25.0, 1.20, 1.2, 28.0, count=5),
    _nayy("5x35", "RE", 35.0, 0.868, 1.2, 31.0, count=5),
    _nayy("5x50", "RMV", 50.0, 0.641, 1.4, 37.0, count=5),
    _nayy("5x50", "SM", 50.0, 0.641, 1.4, 34.0, count=5),
    _nayy("5x70", "RMV", 70.0, 0.443, 1.4, 42.0, count=5),
    _nayy("5x70", "SM", 70.0, 0.443, 1.4, 39.0, count=5),
    _nayy("5x95", "RMV", 95.0, 0.320, 1.6, 48.0, count=5),
    _nayy("5x95", "SM", 95.0, 0.320, 1.6, 44.0, count=5),
    _nayy("5x120", "RMV", 120.0, 0.253, 1.6, 52.0, count=5),
    _nayy("5x120", "SM", 120.0, 0.253, 1.6, 48.0, count=5),
]

#: Voltage level of the two cable families, kV.
MV_LEVEL = 20.0
LV_LEVEL = 0.4


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
        it); ``voltage_level`` 20 kV and, where the row has one, the
        capacitance ``c_shield_nf_per_km``.

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
    capacitance = f" Capacitance: {data.capacitance_source}." if data.capacitance_source else ""
    return BranchType(
        name=name or data.designation,
        description=(f"3 x {data.designation}, trefoil, screens bonded at both ends, 20 °C. "
                     f"Source: {data.source}.{capacitance}"),
        grounding_conductor=True,
        self_impedance_formula=f"{z_s}*l",
        mutual_impedance_formula=f"{z_m}*l",
        phase_impedance_formula=f"({z_pp} - {z_m}**2/{z_s})*l",
        conductor_material="Cu",
        cross_section_mm2=round(data.screen_cross_section_mm2 / share, 1),
        c_shield_nf_per_km=data.capacitance_nf_per_km,
        voltage_level=MV_LEVEL,
    )


def lv_cable_type(data: LvCableData, *, name: str = "") -> BranchType:
    r"""
    The branch type of an LV cable with a PEN or a protective conductor.

    See the module docstring for the formulas: the earth loop of the PEN
    (four cores, also reduced) or of the protective conductor (five cores),
    the coupling of the phases to it over the geometric mean axis distance,
    the reduced phase impedance; 20 °C.

    Parameters
    ----------
    data : LvCableData
        The data-sheet values of the cable.
    name : str, optional
        Name of the type; default ``data.designation``.

    Returns
    -------
    BranchType
        ``grounding_conductor=True``; material and cross-section of the
        earthing conductor, ``voltage_level`` 0.4 kV.

    Examples
    --------
    >>> from groundinsight.library import LV_CABLES, lv_cable_type
    >>> lv_cable_type(LV_CABLES[2]).name
    'NAYY 4x150SM 0.6/1 kV'
    """
    r_core = math.sqrt(data.cross_section_mm2 / math.pi) * 1e-3  # round-equivalent radius (estimate)
    gmr = round(math.exp(-0.25) * r_core, 8)
    z_pp = _loop(repr(data.conductor_resistance), gmr)
    if data.pen_cross_section_mm2 is None:
        # PEN (or PE) of the phase cross-section: its earth loop is the phase-earth loop.
        axis = 2 * (r_core + data.insulation_thickness_mm * 1e-3)  # neighbouring cores touching (estimate)
        pen_area, z_s = data.cross_section_mm2, z_pp
    else:
        r_pen = math.sqrt(data.pen_cross_section_mm2 / math.pi) * 1e-3
        axis = r_core + r_pen + (data.insulation_thickness_mm + data.pen_insulation_thickness_mm) * 1e-3
        pen_area = data.pen_cross_section_mm2
        z_s = _loop(repr(data.pen_resistance), round(math.exp(-0.25) * r_pen, 8))
    # geometric mean of the three phase distances: d, d, sqrt(2)*d in four cores; d, phi*d, phi*d in five
    spread = 2 ** (1 / 6) if data.cores == 4 else ((1 + math.sqrt(5)) / 2) ** (2 / 3)
    z_m = _loop("", round(spread * axis, 8))
    conductor = "PEN" if data.cores == 4 else "protective conductor"
    return BranchType(
        name=name or data.designation,
        description=f"{data.designation}, {conductor} as earthing conductor, 20 °C. Source: {data.source}.",
        grounding_conductor=True,
        self_impedance_formula=f"{z_s}*l",
        mutual_impedance_formula=f"{z_m}*l",
        phase_impedance_formula=f"({z_pp} - {z_m}**2/{z_s})*l",
        conductor_material=data.conductor_material,
        cross_section_mm2=pen_area,
        voltage_level=LV_LEVEL,
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
    (True, 66)
    """
    types = [cable_system_type(row) for row in MV_CABLES] + [lv_cable_type(row) for row in LV_CABLES]
    return {t.name: t for t in types}
