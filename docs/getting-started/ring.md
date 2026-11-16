# From a line to a ring

Distribution networks are rarely a single line. In a ring the fault current
has two routes to the fault, and how it divides between them decides how much
current each cable shield is driven with. This tutorial lays the cable of
[Your first fault calculation](first-calculation.md) as a ring, shows how
`groundinsight` divides the current, adds a harmonic, and checks the numbers
by hand again.

## What you will build

Four stations on a ring of four 2.5 km sections of the same cable. The infeed
sits at `bus_source`, the fault at `bus_fault` on the opposite side; each of
the two routes is 5 km long, like the line of the first tutorial:

```text
                cable_1, 2.5 km         cable_2, 2.5 km
   bus_source ================= bus_a ================= bus_fault
       ||                                                  ||
       ||       cable_3, 2.5 km         cable_4, 2.5 km    ||
       ++====================== bus_b =====================++
```

Every station has the electrode of the first tutorial,
$Z_B = 1 + 0.1j\ \Omega$ at 50 Hz.

## Step 1 — Build the ring

The construction goes into a function, because the ring is built again in
later steps:

```python
--8<-- "docs/snippets/ring.py:build"
```

## Step 2 — Solve: the current divides

```python
--8<-- "docs/snippets/ring.py:first_solve"
```

```text
ModelAssumptionWarning PHASE_PROXY
['PHASE_PROXY']
```

In a line every branch between infeed and fault carries the full fault
current. In a ring the current divides, and `groundinsight` finds the split by
solving a second network made of the phase conductors alone, with the fault as
reference. That network needs the impedance of each phase conductor, which
the type does not declare yet. The solver therefore falls back to a proxy —
the shield impedance — and says so with a `ModelAssumptionWarning`, code
`PHASE_PROXY`. Here the proxy happens to be harmless: both routes are the same
cable over the same length, so any impedance proportional to length divides
the current 500 A : 500 A. In a ring that mixes a cable with an overhead line
the proxy is a guess
([Phase currents in rings and meshes](../explanation/phase-currents.md)).

## Step 3 — Declare the phase impedance

What the split needs is the loop of the faulted phase conductor with earth
return, *with the shield bonded at both ends*:
$Z_\text{red} = Z_{pp} - Z_m^2/Z_s$, where $Z_{pp}$ is the phase–earth loop on
its own. With an illustrative $Z_{pp} = 0.15 + j\,f\,0.015\ \Omega$/km the
formula reads:

```python
--8<-- "docs/snippets/ring.py:phase_impedance"
```

```text
[]
U_E  = 200.84 V
Z_E  = 0.5631 Ohm at 27.11 deg
I_E  = 356.69 A
r    = 0.356691
```

The warning is gone, and the numbers are those of Step 2: the symmetric split
does not depend on the impedance value.
[Bus and branch types from data sheets](../howto/types-from-datasheets.md#phase-impedance-for-rings-and-meshes)
computes $Z_\text{red}$ for a real cable.

## Step 4 — Where the current goes

```python
--8<-- "docs/snippets/ring.py:electrodes"
```

```text
bus_source  EPR   200.84 V   electrode   199.85 A
bus_a       EPR     0.00 V   electrode     0.00 A
bus_fault   EPR   200.84 V   electrode   199.85 A
bus_b       EPR     0.00 V   electrode     0.00 A
```

The two stations half way along the routes stay at zero potential and carry
no current: the ring is symmetric about them, and the soil current enters
at the fault and leaves at the infeed only. The earth current $I_E = U_E/Z_E$
= 356.69 A is more than the 199.85 A of the fault station's own electrode: it
is the current into the whole earthing system $Z_E$, the paths through the
shields to the other electrodes included — the current an ideal electrode at
`bus_fault` would take.

## Step 5 — Check it by hand

The symmetry gives the closed forms. Each route carries $I/2$; because the
ratio $k = Z_m/Z_s$ is the same on every section, the coupling of the phase
current shows up as a current $(1-k)\,I$ that enters the earthing network at
the infeed and leaves it at the fault. With `bus_a` and `bus_b` at zero
potential, the earthing network seen from the fault is its own electrode in
parallel with the two half-routes, $Z_s/2$ for one section of 2.5 km
($Z_s = 0.625 + 1.5j\ \Omega$, $Z_m = 1.5j\ \Omega$):

$$
\lvert U_E\rvert = \lvert 1-k\rvert\, I\, \bigl\lvert Z_B \parallel \tfrac{Z_s}{2} \bigr\rvert,
\qquad
Z_E = Z_B \parallel \Bigl(\tfrac{Z_s}{2} + \tfrac{Z_B}{2} \parallel \bigl(\tfrac{Z_s}{2} + Z_B\bigr)\Bigr).
$$

The second one is one ampere injected at `bus_fault`: its own electrode in
parallel with the two half-routes to the electrodes of `bus_a` and `bus_b` (in
parallel by symmetry, $Z_B/2$), and from there the other two half-routes to
the electrode of `bus_source`.

The reduction factor is the current into an ideal electrode at `bus_fault`
over $I$. With that electrode ideal, `bus_a` and `bus_b` still move together
and act as one node $M$ with $Z_B/2$; the ring becomes `bus_source`
$-\,Z_s/2\,-$ $M$ $-\,Z_s/2\,-$ `bus_fault`, with the current $(1-k)\,I$ taken
out at the infeed and put in at the fault. Two node equations give
$u_M = \alpha\, u_S$ with $\alpha = Z_B/(Z_s + 2Z_B)$,

$$
u_S = \frac{-(1-k)\,I}{1/Z_B + 2(1-\alpha)/Z_s},
\qquad
r = \frac{\bigl\lvert (1-k)\,I + 2\alpha\, u_S / Z_s \bigr\rvert}{I}.
$$

```python
--8<-- "docs/snippets/ring.py:by_hand"
```

```text
by hand: U_E = 200.84 V, r = 0.356691, Z_E = 0.5631 Ohm at 27.11 deg
```

Compared with the line of the first tutorial, the second route halves the
shield impedance between infeed and fault and adds the electrodes of `bus_a`
and `bus_b`: $Z_E$ falls from 0.8440 Ω to 0.5631 Ω and $U_E$ from 275.43 V to
200.84 V. `r` rises from 0.326331 to 0.356691, because the two stations half
way give the current a second way into the soil before it reaches the
infeed. The chain of the standard holds in both: $0.356691 \cdot 1000\ \text{A}
\cdot 0.5631\ \Omega$ = 200.8 V.

## Step 6 — Why dividing the current matters

Older versions of `groundinsight` gave every route the *full* source current.
That mode is still available, so that old studies can be reproduced:

```python
--8<-- "docs/snippets/ring.py:legacy"
```

```text
paths mode: U_E = 522.19 V, r = 0.927396
```

With 1000 A on both routes the coupling is counted twice, and the earthing
network sees $(1-2k)\,I$ instead of $(1-k)\,I$. For this cable
$|1-2k| = |0.625 - 1.5j| / |0.625 + 1.5j| = 1$: the EPR comes out as if the
shields did not couple at all, 2.6 times too high, and $r$ is that of the
galvanic paths alone — with $|1-k|$ = 0.384615 it would be
$0.384615 \cdot 0.927396$ = 0.356691, the value of Step 3. With other cables the same
mistake can drive the result towards zero instead. The default,
`phase_current_mode="auto"`, divides the current and needs nothing from you
([Phase currents in rings and meshes](../explanation/phase-currents.md#path-finding)).

## Step 7 — Add a harmonic

A fault current is rarely a pure 50 Hz sine. Add the frequencies to the
network and give the source an RMS value for each; the fault scales every
frequency with 1.0 unless `scalings` says otherwise:

```python
--8<-- "docs/snippets/ring.py:harmonics"
```

```text
 50 Hz: I_F = 1000.00 A   U_E =  200.84 V   r = 0.356691
250 Hz: I_F =  200.00 A   U_E =   15.71 V   r = 0.083285
   RMS: I_F = 1019.80 A   U_E =  201.46 V
```

Every frequency is an independent solve; the 50 Hz row is that of Step 3.
At 250 Hz the shield reactance dominates its resistance, the shield takes
nearly all of the return current, and $r$ falls to 0.083285. The RMS row
combines the frequencies, $\sqrt{200.84^2 + 15.71^2}$ = 201.46 V; it has no
reduction factor, because a ratio of RMS values is not a property of the
network.

## Step 8 — The reduction factor

`r` (column of `res_fault`, `reduction_factor` of `res_all_impedances`, field
`ResultReductionFactor.value`) is the reduction factor of EN 50522,
$\lvert I_E\rvert/\lvert 3I_0\rvert$ with $I_E = U_E/Z_E$ the current into an
ideal electrode at the fault. It does not depend on the fault station's own
electrode; on a long homogeneous run far from the infeed it is the tabulated
$\lvert 1 - Z_m/Z_s\rvert$ of the cable
([Reduction factors and earthing impedance](../explanation/reduction-factors.md)).
For a check against the limits of the standard, compare $U_E$ itself with the
permissible value — [Compare results with EN 50522 limits](../howto/en50522-check.md)
describes the procedure.

## What next

- [Tasks after the tutorials](index.md#tasks-after-the-tutorials) — sweeps,
  outages, imports, thermal checks, transients and more.
- [Phase currents in rings and meshes](../explanation/phase-currents.md) — when
  the split is exact, and what a mixed ring of cable and overhead line does.
- The example notebooks in [Examples](../examples/index.md) — a 20-station
  MV ring, a fault sweep along a feeder, the analysis layer.
