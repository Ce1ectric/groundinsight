# Formula syntax

Bus and branch types describe their impedances with formula strings. A
formula is evaluated once per network frequency and per element, with the
element's own soil resistivity and, for a branch, its length. This page lists
what a formula may contain, the limits the formula guard enforces, why that
guard exists, and the messages you get when a formula is refused.

How to *derive* a formula from a cable or electrode data sheet is the subject
of the how-to [Bus and branch types from data sheets](../howto/types-from-datasheets.md).

## Where formulas appear

| Type | Field | Unit of the result | Used by |
|---|---|---|---|
| `BusType` | `impedance_formula` | Ω | the frequency-domain solve |
| `BusType` | `R_formula`, `L_formula`, `C_formula` | Ω, H, F | the transient solvers only |
| `BranchType` | `self_impedance_formula` | Ω per branch | the solve (shield–earth loop `Z_s`) |
| `BranchType` | `mutual_impedance_formula` | Ω per branch | the solve (phase–shield coupling `Z_m`) |
| `BranchType` | `phase_impedance_formula` | Ω per branch | the phase-current split in rings and meshes (`Z_red`) |
| `BranchType` | `R_self_formula`, `L_self_formula`, `C_self_formula`, `R_mutual_formula`, `M_mutual_formula` | Ω, H, F | the transient solvers only |

A branch formula gives the impedance of the **whole branch**: write a per-km
expression and multiply it by `l`.

## Symbols

| Symbol | Meaning | Unit | Available in |
|---|---|---|---|
| `f` | frequency | Hz | bus and branch formulas |
| `rho` | specific earth resistance ρ of the element (`specific_earth_resistance` of the bus or branch) | Ω·m | bus and branch formulas |
| `l` | branch length (`Branch.length`) | km | only in branch formulas |
| `j`, `I` | imaginary unit | – | bus and branch formulas |
| `pi` | π | – | bus and branch formulas |
| `E` | Euler's number e | – | bus and branch formulas |

Any other name is rejected when the type is created. Names are
case-sensitive, and the message suggests the case-insensitive match when there
is one (`L` → `l`, `Rho` → `rho`). A bus has no length, so `l` in a bus
formula is rejected as well, with the hint that it is defined only in branch
formulas. Constants such as a screen radius are therefore written as numbers
into the formula; the how-to on data sheets builds those strings with a few
lines of Python.

## Numbers and operators

- Numbers are decimal, with an optional exponent: `0.25`, `658`, `1.5e-3`.
  Hexadecimal, octal and binary literals are refused. Write very small or very
  large factors with an exponent part, `1e-15` rather than `10**-15`.
- The operators are `+`, `-`, `*`, `/`, `**` and parentheses, plus the comma
  between the arguments of a call. Nothing else is accepted: no attribute
  access (`.`), indexing (`[]`), comparison, assignment, `;`, `@`, lambda or
  string literal.
- Precedence is Python's: `**` binds tighter than a leading minus, so
  `-f**2` is −(f²).

## Functions

A name followed by `(` must be one of these 28 functions. Every one of them is
a SymPy function that is translated to NumPy for the evaluation.

| Group | Functions |
|---|---|
| roots, exponential, logarithm | `sqrt`, `exp`, `log`, `ln` — `log(x)` is the natural logarithm, `log(x, b)` the logarithm to base `b`, `ln` is the same as `log` |
| complex numbers | `Abs`, `abs` (magnitude), `re`, `im`, `arg`, `conjugate`, `sign` |
| trigonometric | `sin`, `cos`, `tan`, `asin`, `acos`, `atan`, `atan2` |
| hyperbolic | `sinh`, `cosh`, `tanh`, `asinh`, `acosh`, `atanh` |
| piecewise | `Min`, `Max` (at most three arguments each), `floor`, `ceiling` |

A formula is parsed by SymPy (`sympify`), compiled with `lambdify` and
evaluated for all network frequencies at once. A result may be complex. Where
the evaluation on the real axis returns NaN — the square root or logarithm of
a negative number — it is repeated on the complex plane, so
`sqrt(1 - (f/40)**2)` at 50 Hz is `0.75j`, as SymPy defines it.

```python
import groundinsight as gi

rod = gi.BusType(
    name="rod 3 m", system_type="Station", voltage_level=20.0,
    impedance_formula="rho/(2*pi*3.0)*(log(4*3.0/0.01) - 1)",
)
cable = gi.BranchType(
    name="illustrative cable", grounding_conductor=True,
    self_impedance_formula="(0.25 + j*2*pi*f*0.4e-3)*l",
    mutual_impedance_formula="(j*2*pi*f*0.35e-3)*l",
)
net = gi.create_network(name="syntax", frequencies=[50.0, 250.0])
bus = gi.create_bus(name="A", type=rod, specific_earth_resistance=100.0, network=net)
print(bus.impedance[50.0])  # 32.31 Ohm, the same at 250 Hz: the formula has no f
```

## The open end: `"nan"`

A formula string that is exactly `nan` — any case, surrounding whitespace
allowed — is not evaluated: it stands for an infinite impedance at every
frequency. Use it for an open end, for example the self impedance of a broken
shield. A formula that merely contains the three letters (`resonance`) is an
ordinary formula.

## Direct current (`f = 0`)

`0.0` is an ordinary network frequency. Several common formulas cannot be
evaluated *at* 0 Hz as written — Carson's `f*log(658*sqrt(rho/f)/r)` is
0·∞ there — and the evaluator decides by approaching 0 Hz on
`1e-6, 1e-7, 1e-8 Hz`:

- a finite limit (a removable singularity) is used as the value; a conductor
  then tends to its DC resistance;
- a growing sequence (a pole, `1/(j*2*pi*f*C)`) is an open circuit;
- a finite reactance that survives at 0 Hz (`0.25 + j*0.6`) has no physical
  reading; the real part is used and a `DCLimitWarning` names the formula;
- a limit that is still moving over the last decade is used and announced by a
  `DCLimitWarning` ("converges only slowly") — unless the step shrinks at
  least five-fold per decade, which counts as converged however small the
  value is: anything linear in `f` shrinks it ten-fold, Carson's
  `pi**2*1e-4*f` included, while `sqrt(f)` (3.2-fold) and `log(f)` (not at
  all) are reported;
- a real part of −∞ — `log(f)` with no factor `f` in front — is refused for a
  bus, a grounding conductor and a source impedance: it is the limit of a
  negative resistance, not an open circuit. A study that does not list 0 Hz
  never evaluates it there; a transient study always does.

The how-to on data sheets shows what this means for the Carson formulas
([At 0 Hz](../howto/types-from-datasheets.md#at-0-hz)); the full treatment is
in [Direct current (`f = 0`)](../explanation/dc.md).

## Why the guard exists

Formula strings reach `sympy.sympify` and `sympy.lambdify`, and both evaluate
Python. Formulas also travel inside files: a JSON project file, a SQLite
database, the import defaults of a colleague. Without a check, opening such a
file could run arbitrary code, or stall the machine with a short string such as
`9**9999999`, which SymPy would expand into millions of digits while it
parses. The guard therefore checks every formula before SymPy sees it, with
Python's own tokenizer: names, calls and operators against the whitelists
above, and the size of every number the formula can build against the limits
below. A string the tokenizer cannot split is refused rather than passed on.
Types are validated wherever they are created, so the same check runs when a
network is loaded from JSON or from a database.

## Resource limits

| Limit | Value | Why |
|---|---|---|
| characters in a formula string | 4096 | an impedance formula is a handful of terms |
| digits of a number literal | 100 (decimal notation only) | SymPy computes with such a number exactly |
| exponent of a power | 12 (written out or computed; a power in the exponent is refused) | the exponent multiplies the size of an exact result |
| digits of an exact intermediate number | 10 000 | bounds nested powers and products of exact numbers |
| digits of the integer built by `floor`/`ceiling` | 100 | SymPy evaluates the argument to every digit first |
| argument of `exp`, `sinh`, `cosh` | 10<sup>100</sup> in magnitude | the evaluation cost grows with the argument |
| terms (numbers, names, operations and calls) | 512 | SymPy simplifies the whole expression while parsing |
| function calls | 8 | SymPy simplifies every call while parsing |
| arguments of one call | 3 | `Max` and `Min` compare their arguments pairwise |

The longest formula in this documentation, the phase impedance of a
three-phase cable system in the how-to on data sheets, has 213 characters,
99 terms, 6 calls and exponent 2.

## Common errors

Mistakes caught when the type is created (the message excerpt is what
`BranchType(..., self_impedance_formula=<formula>)` reports):

| Formula | Message (excerpt) | Remedy |
|---|---|---|
| `rho*L` | `uses the unknown symbol 'L' (did you mean 'l'?)` | names are case-sensitive |
| `x*l` | `uses the unknown symbol 'x'` | write the value of `x` as a number |
| `10**-15*f` | `uses the exponent -15 -- above the limit of 12` | write `1e-15*f` |
| `f**f**2` | `contains a nested exponentiation` | no power in an exponent |
| `eval(rho)` | `calls 'eval', which is not an allowed function` | only the 28 functions above |
| `rho.real` | `contains a disallowed operator '.'` | write `re(rho)` |
| `f == 50` | `contains a disallowed operator '=='` | use `Min`/`Max`/`sign` for case distinctions |
| `'1'*l` | `must not contain string literals` | write the number without quotes |
| `0x10*l` | `hexadecimal, octal or binary literal` | write `16*l` |
| `Max(f, rho, l, 1)*l` | `calls Max() with 4 arguments, above the limit of 3` | nest: `Max(Max(f, rho), Max(l, 1))` |
| `exp(1e101)*l` | `evaluates exp() at an argument of up to about 1e101` | rescale the argument |
| `sqrt(rho*l` | `could not be tokenized` | close the parenthesis |
| `lambda: 1` | `contains a disallowed name 'lambda'` | formulas are expressions |

Values that are refused when an element is evaluated, here as the
`impedance_formula` of a bus in a 50 Hz network with ρ = 100 Ω·m:

| Bus formula | Message (excerpt) | Remedy |
|---|---|---|
| `0` | `It is exactly zero at 50 Hz` | an ideal earth is a small finite value, e.g. `1e-6` |
| `0.05*rho - 10` | `Its real part is negative` | the fit is used outside its range; check the lowest ρ |

A formula that evaluates to NaN at some frequency — `0/0`, `0*log(0)` away
from 0 Hz, a NaN parameter — is refused with a message that names the
formula, the frequency and the parameters. `log(0)` itself is −∞, and a real
part of −∞ is refused as well ([at 0 Hz](#direct-current-f-0)). Check a new
formula at the lowest ρ, the lowest frequency and the shortest length of the
model.

## See also

- [Conventions and units](conventions.md) — units of `f`, `rho`, `l`, RMS phasors.
- [Glossary](glossary.md) — the symbols `Z_s`, `Z_m`, `Z_red`, `Z_B`.
- API: [`BusType`, `BranchType` and `check_formula_symbols`](../api/core_models.md).
