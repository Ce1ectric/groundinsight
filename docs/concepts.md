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

### Which values a formula may produce

Every impedance that ends up on the diagonal of $Y(f)$ is inverted, so not
every number a formula can produce is a physically meaningful model. Three
results are rejected with a `ValueError` that names the element, the
frequency and the formula:

| Result | Why it is rejected |
|--------|--------------------|
| $\underline{Z} = 0$ exactly | An element with no impedance has no admittance either. It drops out of the nodal system entirely and therefore reports the *opposite* of the ideal-earth limit: full earth potential rise and no current into the soil, indistinguishable from a bus with no electrode at all. |
| $0 < \lvert\underline{Z}\rvert \lesssim 5.6\cdot10^{-309}\ \Omega$ | Too small to invert in double precision. $1/\underline{Z}$ overflows, reaches the admittance matrix as infinity, and the bus current comes back as `NaN`. |
| $\operatorname{Re}(\underline{Z}) < 0$ | An earth electrode, an earthing conductor and a cable screen are passive; a negative resistance generates energy. It also pushes $Y(f)$ towards singularity, where the earth potential rise grows without bound while every intermediate number still looks plausible. |

A **near-ideal earth** is modelled with a small finite value, not with zero.
The solution converges smoothly as $\underline{Z}\to 0$, with a relative error
of the order of the ratio between that value and the other impedances in the
network: in a network whose impedances are of the order of $1\ \Omega$,
$10^{-6}\ \Omega$ reproduces the ideal-earth limit to about seven digits, and
every further decade buys another digit.

A **negative real part** is almost always a fitted formula evaluated outside
the range it was fitted on. `0.05*rho - 2`, a plausible fit for a rod
electrode, is negative below $\rho_E = 40\ \Omega\,\text{m}$ — wet clay, not
an exotic soil. Check a new formula at the lowest $\rho_E$, the lowest
frequency and the shortest length that occur in the model.

The rule applies exactly where an impedance is inverted, and nowhere else:

- **Bus grounding impedances** and the **self impedances of branches with
  `grounding_conductor=True`** are checked; the self impedance of a branch
  with `grounding_conductor=False` is never inverted and is not checked.
- **Mutual impedances** are not checked. `Z_mutual = 0` is the normal way to
  express "no coupling" and stays legal.
- **Source impedances** of voltage sources are checked. (A zero source
  impedance was already rejected when the source was constructed; a negative
  one was not.)
- **Inactive** buses and branches, and frequencies outside
  `network.frequencies`, are not checked — they never reach the matrix.
- Infinity stays legal everywhere: it is the documented open-end sentinel,
  and $1/\infty = 0$ is the right answer. `NaN` is reported by its own
  handler.

The check runs both where the impedance is computed (`calculate_impedance`)
and again immediately before the matrix is assembled, because impedances are
*not* recomputed at solve time — a value written directly into
`bus.impedance[f]`, or restored from JSON or from the database, would
otherwise reach the solver unexamined.

In a **transient simulation** the same rule is applied to every bin of the
FFT frequency grid, the 0 Hz bin included. At 0 Hz the first two rows of the
table are inverted: a purely reactive impedance such as `j*2*pi*f*L` really is
a short circuit at DC, so there the value is accepted and replaced by a small
finite stand-in instead of being rejected — see
[Direct current (`f = 0`)](#direct-current-f-0) below. The negative real part
stays rejected at 0 Hz as well; a passive element is passive at DC too.

## Direct current (`f = 0`)

`f = 0` is an ordinary entry in `Network.frequencies` and in `Fault.scalings`,
and a direct-current study needs nothing beyond it:

```python
net = gi.create_network(name="dc_return", frequencies=[0.0])
# ... buses and branches ...
gi.create_source(name="electrode", bus="A", values={0.0: 1000.0}, network=net)
gi.create_fault(name="F", bus="C", scalings={0.0: 1.0}, network=net)
gi.run_fault(network=net, fault_name="F")
```

DC needs a section of its own not because the solver is special there but
because it is *not*. $Y(f)\,\underline{u}(f) = \underline{i}(f)$ has no
frequency dependence of its own — $f$ enters only through the impedances — so
a DC solve is an ordinary solve and it is exact. What has to be handled at
zero frequency is the other half: what an impedance *formula* means there, and
what happens when the number it produces has no reciprocal.

!!! note "Why `f = 0` and not `f = 0.1 Hz`"

    Entering a small frequency instead of zero was the established workaround,
    and for a conductor whose reactance is written as `j*2*pi*f*L` it was
    accurate: on the reference chain of `notebooks/24_dc_studies.ipynb` (two
    10 km spans of 0.25 Ω/km earthing conductor, 1 kA injected at one end and
    the fault at the other) 0.1 Hz gives
    $456.621004575\ \text{V}$ and 0 Hz gives $456.621004566\ \text{V}$ —
    eleven digits. It was needed only because `f = 0` used to raise.

    For the far more common spelling `(0.25 + j*0.6)*l` the workaround is
    wrong, and not marginally: 0.1 Hz keeps the $6\ \Omega$ of reactance that
    does not exist at DC and reports **718.54 V instead of 456.62 V**, with
    $Z_G = 2.87\ \Omega$ instead of $1.82\ \Omega$ — 57 % high. The reactance
    is constant, so making the frequency smaller does not make the error
    smaller.

### Three singularities, told apart numerically

At $f = 0$ three quite different things can happen to a formula string, and in
floating point all three arrive as `NaN` or `inf`:

| At `f = 0` the formula … | Example | What `groundinsight` does |
|---|---|---|
| has a **removable singularity** — $0\cdot\infty$ with a finite limit | Carson's earth-return term $\omega\ln\!\big(658\sqrt{\rho/f}\,/\,\text{GMR}\big)$, whose limit is 0 because $\omega$ vanishes linearly while the logarithm diverges logarithmically | evaluates the limit and returns it, so the conductor tends to its DC resistance |
| has a **true pole** | a series capacitance, $1/(j\omega C)\to\infty$ | returns infinity, which the solver already reads as an open circuit — the correct physics |
| **genuinely fails** | a `NaN` parameter, $\sqrt{\rho}$ with $\rho<0$, $0/0$ | raises `ValueError` naming the formula, the frequency and the parameters, exactly as at any other frequency |

The limit is determined by approaching zero on the decade sequence
`1e-6, 1e-7, 1e-8 Hz` and comparing two consecutive *absolute* differences.
Measured on the reference cases: an inductance gives $d_1 = 1.41\cdot10^{-8}$,
$d_2 = 1.41\cdot10^{-9}$ — shrinking, hence convergent — against
$d_1 = 1.43\cdot10^{12}$, $d_2 = 1.43\cdot10^{13}$ for a capacitance, growing,
hence a pole. The criterion is deliberately absolute: a relative one cannot
classify a formula whose limit is zero, because there the relative change per
decade stays at 90 % forever. The tie-break is biased towards *convergent*,
because mistaking a pole for a limit yields a very large finite impedance that
behaves almost like the open circuit it should have been, whereas the opposite
mistake would silently disconnect a real earthing conductor.

### A finite reactance has no reading at DC

`(0.25 + j*0.6)*l` reports the same reactance at every frequency, zero
included — and at DC a reactance can only vanish ($j\omega L \to 0$) or be
infinite ($1/(j\omega C)\to\infty$). A finite non-zero one is a statement about
nothing. The 0 Hz bin therefore takes the real part, drops the reactance and
emits a `DCLimitWarning` that quotes $X/R$ and the remedy; all other
frequencies are untouched. Write the reactance as `j*2*pi*f*L` and nothing is
dropped and nothing warns. The warning quotes the *ratio* rather than the two
values because the ratio is length-invariant and therefore identical for every
branch sharing a `BranchType` — which lets Python's default warning filter
collapse a hundred-branch network into a single line.

The fallback applies to impedance formulas only. The `R_formula`, `L_formula`
and `C_formula` fields consumed by the state-space transient solver are real at
every frequency by contract, so a complex value there is reported as an error
rather than repaired.

### An ideal bond is a short circuit, and a short circuit has no admittance

A purely inductive element — a short bonding conductor modelled as
`j*2*pi*f*L*l`, with no resistance — is an *exact* zero at DC. That is correct
physics, and it is also a number the nodal formulation cannot invert. Up to
v0.4.0 both solvers responded by dropping such an element, i.e. by modelling a
short circuit as a disconnection. On the reference chain
($Z_A = 0.8\ \Omega$, $Z_B = 12\ \Omega$, $Z_C = 3.5\ \Omega$, bond A–B purely
inductive, $R_{BC} = 0.35\ \Omega$, 1 kA at A, fault at C) the correct answer is
$u_A = u_B = 57.07\ \text{V}$, $u_C = -266.30\ \text{V}$; what came back was
$800.00$, $-2649.84$ and $-2727.13\ \text{V}$ — wrong by factors of 14.0, 46.4
and 10.2, with the sign of bus B reversed, because bus A had been cut off from
the network and reported its own electrode instead of the parallel combination.

Such an element is now given a finite stand-in **at the 0 Hz bin only**:

$$
Z_{\text{sub}} = \sqrt{\varepsilon_{\text{mach}}}\; Z_{\min}
              = 1.4901\cdot10^{-8}\; Z_{\min},
$$

where $Z_{\min}$ is the smallest finite non-zero impedance magnitude the
network carries at that frequency. Tying the stand-in to $Z_{\min}$ was chosen
by measurement against analytically node-merged reference networks; it costs a
relative error of about $10^{-5}$ in the worst case observed, $1.6\cdot10^{-9}$
on the chain above, and $8.3\cdot10^{-8}$ at the DC bin of the corresponding
transient run — better than modelling the bond as an explicit $10^{-8}\ \Omega$
resistance. Every use is announced by a `DCLimitWarning` naming the elements,
the substitute and the reference it was scaled to.

**When to merge two buses instead.** The exact model of an ideal bond is one
node, not two, and the stand-in is an approximation of that merge. Prefer the
merge — one bus whose impedance is the parallel combination of the two
electrodes — whenever the *difference* in earth potential rise across the bond
is part of the result you report (across an ideal bond it is zero, and whatever
the stand-in produces there is numerical residue), or when the bonded buses
carry impedances many orders of magnitude away from $Z_{\min}$. For the usual
question — the earth potential rise of the bonded group and the current leaving
it — the substitution is accurate to the digits above and needs no change to
the model. Giving the bond a small but honest resistance works equally well and
removes the warning.

### Every transient study contains DC

An `rfft` frequency grid always contains a 0 Hz bin, so *every* transient run
evaluates every formula at zero frequency, whether or not the study is about
DC. There was never a `f = 0.1 Hz` workaround available on that path, which is
why a Carson-type conductor could not be used in a transient study at all and
why a purely inductive bond was silently opened in every one. Both are fixed on
the same code path as the stationary case; the state-space solver works from
the lumped R/L/C fields and never inverts an impedance, so it has no DC
inversion problem and serves as the independent cross-check.

### What a source and a fault need at DC

- **Current source.** `values` needs a `0.0` key: `values={0.0: 1000.0}`. Give
  it a real number — at zero frequency the imaginary part is not a phase shift.
- **Voltage source.** `voltage` and `source_impedance` both need a `0.0` entry,
  and the source impedance must have a real part: the Thevenin loop is closed
  by inverting it, and a purely inductive internal impedance would be zero at
  DC. A zero `source_impedance` is rejected when the source is constructed.
- **Fault.** `scalings` needs a `0.0` key; a fault that does not scale 0 Hz
  contributes nothing at DC.
- **IEC 60909 characteristics.** `i_k_a`, `r_to_x` and `kappa` describe the
  DC offset and thermal equivalent of an *alternating* short-circuit current.
  They do not enter the solve (see [Objects](#objects)) and have no meaning in
  a pure DC study; leave them unset there.
- **Mixed studies.** `frequencies=[0.0, 50.0]` is legal and the two are
  independent: the DC handling is confined to the 0 Hz bin, and a study
  carrying both returns the same 50 Hz answer, bit for bit, as one carrying
  50 Hz alone.

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
paths, and the source current has to be *divided* between them.

Since 0.6.0 that division is the default and is computed, not declared:
`run_fault(..., phase_current_mode="auto")` solves a reduced
**phase-conductor** network per source — the fault bus as reference node,
the source current injected at the source bus — and reads the branch phase
currents off that solution. The split then follows the topology and the
conductor impedances, is independent of the order the branches were declared
in, and needs no user input.

The phase impedance used for that solve comes from
`BranchType.phase_impedance_formula` (symbols `f`, `rho`, `l`, same as the
other formulas). It is optional, because in a network without cycles the
split is fixed by the topology alone and the impedance value cannot change
it. Where cycles *do* exist and the formula is missing, the solve falls back
to a proxy — `1/Z_self` for a branch with a grounding conductor, `1/length`
for one without — and says so once per solve. Those two quantities are not
comparable, so the split between routes of different construction is then a
heuristic. Declare the formula whenever the ring mixes cable and overhead
line.

`phase_current_mode="paths"` selects the pre-0.6 behaviour: every branch on
every enumerated path receives the **full** source current, scaled by that
branch's `parallel_coefficient`. This is correct for a radial network — where
both modes agree exactly — but in a meshed one it multiplies the current
instead of dividing it. At the default coefficient of `1.0` a symmetric ring
then solves to `EPR = 0 V`, `r = 0` and `Z_G = None`, because the doubled
mutual injections cancel the source exactly. The mode is kept so studies
produced before 0.6.0 stay reproducible, and for networks where the split is
known from measurement and set by hand.

The deprecated `auto_parallel_coefficients` argument still works and still
wins when passed: `True` maps to `"auto"`, `False` to `"paths"`.

Two different situations both end in "no paths", and `groundinsight` treats
them differently on purpose. **No sources or no faults at all** is rejected:
the enumeration runs over `sources × faults`, so an empty side means nothing
was ever asked, and the all-zero result that follows is an artefact of an
incomplete model rather than a statement about the system. **No path between
an existing source and an existing fault** is accepted: that is the signature
of an islanded fault bus — the normal outcome of an outage scenario — and 0 V
is then the physically correct answer.

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
I_{\text{branch}}(f) = \frac{u_{\text{to}}(f) - u_{\text{from}}(f)}
                            {Z_{\text{self}}(f)} + I_{\text{mut}}(f),
$$

where the second term accounts for the Norton source representing the
mutual coupling (`compute_branch_currents`).

Mind the orientation: the numerator is $u_{\text{to}} - u_{\text{from}}$, so a
**positive** `ResultBranch.i_s` means the shield current flows from `to_bus`
towards `from_bus` — the opposite of the sign convention used for the phase
currents, where positive is `from_bus` → `to_bus`. Earlier revisions of this
page had the numerator the other way round; the formula above is what
`compute_branch_currents` actually evaluates.

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

The same closed form applies to a fully symmetric ring with the fault
diametrically opposite the source — the Norton injections in the two
ring halves are then perfectly anti-parallel and superpose to the same
expression as the single-line case.

### Reduction factor on a current basis

The ratio above is structurally blind to the impedance *at* the fault bus. Both
solves use the same `Y`, so changing the diagonal entry `Y_ff` is a rank-1
update whose effect cancels in the quotient. Sweeping the fault-bus electrode
over four decades leaves `r` at `0.500000` while `Z_G` moves by two orders of
magnitude and the potential rise by a factor of fifty. That is not a numerical
artefact — the closed form in
[Characterising a location](#characterising-a-location-without-its-electrode)
derives it.

`ResultReductionFactor.value_current` is the **measured** definition alongside
it: the total earth-return current over the total fault current,

$$
r_I(f) = \frac{|\underline{I}_E(f)|}{|\underline{I}_F(f)|}.
$$

`I_E` is the sum of the electrode currents of **every bus that feeds the soil**
— from the fault outwards in *every* direction, a ring or a mesh having more
than one, up to where the potential profile turns — not the electrode current at
the faulted station. In a cable network with
continuous shields the stations are bonded to one another, so the current spreads
along the shields and leaks into the soil at every one of them; the earthing
current is distributed over the whole bonded system by construction. Measured on
a six-station feeder, counting only the faulted station understates `r_I` by a
factor of 1.8 to 4.7 depending on where the fault sits.

Two things make the sum less obvious than it looks.

**Summing over every bus gives exactly zero.** Whatever enters the soil somewhere
leaves it somewhere else, so Kirchhoff at the whole network forces
`Σ I_a = 0`. A group has to be selected — and "every bus except the faulted one"
is not that group: by the same law it collapses back to the electrode current of
the faulted station alone.

**The group cannot be found from the potential profile.** The crossing between
the two groups generally falls *between* two stations rather than on one, and
when the fault sits close to the infeed the profile may never come near zero at
all — it passes through a shallow minimum and rises again. On the verification
feeder with the fault one station from the infeed, `|EPR|` runs 91.4, 19.6, 18.7,
18.1, 17.7, 17.5 V: there is no crossing to find.

What *is* unambiguous per bus is the **direction** of its electrode current, so
the split is made in the complex plane: the group is the set of phasors in one
half-plane, and the half-plane is the one whose sum has the largest magnitude.
That needs no angle nominated and no tolerance tuned, and it reduces to the
obvious answer whenever the two groups are cleanly opposed. Both groups carry the
same sum with opposite signs, so `|I_E|` does not depend on which is called
which; the fault bus anchors the naming.

`ResultReductionFactor` reports `i_earth` (the ampere value) and `earth_buses`
(the group) next to the ratio, because the split is a modelling statement and
should be inspectable. `separation` — reported in the log when it degrades —
says how cleanly the phasors separated; well below one means the electrode
currents are spread in angle and a single scalar earth-return current is a
coarser description than it looks. `res_all_impedances()` carries both factors.

### Characterising a location without its electrode

Adding an electrode `Y_B` at bus `b` is a rank-one change to the nodal matrix, so
by Sherman-Morrison every nodal voltage is a **Möbius function** of it:

$$
\underline{u}(Y_B) = \underline{u}_0
    - \frac{Y_B\, \underline{z}\, u_{0,b}}{1 + Y_B Z_\text{net}}
$$

Three objects on the right, none of which needs the electrode to be known:
`u_0`, the fault solve with the electrode removed; `z`, the voltage everywhere
per ampere injected at `b`; and `Z_net = z_b`, the driving-point impedance of
everything except the local electrode — the parallel impedance the network
offers at that point.

`gi.bus_response(network, fault=..., bus=...)` builds those from two solves.
Evaluating it afterwards costs nothing, for any electrode, including the two
extremes as exact limits: `Z_B → ∞` (none installed) and `Z_B → 0` (ideal, which
the solver itself will not accept because a zero impedance cannot be inverted).
`.extremes()` returns the bracket, `.evaluate(z)` a single electrode and
`.sweep([...])` any number of them.

The driving-point impedance is exactly `Z_dp = 1/(Y_B + 1/Z_net)`, running
monotonically from `Z_net` down to zero as the electrode improves. Over all
*passive* electrodes the largest attainable magnitude is not quite at the open
end: a purely reactive `Y_B = -j·Im(1/Z_net)` cancels the network's susceptance
and gives `|Z_dp| = 1/Re(1/Z_net)`. In a cable network that is a fraction of a
percent above `|Z_net|`, but it is the honest bound and `.extremes()` reports it
as the `worst_passive` row.

The closed form also *derives* the invariance of the EPR-based reduction factor
that the sensitivity work runs into: `u_b(Y_B) = u_{0,b}/(1 + Y_B Z_net)` holds
with and without mutual coupling alike, so the factor cancels out of the
quotient exactly. It is algebra, not a numerical accident.

#### Which one is the EN 50522 reduction factor

`value_current`. The norm writes the earthing voltage as

$$
U_E = 3 I_0 \cdot Z_E \cdot r,
$$

and that chain closes in the model to machine precision, with `U_E` the
earthing voltage of the bonded group, `I_E` its summed electrode current,
`Z_E = U_E/I_E` its earthing impedance and `r = |I_E|/|3I_0|`. All three routes
to `r` — the current ratio, `U_E/(Z_E \cdot 3I_0)`, and the reported factor —
give the same number. `ResultReductionFactor` carries `u_earthing` and
`z_earthing` so the chain can be checked from the result alone.

Rearranging the norm to $U_E(r)/U_E(r{=}1) = r$ is correct, and it is worth
being precise about what the reference case is: **`r = 1` means the whole fault
current flows through `Z_E`**, so `U_E(r=1) = Z_E \cdot 3I_0`. On the
verification feeder that is 3298 V.

`value` uses a *different* reference: the mutual coupling removed, but the
cable shield still in place as a metallic return path. That reference voltage
is 214 V — fifteen times smaller, because most of the current still comes back
through the shield rather than through the soil. The ratio of the two reference
voltages is exactly the ratio of the two factors, and nothing else. Both are
correct answers; they answer different questions.

One caveat on `Z_E`: it is *not* the electrodes in parallel. The shield sections
between the bonded stations add to it — 3.30 Ω against 2.50 Ω on the
verification feeder — which is the quiet reason a hand calculation from the
electrode values alone comes out low. And where the stations counted as one
earthing system are not actually at one potential, `groundinsight` says so at
INFO: the norm's lumped picture assumes they are, and `U_E` is otherwise a
weighted average of genuinely different voltages.

#### The two factors are not two computations of one number

They coincide in exactly one case, and it is worth knowing which. For a route
with shield impedance $Z_s$, mutual impedance $Z_m$ and station electrodes
summing to $Z_E$ along the earth-return path,

$$
r_\text{coupling} = \frac{Z_s - Z_m}{Z_s},
\qquad
r_I = \frac{Z_s - Z_m}{Z_s + Z_E},
$$

so their ratio is the current divider $Z_s/(Z_s + Z_E)$ and nothing else. The
first is the **ideally bonded limit** — the tabulated property of the cable,
$1 - Z_m/Z_s$, independent of the station earths by construction. The second is
what the earthing system of *this* network actually passes into the soil.

Verified against the closed form to machine precision over five decades of
electrode impedance on a two-section line with $Z_s = 0.2 + 0.4\mathrm{j}\ \Omega$:

| electrode per station | $r_\text{coupling}$ | $r_I$ | ratio | $Z_s/(Z_s+Z_E)$ |
|---|---|---|---|---|
| 10 Ω | 0.500000 | 0.011067 | 0.022135 | 0.022135 |
| 1 Ω | 0.500000 | 0.100000 | 0.200000 | 0.200000 |
| 0.1 Ω | 0.500000 | 0.395285 | 0.790569 | 0.790569 |
| 0.01 Ω | 0.500000 | 0.489820 | 0.979639 | 0.979639 |
| 10 µΩ | 0.500000 | 0.499990 | 0.999980 | 0.999980 |

A wide gap is therefore a statement about the network, not a defect: the
stations are not bonded well enough for the cable's tabulated reduction factor
to describe what the soil sees. `groundinsight` logs the divider at INFO where
it falls below 0.5, rather than as a warning — with ordinary station electrodes
it is the normal case, and a log that warns about the normal case stops being
read.

### Splitting the network at the fault

A **cut** is a named set of branches, all incident to the fault bus. Cuts turn
the question "how much grounding does each direction contribute" into numbers:

```
parallel impedance left --- fault location --- parallel impedance right
```

`gi.analyze_cuts(network, fault=..., cuts=[...])` reports two families of
quantity per direction.

**Impedances — a property of the network, not of the fault.** One ampere is
injected at the fault bus with all sources and all mutual injections removed;
the share leaving through each cut is read off, and `Z_side = u_fault / i_cut`.
Because the shares and the local electrode current add up to the injected
ampere, the decomposition closes by construction,

$$
\frac{1}{Z_\text{driving point}} = \frac{1}{Z_\text{local}}
    + \sum_\text{sides} \frac{1}{Z_\text{side}},
$$

and the residual of that identity is reported next to the values. Being
source-free, `Z_side` does not change when the fault bus's own characteristic is
varied — which is the useful statement: the network's parallel contribution is a
fixed property, the local electrode is the variable, and the total is their
parallel combination.

Isolating each side into its own sub-network would be equivalent wherever the
sides are galvanically separate, and wrong in a ring: removing one branch of a
ring separates nothing, so the far side comes out empty and the impedance
infinite. Current division has no such blind spot.

**Currents — how the fault current actually divides.** From a solved fault:
`i_shield` per direction, and `current_share = |i_shield| / |i_inj|`. KCL at the
fault bus closes, `i_inj = i_local + Σ i_shield`, and the residual is reported.
Where the directions are disjoint — no ring — the far-side sums also give
`i_earth`, `i_total` and the side reduction factor `r = |i_earth| / |i_total|`.
Where they overlap, those three are `None` and `current_share` carries the
meaning; `sides_are_disjoint` says which case you are in.

Every cut branch has to be incident to the fault bus. A cut placed further out
still separates the network, but its far side is no longer a parallel element of
the fault location and the impedances would not sum. Incident branches that no
cut claims form the implicit side `"rest"`, so the decomposition always covers
the whole network.

#### Frequency dependence of the reduction factor

For a shielded cable the impedances split into a resistive and an
inductive part,

$$
Z_{\text{self}}(f) = R + j\,\omega L,
\qquad
Z_{\text{mutual}}(f) = j\,\omega M,
\qquad \omega = 2\pi f.
$$

With full coupling between the faulted phase and the grounding conductor
(the geometric ideal $M = L$) the closed form simplifies to

$$
r(f) \;=\; \left| 1 - \frac{j\,\omega L}{R + j\,\omega L} \right|
       \;=\; \frac{R}{\sqrt{R^{2} + (\omega L)^{2}}}.
$$

Two limits follow directly:

- $f \to 0$: $\omega L \to 0$, so $Z_{\text{mutual}}/Z_{\text{self}} \to 0$
  and $r \to 1$. At DC the shield carries no induced current, the entire
  fault current flows through the local earth path and the reduction
  factor equals 1.
- $f \to \infty$: the imaginary parts dominate, so
  $Z_{\text{mutual}}/Z_{\text{self}} \to M/L = 1$ and $r \to 0$. At high
  frequency the shield short-circuits the inductive coupling, almost the
  entire fault current returns metallically and the EPR collapses.

In a real cable the resistive part is small compared with $\omega L$
already at power frequency, which is why MV cables typically reach
$r \approx 0.3 \ldots 0.4$ at 50 Hz and the reduction factor decays
quickly above one or two hundred Hz. This convergence is exercised
explicitly by
`tests/test_topology_and_reduction.py::test_reduction_factor_sweep_*`,
which sweeps a single MV cable section and a 20-bus symmetric ring
from 50 Hz to 5 kHz and asserts both the closed form above and the
monotonic decay towards zero.

### Grounding impedance

`ResultGroundingImpedance.value` is `Z_E` in the EN 50522 sense: the earthing
voltage of the bonded earthing system divided by the current it passes into the
soil. The norm's chain `U_E = 3*I_0 * Z_E * r` therefore closes on the reported
value, with `r` the current-based reduction factor. Note that `Z_E` is not the
station electrodes in parallel — the shield sections between them add to it.


The effective grounding impedance seen at the fault bus is

$$
Z_G(f) = \frac{u_{\text{EPR}}(f)}{r(f)\,I_{\text{fault}}(f)}.
$$

It is exposed per frequency and as RMS-scalar through
`net.res_all_impedances()`.

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

`PathFinder` skips inactive elements when enumerating source-to-fault
paths, so the per-path Norton bookkeeping stays consistent. The flag
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
the EPR at the fault bus exceeds a touch-voltage limit
$u_{\max}$?"* — is answered by
`groundinsight.analysis.find_max_rho_scaling`. It log-bisects a
uniform scaling factor $c$ of `Bus.specific_earth_resistance` on a
user-selected bus set, re-evaluates each `BusType.impedance_formula`
through the existing SymPy machinery and triggers `run_fault` at
every trial value. The output is the largest *verified admissible*
$c$ — a factor for which $|U_\text{EPR}(f)|_{\text{RMS}} \le u_{\max}$
was actually measured — together with the EPR at that point and the
per-bus $\rho_{\max} = c\,\rho_0$. The original $\rho$ values are
restored via a `finally` block, so the network is unchanged after the
call. A frequency-dependent variant (`find_max_rho_f_scaling`)
extends the same idea to two-parameter rho-f curves.

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
``f(t) -> values``. See the
[transient-simulations reference](api/transient.md) for the full API.

## Worked example

The full physical model behind the equations above maps onto the
Python API in a single block:

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

gi.run_fault(network=net, fault_name="F1")
print(net.res_all_impedances())   # Z_G and r per frequency
```

The `Network` API hides every step listed in the next section behind
`run_fault`; the results are exposed as Polars DataFrames via
`net.res_buses(fault="F1")`, `net.res_branches(fault="F1")` and
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
