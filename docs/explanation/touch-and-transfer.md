# Touch voltage and transferred potential

The network solve gives the earth potential rise of every electrode, $U_E$.
The touch voltage of EN 50522 is something else: the part of $U_E$ that a
person bridges between a touched part and the ground 1 m away, and that
depends on the potential of the earth surface. The network model has no
surface — each electrode is one impedance to remote earth. An
`EarthingCharacteristic` brings the missing piece as two dimensionless
factors, computed once for the electrode by a field solver. This page
explains why a factor is enough, how the result is formed, and where the
factors stop describing the installation.

## Why a factor is enough

The field of an electrode in soil is linear in its current: doubling the
current doubles the potential everywhere, at the electrode and on the
surface around it. The ratio of any two potentials is therefore a property of
the geometry and the soil alone, not of the current. Two such ratios matter:

$k_\mathrm{touch} = U_\mathrm{vT} / U_E$
:   the largest prospective touch voltage around the electrode — feet 1 m
    from the conductors, in every direction, optionally inside the meshes —
    over the electrode potential. groundfield's touch-voltage envelope gives
    the numerator.

$k_\mathrm{transfer} = \max_P |U_E - \varphi(P)| / |U_E|$
:   the largest difference between the electrode potential and the surface
    potential $\varphi(P)$ at target points $P$, over the electrode potential.
    A conductor bonded to the earthing system and run to $P$ — the PEN to a
    house, the sheath of a cable, a pipe — carries $U_E$ there, and a person
    at $P$ bridges that difference (EN 50522, Clause 6).

The soil does change the ratios: in layered soil the field takes another
shape when the layers are wetter or drier, and at higher frequencies the
current distribution along the conductors changes. The factors are
therefore tabulated over the same grid as the impedance — the effective
specific earth resistance and the frequency — and interpolated linearly in
$\ln\rho$ and $\ln f$.

## How the result is formed

For every bus with a factor and every frequency of the solve

$$
U_\mathrm{vT}(f) = k_\mathrm{touch}(\rho, f)\,|U_E(f)|, \qquad
U_\mathrm{transfer}(f) = k_\mathrm{transfer}(\rho, f)\,|U_E(f)|,
$$

with $U_E(f)$ the EPR of the bus in the solved network and $\rho$ its
specific earth resistance. The RMS over the frequencies is formed like that
of the EPR, $\sqrt{\sum_f U_\mathrm{vT}(f)^2}$. `res_buses` reports both per
bus (`U_vT_V`, `U_transfer_V`), `res_fault` the largest of each with its bus,
and `check_touch_voltages` compares the RMS touch voltage with the
permissible value you pass.

Only magnitudes enter: a factor is a ratio of magnitudes, and the phase of
the surface potential relative to the electrode is not part of the table. A
bus without a characteristic, or with one that tabulates no factor, has no
touch voltage — the columns are null there, which is not the same as zero.

$U_\mathrm{vT}$ is the *prospective* touch voltage, before body and
additional resistances (footwear, a surface layer). Comparing it with the
permissible touch voltage $U_\mathrm{Tp}$ is conservative; comparing it with
the prospective permissible value, which includes the additional resistances
of EN 50522 Annex B, is the refined check. The report should say which
([EN 50522 how-to](../howto/en50522-check.md)).

## Electrodes that see each other

A characteristic is computed for one electrode alone in the soil. The
factor and the impedance describe that electrode as long as its potential
funnel is its own. In a network, electrodes connected by cable screens or a
PEN carry currents of their own; when they lie close together, each raises
the soil potential at the others. The network model does not see that: it
connects the electrode impedances through the conductors, which amounts to
connecting the electrodes in parallel, and leaves out the *mutual
resistance* between them. Two things change:

- **The earthing impedance is too low.** The neighbours lift the potential
  of each electrode, so the same current needs a higher $U_E$ than the
  parallel connection says.
- **The touch factor is too high.** The neighbours lift the surface around
  the station as well, so the surface is closer to the electrode potential
  and the touch voltage is a smaller part of it.

The two errors partly cancel in $U_\mathrm{vT}$, but not completely.

### An example

A 20/0.4 kV station (ring of radius 2.5 m at 0.6 m depth with four 2.5 m
rods) feeds five houses (foundation electrodes 10 m × 10 m with a 2 × 2
mesh at 0.8 m) through a PEN of 150 mm² aluminium, the houses 25 m apart, in
two-layer soil (100 Ω·m over 2 m, 500 Ω·m below). An earth fault on the MV
side drives 1000 A into the station. groundfield (0.15.0, backend `mom`)
solved the whole arrangement — station, houses and PEN in one field — and
characterised each electrode alone; the network built from those
characteristics is the [example project](../examples/ortsnetzstation_5_haeuser.json)
of the documentation.

| f | $Z_E$ field | $Z_E$ network of single electrodes | difference | $U_\mathrm{vT}$ field | $U_\mathrm{vT}$ network | difference |
|---|---|---|---|---|---|---|
| 50 Hz | 3.324 Ω | 1.879 Ω | −43.5 % | 724.3 V | 573.9 V | −20.8 % |
| 250 Hz | 3.329 Ω | 1.898 Ω | −43.0 % | 726.1 V | 579.7 V | −20.2 % |
| 1000 Hz | 3.401 Ω | 2.055 Ω | −39.6 % | 751.3 V | 627.5 V | −16.5 % |

The network's $Z_E$ of 1.879 Ω is the parallel connection of the single
characteristics — station 17.20 Ω, five houses 10.48 Ω each, together
1.868 Ω — plus a few milliohms of PEN. The field sees 3.324 Ω. The station's
touch factor falls from 0.305 alone to 0.218 among its neighbours, which is
why $U_\mathrm{vT}$ is less wrong than $Z_E$.

Moving the houses apart shows that the difference is the coupling. Each row
compares the field solution with the network of single electrodes and a PEN
of that length, at 50 Hz:

| House spacing | 25 m | 50 m | 100 m | 200 m | 400 m | 800 m |
|---|---|---|---|---|---|---|
| $Z_E$ field | 3.324 Ω | 2.651 Ω | 2.297 Ω | 2.155 Ω | 2.198 Ω | 2.533 Ω |
| $Z_E$ network | 1.879 Ω | 1.891 Ω | 1.916 Ω | 1.975 Ω | 2.121 Ω | 2.486 Ω |
| difference | −43.5 % | −28.7 % | −16.6 % | −8.3 % | −3.5 % | −1.9 % |

At large spacing both rise again, because the PEN itself gets long; the
difference goes to zero.

### What to do

- **Characterise a closely coupled system as one.** A station with the
  houses on its PEN, a meshed settlement, a substation with its adjacent
  buildings: compute the field of the whole system and attach the
  impedance seen at the feed to the bus where the fault current enters
  (`characterize(..., quantity="input")`,
  [how-to](../howto/groundfield-characteristics.md#electrodes-close-together-one-characteristic-at-the-feed)).
  In the example that characteristic, attached to the station bus,
  reproduces the field solution at every grid point.
- **Keep separate characteristics for electrodes far apart**, where the
  funnels do not overlap. How far that is depends on the soil — deep,
  high-resistive layers carry the coupling far; in the example the error is
  still 8 % at 200 m.
- **A system characteristic is valid for that system.** It describes the
  houses as they were computed. A study that changes them — an outage of a
  house electrode, a new building — needs a new characterisation;
  modelling the mutual resistance between buses in the network itself is
  not part of the package.

## Relation to the EPR bound

Without characteristics, $U_E$ is an upper bound of the touch voltage for a
person standing in the funnel of the same earthing system
([EN 50522 how-to](../howto/en50522-check.md#epr-is-an-upper-bound-of-the-touch-voltage)),
and the bound is what the design procedure of EN 50522 compares with. With a
touch factor the model gives the prospective touch voltage itself. The
factor rests on the field calculation and on the geometry it assumed; where
the installation is measured (EN 50522, Annex L), the measurement decides.
