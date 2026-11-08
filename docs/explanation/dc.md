# Direct current (`f = 0`)

`f = 0` is an ordinary entry in `Network.frequencies` and in `Fault.scalings`,
and a direct-current study needs nothing beyond it. The reference chain of
this page ([`dc_chain.py`](../snippets/dc_chain.py)): three earthing systems
`A – B – C` (0.8, 12 and 3.5 Ω) joined by two 10 km spans of an earthing
conductor of 0.25 Ω/km, with 1 kA of direct current entering at `A` and
leaving at `C`:

```python
--8<-- "docs/snippets/dc_chain.py:build"
```

The earth potential rise at `A` is 456.621004566 V and the earthing impedance
at the fault bus `C` is 2.073630 Ω; a nodal solve of the three electrodes and
two 2.5 Ω conductors by hand gives the same numbers.

DC needs a section of its own not because the solver is special there but
because it is *not*. $Y(f)\,\underline{u}(f) = \underline{i}(f)$ has no
frequency dependence of its own — $f$ enters only through the impedances — so
a DC solve is an ordinary solve and it is exact. What has to be handled at
zero frequency is the other half: what an impedance *formula* means there, and
what happens when the number it produces has no reciprocal.

!!! note "Why `f = 0` and not `f = 0.1 Hz`"

    Entering a small frequency instead of zero was the established workaround,
    and for a conductor whose reactance is written as `j*2*pi*f*L` it was
    accurate: on the reference chain above 0.1 Hz gives
    $456.621004575\ \text{V}$ and 0 Hz gives $456.621004566\ \text{V}$ —
    eleven digits. It was needed only because `f = 0` used to raise.

    For the far more common spelling `(0.25 + j*0.6)*l` the workaround is
    wrong, and not marginally: 0.1 Hz keeps the $6\ \Omega$ of reactance that
    does not exist at DC and reports **718.54 V instead of 456.62 V** — 57 %
    high — with an earthing impedance $Z_E = 2.90\ \Omega$ instead of
    $2.07\ \Omega$. The reactance is constant, so making the frequency smaller
    does not make the error smaller.

    ```python
    --8<-- "docs/snippets/dc_chain.py:workaround"
    ```

    | Spelling | f | \|EPR(A)\| | \|Z<sub>E</sub>\| |
    |---|---|---|---|
    | `j*2*pi*f*L` | 0.1 Hz | 456.621005 V | 2.073630 Ω |
    | `j*2*pi*f*L` | 0 Hz | 456.621005 V | 2.073630 Ω |
    | constant `j*X` | 0.1 Hz | 718.537269 V | 2.902855 Ω |
    | constant `j*X` | 0 Hz | 456.621005 V | 2.073630 Ω |

## Three singularities, told apart numerically

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

A limit found this way is also checked for *resolution*: if the real part
still moves over the last decade, the value at $10^{-8}$ Hz is only an
approximation and a `DCLimitWarning` says so ("converges only slowly"). A move
that is small against the value passes, and so does one that shrinks at least
five-fold from one decade to the next — the only test that can judge a limit
of zero, such as the earth-return resistance $\pi^2 10^{-4} f$ of a Carson
mutual impedance: its change per decade is 90 % of its value, but that change
shrinks ten-fold from one decade to the next. `sqrt(f)` (3.2-fold) and
`log(f)` (not at all) are reported, and since the ratio does not depend on the
length, all branches of one type get the same verdict.

A real part that runs to $-\infty$ — a `log(f)` term without a factor $f$ —
is none of the three. It is the limit of negative resistances, not the open
circuit $+\infty$ of a pole, and it is refused for every impedance that is
inverted, like a negative real part. Earlier releases accepted it and read it
as an open circuit, because $1/(-\infty) = 0$.

## A finite reactance has no reading at DC

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

## An ideal bond is a short circuit, and a short circuit has no admittance

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

**Phase conductors follow the same rule.** A `phase_impedance_formula` without
a resistive part is an ideal short at 0 Hz as well. When the phase network is
solved (`phase_current_mode="auto"`), such a conductor receives the same
stand-in, announced by the same `DCLimitWarning`; $Z_{\min}$ stays the smallest
impedance of the earthing network, so the earthing results of a network with a
DC short do not move because its phase conductors are declared. Above 0 Hz a
phase impedance that is zero, too small to invert or has a negative real part
is refused with a `ValueError` naming the branch, as for every other element.
One consequence is worth knowing: where *all* parallel routes of the phase
network are ideal shorts at DC, the model has no DC split of its own, and every
conductor carrying the same stand-in splits the current as if all of them had
one and the same impedance, whatever their lengths. Declare the DC resistance
in `phase_impedance_formula` (e.g. `R*l + j*2*pi*f*L*l`) when the DC split
between routes matters.

## Every transient study contains DC

An `rfft` frequency grid always contains a 0 Hz bin, so *every* transient run
evaluates every formula at zero frequency, whether or not the study is about
DC. There was never a `f = 0.1 Hz` workaround available on that path, which is
why a Carson-type conductor could not be used in a transient study at all and
why a purely inductive bond was silently opened in every one. Both are fixed on
the same code path as the stationary case; the state-space solver works from
the lumped R/L/C fields and never inverts an impedance, so it has no DC
inversion problem and serves as the independent cross-check.

## What a source and a fault need at DC

- **Current source.** `values` needs a `0.0` key: `values={0.0: 1000.0}`. Give
  it a real number — at zero frequency the imaginary part is not a phase shift.
- **Voltage source.** `voltage` and `source_impedance` both need a `0.0` entry,
  and the source impedance must have a real part: the Thevenin loop is closed
  by inverting it, and a purely inductive internal impedance would be zero at
  DC. A zero `source_impedance` is rejected when the source is constructed.
- **Fault.** `scalings` does not need a `0.0` key: a frequency without a key
  is scaled with 1.0, i.e. the sources contribute in full at DC. Give
  `0.0: 0.0` to exclude the DC bin from a fault. A mapping that names some
  network frequencies but not others is announced once per run
  (`InputWarning`, code `SCALINGS_INCOMPLETE`); `scalings` may also be left
  out entirely, which means 1.0 everywhere.
- **IEC 60909 characteristics.** `i_k_a`, `r_to_x` and `kappa` describe the
  DC offset and thermal equivalent of an *alternating* short-circuit current.
  They do not enter the solve (see [Objects](theory.md#objects)) and have no
  meaning in a pure DC study; leave them unset there.
- **Mixed studies.** `frequencies=[0.0, 50.0]` is legal and the two are
  independent: the DC handling is confined to the 0 Hz bin, and a study
  carrying both returns the same 50 Hz answer, bit for bit, as one carrying
  50 Hz alone.
