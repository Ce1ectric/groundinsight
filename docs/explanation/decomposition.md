# Splitting the network, location response

Two analyses sit on top of a fault solve and answer questions about one
location rather than about one fault: what the rest of the network contributes
at the fault bus, direction by direction (`gi.analyze_cuts`), and how the
network responds to *any* electrode at a bus (`gi.bus_response`). Both rest
on the same nodal model as the solve, described in
[Model and assumptions](theory.md).

## Characterising a location without its electrode

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

The driving-point impedance is exactly `Z_dp = 1/(Y_B + 1/Z_net)`. Its magnitude
falls monotonically from `|Z_net|` to zero as the electrode improves only for an
electrode whose admittance lies within 90° of `1/Z_net`: along
`Y_B = t·e^{jφ}`, `|Z_dp|` decreases for every `t` if and only if
`cos(φ − arg(1/Z_net)) ≥ 0`. A resistive electrode (real `Y_B ≥ 0`) always
qualifies, since `Re(1/Z_net) ≥ 0` in a passive network. A reactive electrode of
the opposite sign to the network's susceptance first *raises* `|Z_dp|` above
`|Z_net|`; the largest magnitude over all passive electrodes is reached at
`Y_B = -j·Im(1/Z_net)`, which cancels that susceptance and gives
`|Z_dp| = 1/Re(1/Z_net) = |Z_net|/cos(arg Z_net)`. That is 0.08 % above
`|Z_net|` where the network seen from the bus is nearly resistive
(`arg Z_net` = 2.2°), but 57 % at the far end of a four-bus cable chain
(`arg Z_net` = 50°). `.extremes()` therefore reports it as the `worst_passive`
row instead of assuming it away.

The closed form also *derives* the invariance of the EPR-based reduction factor
that the sensitivity work runs into: `u_b(Y_B) = u_{0,b}/(1 + Y_B Z_net)` holds
with and without mutual coupling alike, so the factor cancels out of the
quotient exactly. It is algebra, not a numerical accident. The current-based
factor has no such cancellation and responds to the electrode — see
[Reduction factors and earthing impedance](reduction-factors.md#reduction-factor-on-a-current-basis).

## Splitting the network at the fault

A **cut** is a named set of branches, all incident to the fault bus. Cuts turn
the question "how much grounding does each direction contribute" into numbers:

```text
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

In a network without voltage sources the driving-point impedance that the cuts
decompose is the earthing impedance $Z_E$ of the fault bus, the number
[Reduction factors and earthing impedance](reduction-factors.md#grounding-impedance)
defines.

## See also

- [Location response bracket](../examples/bus_response_extremes.ipynb) — the
  response of a station to every electrode from two solves, checked against
  solved networks, and what the bracket of the extremes does not cover.
- [Electrode at the fault location](../examples/cuts_and_rho_f_sweep.ipynb) —
  the cuts on a feeder with harmonics against the closed forms of the ladder,
  a catalogue of electrodes in the $\rho$–$f$ form, and a closed ring.
