# Model and assumptions

This page explains the physical and numerical model that `groundinsight`
implements. It is aimed at readers who want to understand *why* the code
returns the numbers it returns — for example to validate results against
measurements or analytical expressions. The pages next to it go deeper into
single parts of the model: [impedance formulas](formulas.md),
[phase currents in rings and meshes](phase-currents.md),
[reduction factors and the earthing impedance](reduction-factors.md),
[splitting the network and the location response](decomposition.md) and
[direct current](dc.md).

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
- `Fault` — a marker at a bus. Holds an optional dictionary of
  frequency-dependent scaling factors applied to the source currents; a
  frequency without an entry is scaled with 1.0.

Bus and branch properties are derived from reusable *types* (`BusType`,
`BranchType`) whose impedances are parameterised by formula strings. The
formulas are parsed with SymPy, turned into `lambdify` callables and evaluated
for every network frequency — see
[Impedance formulas and where they come from](formulas.md).

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
mutual-coupling contributions. For every branch that carries phase current a
Norton equivalent is added: the phase current $I_{\text{phase}}$ driving the
branch induces a shield current of magnitude $I_{\text{mut}} =
I_{\text{phase}}\,Z_{\text{mutual}}/Z_{\text{self}}$, injected as
$-I_{\text{mut}}$ at the *from* bus and $+I_{\text{mut}}$ at the *to* bus.
The phase currents come from a solve on the phase-conductor network, which
divides the source current between the routes of a ring or a mesh — see
[Phase currents in rings and meshes](phase-currents.md).

The EPR vector is then

$$
\underline{u}(f) = Y(f)^{-1}\,\underline{i}(f).
$$

Numerically the system is solved with SciPy's sparse LU decomposition
(`scipy.sparse.csc_matrix` + `splu`) — this scales well to meshed
low-voltage networks with thousands of buses. The *path enumeration* of the
legacy phase-current mode does not; see
[Path finding and the legacy mode](phase-currents.md#path-finding).

The source current enters the earthing network at the source bus and leaves it
at the fault bus. That is the reverse of the physical fault current, which
flows from the source through the phase conductor to the fault and returns
through soil and shields, so the potentials and electrode currents come out
with the opposite sign of their physical values; magnitudes, RMS values,
reduction factors and impedances are not affected.
[Conventions and units](../reference/conventions.md#signs-and-phase-angles)
gives the two frames with a worked two-bus example.

## Derived quantities

Once $\underline{u}(f)$ is known for every frequency, three result families
are computed: the earth potential rise and the branch currents (below), and
the reduction factors and the grounding impedance
([Reduction factors and earthing impedance](reduction-factors.md)).

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
I_{\text{branch}}(f) = \frac{u_{\text{to}}(f) - u_{\text{from}}(f)}
                            {Z_{\text{self}}(f)} + I_{\text{mut}}(f),
$$

where the second term accounts for the Norton source representing the
mutual coupling (`compute_branch_currents`).

Mind the frame: the potentials $u$ in the numerator are the earth-side
potentials of the solve, the negatives of the physical ones, so
$u_{\text{to}} - u_{\text{from}}$ is the physical $u_{\text{from}} -
u_{\text{to}}$. `ResultBranch.i_s` is therefore the physical shield current,
counted positive from `from_bus` to `to_bus` — the same direction as the phase
current. In the earth-side frame the same number reads as a current from
`to_bus` towards `from_bus`. A shield that carries the return current of a
phase current flowing from `from_bus` to `to_bus` shows a negative `i_s`;
[Conventions and units](../reference/conventions.md#signs-and-phase-angles)
works this through on a line.

## Active flag and outage studies

Both `Bus` and `Branch` carry a boolean `active` field (default
`True`). The flag has a clean physical interpretation:

- An inactive `Bus` is **removed from the nodal system** entirely.
  Its row and column drop from $Y(f)$ and the bus contributes
  nothing to the right-hand-side vector $\underline{i}(f)$.
- An inactive `Branch` behaves as an **open circuit**: no
  contribution to the admittance matrix, no Norton-equivalent
  injection from the mutual coupling, and a zero current in the
  result.

The phase-conductor network leaves inactive elements out in the same way,
and so does `PathFinder` when it enumerates source-to-fault paths for the
legacy mode, so the per-path Norton bookkeeping stays consistent. The flag
is round-tripped through SQLite and JSON; existing payloads load
with `active=True` for every element, which keeps backwards
compatibility intact.

That makes maintenance scenarios, planned outages, broken shields
and N-1 contingencies expressible without rebuilding the network.
The `groundinsight.simulation.outage` sub-package wraps this into
two convenience entry points:

- `outage_context(network, outage)` — context manager that flips
  the listed elements to `active=False` for the duration of a
  `with` block and restores the previous state (including the
  cached path list) afterwards.
- `run_outage_study(network, fault, scenarios=[...])` — executes
  the base case plus one fault calculation per `Outage` scenario
  and returns an `OutageStudyResult` whose
  `compare_buses(...)` / `compare_branches(...)` accessors yield
  long-format Polars DataFrames with absolute and relative deltas
  against a chosen reference scenario.

## Inverse rho analysis

The forward solve answers "given $\rho_E$, what is the EPR?" The
sister inverse question — *"how large can $\rho_E$ become before
the EPR at the fault bus exceeds a limit $u_{\text{limit}}$?"* — is answered by
`groundinsight.analysis.find_max_rho_scaling`. It log-bisects a
uniform scaling factor $c$ of `Bus.specific_earth_resistance` on a
user-selected bus set, re-evaluates each `BusType.impedance_formula`
through the existing SymPy machinery and triggers `run_fault` at
every trial value. The output is the largest *verified admissible*
$c$ — a factor for which $|U_\text{EPR}(f)|_{\text{RMS}} \le u_{\text{limit}}$
was actually measured — together with the EPR at that point and the
per-bus $\rho_{\max} = c\,\rho_0$. The original $\rho$ values are
restored via a `finally` block, so the network is unchanged after the
call. A frequency-dependent variant (`find_max_rho_f_scaling`)
extends the same idea to two-parameter rho-f curves.

The limit is an earth potential rise, not a touch voltage: the EPR is an upper
bound of the touch voltage, and how a permissible touch voltage turns into a
limit for the EPR is the subject of
[Compare results with EN 50522 limits](../howto/en50522-check.md).

"Verified admissible" and "maximal" are not the same claim, and the
result distinguishes them. A bisection can also end because the
entire bracket was admissible (the true maximum lies above
`c_bounds[1]` and was never determined) or because the iteration cap
was reached before the bracket closed. In both cases the returned
$c$ is still a factor that satisfies the limit — it is simply not the
largest one. The `status` and `converged` keys carry that
distinction, and `c_bracket` gives the interval that provably
contains the true threshold; an infinite upper bound is the
machine-readable form of "widen `c_bounds`". Only a converged result
is a maximum in the sense the question asks for.

The scaling acts on the soil resistivity of the selected **buses** only. A
branch whose formulas contain `rho` — Carson's earth-return term, for
instance — keeps its own `Branch.specific_earth_resistance`. For a Carson
formula that matters: its reactance grows by
$\omega \cdot 2\cdot10^{-4} \ln\sqrt{10} = 0.072\ \Omega/\text{km}$ per decade
of $\rho$ at 50 Hz, about 11 % of the screen-loop reactance of the cable in
[Bus and branch types from data sheets](../howto/types-from-datasheets.md).

## Transient simulations

The phasor-domain pipeline above answers the *stationary* question.
For non-sinusoidal fault currents (fault inception, DC offset
asymmetry, clearing, switching transients) the
`groundinsight.simulation.transient` sub-module adds a time-domain
layer on top of the same network model. Two solver paths are
available: an FFT-based path that re-uses the existing
`impedance_formula` of `BusType` and `BranchType` per FFT bin, and a
state-space ODE path that consumes the lumped RLC fields
(`R_formula`, `L_formula`, `C_formula`, `R_self_formula`,
`L_self_formula`, `C_self_formula`, `R_mutual_formula`,
`M_mutual_formula`) and integrates with `scipy.signal.lsim`. Source
waveforms come from the `groundinsight.simulation.waveforms`
library (`step`, `sinusoidal_with_dc_offset`,
`damped_oscillation`) or from any user-supplied vectorised callable
``f(t) -> values``. See [Transient simulation](../transient.md) for the
derivations and the [transient-simulations reference](../api/transient.md)
for the full API.

## Assumptions and limits of the model

Every result rests on the following assumptions. An assessment has to check
that they fit the installation; [Verification and validation](verification.md)
lists what has been checked against what.

1. **Quasi-stationary phasors.** One linear solve per frequency with lumped
   elements: no travelling waves, no soil ionisation, no frequency dependence
   other than what the formulas contain. The transient solvers drop the
   phasor assumption, not the lumped one.
2. **Lumped electrodes in homogeneous soil.** Every bus is one impedance to
   remote earth; neighbouring electrodes do not couple resistively; the soil
   is one `rho` per bus and per branch. The model gives earth potential rises,
   not touch or step voltages and not the potential on the surface.
3. **One-way coupling.** The phase currents are determined first, on the
   phase-conductor network, and then drive the shields; the shields do not act
   back on the split. That is exact when the ratio $k = Z_m/Z_s$ is the same on
   every branch of each mesh of the phase network, and an approximation
   otherwise — see
   [When the split is exact](phase-currents.md#one-way-coupling-when-the-split-is-exact).
4. **One phase conductor.** The phase network contains the faulted conductor
   only. In a ring the healthy phases form closed loops of their own, and the
   currents the fault induces in them are not modelled. The size of that
   effect has not been quantified.
5. **Current sources for the stationary solve.** A Thévenin source
   (`create_voltage_source`) couples to the shields with $E/Z_\text{src}$
   instead of its actual loop current; where branches with mutual coupling
   carry its phase current, `run_fault` warns (`ModelAssumptionWarning`,
   code `THEVENIN_COUPLING`), and the EPR at the fault can come out too low.
   Model the infeed as a current source carrying the prospective fault current
   for stationary studies.
6. **A source on the fault bus counts as fault current.** The current-based
   reduction factor divides by the sum of all sources that feed the fault,
   including one that sits on the fault bus itself, whose current never
   reaches the earthing network. With 1000 A from a remote infeed and 500 A
   from a source on the fault bus, `value_current` is two thirds of its value
   without the local source, while the earth current $I_E$ is the same. For
   the reduction factor of the line, divide `i_earth` by the remote infeed
   yourself; see [Reduction factors](reduction-factors.md#reduction-factor-on-a-current-basis).
7. **Bus ρ only in the inverse analysis.** `find_max_rho_scaling` and
   `find_max_rho_f_scaling` scale the soil resistivity of buses; branch
   formulas keep theirs (see [Inverse rho analysis](#inverse-rho-analysis)).
8. **Signs in the earth-side frame.** Potentials and electrode currents are
   reported as the negatives of their physical values
   ([Conventions and units](../reference/conventions.md#signs-and-phase-angles)).
9. **The formulas are the user's.** The package checks a formula's syntax,
   its symbols and that the impedance it gives is passive, not whether it
   describes the conductor it is attached to
   ([Impedance formulas](formulas.md)).

## Worked example

The full physical model behind the equations above maps onto the
Python API in a single block:

!!! note "Illustrative values"

    The formulas below are round numbers chosen to make the example short,
    not the data of a real cable or electrode.
    [Bus and branch types from data sheets](../howto/types-from-datasheets.md)
    derives them for a real cable.

```python
import groundinsight as gi

net = gi.create_network(name="demo", frequencies=[50.0, 250.0])

bus_type = gi.BusType(
    name="SubstationBus", system_type="Substation",
    voltage_level=20.0,
    impedance_formula="rho * 0.01 + j * f * 1/50 * 0.1",
)
cable_type = gi.BranchType(
    name="MSCable", grounding_conductor=True,
    self_impedance_formula="(0.25 + j * f * 0.012) * l",
    mutual_impedance_formula="(0.0  + j * f * 0.012) * l",
)

gi.create_bus(name="b0", type=bus_type, network=net)
gi.create_bus(name="b1", type=bus_type, network=net)
gi.create_branch(name="ln", type=cable_type,
                 from_bus="b0", to_bus="b1", length=5.0, network=net)
gi.create_source(name="src", bus="b0",
                 values={50.0: 1000.0, 250.0: 200.0}, network=net)
gi.create_fault(name="F1", bus="b1",
                scalings={50.0: 1.0, 250.0: 1.0}, network=net)

gi.run_fault(network=net, fault="F1")
print(net.res_all_impedances())   # Z_E and both reduction factors per frequency
```

The `Network` API hides every step listed in the next section behind
`run_fault`; the results are exposed as Polars DataFrames via
`net.res_buses(fault="F1")`, `net.res_branches(fault="F1")` and
`net.res_all_impedances()`.

## Summary of the calculation pipeline

`run_fault(network, fault)` executes the following steps (see
`network_operations.run_fault` and `ElectricalNetwork` for details):

1. `set_active_fault` — select the target fault.
2. Find the sources that reach the fault through the active network — a
   breadth-first search; paths are enumerated only in the legacy
   `phase_current_mode="paths"`.
3. `build_electrical_network` — create an `ElectricalNetwork` helper that
   holds the numerical arrays: $Y(f)$, and $\underline{i}(f)$ with the source
   injections and the Norton injections of the coupling. The phase currents
   behind the latter come from a solve on the phase-conductor network per
   source and frequency. The input checks of `run_fault` (source values per
   frequency, fault scalings) run here.
4. `solve_network` — solve the linear system per frequency (sparse LU).
5. `compute_branch_currents` — derive branch currents from $\Delta u$ plus
   Norton contributions.
6. `compute_reduction_factors` — re-solve without mutual Norton sources and
   take the EPR ratio at the fault bus (`value`); sum the electrode currents of
   the earth group for the current-based factor (`value_current`); record
   $U_E$, $Z_E$ and $I_E$.
7. `compute_grounding_impedance` — the driving-point impedance of the earthing
   network at the fault bus, per frequency.

The persistent state of the calculation (sparse matrices, vectors) lives on
a private `ElectricalNetwork` attribute of the `Network` instance, so
re-solving after topology changes only requires a fresh `run_fault` call.
Conditions found on the way that change how a result must be read are
collected in `Result.diagnostics`.
