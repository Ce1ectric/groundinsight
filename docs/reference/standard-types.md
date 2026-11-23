# Standard types

`groundinsight` ships a small library of types so that a network can be drawn
without first writing formulas: 20 kV cable systems, LV cables with a PEN or a
protective conductor and
typical earth electrodes. Every entry is an ordinary `BranchType` or `BusType`;
copy it into a project and change the copy where the site differs. In the
GUI: *From the standard library …* under the bus-type and branch-type tables
of the network tab, or the **+** next to the palette's type fields.

```python
import groundinsight as gi

cables = gi.standard_branch_types()     # 66 branch types, by name
electrodes = gi.standard_bus_types()    # 10 bus types with groundfield characteristics
print(len(cables), len(electrodes))
print(cables["NA2XS(F)2Y 1x240RM/25 12/20 kV"].cross_section_mm2)
```

```text
66 10
69.6
```

## 20 kV cable systems

Each type is a **three-phase system of three single-core cables in trefoil,
screens bonded at both ends**, at 20 °C — the formulas and their derivation are
on [Types from data sheets](../howto/types-from-datasheets.md#three-phase-system-the-other-two-screens).
The three screens return the current in parallel; a single cable's own screen
alone would give about twice the reduction factor. The first eleven rows take
conductor data and diameters from Prysmian: data sheet "NA2XS(F)2Y 12/20KV
CPR F" (aluminium, first row of each construction) [1] and the MKM product
catalogue 2019, p. 69 (copper) [2]. The other rows are the remaining
cross-sections of the NKT data sheets "NA2XS(F)2Y 12/20 kV" [5] and
"N2XS(F)2Y 12/20" [6], every value of a row from its sheet. Screen resistance
from EN 60228 [3]. The build-up between insulation and screen wires is
estimated as on the how-to page.

Every type carries its voltage level (20 kV) and the **capacitance** C′ of a
conductor to its screen, `c_shield_nf_per_km`, from the NKT sheets — for the
Prysmian rows as well, whose construction (conductor, 5.5 mm XLPE) is the
standardised one. A neutral-point element charges it in an earth fault
([Capacitive currents](../explanation/capacitive-currents.md)). The values
agree with the coaxial capacitor of the tabulated diameters within 6 %.

`r` is the reduction factor of the cable on a long line, $|1 - Z_m/Z_s|$, in
soil of 100 Ω·m; in a network the solver's $r$ also contains the earthing of
the stations ([Reduction factors](../explanation/reduction-factors.md)). The thermal
cross-section is what the type gives `check_conductor_limits`: the screen
cross-section divided by the share of the faulted core's screen in the bundle
current at 50 Hz.

| Type | Conductor R′ [Ω/km] | Screen [mm²] | Screen R′ [Ω/km] | Ø over insulation [mm] | Ø outer [mm] | r 50 Hz | r 250 Hz | Thermal cross-section [mm²] | C′ [nF/km] |
|---|---|---|---|---|---|---|---|---|---|
| NA2XS(F)2Y 1x95RM/25 12/20 kV | 0.32 | 25 | 0.727 | 23.3 | 32.3 | 0.3341 | 0.0780 | 69.4 | 210 |
| NA2XS(F)2Y 1x150RM/25 12/20 kV | 0.206 | 25 | 0.727 | 26 | 34.8 | 0.3363 | 0.0786 | 69.6 | 250 |
| NA2XS(F)2Y 1x185RM/25 12/20 kV | 0.164 | 25 | 0.727 | 27.8 | 36.8 | 0.3378 | 0.0791 | 69.6 | 270 |
| NA2XS(F)2Y 1x240RM/25 12/20 kV | 0.125 | 25 | 0.727 | 30.2 | 39.2 | 0.3396 | 0.0796 | 69.6 | 300 |
| NA2XS(F)2Y 1x300RM/25 12/20 kV | 0.1 | 25 | 0.727 | 32.9 | 41.9 | 0.3416 | 0.0802 | 69.7 | 320 |
| NA2XS(F)2Y 1x500RM/35 12/20 kV | 0.0605 | 35 | 0.524 | 38.6 | 47.7 | 0.2582 | 0.0588 | 96.1 | 400 |
| N2XS(F)2Y 1x95RM/16 12/20 kV | 0.193 | 16 | 1.15 | 23.6 | 32.6 | 0.4839 | 0.1225 | 45 | 210 |
| N2XS(F)2Y 1x150RM/25 12/20 kV | 0.124 | 25 | 0.727 | 26.4 | 35.4 | 0.3367 | 0.0788 | 69.5 | 250 |
| N2XS(F)2Y 1x185RM/25 12/20 kV | 0.0991 | 25 | 0.727 | 28 | 37 | 0.3380 | 0.0791 | 69.6 | 270 |
| N2XS(F)2Y 1x240RM/25 12/20 kV | 0.0754 | 25 | 0.727 | 30.4 | 39.4 | 0.3398 | 0.0797 | 69.6 | 300 |
| N2XS(F)2Y 1x300RM/25 12/20 kV | 0.0601 | 25 | 0.727 | 32.8 | 41.9 | 0.3415 | 0.0802 | 69.7 | 330 |
| NA2XS(F)2Y 1x50RM/16 12/20 kV | 0.641 | 16 | 1.15 | 20.5 | 29 | 0.4801 | 0.1211 | 45 | 170 |
| NA2XS(F)2Y 1x70RM/16 12/20 kV | 0.443 | 16 | 1.15 | 22 | 30 | 0.4815 | 0.1216 | 45.1 | 190 |
| NA2XS(F)2Y 1x95RM/16 12/20 kV | 0.32 | 16 | 1.15 | 23.5 | 32 | 0.4835 | 0.1223 | 45.1 | 210 |
| NA2XS(F)2Y 1x120RM/16 12/20 kV | 0.253 | 16 | 1.15 | 25 | 33 | 0.4847 | 0.1228 | 45.1 | 230 |
| NA2XS(F)2Y 1x150RM/16 12/20 kV | 0.206 | 16 | 1.15 | 26.4 | 35 | 0.4865 | 0.1235 | 45.1 | 250 |
| NA2XS(F)2Y 1x400RM/35 12/20 kV | 0.0778 | 35 | 0.524 | 35.5 | 44 | 0.2564 | 0.0584 | 96.2 | 360 |
| NA2XS(F)2Y 1x630RM/35 12/20 kV | 0.0469 | 35 | 0.524 | 42.1 | 51 | 0.2599 | 0.0593 | 96.3 | 440 |
| NA2XS(F)2Y 1x800RM/35 12/20 kV | 0.0367 | 35 | 0.524 | 46.4 | 56 | 0.2620 | 0.0599 | 96.2 | 490 |
| NA2XS(F)2Y 1x1000RM/35 12/20 kV | 0.0291 | 35 | 0.524 | 50.3 | 60 | 0.2637 | 0.0603 | 96.3 | 510 |
| N2XS(F)2Y 1x35RM/16 12/20 kV | 0.524 | 16 | 1.15 | 19.5 | 28 | 0.4789 | 0.1206 | 45 | 160 |
| N2XS(F)2Y 1x50RM/16 12/20 kV | 0.387 | 16 | 1.15 | 20.5 | 29 | 0.4801 | 0.1211 | 45 | 170 |
| N2XS(F)2Y 1x70RM/16 12/20 kV | 0.268 | 16 | 1.15 | 22.1 | 31 | 0.4822 | 0.1219 | 45 | 190 |
| N2XS(F)2Y 1x120RM/16 12/20 kV | 0.153 | 16 | 1.15 | 25.1 | 34 | 0.4854 | 0.1231 | 45 | 230 |
| N2XS(F)2Y 1x400RM/35 12/20 kV | 0.047 | 35 | 0.524 | 35.6 | 44 | 0.2565 | 0.0584 | 96.2 | 360 |
| N2XS(F)2Y 1x500RM/35 12/20 kV | 0.037 | 35 | 0.524 | 38.8 | 47 | 0.2581 | 0.0588 | 96.4 | 400 |

The reduction factor depends almost only on the screen: the conductor enters
the reduced phase impedance, not $Z_s$ or $Z_m$. The copper 1×95 cable has a
16 mm² screen in the catalogue and therefore a higher r.

## LV cables

NAYY cables with a conductor that joins the earthing of the buses: four-core
cables whose fourth core is the PEN, cables with a **reduced PEN** (3x150+70)
and five-core cables, whose protective conductor is the earthing conductor.
The first five rows are from Prysmian [4], the others are the rows of the NKT
data sheet "NAYY" [7]. Its single-core and three-core cables are left out:
they have no conductor that connects earthing systems.

The faulted phase core couples to the earthing conductor over the geometric
mean of the three phase distances, so the type does not depend on which phase
is faulted: $2^{1/6} d$ in four cores (two neighbours at $d$, one across at
$\sqrt2\,d$), $\varphi^{2/3} d$ in five (cores in the order PE, N, L1, L2,
L3; $\varphi$ the golden ratio). The data sheets give no core geometry: a
sector core is replaced by a round core of the same cross-section,
$r = \sqrt{A/\pi}$, and neighbouring cores touch, $d = 2(r + t)$ with the
PVC insulation thickness $t$; next to a reduced PEN
$d = r + t + r_\mathrm{PEN} + t_\mathrm{PEN}$. These are estimates; they
enter through a logarithm, and 20 % on $d$ moves $|Z_m|$ by about 2 % at
50 Hz. The types carry the voltage level 0.4 kV and no capacitance: a TN
system is solidly earthed.

| Type | Phase core [mm²] | Earthing conductor [mm²] | R′ phase [Ω/km] | R′ earthing conductor [Ω/km] | Insulation [mm] | Ø outer [mm] | r 50 Hz | r 250 Hz |
|---|---|---|---|---|---|---|---|---|
| NAYY 4x50SE 0.6/1 kV | 50 | 50 | 0.641 | 0.641 | 1.4 | 29.1 | 0.6154 | 0.2020 |
| NAYY 4x95SE 0.6/1 kV | 95 | 95 | 0.32 | 0.32 | 1.6 | 37.5 | 0.3861 | 0.1430 |
| NAYY 4x150SM 0.6/1 kV | 150 | 150 | 0.206 | 0.206 | 1.8 | 47 | 0.2768 | 0.1275 |
| NAYY 4x185SM 0.6/1 kV | 185 | 185 | 0.164 | 0.164 | 2 | 51.9 | 0.2343 | 0.1240 |
| NAYY 4x240SE 0.6/1 kV | 240 | 240 | 0.125 | 0.125 | 2.2 | 54.7 | 0.1949 | 0.1212 |
| NAYY 4x16RE 0.6/1 kV | 16 | 16 | 1.91 | 1.91 | 1 | 21 | 0.8989 | 0.4413 |
| NAYY 4x25RE 0.6/1 kV | 25 | 25 | 1.2 | 1.2 | 1.2 | 25 | 0.8069 | 0.3134 |
| NAYY 4x35RE 0.6/1 kV | 35 | 35 | 0.868 | 0.868 | 1.2 | 28 | 0.7152 | 0.2466 |
| NAYY 4x35SM 0.6/1 kV | 35 | 35 | 0.868 | 0.868 | 1.2 | 26 | 0.7152 | 0.2466 |
| NAYY 4x50RE 0.6/1 kV | 50 | 50 | 0.641 | 0.641 | 1.4 | 32 | 0.6154 | 0.2020 |
| NAYY 4x50SM 0.6/1 kV | 50 | 50 | 0.641 | 0.641 | 1.4 | 30 | 0.6154 | 0.2020 |
| NAYY 4x70SE 0.6/1 kV | 70 | 70 | 0.443 | 0.443 | 1.4 | 33 | 0.4878 | 0.1630 |
| NAYY 4x70SM 0.6/1 kV | 70 | 70 | 0.443 | 0.443 | 1.4 | 34 | 0.4878 | 0.1630 |
| NAYY 4x95SM 0.6/1 kV | 95 | 95 | 0.32 | 0.32 | 1.6 | 39 | 0.3861 | 0.1430 |
| NAYY 4x120SE 0.6/1 kV | 120 | 120 | 0.253 | 0.253 | 1.6 | 41 | 0.3230 | 0.1324 |
| NAYY 4x120SM 0.6/1 kV | 120 | 120 | 0.253 | 0.253 | 1.6 | 42 | 0.3230 | 0.1324 |
| NAYY 4x150SE 0.6/1 kV | 150 | 150 | 0.206 | 0.206 | 1.8 | 44 | 0.2768 | 0.1275 |
| NAYY 4x185SE 0.6/1 kV | 185 | 185 | 0.164 | 0.164 | 2 | 50 | 0.2343 | 0.1240 |
| NAYY 4x240SM 0.6/1 kV | 240 | 240 | 0.125 | 0.125 | 2.2 | 59 | 0.1949 | 0.1212 |
| NAYY 4x300SE 0.6/1 kV | 300 | 300 | 0.1 | 0.1 | 2.4 | 62 | 0.1708 | 0.1201 |
| NAYY 4x300SM 0.6/1 kV | 300 | 300 | 0.1 | 0.1 | 2.4 | 65 | 0.1708 | 0.1201 |
| NAYY 3x35+16RE 0.6/1 kV | 35 | 16 | 0.868 | 1.91 | 1.2 | 26 | 0.8992 | 0.4443 |
| NAYY 3x50+25SM/RMV 0.6/1 kV | 50 | 25 | 0.641 | 1.2 | 1.4 | 29 | 0.8075 | 0.3178 |
| NAYY 3x70+35SM/RMV 0.6/1 kV | 70 | 35 | 0.443 | 0.868 | 1.4 | 34 | 0.7161 | 0.2526 |
| NAYY 3x95+50SM 0.6/1 kV | 95 | 50 | 0.32 | 0.641 | 1.6 | 38 | 0.6166 | 0.2090 |
| NAYY 3x120+70SM 0.6/1 kV | 120 | 70 | 0.253 | 0.443 | 1.6 | 41 | 0.4895 | 0.1705 |
| NAYY 3x150+70SM 0.6/1 kV | 150 | 70 | 0.206 | 0.443 | 1.8 | 46 | 0.4904 | 0.1745 |
| NAYY 3x185+95SM 0.6/1 kV | 185 | 95 | 0.164 | 0.32 | 2 | 51 | 0.3893 | 0.1546 |
| NAYY 3x240+120SM 0.6/1 kV | 240 | 120 | 0.125 | 0.253 | 2.2 | 58 | 0.3276 | 0.1463 |
| NAYY 5x16RE 0.6/1 kV | 16 | 16 | 1.91 | 1.91 | 1 | 23 | 0.8992 | 0.4448 |
| NAYY 5x25RE 0.6/1 kV | 25 | 25 | 1.2 | 1.2 | 1.2 | 28 | 0.8076 | 0.3192 |
| NAYY 5x35RE 0.6/1 kV | 35 | 35 | 0.868 | 0.868 | 1.2 | 31 | 0.7164 | 0.2543 |
| NAYY 5x50RMV 0.6/1 kV | 50 | 50 | 0.641 | 0.641 | 1.4 | 37 | 0.6171 | 0.2118 |
| NAYY 5x50SM 0.6/1 kV | 50 | 50 | 0.641 | 0.641 | 1.4 | 34 | 0.6171 | 0.2118 |
| NAYY 5x70RMV 0.6/1 kV | 70 | 70 | 0.443 | 0.443 | 1.4 | 42 | 0.4906 | 0.1753 |
| NAYY 5x70SM 0.6/1 kV | 70 | 70 | 0.443 | 0.443 | 1.4 | 39 | 0.4906 | 0.1753 |
| NAYY 5x95RMV 0.6/1 kV | 95 | 95 | 0.32 | 0.32 | 1.6 | 48 | 0.3901 | 0.1573 |
| NAYY 5x95SM 0.6/1 kV | 95 | 95 | 0.32 | 0.32 | 1.6 | 44 | 0.3901 | 0.1573 |
| NAYY 5x120RMV 0.6/1 kV | 120 | 120 | 0.253 | 0.253 | 1.6 | 52 | 0.3281 | 0.1479 |
| NAYY 5x120SM 0.6/1 kV | 120 | 120 | 0.253 | 0.253 | 1.6 | 48 | 0.3281 | 0.1479 |

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
| House ring electrode 15 x 5 m | LV | 5.329 | 0.483 |
| Cable distribution cabinet, rod 3 m | LV | 35.49 | 0.733 |
| Transformer station ring 5 x 5 m, rod 6 m | MV/LV | 7.526 | 0.543 |
| Transformer station ring 5 x 5 m, 2 rods 12 m at 5 m | MV/LV | 3.99 | 0.392 |
| Substation mesh 50 x 100 m, meshes 10 x 10 m | HV/MV | 0.6503 | 0.220 |
| Substation mesh 200 x 200 m, meshes 10 x 10 m | HV/MV | 0.226 | 0.225 |

The geometries, all in soil without layers:

- **Substation ring 2.5 m, 4 rods 2.5 m** — a circular ring of 2.5 m radius at
  0.6 m depth with four rods of 2.5 m inside it.
- **House foundation 10 x 10 m** — a square foundation electrode at 0.8 m depth
  with 2 × 2 meshes.
- **Rod 2.5 m, Rod 5 m, Cable distribution cabinet, rod 3 m** — a single driven
  rod, its top at the surface.
- **House ring electrode 15 x 5 m** — a rectangular ring at 0.8 m depth.
- **Transformer station ring 5 x 5 m, rod 6 m** — a square ring at 0.8 m depth
  with one rod of 6 m at a corner.
- **Transformer station ring 5 x 5 m, 2 rods 12 m at 5 m** — the same ring with
  two rods of 12 m, each 5 m outside the ring on opposite sides and joined to
  it by a bare earth conductor (round steel or copper rope) at 0.8 m depth;
  the joining conductors are part of the electrode.
- **Substation mesh 50 x 100 m** and **200 x 200 m** — a mesh electrode at
  0.8 m depth with meshes of 10 m × 10 m.

The rods agree with Dwight's closed form $\frac{\rho}{2\pi L}\left(\ln\frac{4L}{a} - 1\right)$
(wire radius $a$ = 5 mm) within 1.5 %, the two meshes with
$\rho/(2D) + \rho/L$ ($D$ the diameter of the circle of the same area, $L$ the
buried conductor length) within 10 %. Layered soil is site data: compute the
site's own characteristic with groundfield — see
[Earthing characteristic from groundfield](../howto/groundfield-characteristics.md).
The presets are computed with `scripts/build_standard_bus_types.py` (needs
groundfield); a preset that exists is kept when another one is added, so the
results of networks built on it do not move.

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
5. NKT, data sheet "NA2XS(F)2Y 12/20 kV — Mittelspannungskabel", CTL00205
   rev. 2, standard DIN VDE 0276, tables "Technische Daten".
6. NKT, data sheet "N2XS(F)2Y 12/20 — Mittelspannungskabel mit
   VPE-Isolierung", standard HD 620 10C / DIN VDE 0276-620, tables "Technische
   Daten" and "Elektrische Eigenschaften".
7. NKT, data sheet "NAYY — Starkstromkabel 1 kV", CTL00061 rev. 2, standard
   VDE 0276-603 3G-2, table "Technische Daten".
