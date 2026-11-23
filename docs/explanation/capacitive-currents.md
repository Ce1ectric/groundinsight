# Capacitive currents and the reduction factor

In an earth fault of a system with an isolated or resonant-earthed neutral the
fault is not fed from one place. The phase-to-earth capacitances of every cable
and line of the system are charged, a coil adds its current at the substation,
and all of these currents close through the fault. This page derives how
`groundinsight` models that, and what it means for the reduction factor.

The main results:

1. The charging current of a branch is exactly equivalent to **half of it taken
   at each end of the branch** — as a current that the phase conductors carry
   to the fault, not as an injection into the earthing nodes alone
   ([section 4](#charging-current)).
2. Every bus at which current is taken has **its own reduction factor**
   $r_S$, and the earth current of the fault is $I_E = \sum_S r_S\,I_S$
   ([section 3](#superposition)).
3. The factor of the fault, $r = \lvert I_E\rvert / \lvert I_F\rvert$, stays
   below the largest $\lvert r_S\rvert$ as long as all currents have the same
   phase. **In a resonant-earthed system it exceeds 1**, without bound as the
   coil is tuned, because coil current and charging currents cancel in the
   fault current but return to different buses
   ([section 5](#above-one)).

!!! note "Status of the third result"
    That $r > 1$ follows from the model on this page. We have not found it
    described in the earthing literature. The derivation is therefore given
    step by step, once with the lumped model and once from the distributed
    one, and [section 10](#checks) lists the independent checks behind every
    formula. Treat it as a result to be examined, not as established practice.

Throughout, phasors refer to the voltage that drives the fault: a capacitive
current is $+j$ times its magnitude, a coil current $-j$ times, a current in
phase with the voltage is real.

## 1 The earthing impedance and the reduction factor { #definitions }

$Z_E$ is what a measurement at a station sees. A test current $I$ is fed into
the earthing of the station and returns through a **remote auxiliary
electrode**; the lead to it is laid at 90° to cables and lines, so that it
induces nothing in them. Then

$$
Z_E = \frac{U_E}{I} \qquad\text{(measurement)} .
$$

Every electrode and every screen or earth wire bonded to the station is part of
$Z_E$. It is the driving-point impedance of the earthing network at that bus,
and it is what `groundinsight` reports as `Z_E_Ohm`
([Reduction factors and earthing impedance](reduction-factors.md)).

A fault sends a current into the same station. The reduction factor says what
is different from the measurement:

$$
r = \frac{U_E}{Z_E\,I} .
$$

For the measuring arrangement $r = 1$ by definition. There is no reference to a
current of a standard in this definition; $I$ is the current that flows.

## 2 One feeding bus: two stations and one cable { #two-stations }

Station A feeds, station B has the fault. A has the earth electrode $Z_A$, B has
$Z_B$. One cable joins them; its screen is bonded to both stations and
insulated from the soil in between. $Z_s$ is the impedance of the loop
screen–earth, $Z_m$ the mutual impedance between the phase conductor and that
loop, both for the whole length.

A current $I$ is taken from the earthing of A, flows through the phase conductor
to B and enters the earthing of B through the fault.

**Step 1 — unknowns.** $u_A$, $u_B$: potentials of the two stations against
remote earth. $I_s$: screen current, counted from B to A.

**Step 2 — node B.** The fault brings $I$; it leaves through the electrode and
the screen:

$$
I = \frac{u_B}{Z_B} + I_s . \tag{1}
$$

**Step 3 — node A.** The screen brings $I_s$; the source takes $I$; the rest
leaves through the electrode:

$$
I_s = I + \frac{u_A}{Z_A} . \tag{2}
$$

**Step 4 — the screen loop.** Along the screen from B to A and back through the
earth the voltage is $Z_s I_s$, reduced by what the phase conductor induces.
The phase current flows from A to B, opposite to the counting direction of
$I_s$, so its induced voltage drives the screen current:

$$
u_B - u_A = Z_s\,I_s - Z_m\,I . \tag{3}
$$

**Step 5 — solve.** From (1) and (2), $u_B = Z_B (I - I_s)$ and
$u_A = -Z_A (I - I_s)$. In (3):

$$
(Z_A + Z_B)(I - I_s) = Z_s I_s - Z_m I
\quad\Longrightarrow\quad
I_s = I\,\frac{Z_A + Z_B + Z_m}{Z_s + Z_A + Z_B} .
$$

The current through the electrode of B and its potential are

$$
I - I_s = I\,\frac{Z_s - Z_m}{Z_s + Z_A + Z_B}, \qquad
U_E = u_B = I\,\frac{Z_B\,(Z_s - Z_m)}{Z_s + Z_A + Z_B} . \tag{4}
$$

**Step 6 — the measurement at B.** The test current sees $Z_B$ in parallel with
the screen in series with $Z_A$:

$$
Z_E = \frac{Z_B\,(Z_s + Z_A)}{Z_s + Z_A + Z_B} . \tag{5}
$$

**Step 7 — the reduction factor.** Dividing (4) by (5) and by $I$:

$$
\boxed{\,r_{A \to B} = \frac{Z_s - Z_m}{Z_s + Z_A}
= 1 \;-\; \underbrace{\frac{Z_A}{Z_s + Z_A}}_{\text{galvanic}}
\;-\; \underbrace{\frac{Z_m}{Z_s + Z_A}}_{\text{inductive}}\,} \tag{6}
$$

Two things differ from the measurement, and they add:

- **Galvanic — the way back.** In the measurement the current leaves the
  earthing network through the remote electrode. In the fault it is taken out
  at A, and A hangs on the same earthing network through the screen. Taking it
  out there lowers the potential at B.
- **Inductive — the way there.** The conductor runs along the screen. Its
  current induces a voltage in the loop screen–earth that drives a current
  back through the screen; that part never reaches the electrodes.

$r$ does not depend on $Z_B$: the fault short-circuits the electrode of its own
station against the measurement at the same place. For a long cable
($\lvert Z_s\rvert \gg Z_A$) it tends to the classical $\lvert 1 - Z_m/Z_s\rvert$.

The three arrangements that send one ampere into station B, with the library
(5 km of the 150 mm² cable, $Z_A = 0.5\,\Omega$, $Z_B = 2\,\Omega$):

```python
import cmath
import math

import groundinsight as gi

F = 50.0
uw = gi.BusType(name="UW", system_type="Substation", voltage_level=20.0, impedance_formula="0.5")
ons = gi.BusType(name="ONS", system_type="Substation", voltage_level=20.0, impedance_formula="2")
cable = gi.standard_branch_types()["NA2XS(F)2Y 1x150RM/25 12/20 kV"]


def two_stations(branch_type, km=5.0):
    net = gi.create_network(name="two stations", frequencies=[F])
    gi.create_bus(name="A", type=uw, network=net)
    gi.create_bus(name="B", type=ons, network=net)
    gi.create_branch(name="K", type=branch_type, from_bus="A", to_bus="B", length=km, network=net)
    gi.create_fault(name="F", bus="B", network=net)
    return net


def key_figures(net):
    gi.run_fault(net, "F")
    return net.res_fault("F").to_dicts()[0]


# 1 the measurement: a remote auxiliary electrode and a lead that couples to nothing
measurement = two_stations(cable)
gi.create_bus(name="aux", network=measurement, type=gi.BusType(
    name="auxiliary electrode", system_type="auxiliary", voltage_level=20.0, impedance_formula="20"))
gi.create_branch(name="lead", from_bus="aux", to_bus="B", length=1.0, network=measurement, type=gi.BranchType(
    name="measuring lead", grounding_conductor=False, self_impedance_formula="1e-9*l",
    mutual_impedance_formula="0*l", phase_impedance_formula="0.5*l"))
gi.create_source(name="test current", bus="aux", values={F: 1.0}, network=measurement)

# 2 an uncoupled lead that ends at the earthing of A: the galvanic part alone
galvanic = two_stations(cable.model_copy(update={"name": "uncoupled", "mutual_impedance_formula": "0*l"}))
gi.create_source(name="test current", bus="A", values={F: 1.0}, network=galvanic)

# 3 through the phase conductor of the cable from A: the fault
fault = two_stations(cable)
gi.create_source(name="fault current", bus="A", values={F: 1.0}, network=fault)

branch = fault.branches["K"]
z_s, z_m = complex(branch.self_impedance[F]), complex(branch.mutual_impedance[F])
z_a, z_b = 0.5, 2.0
closed = {"measurement": 1.0, "galvanic": abs(z_s / (z_s + z_a)), "fault": abs((z_s - z_m) / (z_s + z_a))}
solved = {"measurement": key_figures(measurement)["r"], "galvanic": key_figures(galvanic)["r"],
          "fault": key_figures(fault)["r"]}
for name in closed:
    print(f"{name:12s} closed form {closed[name]:.6f}   library {solved[name]:.6f}")
print(f"Z_E closed form {abs(z_b * (z_s + z_a) / (z_s + z_a + z_b)):.6f}   "
      f"library {key_figures(fault)['Z_E_Ohm']:.6f}   long-cable limit {abs(1 - z_m / z_s):.6f}")
```

| Arrangement | Closed form | $r$ |
|---|---|---|
| 1 lead at 90° to a remote auxiliary electrode | 1 | 1.000 |
| 2 uncoupled lead that ends at the earthing of A | $Z_s/(Z_s + Z_A)$ | 0.940 |
| 3 through the conductor of the cable from A | $(Z_s - Z_m)/(Z_s + Z_A)$ | 0.316 |

Arrangement 2 separates the two parts by measurement as well: a test current
returned to the substation earth without coupling shows the galvanic part
alone. A test current fed through a disconnected cable measures $r\,Z_E$, not
$Z_E$.

## 3 Many feeding buses: superposition { #superposition }

The network is linear. Let $S$ run over the buses at which a current $I_S$ is
taken from the earthing network and driven through the phase conductors to the
fault bus $F$. Each $I_S$ alone raises the potential $U_E(S)$ at $F$. Define

$$
r_S = \frac{U_E(S)}{Z_E\,I_S} . \tag{7}
$$

$r_S$ is the factor of section 2 for the pair of buses $(S, F)$. It depends on
the electrodes and lines between them and on nothing else — in particular not
on what drives the current. Adding the contributions:

$$
\boxed{\,U_E = Z_E \sum_S r_S\,I_S, \qquad I_E = \sum_S r_S\,I_S, \qquad I_F = \sum_S I_S\,} \tag{8}
$$

and the factor of the fault is the current-weighted mean

$$
r = \frac{\lvert I_E\rvert}{\lvert I_F\rvert}
  = \frac{\bigl\lvert \sum_S r_S\,I_S \bigr\rvert}{\bigl\lvert \sum_S I_S \bigr\rvert} . \tag{9}
$$

Two special values:

- **$r_F = 0$.** A current taken at the fault bus itself is put back where it
  is taken. It appears in $I_F$ and does nothing in the earthing network.
- **Remote earth.** A current that comes out of the soil far from every
  electrode (the charging current of an overhead line, [section 6](#overhead-lines))
  is not taken at any bus. For it the galvanic part of (6) is missing.

`gi.earth_current_contributions(network, fault)` returns $I_S$, $r_S$ and
$r_S I_S$ for every bus. It does not solve once per bus: the coupled system of
earth and phase nodes is reciprocal, so one solve with one ampere at the earth
node $e_F$ of the fault bus gives all factors,

$$
r_S = \frac{x(p_S) - x(e_S)}{x(e_F)}, \qquad Z_E = x(e_F),
$$

where $x$ are the node potentials of that solve and $p_S$, $e_S$ the phase and
earth node of bus $S$: the potential at $F$ caused by a current driven from
$e_S$ into $p_S$ equals the voltage between $p_S$ and $e_S$ caused by the same
current at $F$. A test compares this with a direct solve per bus.

## 4 The charging current of a cable { #charging-current }

During an earth fault the healthy phases lie at the line-to-line voltage
against earth. One phase of a branch of length $l$ has the capacitance $C'l$
against the screen; the three phases together carry the charging current

$$
I_C = j\,\sqrt{3}\,\omega\,C'\,l\,U_n , \qquad
I_{CE} = \sum_{\text{branches}} I_C = j\,\sqrt{3}\,\omega\,C_E\,U_n . \tag{10}
$$

It crosses from the phase conductors to the screen **along** the cable. The
model works with the sum of the three phase currents, $3I_0$, as "the phase
current" of a branch; the question is where this distributed current may be
put.

**Step 1 — currents along the cable.** Let $x$ run from end A to end B and
$j' = I_C / l$ be the current per length that the screen hands to the phase
conductors (they carry it on to the fault). With $i_c(x)$ the phase current and
$i_s(x)$ the screen current, both counted from A to B:

$$
\frac{\mathrm{d} i_c}{\mathrm{d} x} = +j', \qquad \frac{\mathrm{d} i_s}{\mathrm{d} x} = -j'
\quad\Longrightarrow\quad
i_c(x) = i_c(0) + j'x, \qquad i_s(x) = i_s(0) - j'x . \tag{11}
$$

**Step 2 — the screen loop.** With the impedances per length $Z_s'$, $Z_m'$:

$$
u_A - u_B = \int_0^l \bigl(Z_s'\,i_s + Z_m'\,i_c\bigr)\,\mathrm{d}x
= Z_s\,\Bigl(i_s(0) - \tfrac{I_C}{2}\Bigr) + Z_m\,\Bigl(i_c(0) + \tfrac{I_C}{2}\Bigr) . \tag{12}
$$

Only the **mean** currents of the section enter, because $i_s$ and $i_c$ are
linear in $x$.

**Step 3 — the phase loop.** The same holds for the voltage along the phase
conductors, $\int (Z_{pp}'\,i_c + Z_m'\,i_s)\,\mathrm{d}x$: it depends on the
same two means.

**Step 4 — the equivalent.** Take $I_C/2$ from the screen to the phase
conductors at end A and $I_C/2$ at end B, and nothing in between. Then the
currents are constant along the section, $i_s(0) - I_C/2$ and
$i_c(0) + I_C/2$ — the means of (12). Screen loop and phase loop see the same
voltages, the nodes at both ends the same currents. Every potential and every
current outside the section is unchanged.

So for a charging current that is uniform along the branch, **half of it at
each end is exact, not an approximation.** A test cuts every cable into ten
pieces with unearthed joints and finds the same potentials to rounding
($10^{-14}$ relative).

**What "taken at the end" means.** The half current goes from the earthing node
of the end bus (the screen is bonded there) into the phase conductors and flows
through them to the fault. That is a current source in the sense of
`gi.Source`. Feeding the half currents into the earthing nodes *without* the
conductor current would be a different circuit: the screens would lose their
reducing effect. In the feeder of [section 5.4](#feeder) that gives 27.2 V
instead of 9.1 V at the fault station, a factor of 2.97
$\approx 1/\lvert 1 - Z_m/Z_s\rvert$.

**What is left out.** The voltage along a line is not constant; the charging
current of an open-ended line is $\tan(\beta l)/(\beta l)$ times the lumped
value, $\beta = \omega\sqrt{L'C'}$. For the coaxial mode of a cable
($v \approx 1.9\cdot10^{5}$ km/s, lossless estimate):

| $f$ | 1 km | 5 km | 10 km | 30 km |
|---|---|---|---|---|
| 50 Hz | 0.0001 % | 0.002 % | 0.009 % | 0.08 % |
| 250 Hz | 0.002 % | 0.05 % | 0.22 % | 2.0 % |
| 550 Hz | 0.01 % | 0.27 % | 1.1 % | 11 % |
| 1000 Hz | 0.04 % | 0.89 % | 3.7 % | 51 % |

At 50 Hz the error is irrelevant for any real cable. Long cables at high
harmonic orders are outside the model.

## 5 When the reduction factor exceeds 1 { #above-one }

### 5.1 Currents of one phase: the factor stays below the largest one

If all $I_S$ have the same phase angle, $\lvert\sum_S I_S\rvert = \sum_S \lvert I_S\rvert$ and

$$
\Bigl\lvert \sum_S r_S\,I_S \Bigr\rvert \le \sum_S \lvert r_S\rvert\,\lvert I_S\rvert
\le \max_S \lvert r_S\rvert \cdot \Bigl\lvert \sum_S I_S \Bigr\rvert
\quad\Longrightarrow\quad r \le \max_S \lvert r_S\rvert . \tag{13}
$$

That is the case of the **isolated neutral**: every current is a charging
current, all are $+j$. The factor of the fault is then *smaller* than that of
the substation alone, because the charging current that arises near the fault
has little way through the earthing network, and the half of the cable that
ends at the fault bus none at all.

For two stations and one cable: half of $I_C$ is taken at A with $r_{A\to B}$,
half at B with 0, so

$$
U_E = Z_E\,r_{A\to B}\,\frac{I_C}{2}, \qquad r = \frac{\lvert r_{A\to B}\rvert}{2} . \tag{14}
$$

### 5.2 The tuned coil, from the distributed model { #tuned-coil }

Now add a coil at A that is tuned exactly, $I_L = I_C$, and has no losses. The
fault current is zero. This case is derived here **without** the half-current
equivalent, directly from the distributed currents, so that it does not rest
on section 4.

**Step 1 — the phase current.** The coil sits between the neutral and the
earthing of A. Its current enters the phase conductors at A and leaves them
through the capacitance along the cable. With the magnitude $I_C$ and counted
from A to B (the common phase factor is left out):

$$
i_c(x) = I_C\,\Bigl(1 - \frac{x}{l}\Bigr), \qquad i_c(l) = 0 = I_F . \tag{15}
$$

**Step 2 — the screen current.** The screen collects what the conductors lose:

$$
i_s(x) = i_s(0) + I_C\,\frac{x}{l} . \tag{16}
$$

**Step 3 — node A.** The coil takes $I_C$ out of the earthing of A, the screen
$i_s(0)$; the electrode supplies them:

$$
\frac{u_A}{Z_A} + I_C + i_s(0) = 0 . \tag{17}
$$

**Step 4 — node B.** The screen brings $i_s(l)$, the fault nothing:

$$
i_s(l) = \frac{u_B}{Z_B} . \tag{18}
$$

**Step 5 — the screen loop**, with (15) and (16) integrated:

$$
u_A - u_B = Z_s\,\Bigl(i_s(0) + \frac{I_C}{2}\Bigr) + Z_m\,\frac{I_C}{2} . \tag{19}
$$

**Step 6 — solve.** Write $s = i_s(0) + I_C/2$ for the mean screen current.
Then $i_s(l) = s + I_C/2$ and $i_s(0) + I_C = s + I_C/2$, so (17) and (18) give
$u_B = Z_B\,(s + I_C/2)$ and $u_A = -Z_A\,(s + I_C/2)$. In (19):

$$
-(Z_A + Z_B)\Bigl(s + \frac{I_C}{2}\Bigr) = Z_s\,s + Z_m\,\frac{I_C}{2}
\quad\Longrightarrow\quad
s + \frac{I_C}{2} = \frac{I_C}{2}\cdot\frac{Z_s - Z_m}{Z_s + Z_A + Z_B} .
$$

**Step 7 — the result.**

$$
\boxed{\,U_E = u_B = \frac{I_C}{2}\cdot\frac{Z_B\,(Z_s - Z_m)}{Z_s + Z_A + Z_B}
= Z_E\; r_{A\to B}\;\frac{I_C}{2}\,} \qquad\text{with } I_F = 0 . \tag{20}
$$

The fault carries no current, and the fault station still has the potential
that half the charging current of the cable, fed from A, would give. $r$ of
(9) is infinite.

**What flows.** The coil current enters the phase conductors at A and has
left them at B. On the way, the conductors carry $I_C/2$ on average, and the
screen carries the same current back — except for the share
$r_{A\to B}$, which closes through the electrode of B, the soil and the
electrode of A. Nothing of this passes through the fault.

**The same with the half currents.** Coil at A: $-jI_C$ with $r_{A\to B}$.
Charging current: $+jI_C/2$ at A with $r_{A\to B}$, $+jI_C/2$ at B with 0. Sum:
$I_E = r_{A\to B}\,(-jI_C + jI_C/2) = -j\,r_{A\to B}\,I_C/2$, the magnitude of
(20). The two derivations agree.

### 5.3 Any detuning and damping { #detuning }

With the detuning $v = (I_C - I_L)/I_C$ (negative when over-compensated) and
the damping $d = I_W/I_C$ ($I_W$: watt residual current), the fault current is

$$
I_F = jI_C - jI_L + I_W = I_C\,(d + jv) .
$$

Everything except the half of the cable at B is taken at A:
$I_A = I_F - jI_C/2 = I_C\,\bigl(d + j(v - \tfrac12)\bigr)$. With (8) and (9):

$$
\boxed{\,r = \lvert r_{A\to B}\rvert\;\sqrt{\frac{d^2 + (v - \tfrac12)^2}{d^2 + v^2}}\,} \tag{21}
$$

| $v$ | Meaning | $r / \lvert r_{A\to B}\rvert$ for $d = 3\,\%$ | $r$ for $\lvert r_{A\to B}\rvert = 0.316$ |
|---|---|---|---|
| 1 | no coil (isolated) | 0.50 | 0.16 |
| 0.5 | coil compensates half | 0.06 | 0.02 |
| 0.1 | 10 % under-compensated | 3.84 | 1.21 |
| 0 | tuned | 16.7 | 5.28 |
| −0.05 | 5 % over-compensated | 9.45 | 2.99 |
| −0.1 | 10 % over-compensated | 5.75 | 1.82 |

$r > 1$ as soon as the square root exceeds $1/\lvert r_{A\to B}\rvert$. The
numerator hardly changes near $v = 0$ — the earthing network carries about
$I_C/2$ whatever the tuning — while the denominator, the residual current,
becomes small. At $v = \tfrac12$ the coil takes out at A exactly what the
cable hands over on average, and the fault station is nearly at rest.

```python
I_C = math.sqrt(3) * 2 * math.pi * F * cable.c_shield_nf_per_km * 1e-9 * 5.0 * 20e3
r_ab = abs((z_s - z_m) / (z_s + z_a))


def resonant(v, d):
    net = two_stations(cable)
    gi.create_resonant_neutral(name="coil", bus="A", voltage_level=20.0, coil_current=(1 - v) * I_C,
                               active_residual_current=d * I_C, network=net)
    return key_figures(net)


for v in (0.5, 0.1, 0.0, -0.05, -0.1):
    formula = r_ab * math.sqrt((0.03**2 + (v - 0.5) ** 2) / (0.03**2 + v**2))
    print(f"v = {v:+.2f}   r formula {formula:.4f}   library {resonant(v, 0.03)['r']:.4f}")

isolated = two_stations(cable)
gi.create_isolated_neutral(name="neutral", bus="A", voltage_level=20.0, network=isolated)
print(f"isolated   r formula {r_ab / 2:.4f}   library {key_figures(isolated)['r']:.4f}")
```

### 5.4 A feeder in numbers { #feeder }

A substation (0.5 Ω) and three stations (2 Ω each) on 3 + 3 + 4 km of the
150 mm² cable; $I_{CE} = 27.2$ A; fault at the last station S3. The coil is 5 %
over-compensated with 3 % watt residual; the resistor carries 1000 A.

```python
sections = (("UW", "S1", 3.0), ("S1", "S2", 3.0), ("S2", "S3", 4.0))


def feeder():
    net = gi.create_network(name="feeder", frequencies=[F])
    gi.create_bus(name="UW", type=uw, network=net)
    for number, (a, b, km) in enumerate(sections, start=1):
        gi.create_bus(name=b, type=ons, network=net)
        gi.create_branch(name=f"K{number}", type=cable, from_bus=a, to_bus=b, length=km, network=net)
    gi.create_fault(name="F", bus="S3", network=net)
    return net


i_ce = 2 * I_C
cases = {"isolated": feeder(), "resonant": feeder(), "low-impedance": feeder()}
gi.create_isolated_neutral(name="neutral", bus="UW", voltage_level=20.0, network=cases["isolated"])
gi.create_resonant_neutral(name="neutral", bus="UW", voltage_level=20.0, coil_current=1.05 * i_ce,
                           active_residual_current=0.03 * i_ce, network=cases["resonant"])
gi.create_low_impedance_neutral(name="neutral", bus="UW", voltage_level=20.0, current=1000.0,
                                network=cases["low-impedance"])
for name, net in cases.items():
    table = gi.earth_current_contributions(net, "F")
    row = key_figures(net)
    print(f"{name}: I_F {row['I_F_A']:.2f} A   I_E {row['I_E_A']:.2f} A   U_E {row['U_E_V']:.2f} V   r {row['r']:.3f}")
    print(table.select("bus_name", "I_S_A", "r_S", "I_E_A"))
```

| Bus $S$ | $\lvert r_S\rvert$ | $I_S$ isolated | resonant | low-impedance | $r_S I_S$ isolated | resonant | low-impedance |
|---|---|---|---|---|---|---|---|
| UW | 0.340 | 4.08 A | 24.50 A | 1000.0 A | 1.39 A | 8.33 A | 340.1 A |
| S1 | 0.315 | 8.16 A | 8.16 A | 8.16 A | 2.57 A | 2.57 A | 2.57 A |
| S2 | 0.246 | 9.52 A | 9.52 A | 9.52 A | 2.34 A | 2.34 A | 2.34 A |
| S3 (fault) | 0 | 5.44 A | 5.44 A | 5.44 A | 0 | 0 | 0 |
| **phasor sum** | | $I_F$ 27.21 A | 1.59 A | 1000.4 A | $I_E$ 6.28 A | 3.52 A | 339.4 A |
| $U_E$ | | | | | 9.14 V | 5.12 V | 493.9 V |
| $r$ | | | | | 0.231 | **2.218** | 0.339 |

Read it row by row:

- The rows S1 to S3 are the same in all three columns. The capacitive currents
  do not depend on the neutral treatment; only the current at the bus of the
  neutral changes.
- **Isolated:** all currents are in phase, $r = 0.231$ is below every factor
  of a feeding bus but that of the fault bus itself, as (13) says.
- **Resonant:** the fault carries 1.59 A. The earthing network carries 24.5 A
  at the substation and 23.1 A at the cable ends. $I_E = 3.52$ A is more than
  twice the fault current.
- **Low-impedance:** one current dominates, $r \approx r_\mathrm{UW}$.

A fault at the bus of the coil shows the same thing from the other side: what
flows through the neutral is taken at the fault bus ($r_F = 0$), so the fault
station has the same 3.0 V with an isolated neutral, a coil or a resistor.

### 5.5 What follows, and what does not { #consequences }

- **$r > 1$ is not an error and not a gain of energy.** $r$ compares the earth
  current with the current through the fault. In a resonant-earthed system the
  current through the fault is not the current that loads the earthing network.
  Use the table by bus, not $r$, to see what loads it.
- **The voltages remain small.** They are of the order $Z_E \cdot r_S \cdot
  I_C/2$: a few volts for the feeder above. The statement is about *which
  current* to multiply with $Z_E$, not about a hazard that was overlooked.
- **A single source with the residual current underestimates.** Entering
  $I_\mathrm{rest}$ as one source at the substation gives 0.79 V for the feeder
  above; the model gives 5.12 V, and nearly the same for every fault location.
- **A single source with $I_{CE}$ overestimates for an isolated neutral:**
  13.5 V instead of 9.1 V.
- **Not shown:** that these numbers hold for a real system. The model assumes a
  rigid voltage, a uniform charging current and screens bonded at both ends
  ([section 9](#limits)); a measurement would have to confirm it.

## 6 Overhead lines { #overhead-lines }

Between the conductors of an overhead line and the soil there is no screen. The
charging current of `c_earth_nf_per_km` enters the conductors out of the soil
along the route, far from any electrode: in the node model from remote earth.
The half currents of such a branch are therefore not taken at the earthing
nodes of its ends. All of that current flows through the electrodes that the
fault station offers, and $r_S = 1$ where no earthing conductor joins the
stations. Put on the station nodes instead, a 10 km line gives 0.90 V where
$I_C \cdot Z_E = 1.20$ V is right.

A line with an earth wire has both: `c_shield_nf_per_km` for the capacitance
conductor–earth wire, which behaves like a cable screen, and
`c_earth_nf_per_km` for conductor–earth. How the earth-wire current divides
over the tower footings is only seen where the towers are buses.

## 7 Harmonics { #harmonics }

Every neutral-point element takes a current spectrum in A
(`harmonic_currents`, empty by default). Where it flows depends on the
element:

- **Isolated:** a current can only close through the capacitances. The entered
  value is the fault current at that frequency and is distributed over the
  branches like the charging current at the nominal frequency.
- **Resonant:** as above, and the coil compensates a share. At the harmonic
  order $h$ the capacitive current is $h$ times and the coil current $1/h$
  times the value at the nominal frequency for the same voltage, so the coil
  takes $k/h^2$ of the capacitive current, $k = I_L/I_{CE}$. For an entered
  residual $I_\mathrm{res}(h)$ the capacitive current is
  $I_\mathrm{res}(h) / (1 - k/h^2)$, distributed over the branches, and the
  coil's part is taken at its bus. At $h = 5$ and $k = 1.05$ that is 4.4 %
  more than the residual.
- **Low-impedance:** the current flows through the neutral and is scaled with
  the fault like the current at the nominal frequency.

Without any capacitance in the supply area the current stays at the bus of the
element. A harmonic exactly on the resonance $h^2 = k$ is refused.

## 8 Voltage levels and supply areas { #levels }

The earth wire of a 110 kV line, the screens of 20 kV cables and the PEN of LV
cables are one earthing network. A fault belongs to **one** voltage level:
only the branches of that level (`BranchType.voltage_level`) carry its phase
current and only their capacitances are charged. The branches of the other
levels are earthing conductors in that solve, and carry the potentials on.

The voltage is given at the feeding element: a neutral-point element always
has a `voltage_level`, a `Source` when it is set. The **supply area** of an
element is the set of branches of its level that its phase conductors reach;
open switches divide areas. `gi.supply_areas(network)` lists them with the
capacitive earth-fault current, the sum of the coil currents and the detuning
— several coils in one area add up.

- The level of a fault follows from its bus if only one supply area reaches
  it. At a bus where two fed levels meet (a substation, a distribution
  substation) `Fault.voltage_level` says which one; `run_fault` asks for it
  instead of guessing.
- A `Source` carries a prescribed current. Its supply area is not charged:
  in a solidly earthed system the displacement voltage is small and depends on
  the location.
- Levels come into play only when a feeding element carries one. A network
  without any is one system, and solves exactly as before.

The scaling of a fault (`Fault.scalings`) stands for the line impedance that
limits a short-circuit current. It applies to sources and to the current of a
low-impedance neutral, **not** to charging currents and coil currents, which
depend on voltage and capacitance.

## 9 Assumptions and limits { #limits }

- **Rigid voltage.** The capacitances are charged with
  $U_n/\sqrt3$ everywhere. The effect of the earth potential rise on that
  voltage (volts against kilovolts) and the voltage profile along the line
  are neglected. For a low-impedance neutral the full displacement voltage is
  an upper bound.
- **Uniform charging current** along each branch (section 4).
- **Sum current.** The three phase conductors are represented by their sum,
  with one mutual impedance to the earthing conductor. That is exact where
  every phase couples equally to it (single-core cables in trefoil, the mean
  used for four-core cables) and an average otherwise.
- **Screens bonded at both ends**, insulated from the soil in between.
- **Losses.** The watt residual current is taken at the bus of the coil; the
  losses of the cable insulation are in fact distributed.
- **No resonance of harmonics.** The spectrum is an input. What the inductance
  of the system and its capacitance do to a harmonic voltage needs the
  capacitances as shunt admittances and a voltage-driven fault; that is on the
  roadmap.
- **Transient studies** do not include neutral-point elements.

## 10 How the statements are checked { #checks }

Every formula on this page is a test, and each is checked a second way.

| Statement | Check | Second way |
|---|---|---|
| (6), three arrangements | closed forms against the solve | the measuring arrangement built with an auxiliary electrode |
| (8) $I_E = \sum r_S I_S$ | sum of the table against `res_fault` | three neutral treatments, three fault locations |
| $r_S$ by reciprocity | against a solve with one ampere from each bus | closed form for two stations |
| (10) fault current | $\sqrt3\,\omega\,C_E\,U_n$ against the solve | phase currents of a feeder by Kirchhoff's law |
| section 4, half currents | the same network with the currents written out as sources | every cable cut into ten pieces (study notebook) |
| the written-out network | the independent solver `tests/_reference_mna.py` (branch currents as unknowns) | a nodal system set up by hand (study notebook) |
| (14), (20), (21) | closed forms against the solve | (20) derived twice on this page |
| section 6 | $U_E = I_C\,Z_E$, nothing taken at the stations | — |
| section 8 | each level against the network built by hand for that level | three levels, ambiguous buses refused |
| unchanged networks | results and digest of a network without the new data pinned to the released values | — |

The tests are `tests/test_capacitive_currents.py`,
`tests/test_earth_current_contributions.py`, `tests/test_supply_areas.py` and
`tests/test_docs_capacitive_currents.py`.
