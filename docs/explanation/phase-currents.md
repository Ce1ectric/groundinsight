# Phase currents in rings and meshes

The coupling between the faulted phase conductor and the shields is driven by
the phase current in every branch. On a radial feeder that current is the
source current on every branch between source and fault. In ring or meshed
topologies a single source–fault pair yields multiple routes, and the source
current has to be *divided* between them. This page explains how
`groundinsight` divides it, what `phase_impedance_formula` must describe, when
the result is exact, and what the legacy path-based mode does differently.

## The phase-conductor network

The division is the default and is computed, not declared:
`run_fault(..., phase_current_mode="auto")` solves a reduced
**phase-conductor** network per source — the fault bus as reference node,
the source current injected at the source bus — and reads the branch phase
currents off that solution. The split then follows the topology and the
conductor impedances, is independent of the order the branches were declared
in, and needs no user input.

The solve runs on the galvanic component of the phase network that contains
the fault bus; inactive branches and a phase conductor declared open
(`phase_impedance_formula="nan"`) are not part of it. A source that reaches
the fault through the earthing network but has no phase-conductor connection
to it cannot feed the fault; `run_fault` still injects its current into the
earthing system — as if it returned entirely through soil and shields, which
is conservative for its share — and says so with a `ModelAssumptionWarning`,
code `SOURCE_WITHOUT_PHASE_PATH`.

## What `phase_impedance_formula` describes

The phase impedance used for that solve comes from
`BranchType.phase_impedance_formula` (symbols `f`, `rho`, `l`, same as the
other formulas). It is optional, because in a network without cycles the
split is fixed by the topology alone and the impedance value cannot change
it.

Where it matters, it has to be the impedance the one-way model needs. Per
branch, the phase conductor (voltage $V$) and the shield (potential $u$) obey,
both loops with earth return and both currents in the same direction,

$$
\begin{pmatrix} V_a - V_b \\ u_a - u_b \end{pmatrix}
= \begin{pmatrix} Z_{pp} & Z_m \\ Z_m & Z_s \end{pmatrix}
  \begin{pmatrix} I_p \\ I_s \end{pmatrix}.
$$

Eliminating the shield current $I_s$ gives

$$
(V_a - k\,u_a) - (V_b - k\,u_b) = Z_\text{red}\, I_p,
\qquad
Z_\text{red} = Z_{pp} - \frac{Z_m^2}{Z_s},
\qquad
k = \frac{Z_m}{Z_s}.
$$

`phase_impedance_formula` is therefore the **phase–earth loop impedance with
the shield bonded at both ends**, $Z_\text{red}$, for a branch with a grounding
conductor, and the plain phase–earth loop impedance $Z_{pp}$ for a branch
without one. It is neither the positive-sequence impedance $R_1 + jX_1$ nor the
resistance of the conductor alone.
[Bus and branch types from data sheets](../howto/types-from-datasheets.md#phase-impedance-for-rings-and-meshes)
computes $Z_\text{red}$ for a real cable.

## Without the formula: the proxy

Where cycles exist and the formula is missing, the solve falls back to a
proxy — `1/Z_self` for a branch with a grounding conductor, `1/length` for
one without — and says so once per solve with a `ModelAssumptionWarning`,
code `PHASE_PROXY`, that names the branches. Those two quantities are not
comparable, so the split between routes of different construction is then a
heuristic. Declare the formula whenever the ring mixes cable and overhead
line.

The warning is raised only where it means something: in a network without a
cycle around the fault, the proxy cannot change the result, and it stays
silent. A branch whose shield is broken (`self_impedance_formula="nan"`) keeps
its phase conductor in the phase network with the length proxy — a broken
screen does not open the conductor next to it.

## One-way coupling: when the split is exact

The model determines the phase currents first and lets them drive the shields
afterwards; the shield currents do not act back on the split. The elimination
above shows when that is exact. If $k$ has the same value on every branch of a
mesh, $W = V - k\,u$ is a potential on that mesh, the phase network written in
$W$ no longer contains the shields, and the split it gives is the split of the
fully coupled system. If $k$ differs between the routes of a mesh, the term
$(k_1 - k_2)(u_S - u_F)$ drives a circulating current that the one-way model
does not see.

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

The one-way model divides the source current between the two routes like a
current divider of their phase impedances; `split` computes that share for
route 1. The results against a fully coupled multi-conductor model — phase
conductors, shields and electrodes solved as one system, the test oracle of
the suite:

| Ring | `phase_impedance_formula` | phase current, route 1 | \|EPR\| at `F` | coupled model |
|---|---|---|---|---|
| homogeneous | `Z_red` | 555.56 A | 205.863 V | 555.56 A, 205.863 V |
| homogeneous | `Z_pp` | 555.56 A | 205.863 V | |
| homogeneous | proxy | 555.56 A | 205.863 V | |
| mixed | `Z_red` | 728.39 A | 310.735 V (+7.4 %) | 764.90 A, 289.231 V |
| mixed | `Z_pp` | 582.47 A | 298.635 V (+3.3 %) | |
| mixed | proxy | 653.92 A | 278.187 V (−3.8 %) | |

In the homogeneous ring all three choices give the coupled result — any phase
impedance proportional to length divides the current in inverse proportion to
the route lengths (3.0 km against 2.4 km: 555.56 A on route 1). In the mixed ring none is exact. `Z_red` is the quantity the
elimination defines and gives the split closest to the coupled one; the
smaller EPR error of the other two choices in this ring is a cancellation of
a wrong split against the missing circulating current, not a better model.
The size and the sign of the error depend on the network; it grows with the
difference in $k$ between the routes and is largest where a route without an
earth wire ($k = 0$) runs parallel to a cable.

## One phase conductor

The phase network contains the faulted conductor only. In a ring, the two
healthy phases form closed loops as well, and the fault currents induce
currents in them that couple back to the faulted phase and to the shields.
The model does not represent them, and the size of the effect has not been
quantified.

## Enumerated paths and the legacy mode { #path-finding }

Mutual-coupling injections require a direction. In the legacy
`phase_current_mode="paths"` `groundinsight` derives that direction by
enumerating every simple path from each source bus to the active fault bus
via a depth-first search (`PathFinder`). Each path is stored as an ordered
list of `Branch` objects; its injection signs follow the traversal order.

!!! warning "The enumeration is exponential in the number of loops"

    The number of simple paths grows exponentially with the number of
    independent loops, not with the number of buses: a 500-bus ring has
    two paths, a 5×5 grid 8 512 and a 6×6 grid 1 262 816 — roughly a
    minute of enumeration against milliseconds in the solver. The default
    `phase_current_mode="auto"` therefore does **not** enumerate: it only
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
symmetric ring then solves to `EPR = 0 V` at the fault, an EPR ratio
`value = 0` and no current-based factor at all (`value_current` is `None`: no
current reaches the soil), because the doubled mutual injections cancel the
source exactly. With the cable of the tutorials, $\lvert 1-2k\rvert = 1$ and
the EPR comes out as if the shields did not couple at all, 2.6 times the
correct value ([From a line to a ring](../getting-started/ring.md#step-6-why-dividing-the-current-matters)).
The earthing impedance $Z_E$ is a property of the earthing network alone and
does not change with the mode. The mode is kept so studies
produced before 0.6.0 stay reproducible, and for networks where the split is
known from measurement and set by hand. When a source reaches the fault over
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
