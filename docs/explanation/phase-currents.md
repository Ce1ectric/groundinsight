# Phase currents in rings and meshes

The coupling between the faulted phase conductor and the shields is driven by
the phase current in every branch. On a radial feeder that current is the
source current on every branch between source and fault. In ring or meshed
topologies a single source–fault pair yields multiple routes, and the source
current has to be *divided* between them. This page explains how
`groundinsight` divides it, what `phase_impedance_formula` must describe, and
what the deprecated path-based mode does differently.

## One coupled solve

The division is computed, not declared: `run_fault` solves phase conductors,
shields and electrodes **together**, in one linear system per frequency. Every
bus has an earth node (its electrode to remote earth) and a phase node; the
fault bonds the phase node of the fault bus to its earth node; a current
source drives its current from the earth node of its bus into the phase node;
a Thevenin source is its EMF in series with `Z_src` between the two. Per
branch, the phase conductor (voltage $V$) and the shield (potential $u$) obey,
both loops with earth return and both currents in the same direction,

$$
\begin{pmatrix} V_a - V_b \\ u_a - u_b \end{pmatrix}
= \begin{pmatrix} Z_{pp} & Z_m \\ Z_m & Z_s \end{pmatrix}
  \begin{pmatrix} I_p \\ I_s \end{pmatrix}.
$$

The split then follows the topology, the conductors **and** what the shields
and electrodes carry, is independent of the order the branches were declared
in, and needs no user input. The loop current of a Thevenin source is part of
the solution.

The solve runs on the galvanic component of the phase network that contains
the fault bus; inactive branches and a phase conductor declared open
(`phase_impedance_formula="nan"`) are not part of it. A source that reaches
the fault through the earthing network but has no phase-conductor connection
to it cannot feed the fault; `run_fault` still injects its current into the
earthing system — as if it returned entirely through soil and shields, which
is conservative for its share — and says so with a `ModelAssumptionWarning`,
code `SOURCE_WITHOUT_PHASE_PATH`.

## What `phase_impedance_formula` describes

Eliminating the shield current $I_s$ from the branch equations gives

$$
(V_a - k\,u_a) - (V_b - k\,u_b) = Z_\text{red}\, I_p,
\qquad
Z_\text{red} = Z_{pp} - \frac{Z_m^2}{Z_s},
\qquad
k = \frac{Z_m}{Z_s}.
$$

`phase_impedance_formula` is the **phase–earth loop impedance with the shield
bonded at both ends**, $Z_\text{red}$, for a branch with a grounding
conductor, and the plain phase–earth loop impedance $Z_{pp}$ for a branch
without one. The solve rebuilds $Z_{pp} = Z_\text{red} + Z_m^2/Z_s$ from it, so
a type carries exactly one phase quantity, the one a data sheet calculation
gives. It is neither the positive-sequence impedance $R_1 + jX_1$ nor the
resistance of the conductor alone.
[Bus and branch types from data sheets](../howto/types-from-datasheets.md#phase-impedance-for-rings-and-meshes)
computes $Z_\text{red}$ for a real cable.

The formula is optional, because in a network without cycles the split is
fixed by the topology alone and the impedance value cannot change it.

## Without the formula: the proxy

Where cycles exist and the formula is missing, the solve falls back to a
proxy for $Z_\text{red}$ — `Z_self` for a branch with a grounding conductor,
`length` for one without — and says so once per solve with a
`ModelAssumptionWarning`, code `PHASE_PROXY`, that names the branches. Those
two quantities are not comparable, so the split between routes of different
construction is then a heuristic. Declare the formula whenever the ring mixes
cable and overhead line.

The warning is raised only where it means something: in a network without a
cycle around the fault, the proxy cannot change the result, and it stays
silent. A branch whose shield is broken (`self_impedance_formula="nan"`) keeps
its phase conductor in the phase network with the length proxy — a broken
screen does not open the conductor next to it.

## Why the shields belong in the split

A phase-only network with $Z_\text{red}$ — the one-way model, in which the
phase currents are fixed first and drive the shields afterwards — assumes that
every shield carries the induced current $k I_p$ and nothing else. The
elimination above shows when that is exact: if $k$ has the same value on
every branch of a mesh, $W = V - k\,u$ is a potential on that mesh, the phase
network written in $W$ no longer contains the shields, and the one-way split
is the coupled one. If $k$ differs between the routes of a mesh, the term
$(k_1 - k_2)(u_S - u_F)$ drives a circulating current that the one-way model
does not see; the coupled solve does.

The example below makes this concrete
([`phase_split.py`](../snippets/phase_split.py)). A ring `S – A – F – B – S`
is fed with 1 kA at `S` and faulted at `F`; route 1 is a cable
(2 × 1.2 km), route 2 the same cable (*homogeneous*) or an overhead line with
an earth wire (*mixed*, 2 × 1.5 km). The per-km values are illustrative 50 Hz
values:

```python
--8<-- "docs/snippets/phase_split.py:constructions"
```

```python
--8<-- "docs/snippets/phase_split.py:ring"
```

`split` is the one-way current divider of the two routes' phase impedances;
`solve` reads the phase current and the EPR off `run_fault`. The test suite
checks `run_fault` against the fully coupled multi-conductor model of its
oracle:

| Ring | `phase_impedance_formula` | one-way split, route 1 | `run_fault`, route 1 | \|EPR\| at `F` |
|---|---|---|---|---|
| homogeneous | `Z_red` | 555.56 A | 555.56 A | 205.863 V |
| homogeneous | `Z_pp` | 555.56 A | 555.56 A | 205.863 V |
| homogeneous | proxy | 555.56 A | 555.56 A | 205.863 V |
| mixed | `Z_red` | 728.39 A | 764.90 A | 289.231 V |
| mixed | `Z_pp` | 582.47 A | 612.19 A | 283.011 V |
| mixed | proxy | 653.92 A | 683.49 A | 264.794 V |

In the homogeneous ring every choice gives the same result — any phase
impedance proportional to length divides the current in inverse proportion to
the route lengths (3.0 km against 2.4 km: 555.56 A on route 1), and the
coupling changes nothing. In the mixed ring the one-way split of the correct
$Z_\text{red}$ is 37 A off and would put the EPR at 310.7 V, 7.4 % too high;
the coupled solve with $Z_\text{red}$ is the reference result, 764.90 A and
289.231 V. Declaring $Z_{pp}$ or leaving the proxy is a wrong input for the
coupled solve as well: it adds $Z_m^2/Z_s$ to a quantity that already contains
it, or replaces the phase conductor by the shield.

## One phase conductor

The phase network contains the faulted conductor only. In a ring, the two
healthy phases form closed loops as well, and the fault currents induce
currents in them that couple back to the faulted phase and to the shields.
The model does not represent them, and the size of the effect has not been
quantified.

## Enumerated paths and the deprecated mode { #path-finding }

Mutual-coupling injections require a direction. In the deprecated
`phase_current_mode="paths"` (a `DeprecationWarning` on use) `groundinsight`
derives that direction by
enumerating every simple path from each source bus to the active fault bus
via a depth-first search (`PathFinder`). Each path is stored as an ordered
list of `Branch` objects; its injection signs follow the traversal order.

!!! warning "The enumeration is exponential in the number of loops"

    The number of simple paths grows exponentially with the number of
    independent loops, not with the number of buses: a 500-bus ring has
    two paths, a 5×5 grid 8 512 and a 6×6 grid 1 262 816 — roughly a
    minute of enumeration against milliseconds in the solver. The default
    `phase_current_mode="auto"` does **not** enumerate: it only
    needs to know which sources reach the fault, and
    `Network.sources_reaching(...)` answers that with a breadth-first
    search. `network.paths` is empty after a default `run_fault`; call
    `gi.create_paths` on the network if you want the paths as data.

`phase_current_mode="paths"` selects the pre-0.6 behaviour: every branch on
every enumerated path receives the **full** source current, scaled by that
branch's `parallel_coefficient`. This is correct for a radial network — where
both modes agree exactly — but in a meshed one it multiplies the current
instead of dividing it. In a ring of two routes of the same construction, at
the default coefficient of `1.0`, the Norton injections add up to
$(1-2k)\,I$ instead of $(1-k)\,I$. With $Z_m = Z_s/2$ ($k = 0.5$) the
symmetric ring then solves to `EPR = 0 V` at the fault and a reduction
factor `value = 0`, because the doubled mutual injections cancel the source
exactly. With the cable of the tutorials, $\lvert 1-2k\rvert = 1$ and
the EPR comes out as if the shields did not couple at all, 2.6 times the
correct value ([From a line to a ring](../getting-started/ring.md#step-6-why-dividing-the-current-matters)).
The earthing impedance $Z_E$ is a property of the earthing network alone and
does not change with the mode. The mode is kept for the deprecation period
so studies produced before 0.6.0 stay reproducible; it couples a Thevenin
source with its Norton current `E/Z_src` (`THEVENIN_COUPLING`). When a source reaches the fault over
more than one path and the coefficients are still at their default, the mode
says so (`ModelAssumptionWarning`, code `PARALLEL_COEFFICIENT_DEFAULT`).

The deprecated `auto_parallel_coefficients` argument still works and still
wins when passed: `True` maps to `"auto"`, `False` to `"paths"`.

## When there is no path

Two different situations both end in "no paths", and `groundinsight` treats
them differently on purpose. **No sources or no faults at all** is rejected:
the enumeration runs over `sources × faults`, so an empty side means nothing
was ever asked, and the all-zero result that follows is an artefact of an
incomplete model rather than a statement about the system. **No path between
an existing source and an existing fault** is accepted: that is the signature
of an islanded fault bus — the normal outcome of an outage scenario — and 0 V
is then the physically correct answer.

## See also

- [From a line to a ring](../getting-started/ring.md) — the tutorial: a
  symmetric ring, the phase impedance and the legacy mode.
- [Phase currents in meshes](../examples/mesh_phase_currents.ipynb) — the
  example notebook: a line, a ring, two parallel cables and a mesh in both
  modes, checked against a fully coupled multi-conductor solve.
