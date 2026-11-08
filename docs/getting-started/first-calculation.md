# Your first fault calculation

This tutorial builds the smallest network that shows what `groundinsight` is
for, solves it, and then computes every number it prints a second time, by
hand. It takes about fifteen minutes; you need an
[installation](installation.md) and a notebook or a Python file.

## What you will build

Two substations, joined by a 5 km medium-voltage cable whose shield is earthed
at both ends. An earth fault at `bus_fault` draws 1000 A from the infeed at
`bus_source`:

```text
   1000 A infeed                                         earth fault
    bus_source ============ cable_1, 5 km ============ bus_fault
        |            phase conductor + shield              |
       Z_B                                                Z_B
        |                                                  |
      remote earth  - - - - - - - - - - - - - - - -  remote earth
```

The fault current flows from the infeed through the phase conductor to the
fault and has two ways back: through the cable shield, and through the soil —
into the earth at `bus_fault`, out again at `bus_source`. The phase current
induces a voltage along the shield, which pulls most of the return current
into the shield. The questions are the ones of every earthing study: how much
of the fault current flows through the soil, and to what potential the
earthing system at the fault rises against remote earth (its *earth potential
rise*, EPR).

Lengths are in km, soil resistivity in Ω·m, currents are RMS values in A, and
impedances are in Ω ([Conventions and units](../reference/conventions.md)).

## Step 1 — A network at 50 Hz

Everything lives in a `Network`, which fixes the frequencies to solve. One
frequency is enough for the first calculation:

```python
--8<-- "docs/snippets/first_calculation.py:network"
```

## Step 2 — Types

A *type* describes a kind of station or cable once; buses and branches refer
to it. Its impedances are formulas in the frequency `f`, the soil resistivity
`rho` and, for a cable, the length `l`:

```python
--8<-- "docs/snippets/first_calculation.py:types"
```

- `impedance_formula` is the electrode of a station against remote earth,
  $Z_B$. With $\rho$ = 100 Ω·m at 50 Hz it is $Z_B = 1 + 0.1j\ \Omega$.
- `self_impedance_formula` is the loop formed by the shield and the earth
  return, $Z_s = 0.25 + 0.6j\ \Omega$/km at 50 Hz.
- `mutual_impedance_formula` is the coupling between the phase conductor and
  that loop, $Z_m = 0.6j\ \Omega$/km at 50 Hz.

!!! note "Illustrative values"

    These formulas are round numbers that keep the tutorial short, not the
    data of a real cable or electrode.
    [Bus and branch types from data sheets](../howto/types-from-datasheets.md)
    derives them for a real 20 kV cable;
    [Formula syntax](../reference/formula-syntax.md) lists what a formula may
    contain.

## Step 3 — Buses and the cable

```python
--8<-- "docs/snippets/first_calculation.py:elements"
```

Each element evaluates the formulas of its type with its own `rho` and
`length` as soon as it joins the network.

## Step 4 — The source and the fault

```python
--8<-- "docs/snippets/first_calculation.py:source_fault"
```

`values` holds the source current per frequency as an RMS phasor. A fault
can scale the sources per frequency with `scalings`; without it, every
frequency counts in full
([Conventions and units](../reference/conventions.md#frequencies-source-values-and-fault-scalings)).

## Step 5 — Solve

```python
--8<-- "docs/snippets/first_calculation.py:solve"
```

```text
diagnostics: []
```

`run_fault` solves the network for the fault and returns a `Result`; the
network keeps it under `net.results["fault_at_remote_bus"]` as well. When the
solver has to make an assumption that changes how a result must be read, it
says so with a Python warning and keeps the same message, with a code and the
elements it is about, in `result.diagnostics`. For this network there is
nothing to say. The [next tutorial](ring.md) shows one.

## Step 6 — Read the results

`net.res_fault(...)` returns the key figures of the fault as a Polars
DataFrame, one row per frequency and one RMS row; `net.res_buses(...)` and
`net.res_branches(...)` give the potential and the currents of every bus and
branch:

```python
--8<-- "docs/snippets/first_calculation.py:results"
```

Expected output:

```text
U_E  = 275.43 V        EPR at the fault bus
Z_E  = 0.8440 Ohm at 15.18 deg
I_E  = 274.06 A        earth current
r    = 0.274064       reduction factor, EN 50522
EPR ratio = 0.384615  reduction factor of the cable
bus_source EPR 275.43 V at  -38.85 deg, electrode 274.06 A at  -44.56 deg
bus_fault  EPR 275.43 V at  141.15 deg, electrode 274.06 A at  135.44 deg
cable_1    shield 827.37 A at -166.56 deg
```

- $U_E$ = 275.43 V is the EPR of the station at the fault.
- $Z_E$ is the *earthing impedance* at the fault: the impedance one would
  measure there against remote earth with everything bonded to the station —
  its own electrode and, through the cable shield, the electrode at
  `bus_source`.
- $I_E$ = 274.06 A of the 1000 A return through the soil; the shield carries
  827.37 A. The two do not add up to 1000 A because they are phasors with
  different angles.
- Two reduction factors describe the split. `r` = $|I_E|/|3I_0|$ is the
  reduction factor of EN 50522, the share of the fault current that returns
  through the soil. The *EPR ratio* compares the EPR at the fault with and
  without the coupling between phase conductor and shield; for one cable it is
  the tabulated reduction factor of the cable, $|1 - Z_m/Z_s|$, whatever the
  electrodes. [Reduction factors and earthing impedance](../explanation/reduction-factors.md)
  explains why there are two.

The angles are relative to the source current. The potentials and electrode
currents are reported for a current that enters the earthing system at the
infeed and leaves it at the fault — the reverse of the physical direction — so
add 180° for the physical angle; the shield current is the physical one,
counted from `from_bus` to `to_bus`
([Signs and phase angles](../reference/conventions.md#signs-and-phase-angles)).
Both stations rise to the same EPR here because their electrodes are equal;
their electrode currents are equal and opposite — whatever enters the soil at
one station leaves it at the other.

## Step 7 — Check it by hand

A single line has closed forms for all of these numbers. Follow the return
current around the loop formed by the shield and the earth path: the soil
carries $I_E$ through both electrodes, the shield carries $I_s = I - I_E$, and
the phase current $I$ induces $Z_m I$ in the shield. Kirchhoff's voltage law
around that loop gives

$$
2 Z_B\, I_E = Z_s\,(I - I_E) - Z_m\, I
\quad\Longrightarrow\quad
I_E = I\,\frac{Z_s - Z_m}{Z_s + 2Z_B},
\qquad
U_E = Z_B\, I_E .
$$

Without the coupling ($Z_m = 0$) the same loop gives $I\,Z_s/(Z_s + 2Z_B)$, so
the EPR ratio is $|Z_s - Z_m|/|Z_s| = |1 - Z_m/Z_s|$. One ampere injected at
`bus_fault` finds its own electrode in parallel with the shield and the other
electrode: $Z_E = Z_B \parallel (Z_s + Z_B)$. With $Z_B = 1 + 0.1j\ \Omega$,
$Z_s = 1.25 + 3j\ \Omega$ and $Z_m = 3j\ \Omega$ for the 5 km:

| Quantity | By hand | `groundinsight` |
|---|---|---|
| EPR ratio | $1.25 / \lvert 1.25 + 3j\rvert = 1.25/3.25$ = 0.384615 | 0.384615 |
| `r` | $1.25 / \lvert 3.25 + 3.2j\rvert = 1.25/4.5610$ = 0.274064 | 0.274064 |
| $\lvert U_E\rvert$ | $\lvert 1 + 0.1j\rvert \cdot 274.06$ A = 275.43 V | 275.43 V |
| $Z_E$ | $(1+0.1j)(2.25+3.1j)/(3.25+3.2j)$ = 0.8146 + 0.2210j Ω, \|Z<sub>E</sub>\| = 0.8440 Ω | 0.8440 Ω at 15.18° |

The same in code:

```python
--8<-- "docs/snippets/first_calculation.py:by_hand"
```

```text
by hand: EPR ratio = 0.384615, r = 0.274064, U_E = 275.43 V, |Z_E| = 0.8440 Ohm
```

EN 50522 writes the EPR as the chain $U_E = r \cdot 3I_0 \cdot Z_E$. On this
short line it does not close: $0.274064 \cdot 1000\ \text{A} \cdot
0.8440\ \Omega$ = 231.3 V, and with the cable's 0.384615 it is 324.6 V,
against the 275.43 V both ways of computing gave above. The chain is the
standard's picture of a fault far down a long bonded run, where it does hold;
on a single line the electrode of the infeed station sits in the return path.
Make that electrode ideal and the chain closes exactly with the cable's
factor, $U_E = |1 - Z_m/Z_s|\, I\, |Z_E|$.
[Reduction factors and earthing impedance](../explanation/reduction-factors.md#which-one-is-the-en-50522-reduction-factor)
shows the long run.

## Step 8 — Plot and save

```python
--8<-- "docs/snippets/first_calculation.py:plot_save"
```

`plot_bus_voltages` draws the EPR of every bus and returns the Matplotlib
figure; `save_network_to_json` writes the network — types, elements, sources
and faults — to a project file that `load_network_from_json` reads back.

## What next

- [From a line to a ring](ring.md) — the same cable laid as a ring: how the
  fault current divides between the routes, the phase impedance, harmonics.
- [Understanding the model](../explanation/index.md) — the model behind the
  numbers and its assumptions.
- [Tasks after the tutorials](index.md#tasks-after-the-tutorials) — where the
  documentation describes the next steps of a study.
