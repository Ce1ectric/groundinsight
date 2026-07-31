# Transient simulation

The stationary solver described in [Concepts](concepts.md) operates in the
phasor domain: one linear system per frequency, RMS / per-frequency
post-processing. Many fault studies in low- and medium-voltage grounding
analysis need a *time-domain* answer instead — for example to look at the
fault-on / fault-off transient, the asymmetric peak with a decaying DC
component, the L/C ringing on a switching edge, or the post-clearing
ring-down of a grounding network with non-zero capacitive coupling.

`groundinsight` ships two solvers for this. They share the user-facing
contract — define waveforms per source, declare observation points, get
back a `ResultTransient` with time series — but they take fundamentally
different mathematical paths. This page derives both, lists the
assumptions each one makes, and explains where one is the right choice
over the other.

```python
study = gi.TransientStudy(network, fault_name="F1")
study.set_source_waveform("src", waveforms.sinusoidal_with_dc_offset(...))
study.set_observation(buses=["b10"], branches=["L_09_10"])

result_fft = study.solve(t_end=0.2, dt=1e-4, solver="fft")
result_ss  = study.solve(t_end=0.2, dt=5e-5, solver="state_space")
```

## Problem statement

For a linear, time-invariant grounding network with a single excitation
$u(t)$ at one source and a fixed topology between the observation start
$t = 0$ and the end $t_{\text{end}}$, the response at any observation
point is the convolution

$$
y(t) \;=\; \int_{0}^{t} h(t - \tau)\, u(\tau)\, \mathrm{d}\tau
$$

where $h(t)$ is the impulse response from the source's input port to the
observation point. The two solvers are two ways of evaluating this
convolution numerically; both, when applied to the same physical model
with the same input and zero initial conditions, return the same
$y(t)$ to numerical precision.

## FFT solver

### Derivation

For an LTI system, multiplication in the frequency domain replaces
convolution in the time domain:

$$
Y(f) \;=\; H(f)\, U(f).
$$

In `groundinsight` the transfer function $H(f)$ is *not* stored as an
analytic expression; instead it is evaluated implicitly per frequency by
re-using the existing nodal solver. Pick a regular time grid

$$
t_k \;=\; k\,\Delta t,\quad k = 0,\dots,N-1,
$$

with $N$ even. The discrete real-valued FFT of the source samples is

$$
U_m \;=\; \sum_{k=0}^{N-1} u(t_k)\,e^{-2\pi\mathrm{i}\, km / N},
\qquad m = 0,\dots,\tfrac{N}{2},
$$

evaluated on the frequency grid $f_m = m / (N\,\Delta t)$ (the real-FFT
frequencies). The Nyquist limit is $f_{\max} = 1 / (2\,\Delta t)$.

For each $f_m$ the solver builds the per-frequency admittance matrix
$Y(f_m)$ from the network's `BusType.impedance_formula` and
`BranchType.self_impedance_formula` (exactly the same matrices the
stationary `run_fault` builds at $f = 50\,\mathrm{Hz}$), then solves

$$
Y(f_m)\,\underline{v}(f_m) \;=\; \underline{i}(f_m)
$$

with the right-hand side derived from the source spectrum:
$\underline{i}(f_m)$ has $+U_m$ at the source bus and $-U_m$ at the
fault bus (current-source convention; see notes below for voltage
sources). For each observation bus $b$ the resulting spectrum
$\underline{v}_b(f_m)$ is transformed back into the time domain via the
inverse real FFT:

$$
y_b(t_k) \;=\; \frac{1}{N}\sum_{m=0}^{N/2} \underline{v}_b(f_m)\,
              e^{+2\pi\mathrm{i}\,km / N}\,(\text{conjugate-symmetric reconstruction}).
$$

Branch currents are computed analogously: each branch's frequency-domain
current is $(\underline{v}_{\text{to}} - \underline{v}_{\text{from}})/Z_{\text{self}}$,
inverse-FFTed sample-by-sample.

### Implementation notes

*Compile cache.* The compiled SymPy callables for every type's impedance
formula are cached, so even at $\sim 2000$ FFT bins each unique formula
is parsed exactly once.

*Mutual coupling.* The FFT solver uses the existing
`_compute_phase_currents_from_paths` machinery to inject Norton-equivalent
mutual currents per branch in the same way the stationary solver does.

*DC bin.* The lowest FFT bin sits at $f_0 = 0$, so **every** transient run
evaluates every impedance formula at zero frequency — there is no such thing
as a transient study that avoids DC. Three cases have to be told apart there,
and all of them arrive as `NaN` or `inf` in floating point: a removable
singularity such as Carson's earth-return term (limit 0, so the conductor
tends to its DC resistance), a true pole such as a series capacitance
(infinite, i.e. the open circuit it should be) and a genuine formula error
(raises). A purely inductive element is an exact *short circuit* at DC; since
zero has no reciprocal, that one bin uses a small finite stand-in and warns
with a `DCLimitWarning`. Up to v0.4.0 such an element was dropped from the
matrix instead, i.e. a short circuit was modelled as a disconnection — see
[Direct current (`f = 0`)](concepts.md#direct-current-f-0) in the concepts
chapter for the measured error and for when to merge the two buses instead.
If $Y(f_0)$ is still singular after all this (a genuinely floating
sub-network) the bin is set to zero, which is equivalent to a DC-removed
reconstruction.

### Limitations

- **LTI and fixed topology.** The FFT solver assumes the network is the
  same at every $f_m$ that the FFT visits. Topology changes
  mid-simulation (e.g. a breaker that opens half-way through the
  trace) are not modelled.
- **Wraparound.** The DFT is implicitly periodic. If the network's
  impulse response has not decayed by $t_{\text{end}}$, the tail
  wraps around and corrupts the start of the next "period", which is
  what the user sees as the simulation start. Choose
  $t_{\text{end}} \gg \tau_{\text{slowest}}$ in practice.
- **Aliasing.** Source-waveform content above the Nyquist
  $1 / (2\,\Delta t)$ is folded back into the resolved band. Sharp
  switching edges have nominally infinite bandwidth; the FFT result
  shows Gibbs-style ringing on the rising/falling edges.
- **Voltage sources.** Voltage-mode sources are rejected by the FFT
  solver at `solve()` time with a clear pointer to the state-space
  alternative. The reason: a voltage source's loop current depends on
  the network state, which means $\underline{i}(f_m)$ is no longer a
  pure function of the source spectrum.

## State-space solver

### Modified nodal analysis

The state-space path builds an explicit ODE system from the lumped RLC
formulas on `BusType` and `BranchType`. Each active bus contributes up
to three parallel paths to remote earth:

- a resistive shunt with conductance $G_k = 1 / R_k$ (mandatory),
- an inductive shunt $L_k$ (optional, contributes one state),
- a capacitive shunt $C_k$ (optional, contributes one state).

Each grounding branch with $L_{\text{self}} > 0$ contributes one inductor
state $i_{\text{br}}$ with the convention that $i_{\text{br}} > 0$ when
current flows from the *to* bus to the *from* bus internally — matching
the project-wide branch-current convention `i_branch = (v_to - v_from)
* Y_self`.

### State-vector layout

The full state vector $\underline{x} = [\underline{x}_L;\,\underline{v}_C]$ stacks

$$
\underline{x}_L \;=\;
\begin{bmatrix}
\text{bus inductor currents } i_{L,k} \\[2pt]
\text{branch inductor currents } i_{\text{br},j} \\[2pt]
\text{voltage-source loop currents } i_{\text{loop},s}
\end{bmatrix},
\qquad
\underline{v}_C \;=\;
\begin{bmatrix} v_C \text{ at every capacitive bus} \end{bmatrix}.
$$

### Algebraic constraint

Bus voltages without a capacitive shunt are determined algebraically by
KCL. Collect the bus voltages into a single vector $\underline{v}$ and
partition it into $\underline{v}_R$ (non-capacitive buses) and
$\underline{v}_C$ (capacitive buses, which are also state variables).
Then the nodal equation reads

$$
G_a\,\underline{v} + B_L\,\underline{x}_L \;=\; \underline{u}_{\text{kcl}},
$$

where $G_a$ is the conductance matrix from all resistive elements (bus
shunts, resistive branches, and Norton-equivalent admittances of any
voltage sources with purely resistive $Z_{\text{src}}$), $B_L$ maps
inductor currents to KCL contributions, and $\underline{u}_{\text{kcl}}$
collects the source injections. Partitioned by capacitive vs.
non-capacitive buses,

$$
\begin{bmatrix} G_{RR} & G_{RC} \\ G_{CR} & G_{CC} \end{bmatrix}
\begin{bmatrix} \underline{v}_R \\ \underline{v}_C \end{bmatrix}
+
\begin{bmatrix} B_{L,R} \\ B_{L,C} \end{bmatrix}
\underline{x}_L
\;=\;
\begin{bmatrix} \underline{u}_R \\ \underline{u}_C \end{bmatrix}.
$$

The non-capacitive sub-system is purely algebraic and is solved for
$\underline{v}_R$ once, in closed form:

$$
\underline{v}_R \;=\; G_{RR}^{-1}\!\left(
\underline{u}_R - G_{RC}\,\underline{v}_C - B_{L,R}\,\underline{x}_L
\right).
$$

Substituting back into the capacitive KCL gives an ODE for
$\underline{v}_C$, and substituting the same expression into the
voltage-driven inductor equations gives an ODE for $\underline{x}_L$.

### State-space form

Let $M$ be the matrix that maps bus voltages to inductor-current
derivatives ($M$ has $1/L$ entries that pick out the relevant bus
voltages per inductor) and $N$ be the diagonal of $-R/L$ entries that
captures any series resistance in inductive branches. Define the helper
products

$$
\tilde{M} \;=\; M_R\,G_{RR}^{-1},
\qquad
\tilde{G} \;=\; G_{CR}\,G_{RR}^{-1}.
$$

The full state derivative reads

$$
\dot{\underline{x}} =
\underbrace{\begin{bmatrix}
N - \tilde{M}\,B_{L,R} & M_C - \tilde{M}\,G_{RC} \\
C^{-1}(\tilde{G}\,B_{L,R} - B_{L,C}) & C^{-1}(\tilde{G}\,G_{RC} - G_{CC})
\end{bmatrix}}_{A}
\underline{x}
+
\underbrace{\begin{bmatrix}
\tilde{M}\,B_{\text{kcl},R} + B_{\text{emf}} \\
C^{-1}(B_{\text{kcl},C} - \tilde{G}\,B_{\text{kcl},R})
\end{bmatrix}}_{B}
\underline{u},
$$

where $C^{-1}$ is the diagonal $\mathrm{diag}(1/C_k)$ over the
capacitive buses, $B_{\text{kcl}}$ encodes how source waveforms map onto
KCL injections, and $B_{\text{emf}}$ encodes the EMF input of any
voltage source (see below). Output bus voltages and branch currents are
recovered with a separate $C\,\underline{x} + D\,\underline{u}$ pair
that simply re-applies the algebraic substitution of $\underline{v}_R$.

### Voltage sources

A Thevenin source between source bus $a$ and active fault bus $b$ with
EMF $u(t)$ and internal impedance $Z_{\text{src}}(f_{\text{eval}})$ is
decomposed at the network's lowest frequency into

$$
R_{\text{src}} \;=\; \mathrm{Re}\,Z_{\text{src}},
\qquad
L_{\text{src}} \;=\; \mathrm{Im}\,Z_{\text{src}}\,/\,(2\pi f_{\text{eval}}).
$$

Two cases:

1. **$L_{\text{src}} = 0$** — the source reduces to its Norton
   equivalent: a current $u(t)/R_{\text{src}}$ injected at $a$ and
   $-u(t)/R_{\text{src}}$ at $b$, plus a loop conductance $1/R_{\text{src}}$
   added between $a$ and $b$ in $G_a$. The state vector grows by
   nothing; only $B_{\text{kcl}}$ and $G_a$ are augmented.
2. **$L_{\text{src}} > 0$** — a synthetic loop branch with series
   $R_{\text{src}}$ and $L_{\text{src}}$ is added between $a$ and $b$
   exactly like a regular grounding branch with an inductor state. The
   EMF enters the loop's KVL via $B_{\text{emf}}$, contributing
   $1/L_{\text{src}}$ at the source's input column. The user sees the
   loop dynamics — including the L/R rise time of the fault current —
   without doing any Norton math by hand.

### Mutual coupling

When `R_mutual_formula` and `M_mutual_formula` are populated on the
branch type, the shield's KVL gains two additional terms:

$$
v_{\text{to}} - v_{\text{from}} \;=\;
R_{\text{self}}\,i_s + L_{\text{self}}\,\dot{i}_s
\;+\; R_{\text{mut}}\,I_p + M\,\dot{I}_p,
$$

with $I_p$ the phase current on the source-to-fault path traversing
this branch. The $\dot{I}_p$ term is awkward because $I_p$ is a function
of the source waveform, not a state — we would have to numerically
differentiate the input. The state-space solver avoids this with the
substitution

$$
z \;=\; i_s + \frac{M}{L_{\text{self}}}\,I_p,
$$

after which

$$
\dot{z} \;=\; \frac{v_{\text{to}} - v_{\text{from}}}{L_{\text{self}}}
- \frac{R_{\text{self}}}{L_{\text{self}}}\,z
+ \underbrace{\frac{R_{\text{self}}\,M - R_{\text{mut}}\,L_{\text{self}}}{L_{\text{self}}^2}}_{K_{\text{mut}}}
\,I_p,
$$

a clean ODE in $z$ with $I_p$ entering as a regular linear input. The
phase factor $I_p / I_{\text{src}}$ per branch is derived from the
network's path topology — same algorithm as the FFT solver's mutual
Norton injection. The actually observed shield current is recovered at
output time via $i_s = z - (M/L_{\text{self}})\,I_p$.

The mutual coupling for state-space sources is currently restricted to
current sources; voltage sources are skipped with a one-time warning
because their loop current is itself a state and feeding it into the
mutual feedforward needs an additional substitution layer.

### Pi-section lumping of branch shunt capacitance

Each grounding branch with a defined `C_self_formula` is treated as a
pi-section: $C_{\text{self}}/2$ is added to each endpoint bus's
effective capacitance. Buses with non-zero effective $C$ become
capacitive states (see [Algebraic constraint](#algebraic-constraint));
for branches without `L_self` the lumping still happens but only
modifies the bus's algebraic capacitance term. The rule is purely
additive: a substation with its own grounding capacitance plus two
adjacent cables sees

$$
C_{\text{eff},k} \;=\; C_{\text{bus},k} + \tfrac{1}{2}\sum_{j \in \mathcal{E}(k)} C_{\text{self},j}.
$$

For a uniform ring topology this simplifies to
$C_{\text{eff}} = C_{\text{branch}}$ at every bus.

### Time integration

`scipy.signal.lsim` is used for the actual ODE integration. For an LTI
system on a regular time grid it discretises the state-space form via
the matrix exponential

$$
\Phi \;=\; \exp(A\,\Delta t),
\qquad
\Gamma \;=\; \int_0^{\Delta t} \exp\!\bigl(A(\Delta t - \tau)\bigr)\,B\,\mathrm{d}\tau,
$$

and steps through the discrete-time system

$$
\underline{x}_{k+1} \;=\; \Phi\,\underline{x}_k + \Gamma\,\underline{u}_k
$$

with first-order hold on the input. Both $\Phi$ and $\Gamma$ are
computed *once* (per integration step size $\Delta t$); each subsequent
sample is just a matrix-vector product. There is no Runge-Kutta
function evaluation, no adaptive step control, no event detection — for
the LTI case this is a strict win over a generic ODE solver.

### Limitations

- **LTI.** The current implementation cannot handle nonlinear elements
  (saturable inductors, surge arresters, arc models). Adding a
  nonlinear path requires switching to `scipy.integrate.solve_ivp` with
  an implicit method.
- **Fixed topology.** Switching events are modelled via the source
  waveform window (`t_on`, `t_off` of the waveform factories), not via
  topology changes mid-integration. A real breaker that opens at a
  zero crossing, or an arc that re-strikes, would need event detection
  and state continuation between segments.
- **Voltage-source mutual coupling.** Mutual coupling is evaluated only
  for current sources. The phase current of a voltage source is itself
  a state and is not yet woven into the mutual feedforward.

## When to choose which solver

The two solvers, applied to the *same* physical network with the *same*
input and zero initial conditions, return the same time series to
within numerical precision. The relevant differences are practical, not
mathematical:

| Concern | FFT | State-space |
| --- | --- | --- |
| Source types | current only | current + voltage (Thevenin) |
| Mutual coupling | current sources only | current sources only |
| Bus capacitance | via `impedance_formula` | via `C_formula` and pi-lumping |
| Topology change mid-sim | not supported | not supported (yet) |
| Initial conditions | always zero | always zero (extension point) |
| Speed (LTI) | very fast for short traces | very fast via matrix exponential |
| Speed (long traces) | grows with sample count | grows linearly per sample, $\Phi$ reused |
| Future nonlinear elements | impossible | natural via `solve_ivp` |

Use the FFT solver when:

- the network is strictly LTI with a fixed topology,
- the source is a current source (any waveform, including a switched
  sinusoid with DC offset),
- you want a fast cross-check against the stationary solver — both use
  the same per-frequency admittance matrices.

Use the state-space solver when:

- the source is a Thevenin voltage source and you want to see the
  loop dynamics including $L_{\text{src}}$,
- you need to look at clean post-clearing ring-down behaviour
  (FFT can have wraparound artifacts in this region if $t_{\text{end}}$
  is short),
- you plan to extend the model with nonlinear elements, custom event
  handling, or non-zero initial conditions in a future iteration.

## Why the state-space integration is fast

A common surprise from users with a Matlab/Simulink background is that
the state-space solver runs at roughly the same speed as the FFT
solver, even for a 20-bus ring with mutual coupling and pi-lumped
capacitances. Several factors combine:

1. **Closed-form LTI integration.** `lsim` discretises the LTI system
   once per $\Delta t$ via the matrix exponential. The inner loop is
   a pair of matrix-vector products, not the 4–7 function evaluations
   of a Runge-Kutta step plus adaptive step control.
2. **One-shot algebraic elimination.** The Schur complement that
   eliminates $\underline{v}_R$ runs once at matrix-build time. A
   generic DAE solver iterates the algebraic sub-system at every
   integration step.
3. **Small dense matrices on BLAS/LAPACK.** A 20-bus ring with mutual
   coupling has on the order of 50 states; the resulting $50 \times 50$
   matrices fit in CPU cache and run at SIMD-vectorised speed. Block
   diagram engines that route signals between hundreds of separate
   blocks pay overhead for every connection.
4. **Pure linear network.** No nonlinear element forces a Newton
   iteration per step; no event detection forces step refinement.

The combination is essentially a hand-tuned LTI integrator without any
of the bells and whistles a general-purpose simulation engine has to
provide. As soon as nonlinear elements, real switching events, or
adaptive step control are needed the speed advantage closes — at that
point the path forward is `scipy.integrate.solve_ivp` with an implicit
method, which is an extension point of the current implementation.
