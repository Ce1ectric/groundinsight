# Impedance formulas and where they come from

Every impedance in a `groundinsight` network is a formula string on a type:
the electrode of a bus (`BusType.impedance_formula`), the shield–earth loop
and the phase–shield coupling of a branch (`BranchType.self_impedance_formula`,
`mutual_impedance_formula`) and the phase conductor
(`phase_impedance_formula`). This page explains what a formula means to the
model, which values it may produce, how it is evaluated and where good
formulas come from. The exact syntax — every symbol, function and limit — is
in the reference [Formula syntax](../reference/formula-syntax.md).

## Impedance formulas

Formula strings may contain the symbols

| Symbol | Meaning                           |
|--------|-----------------------------------|
| `rho`  | specific earth resistance $\rho_E$ in $\Omega\,\text{m}$ |
| `f`    | frequency $f$ in Hz               |
| `l`    | branch length $l$ in km           |
| `j`    | imaginary unit (internally `1j`)  |

plus `I` as a second spelling of the imaginary unit, `pi`, and a fixed list
of mathematical functions ([Formula syntax](../reference/formula-syntax.md#symbols)).

Expressions are evaluated symbolically, so any analytic expression supported
by SymPy is admissible. The literal string `"nan"` maps to an effectively
infinite impedance and can be used to model open ends (broken shields,
isolators etc.).

### Which values a formula may produce

Every impedance that ends up on the diagonal of $Y(f)$ is inverted, so not
every number a formula can produce is a physically meaningful model. Four
results are rejected with a `ValueError` that names the element, the
frequency and the formula:

| Result | Why it is rejected |
|--------|--------------------|
| $\underline{Z} = 0$ exactly | An element with no impedance has no admittance either. It drops out of the nodal system entirely and therefore reports the *opposite* of the ideal-earth limit: full earth potential rise and no current into the soil, indistinguishable from a bus with no electrode at all. |
| $0 < \lvert\underline{Z}\rvert \lesssim 5.6\cdot10^{-309}\ \Omega$ | Too small to invert in double precision. $1/\underline{Z}$ overflows, reaches the admittance matrix as infinity, and the bus current comes back as `NaN`. |
| $\operatorname{Re}(\underline{Z}) < 0$ | An earth electrode, an earthing conductor and a cable screen are passive; a negative resistance generates energy. It also pushes $Y(f)$ towards singularity, where the earth potential rise grows without bound while every intermediate number still looks plausible. |
| $\operatorname{Re}(\underline{Z}) = -\infty$ | The limit of a negative resistance, typically `log(f)` at 0 Hz. It is not an open circuit, but $1/(-\infty) = 0$ would read as one and disconnect the element without a word. |

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
- Positive infinity stays legal everywhere: it is the documented open-end
  sentinel, and $1/\infty = 0$ is the right answer. So does a reactance of
  $\pm j\infty$, a capacitance at DC. `NaN` is reported by its own handler.

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
[Direct current (`f = 0`)](dc.md). The negative real part
stays rejected at 0 Hz as well, and so does a real part of $-\infty$; a
passive element is passive at DC too.

## How a formula is evaluated

A formula is evaluated when an element joins a network (`create_bus`,
`create_branch`, `Network.add_*`) and again whenever an input it depends on
changes — the soil resistivity, the type, the length of the element, or the
network frequencies. The solve itself reads the stored impedance tables and
evaluates nothing. Each evaluation substitutes the element's own `rho` and,
for a branch, its `l`, and returns one complex value per network frequency.

Before a string reaches SymPy it passes the formula guard
(`groundinsight.utils.validations.assert_safe_formula`). SymPy then parses it
and `lambdify` turns it into a NumPy function, which is evaluated for all
frequencies at once. The compiled function is cached under the formula string,
so the hundred buses of one `BusType` pay the SymPy cost once.

Zero frequency needs more than substitution: Carson's earth-return term is
$0\cdot\infty$ there, a constant reactance has no meaning, a pure inductance
is a short circuit. How each case is told apart and what the model does with
it is the subject of [Direct current (`f = 0`)](dc.md).

## Where the formulas come from

The formulas are model input, and the package checks them only for syntax,
symbols and passivity. A formula that is passive but describes the wrong
conductor produces plausible, wrong numbers. Three sources are common:

- **Data sheets and line theory.** The shield–earth loop and the coupling of a
  cable or an overhead line follow from the conductor data and Carson's
  earth-return term with the equivalent depth $D_E = 658\sqrt{\rho/f}$ m.
  [Bus and branch types from data sheets](../howto/types-from-datasheets.md)
  derives both for a real 20 kV cable, checks the closed form against the
  earth-return integral, and shows the phase impedance a ring needs.
- **Measurements.** An electrode impedance measured at a station, at one or
  several frequencies, can be written as a constant or fitted as a function of
  `f` — check the fit at the edges of the range it will be evaluated on.
- **Electrode formulas.** Rod, ring and mesh electrodes have textbook formulas
  in `rho`; the data-sheet how-to lists them with their sources.

The formulas in the tutorials and examples, such as
`rho * 0.01 + j * f * 1/50 * 0.1` for an electrode or
`(0.25 + j * f * 0.012) * l` for a cable, are round illustrative numbers that
keep the examples short; they are marked as such where they appear.

## Why a formula is checked before it is parsed

`sympy.sympify` evaluates Python, and formula strings travel with the model:
in JSON project files and SQLite databases that are exchanged between people.
A formula from a file of unknown origin could otherwise run code on the
machine that opens it. The guard admits only numbers, the documented symbols,
the listed functions and arithmetic, and bounds the size of what it admits;
[Formula syntax](../reference/formula-syntax.md#why-the-guard-exists) has the
details and the limits.
