# Concepts

This page explains the physical and numerical model that `groundinsight`
implements. It is aimed at readers who want to understand *why* the code
returns the numbers it returns — for example to validate results against
measurements or analytical expressions.

## Problem statement

Consider a power grid composed of substations (*buses*) and the cables and
overhead lines (*branches*) that connect them. Each bus possesses a grounding
grid tying it to remote earth; each branch carries a grounding conductor
(cable shield, overhead-line earth wire) that is bonded to the grids of its
two terminals. A single-phase-to-ground fault at one bus drives a fault
current back towards the source. That current splits into two parallel paths:

- the **local earth path** through the grounding grid at the fault bus, and
- the **metallic return path** through the grounding conductor(s) of the
  connecting branches.

The split depends on the impedances of both paths and on the mutual coupling
between the faulted phase and the grounding conductor that runs alongside it.
The resulting EPR, the reduction factor and the grounding impedance are the
quantities of interest.

## Objects

`groundinsight` represents the grid with four Pydantic models:

- `Bus` — a node. Carries a grounding impedance $Z_{\text{B}}(\rho_E, f)$ that
  couples the bus to remote earth.
- `Branch` — an edge between two buses with a self impedance
  $Z_{\text{self}}(\rho_E, f, l)$ (series impedance of the grounding
  conductor) and a mutual impedance $Z_{\text{mutual}}(\rho_E, f, l)$
  (coupling between faulted phase and grounding conductor).
- `Source` — a current source anchored at a bus. Holds a dictionary that maps
  each frequency to the injected phasor current.
- `Fault` — a marker at a bus. Holds a dictionary of frequency-dependent
  scaling factors that reduce the source current for each harmonic.

Bus and branch properties are derived from reusable *types* (`BusType`,
`BranchType`) whose impedances are parameterised by formula strings. The
formulas are parsed with SymPy, turned into `lambdify` callables and evaluated
for every network frequency.

## Impedance formulas

Formula strings may contain the symbols

| Symbol | Meaning                           |
|--------|-----------------------------------|
| `rho`  | specific earth resistance $\rho_E$ in $\Omega\,\text{m}$ |
| `f`    | frequency $f$ in Hz               |
| `l`    | branch length $l$ in km           |
| `j`    | imaginary unit (internally `1j`)  |

Expressions are evaluated symbolically, so any analytic expression supported
by SymPy is admissible. The literal string `"nan"` maps to an effectively
infinite impedance and can be used to model open ends (broken shields,
isolators etc.).

## Nodal-admittance formulation

All computations take place per frequency $f$ in the phasor domain.
`groundinsight` assembles an admittance matrix $Y(f)$ of size $N\times N$
(where $N$ is the number of buses):

$$
Y_{ii} = \frac{1}{Z_{\text{B},i}(\rho_E, f)} + \sum_{k \in \mathcal{E}(i)}
        \frac{1}{Z_{\text{self},k}(\rho_E, f, l_k)},
\qquad
Y_{ij} = -\frac{1}{Z_{\text{self},k}(\rho_E, f, l_k)}
\quad (k \text{ connects } i,j).
$$

Each entry can be switched off by setting `grounding_conductor=False` on the
corresponding branch, which is useful for modelling insulated shields.

The right-hand-side vector $\underline{i}(f)$ holds the source injections
scaled by the active fault's frequency scaling and — crucially — the
mutual-coupling contributions. For every branch on a path from source to
fault a Norton equivalent is added: the phase current $I_{\text{phase}}$
driving the branch induces a shield current of magnitude $I_{\text{mut}} =
I_{\text{phase}}\,Z_{\text{mutual}}/Z_{\text{self}}$, injected as
$-I_{\text{mut}}$ at the *from* bus and $+I_{\text{mut}}$ at the *to* bus
(signs follow the direction *source → fault*).

The EPR vector is then

$$
\underline{u}(f) = Y(f)^{-1}\,\underline{i}(f).
$$

Numerically the system is solved with SciPy's sparse LU decomposition
(`scipy.sparse.csc_matrix` + `splu`) — this scales well to meshed
low-voltage networks with thousands of buses.

## Path finding

Mutual-coupling injections require a direction. `groundinsight` derives that
direction by enumerating every simple path from each source bus to the active
fault bus via a depth-first search (`PathFinder`). Each path is stored as an
ordered list of `Branch` objects; its injection signs follow the traversal
order.

In ring or meshed topologies a single source–fault pair yields multiple
paths. By default every path carries the full source current. The optional
`parallel_coefficient` on a branch lets you pre-scale the current share of
individual parallel legs; if you set `auto_parallel_coefficients=True` on
`run_fault`, `groundinsight` solves a reduced phase-only network first and
uses its current distribution as the per-path scaling.

## Derived quantities

Once $\underline{u}(f)$ is known for every frequency, three result families
are computed:

### Earth potential rise (EPR)

The per-frequency bus voltages are stored as `ComplexNumber` entries on
`ResultBus` objects. RMS values across all frequencies are computed via

$$
U_{\text{RMS},i} = \sqrt{\sum_{f} |u_i(f)|^2}.
$$

### Branch currents

For every branch and frequency the current through the grounding conductor
is

$$
I_{\text{branch}}(f) = \frac{u_{\text{from}}(f) - u_{\text{to}}(f)}
                            {Z_{\text{self}}(f)} + I_{\text{mut}}(f),
$$

where the second term accounts for the Norton source representing the
mutual coupling (`compute_branch_currents`).

### Reduction factor

The reduction factor $r$ at the fault bus is defined as

$$
r(f) = \frac{|u_{\text{fault}}^{\text{(with mutual)}}(f)|}
             {|u_{\text{fault}}^{\text{(without mutual)}}(f)|}.
$$

`groundinsight` obtains the denominator by re-solving the same network with
all mutual-coupling Norton sources removed. For a single shielded line
directly between source and fault with identical impedances the expression
collapses to the familiar analytical form

$$
r = \left| 1 - \frac{Z_{\text{mutual}}}{Z_{\text{self}}} \right|.
$$

### Grounding impedance

The effective grounding impedance seen at the fault bus is

$$
Z_G(f) = \frac{u_{\text{EPR}}(f)}{r(f)\,I_{\text{fault}}(f)}.
$$

It is exposed per frequency and as RMS-scalar through
`net.res_all_impedances()`.

## Summary of the calculation pipeline

`run_fault(network, fault_name)` executes the following steps (see
`network_operations.run_fault` and `ElectricalNetwork` for details):

1. `set_active_fault` — select the target fault.
2. `define_paths` — if no paths are set yet, call `PathFinder` to enumerate
   them with a DFS.
3. `build_electrical_network` — create an `ElectricalNetwork` helper that
   holds the numerical arrays.
4. `solve_network` — build $Y(f)$ and $\underline{i}(f)$ and solve the linear
   system per frequency (sparse LU).
5. `compute_branch_currents` — derive branch currents from $\Delta u$ plus
   Norton contributions.
6. `compute_reduction_factors` — re-solve without mutual Norton sources and
   take the EPR ratio at the fault bus.
7. `compute_grounding_impedance` — evaluate $Z_G$ per frequency and as RMS.

The persistent state of the calculation (sparse matrices, vectors) lives on
a private `ElectricalNetwork` attribute of the `Network` instance, so
re-solving after topology changes only requires a fresh `run_fault` call.
