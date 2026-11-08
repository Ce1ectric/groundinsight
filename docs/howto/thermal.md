# Thermal limits of earthing conductors

Does an earthing conductor survive the fault current it carries? This guide
checks the screens and earth wires between the stations with
`gi.check_conductor_limits` and the earthing conductor and the electrode at
each station with `gi.check_node_limits`, by the adiabatic short-time rating
of IEC 60949 with the thermal equivalent current of IEC 60909-0. The script is
[`thermal.py`](../snippets/thermal.py); its output is at the
[end of the page](#output).

## The check

For each conductor the check compares the thermal equivalent short-time
current

$$ I_{th} = I_\text{RMS}\,\sqrt{m + n} $$

with the adiabatic limit

$$ I_\text{adm} = \frac{k\,S}{\sqrt{t_k}}, \qquad
   k = K\,\sqrt{\ln\frac{\theta_f + \beta}{\theta_i + \beta}} , $$

where $S$ is the cross-section, $t_k$ the short-circuit duration (`t_k_s`),
$m$ the DC heat factor from the peak factor $\kappa$ (IEC 60909-0), $n$ the AC
heat factor (1 far from generators), and $K$, $\beta$ the material constants.
`utilization` is $I_{th}/I_\text{adm}$, `within_limit` is
`utilization <= 1`. Every column: [Result tables](../reference/results.md#thermal-checks).

## What the check needs

- **On the types.** A `BranchType` is checked when it declares
  `conductor_material` and `cross_section_mm2` (optionally `theta_initial_C`,
  default 20 °C, and `theta_final_C`). A `BusType` declares its two elements
  separately: `earthing_conductor_*` for the conductor that carries the full
  injected current `I_inj` into the station, and `electrode_*` for the
  electrode that carries the share into the soil, `I_bus`. Mixing the two
  currents up is the classic sizing error — at the faulted station the
  earthing conductor carries the whole fault current, the electrode often a
  small part of it.
- **`current_split`** (`earthing_conductor_current_split`,
  `electrode_current_split`, between 0 and 1, default 1) is the share one
  physical conductor carries when the model lumps several: 0.5 for a ring
  electrode reached from two sides, 1/N for N parallel legs. It is never
  derived — the geometry is not in the model.
- **The excitation.** `t_k_s` and `n_factor` come from the fault, `kappa` or
  `r_to_x` from the sources that feed it (set by hand, or imported from a
  pandapower case — see [Import from pandapower](pandapower.md#write-the-characteristics-onto-the-model)).
  Keyword arguments of the checks (`t_k_s=`, `kappa=`, `r_to_x=`,
  `n_factor=`, `frequency=`) override the model for a sensitivity study.
  Several infeeds with different R/X are combined into one current-weighted κ
  (`SC_KAPPA_MIXED`); sources without data are announced
  (`SC_DATA_INCOMPLETE`).
- An element with **half** of its thermal data (a material without a
  cross-section) is announced (`THERMAL_DATA_INCOMPLETE`); one with none is
  reported with its currents and `within_limit` null — enough to size it by
  hand, but not a pass.

```python
--8<-- "docs/snippets/thermal.py:types"
```

```python
--8<-- "docs/snippets/thermal.py:network"
```

```python
--8<-- "docs/snippets/thermal.py:check"
```

## Materials and final temperatures

The tables are top-level names of the package (`gi.<name>`).

`IEC60949_MATERIALS` holds $K$ and $\beta$ of IEC 60949 and a default final
temperature for copper, aluminium and steel — 300 °C for all three, the value
for bare buried conductors. `FINAL_TEMPERATURES` lists final temperatures
by material and covering (`bare`, `PVC`, `XLPE`, `EPR`, and `tinned` copper or
`galvanized` steel), with the source named in the module;
`final_temperature(material, covering)` reads it and raises for a
combination the table does not have instead of guessing.
`CABLE_INSULATION_LIMITS` holds the short-circuit limits of the insulation
materials (PVC 160 °C, XLPE and EPR 250 °C): the screen of an XLPE cable may
not get hotter than its insulation, whatever the copper could take.

| Covering | Cu | Al | Steel |
|---|---|---|---|
| bare | 300 °C | 300 °C | 300 °C |
| PVC | 160 °C | 160 °C | 160 °C |
| XLPE, EPR | 250 °C | 250 °C | 250 °C |

Check the values against the edition of EN 50522 you apply before a design
decision; the tables are deliberately incomplete rather than filled with
plausible numbers.

## A direct-current component

The check excludes the 0 Hz component of a current: $I_{th}$ is built from
the alternating components, because $m$ already describes the decaying DC
offset of an alternating fault current. A *genuine* direct current that flows
during $t_k$ — a DC railway, a rectifier — adds about $I_{dc}^2$ to $I_{th}^2$,
which the check would then miss. It reports the 0 Hz part in `I_dc_A` and
announces it (`THERMAL_DC_EXCLUDED`, `ModelAssumptionWarning`) with the
increase of $I_{th}$ that $I_{th}^2 = I_{ac}^2\,(m + n) + I_{dc}^2$ gives.

```python
--8<-- "docs/snippets/thermal.py:dc"
```

## Output

```text title="Output of thermal.py"
K1: I_th = 864.1 A of I_adm = 17874.6 A, utilization 0.048
K2: I_th = 870.9 A of I_adm = 17874.6 A, utilization 0.049
K3: I_th = 899.3 A of I_adm = 17874.6 A, utilization 0.050
K4: I_th = 112.6 A of I_adm = 17874.6 A, utilization 0.006
K5: I_th = 54.6 A of I_adm = 17874.6 A, utilization 0.003
kappa = 1.7460, m = 0.06825, k = 181.3 A*s^0.5/mm2
S3 earthing_conductor: I = 1000.0 A (split 1), I_adm = 13765.9 A, within limit: True
S3 electrode: I = 32.6 A (split 0.5), I_adm = 8967.6 A, within limit: True
not assessed (no thermal data): ['UW']
with 150 A DC, K1: I_dc = 106.6 A, I_ac = 836.1 A, I_th = 864.1 A; ['THERMAL_DC_EXCLUDED']
```

**Check it by hand.** With R/X = 0.1, $\kappa = 1.02 + 0.98\,e^{-0.3} = 1.7460$;
at 50 Hz and $t_k = 0.5$ s, $m = \frac{1}{2 f t_k \ln(\kappa - 1)}\left(e^{4 f t_k \ln(\kappa - 1)} - 1\right) = 0.06825$.
Copper from 20 °C to 250 °C gives
$k = 226\,\sqrt{\ln\frac{250 + 234.5}{20 + 234.5}} = 181.3\ \text{A}\sqrt{\text{s}}/\text{mm}^2$,
so the screens of 69.7 mm² admit $181.3 \cdot 69.7/\sqrt{0.5} = 17\,875$ A.
The earthing conductor at the faulted station `S3` carries the full 1000 A,
its electrode $2 \cdot 32.6 = 65.2$ A, of which one side of the ring takes
half. The test suite recomputes $k$, $I_\text{adm}$, $I_{th}$ and the node
currents this way.
