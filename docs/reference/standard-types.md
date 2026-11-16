# Standard types

`groundinsight` ships a small library of types so that a network can be drawn
without first writing formulas: 20 kV cable systems, four-core LV cables and
typical earth electrodes. Every entry is an ordinary `BranchType` or `BusType`;
copy it into a project (in the GUI: *Types → From the standard library*) and
change the copy where the site differs.

```python
import groundinsight as gi

cables = gi.standard_branch_types()     # 16 branch types, by name
electrodes = gi.standard_bus_types()    # 4 bus types with groundfield characteristics
print(len(cables), len(electrodes))
print(cables["NA2XS(F)2Y 1x240RM/25 12/20 kV"].cross_section_mm2)
```

```text
16 4
69.6
```

## 20 kV cable systems

Each type is a **three-phase system of three single-core cables in trefoil,
screens bonded at both ends**, at 20 °C — the formulas and their derivation are
on [Types from data sheets](../howto/types-from-datasheets.md#three-phase-system-the-other-two-screens).
The three screens return the current in parallel; a single cable's own screen
alone would give about twice the reduction factor. Conductor data and
diameters: Prysmian data sheet "NA2XS(F)2Y 12/20KV CPR F" (aluminium, first row
of each construction) [1] and Prysmian MKM product catalogue 2019, p. 69
(copper) [2]; screen resistance from EN 60228 [3]. The build-up between
insulation and screen wires is estimated as on the how-to page.

`r` is the reduction factor $|1 - Z_m/Z_s|$ in soil of 100 Ω·m. The thermal
cross-section is what the type gives `check_conductor_limits`: the screen
cross-section divided by the share of the faulted core's screen in the bundle
current at 50 Hz.

| Type | Conductor R′ [Ω/km] | Screen [mm²] | Screen R′ [Ω/km] | Ø over insulation [mm] | Ø outer [mm] | r 50 Hz | r 250 Hz | Thermal cross-section [mm²] |
|---|---|---|---|---|---|---|---|---|
| NA2XS(F)2Y 1x95RM/25 12/20 kV | 0.32 | 25 | 0.727 | 23.3 | 32.3 | 0.3341 | 0.0780 | 69.4 |
| NA2XS(F)2Y 1x150RM/25 12/20 kV | 0.206 | 25 | 0.727 | 26 | 34.8 | 0.3363 | 0.0786 | 69.6 |
| NA2XS(F)2Y 1x185RM/25 12/20 kV | 0.164 | 25 | 0.727 | 27.8 | 36.8 | 0.3378 | 0.0791 | 69.6 |
| NA2XS(F)2Y 1x240RM/25 12/20 kV | 0.125 | 25 | 0.727 | 30.2 | 39.2 | 0.3396 | 0.0796 | 69.6 |
| NA2XS(F)2Y 1x300RM/25 12/20 kV | 0.1 | 25 | 0.727 | 32.9 | 41.9 | 0.3416 | 0.0802 | 69.7 |
| NA2XS(F)2Y 1x500RM/35 12/20 kV | 0.0605 | 35 | 0.524 | 38.6 | 47.7 | 0.2582 | 0.0588 | 96.1 |
| N2XS(F)2Y 1x95RM/16 12/20 kV | 0.193 | 16 | 1.15 | 23.6 | 32.6 | 0.4839 | 0.1225 | 45 |
| N2XS(F)2Y 1x150RM/25 12/20 kV | 0.124 | 25 | 0.727 | 26.4 | 35.4 | 0.3367 | 0.0788 | 69.5 |
| N2XS(F)2Y 1x185RM/25 12/20 kV | 0.0991 | 25 | 0.727 | 28 | 37 | 0.3380 | 0.0791 | 69.6 |
| N2XS(F)2Y 1x240RM/25 12/20 kV | 0.0754 | 25 | 0.727 | 30.4 | 39.4 | 0.3398 | 0.0797 | 69.6 |
| N2XS(F)2Y 1x300RM/25 12/20 kV | 0.0601 | 25 | 0.727 | 32.8 | 41.9 | 0.3415 | 0.0802 | 69.7 |

The reduction factor depends almost only on the screen: the conductor enters
the reduced phase impedance, not $Z_s$ or $Z_m$. The copper 1×95 cable has a
16 mm² screen in the catalogue and therefore a higher r.

## LV cables

Four-core NAYY cables whose fourth core is the PEN, bonded to the buses [4].
The PEN is the earthing conductor; the faulted phase core couples to it over
the geometric mean of the three phase–PEN axis distances, $2^{1/6} d$, so the
type does not depend on which phase is faulted. The data sheet gives no core
geometry: a sector core is replaced by a round core of the same cross-section,
$r = \sqrt{A/\pi}$, and neighbouring cores touch, $d = 2(r + t)$ with the PVC
insulation thickness $t$ of HD 603 / IEC 60502-1. These are estimates; they
enter through a logarithm, and 20 % on $d$ moves $|Z_m|$ by about 2 % at 50 Hz.

| Type | Core [mm²] | R′ [Ω/km] | Insulation [mm] | Ø outer [mm] | r 50 Hz | r 250 Hz |
|---|---|---|---|---|---|---|
| NAYY 4x50SE 0.6/1 kV | 50 | 0.641 | 1.4 | 29.1 | 0.6154 | 0.2020 |
| NAYY 4x95SE 0.6/1 kV | 95 | 0.32 | 1.6 | 37.5 | 0.3861 | 0.1430 |
| NAYY 4x150SM 0.6/1 kV | 150 | 0.206 | 1.8 | 47 | 0.2768 | 0.1275 |
| NAYY 4x185SM 0.6/1 kV | 185 | 0.164 | 2 | 51.9 | 0.2343 | 0.1240 |
| NAYY 4x240SE 0.6/1 kV | 240 | 0.125 | 2.2 | 54.7 | 0.1949 | 0.1212 |

The coupling matters for a fault **in** the LV network (the phase conductor
carries the fault current). For an MV fault the LV cables carry no phase
current; there only $Z_s$, the PEN–earth loop, counts.

## Earth electrodes

Typical electrodes, each alone in **homogeneous** soil, computed with
groundfield (backend `mom`) and stored with their characteristic: impedance
and touch factor $k_\mathrm{touch}$ at 30 … 3000 Ω·m and 50 … 1000 Hz. A bus
of such a type takes its impedance from the table at its own ρ. The values
below are at 100 Ω·m and 50 Hz; in homogeneous soil they scale with ρ.

| Type | System | \|Z\| [Ω] | k_touch |
|---|---|---|---|
| Substation ring 2.5 m, 4 rods 2.5 m | MV/LV | 8.622 | 0.461 |
| House foundation 10 x 10 m | LV | 4.637 | 0.476 |
| Rod 2.5 m | LV | 41.49 | 0.751 |
| Rod 5 m | LV | 22.86 | 0.685 |

The rods agree with Dwight's closed form $\frac{\rho}{2\pi L}\left(\ln\frac{4L}{a} - 1\right)$
(wire radius $a$ = 5 mm) within 1.5 %. Layered soil is site data: compute the
site's own characteristic with groundfield — see
[Earthing characteristic from groundfield](../howto/groundfield-characteristics.md).
The presets are rebuilt with `scripts/build_standard_bus_types.py` (needs
groundfield).

## A cable the library does not list

The builders take the numbers of any data sheet:

```python
import groundinsight as gi
from groundinsight.library import LvCableData, MvCableData

nayy_120 = gi.lv_cable_type(LvCableData(
    designation="NAYY 4x120SE 0.6/1 kV", conductor_material="Al", cross_section_mm2=120.0,
    conductor_resistance=0.253, insulation_thickness_mm=1.6, outer_diameter_mm=40.0,
    source="manufacturer data sheet"))
system_120 = gi.cable_system_type(MvCableData(
    designation="NA2XS(F)2Y 1x120RM/25 12/20 kV", conductor_material="Al",
    conductor_resistance=0.253, screen_cross_section_mm2=25.0, screen_resistance=0.727,
    diameter_over_insulation_mm=24.7, insulation_thickness_mm=5.5, outer_diameter_mm=33.7,
    source="Prysmian data sheet [1]"))
print(nayy_120.name, system_120.cross_section_mm2)
```

```text
NAYY 4x120SE 0.6/1 kV 69.5
```

## References

1. Prysmian, data sheet "NA2XS(F)2Y 12/20KV CPR F", document code
   RO00_NA2XS_F_2Y20KVCPRF_16/11/2026, table "Cable properties".
   <https://datasheet.prysmiangroup.com/pdf/datasheet/en/322930>
2. Prysmian MKM Magyar Kábel Művek, *Product catalogue* 2019, "N2XS(F)2Y
   12/20 kV — Medium-voltage cables", p. 69.
   <https://hu.prysmian.com/sites/default/files/PGHU_Productcatalouge_A4_108pg_EN_web_1.pdf>
3. EN 60228 / IEC 60228:2004, *Conductors of insulated cables*; values as
   tabulated by Bayka, "Conductor resistance".
   <https://www.bayka.de/en/service/technical-information/conductor-resistance>
4. Prysmian, data sheet "NAYY 0,6/1KV CPR E", document code
   RO00_NAYY1KVCPRE_16/11/2026, table "Cable properties"; insulation thickness
   HD 603 / IEC 60502-1.
   <https://datasheet.prysmiangroup.com/pdf/datasheet/en/332537>
