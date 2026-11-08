# Import from pandapower (topology and short circuit)

A distribution network that already exists in
[pandapower](https://www.pandapower.org) does not have to be typed again. This
guide imports its topology, reads a short-circuit case solved by pandapower
and writes the IEC 60909 characteristics of the earth fault onto the grounding
model. Two scripts, [`pandapower_topology.py`](../snippets/pandapower_topology.py)
and [`pandapower_shortcircuit.py`](../snippets/pandapower_shortcircuit.py);
their outputs follow each part.

The importer needs the optional extra:

```bash
pip install "groundinsight[pandapower]"
```

## What pandapower provides — and what it does not

pandapower describes the **electrical** network: buses, lines with their
positive- and zero-sequence data, transformers, switches. It does not describe
the **earthing** network: the electrodes of the stations, the screens of the
cables and their coupling to the phase conductor. The import therefore takes
the topology from pandapower — which buses exist, which lines join them, how
long they are, what is in service — and the earthing side from the types you
pass in `gi.ImportDefaults`: `default_bus_type` for every bus,
`default_branch_type` for every line. Write those types from data sheets
([how-to](types-from-datasheets.md)); the ones below are illustrative.

## Import the topology

```python
--8<-- "docs/snippets/pandapower_topology.py:defaults"
```

`gi.preview_pandapower_import(net_pp, voltage_level_kV=..., defaults=...)`
lists every element with `keep` or `skip` and the reason, before anything is
built; `gi.from_pandapower(...)` builds the network. Pass the same `defaults`
to both, so the preview shows the switches the way they will be imported.

```python
--8<-- "docs/snippets/pandapower_topology.py:preview"
```

```python
--8<-- "docs/snippets/pandapower_topology.py:import"
```

What decides the result:

- **Voltage level.** A bus is imported when its `vn_kv` equals
  `voltage_level_kV` within a relative tolerance of 1e-9 —
  `math.isclose(vn_kv, voltage_level_kV, rel_tol=1e-9)` — so rounding noise
  from a CSV export does not drop the whole level. A line is imported when
  both ends are. A bus without a usable `vn_kv` is skipped
  (`IMPORT_BUS_SKIPPED`); if nothing is left, `IMPORT_EMPTY` says so — usually
  a wrong `voltage_level_kV`. A default bus type written for another level
  is announced (`IMPORT_VOLTAGE_LEVEL_MISMATCH`).
- **Closed bus-to-bus switches bond earthing systems.** A closed switch
  between two buses (`et="b"`) is imported as a short branch of
  `ImportDefaults.switch_branch_type` (length `switch_length_km`, 1 m by
  default). Without `switch_branch_type` it is left out — and a bus reached
  only through it is isolated and reads 0 V, the answer a grounding study must
  never get by accident. The import says so (`IMPORT_SWITCH_NOT_IMPORTED`);
  the preview calls it `no_switch_branch_type`. An open bus-to-bus switch is
  no connection (`switch_open`).
- **An open line switch opens the phase conductor, not the screen.** A
  switch at a line end (`et="l"`) is the switching state of that line. Open,
  it interrupts the phase conductor at that end; the screen is bonded to the
  station earth independently of the switch and stays connected at both
  stations — pandapower carries nothing that would say otherwise. The import
  gives such a line `default_branch_type` with `phase_impedance_formula =
  "nan"` (type `<name>_phase_open`): it carries no fault current, and its
  screen stays in the earthing network. It names line and switch
  (`IMPORT_LINE_SWITCH_OPEN`, severity `info`); the preview marks the switch
  and its line `line_switch_open`, a closed line switch `line_switch_closed`
  (it changes nothing). A ring operated open at a line switch therefore
  arrives open — see [below](#a-ring-operated-open). If the screen is
  interrupted as well, set the branch inactive. The legacy
  `phase_current_mode="paths"` enumerates routes over every active branch
  and does not see an open phase conductor; the default mode does.
- **Transformers and impedance elements** are not modelled; they are listed
  in the preview and announced (`IMPORT_ELEMENT_NOT_IMPORTED`). A switch at a
  transformer terminal changes nothing (`switch_not_bus_to_bus`).
- **`in_service`** becomes `active`, so an element out of service is out of
  the solve as well.
- **Lengths** come from `length_km`; a line without a usable length gets 1 km
  (`IMPORT_LENGTH_DEFAULT`), a self-loop or a length that cannot be a branch
  is skipped (`IMPORT_LINE_SKIPPED`).

The whole skip vocabulary is on the [I/O page](../api/io.md#skip-reason-vocabulary).

```python
--8<-- "docs/snippets/pandapower_topology.py:without_switch_type"
```

## The phase-conductor impedance

The default phase-current mode solves the phase-conductor network of rings
and meshes, and needs a `phase_impedance_formula` on every branch of a mesh
(otherwise the split uses a proxy, `PHASE_PROXY`).
`derive_phase_impedance` — `ImportDefaults(derive_phase_impedance=True)` — writes one per line from
pandapower's `r_ohm_per_km` and `x_ohm_per_km`: `(r + I*x)*l`. Two
approximations come with it:

- It is the positive-sequence impedance **R₁ + jX₁** of the line. The phase
  network wants the reduced impedance **Z_red** $= Z_{pp} - Z_m^2/Z_s$ of the
  phase conductor with the screen's return
  ([data-sheet how-to](types-from-datasheets.md#phase-impedance-for-rings-and-meshes)).
  The split of the current over parallel routes depends only on the ratios of
  their impedances, so the approximation is **exact only** when all routes are
  of the same construction — the same ratio $Z_\text{red}/(R_1 + jX_1)$ on every
  line. Mixed cable and overhead-line rings need their phase formulas written
  by hand.
- The reactance is pandapower's value at the network frequency, used at
  **every** frequency. For harmonic studies write the formula with
  `j*2*pi*f*L` instead; at 0 Hz the constant reactance is dropped with a
  `DC_LIMIT` warning.

The earth-return formulas are never derived: they depend on the soil and the
cable geometry, which pandapower does not carry.

### A ring operated open

`example_simple` has a ring `MV Main Bus` – `MV Bus 1` – `MV Bus 2` whose
`Line 3` is open at its switch at `MV Bus 2`. The import opens the phase
conductor of `Line 3`, so a fault at `MV Bus 2` is fed over `Line 4` alone;
the same net with every switch closed shows the ring that is not operated:

```python
--8<-- "docs/snippets/pandapower_topology.py:open_line"
```

```text title="Output of pandapower_topology.py"
preview keep: 4 bus
preview keep: 3 line
preview keep: 2 switch
preview skip: 3 bus
preview skip: 1 line
preview skip: 6 switch
preview skip: 1 trafo
imported: 4 buses, branches ['Line 2', 'Line 3', 'Line 4', 'switch_bus_1']
import diagnostics: ['IMPORT_LINE_SWITCH_OPEN', 'IMPORT_ELEMENT_NOT_IMPORTED']
Line 3 imported as: 3x NA2XS(F)2Y 1x300RM/25 12/20 kV, trefoil_phase_open [('branch', 'Line 3'), ('switch', 'switch_bus_5')]
derived phase impedance of Line 3, switch closed: (0.5939 + I * 0.372) * l
without switch_branch_type: 3 branches, ['IMPORT_LINE_SWITCH_OPEN', 'IMPORT_SWITCH_NOT_IMPORTED', 'IMPORT_ELEMENT_NOT_IMPORTED']
phase current, as imported (Line 3 open): Line 2 0.0 A, Line 3 0.0 A, Line 4 1000.0 A
phase current, every switch closed: Line 2 129.9 A, Line 3 129.9 A, Line 4 871.6 A
```

With one cable type on every line the switch state moves the phase and screen
currents — what the thermal check reads — but not the EPR: every coupling EMF
is $k\,I_p$ with the same $k = Z_m/Z_s$, which amounts to injecting
$(1-k)\,I_F$ however the phase current divides. In a ring of mixed
construction the EPR moves as well.

The 110 kV buses, the 110 kV line and the transformer are skipped; the 20 kV
level is imported with all four buses connected. Without `switch_branch_type`
the `MV Transformer Bus` would stand alone.

## Read a short-circuit case

A limit check needs more of the fault current than its RMS value: the peak
$i_p$ and the thermal equivalent current $I_{th}$ depend on the R/X of the
fault loop and on the clearing time — quantities of the phase-side
short-circuit calculation, which pandapower does. For an earth fault
(`fault="1ph"`) `pandapower.shortcircuit.calc_sc` needs, at minimum:

| Element | Columns |
|---|---|
| external grid | `s_sc_max_mva`, `rx_max`, `x0x_max`, `r0x0_max` (the `_min` ones for `case="min"`) |
| lines | `r_ohm_per_km`, `x_ohm_per_km`, `c_nf_per_km` and the zero sequence `r0_ohm_per_km`, `x0_ohm_per_km`, `c0_nf_per_km` (`endtemp_degree` for `case="min"`) |
| transformers, if any | the zero-sequence data `vk0_percent`, `vkr0_percent`, `mag0_percent`, `mag0_rx`, `si0_hv_partial` and `vector_group` |

```python
--8<-- "docs/snippets/pandapower_shortcircuit.py:case"
```

`gi.read_shortcircuit_results(net_pp)` turns `net_pp.res_bus_sc` into a frame
in **amperes** with one row per bus; the fault type, case and clearing time
come from the solved net. Two deliberate deviations from pandapower:

- For `fault="1ph"` pandapower leaves `ip_ka` and `ith_ka` empty. The import
  computes $\kappa = 1.02 + 0.98\,e^{-3R/X}$ on the **earth-fault loop**,
  $R/X = (2R_1 + R_0)/(2X_1 + X_0)$, and from it $i_p = \kappa\sqrt{2}\,I_k''$
  and $I_{th} = I_k''\sqrt{m + n}$; `kappa_origin` says
  `iec_closed_form`. Where pandapower does publish `ip_ka`, its value is used
  (`pandapower`).
- $I_{th}$ is always recomputed: pandapower sets the DC heat factor $m$ to
  zero for $\kappa > 1.99$, where its limit is 2 — too small an $I_{th}$, in
  the unsafe direction.

A bus without the data for κ is announced (`SC_KAPPA_MISSING`), a κ outside
(1, 2] implied by pandapower's own numbers as well (`SC_KAPPA_OUT_OF_RANGE`).

```python
--8<-- "docs/snippets/pandapower_shortcircuit.py:read"
```

## Write the characteristics onto the model

`gi.apply_shortcircuit_characteristics(network, net_pp, fault)` takes the row
of the fault bus — matched by bus name, so the groundinsight network may be
imported or built by hand — and writes R/X, κ and the share of $I_k''$ onto
every source that feeds the fault, and the clearing time `t_k_s` and the AC
heat factor `n_factor` onto the fault. `set_source_values=True` also replaces
the source currents by those shares; it is off by default, so a hand-tuned
excitation is not replaced silently. The returned frame is the audit trail:
`i_k_previous_a` next to `i_k_a`. A replaced `t_k_s` or `n_factor` of the
fault is announced (`SC_VALUE_REPLACED`), an equal split between sources that
inject nothing (`SC_SPLIT_EQUAL`), several matching rows (`SC_ROW_AMBIGUOUS`)
and a voltage source that keeps its values (`SC_VOLTAGE_SOURCE_SKIPPED`).
The thermal check then uses these characteristics
([Thermal limits of earthing conductors](thermal.md)).

```python
--8<-- "docs/snippets/pandapower_shortcircuit.py:apply"
```

```text title="Output of pandapower_shortcircuit.py"
S2: I_k'' = 2039.8 A, R/X = 3.169, kappa = 1.0201 (iec_closed_form)
S2: i_p = 2942.6 A, m = 0.00512, I_th = 2045.0 A, t_k = 0.5 s
source 'infeed': 1000.0 A -> 2039.8 A (share 1), kappa 1.0201
fault 'F': t_k_s = 0.5 s, n_factor = 1
apply diagnostics: []
K1: I_th = 1933.2 A, I_adm = 17874.6 A, utilization = 0.108
K2: I_th = 1912.9 A, I_adm = 17874.6 A, utilization = 0.107
K3: I_th = 65.4 A, I_adm = 17874.6 A, utilization = 0.004
```

**Check it by hand.** pandapower reports for `S2` $R_1 = 0.386\ \Omega$,
$X_1 = 1.723\ \Omega$, $R_0 = 17.043\ \Omega$ and $X_0 = 2.175\ \Omega$, so
$R/X = (2 \cdot 0.386 + 17.043)/(2 \cdot 1.723 + 2.175) = 3.169$ and
$\kappa = 1.02 + 0.98\,e^{-9.51} = 1.0201$ — the low-resistance earthing
makes the loop almost resistive. $i_p = 1.0201 \cdot \sqrt 2 \cdot 2039.8 = 2942.6$ A.
The test suite recomputes every row from pandapower's raw results this way.
