# Analysis routines

Higher-level analysis workflows on top of a fully built network: the
**inverse rho problem** at the bus-grounding side, the
**conductor thermal-limit check** that assesses whether a grounding
conductor survives the fault current it carries, and its node-side
counterpart, the **node thermal-limit check** for earthing conductors
and earth electrodes.

## Physical / modelling context

A fully built `Network` solves a forward problem: given the bus
grounding impedances $Z_{\text{B},i}(\rho_E, f)$, find the EPR at the
fault bus. The natural inverse question — *"how poor can the soil
get before the EPR exceeds a touch-voltage limit $u_{\max}$?"* — is
recurring in safety-engineering workflows: it sets the worst-case
specific earth resistance the network can tolerate while still
satisfying the relevant standard (e.g. EN 50522) at the assumed
fault current.

`find_max_rho_scaling` solves that problem by **log-bisection over
a uniform scaling factor $c$** of $\rho_E$ on a user-supplied list of
selected buses. At every trial $c$, the bus-grounding impedance is
re-evaluated through the bus's own `BusType.impedance_formula` and
`run_fault` is invoked. The bracket converges on the largest $c$ for
which $|U_\text{EPR}(f)|_{\text{RMS}} \le u_{\max}$ at the fault bus.
The sister routine `find_max_rho_f_scaling` (and the diagnostic helper
`evaluate_max_epr_under_k`) extend the same idea to a *frequency-
dependent* rho-f characteristic: rho is scaled while a separate
factor $k$ tunes the imaginary, frequency-coupling part of the
formula, so the inverse problem can be posed against a parametric
rho-f curve rather than a single scalar.

The shape of the rho-f curve at each bus is controlled entirely by
the existing `BusType.impedance_formula`; the analysis routines only
vary the scalar factors, so any user-defined parametric form
(linear, square root, frequency-dependent, …) is supported
transparently. Original `rho` values are restored via a `finally`
block, so the network is left untouched even if the bisection fails.

## Example

```python
import groundinsight as gi

# Assume `net` is a built network with at least one fault and one
# source. Pick the buses whose specific_earth_resistance should be
# scaled jointly — typically all buses sharing a soil environment.
result = gi.find_max_rho_scaling(
    network=net,
    fault_name="fault1",
    bus_names=["bus_substation", "bus_fault"],
    u_max=200.0,            # touch-voltage limit in volts (RMS)
    c_bounds=(0.1, 100.0),  # search bracket on the scaling factor
    tol_rel=1e-3,
    max_iter=40,
)

# `result` is a dict with keys: c_max, u_epr_rms_at_c_max,
# rho_max (per-bus dict), iterations, status, converged,
# c_bracket, bracket_rel_width.
#
# Read `converged` before `c_max`: c_max is always a scaling factor
# whose EPR was measured and found admissible, but only a converged
# search has also shown that nothing meaningfully larger is.
if not result["converged"]:
    print(f"not a maximum: {result['status']}, "
          f"bracket {result['c_bracket']}")

print(f"c_max = {result['c_max']:.3f}")
print(f"EPR at c_max = {result['u_epr_rms_at_c_max']:.1f} V")
for bus, rho_max in result["rho_max"].items():
    print(f"  {bus}: rho_max = {rho_max:.0f} Ω·m")
```

## Reading the result

`c_max` carries a one-directional guarantee: the EPR at that scaling
factor was computed and found to satisfy the limit. Whether it is
also the *largest* such factor depends on how the search ended, and
that is what `status` reports.

| `status` | `converged` | what `c_max` means |
| --- | --- | --- |
| `"converged"` | `True` | The bracket closed to within `tol_rel`. The true threshold lies inside `c_bracket`. |
| `"bracket_within_tol_on_entry"` | `True` | The supplied bracket was already narrower than `tol_rel`, so no step was needed. Same guarantee as above. |
| `"bracket_fully_admissible"` | `False` | Every factor in the bracket satisfied the limit, so `c_max` is the *upper* bound — a lower bound on the true maximum, since nothing above it was evaluated. `c_bracket` is `(c_hi, inf)`, so `math.isfinite(result["c_bracket"][1])` tests for this case. Widen `c_bounds`. |
| `"max_iter_reached"` | `False` | The step cap was hit before the bracket closed. `c_max` is admissible but can be far below the true threshold — on a saturating test network, a cap of 3 instead of 60 came out 81 % low. Raise `max_iter`, or narrow `c_bounds`. |

`iterations` on its own does **not** identify a case: two of the four
rows above can produce `iterations == 0`, and their `c_max` comes from
opposite ends of the bracket. Branch on `converged` or `status`, never
on the step count.

`bracket_rel_width` is `(c_hi - c_lo) / c_lo` for the reported
bracket — directly comparable against `tol_rel`, and `inf` when the
bracket is open upwards.

The arguments are validated strictly, because a limit search that
returns a number nobody can tell apart from an answer is worse than
one that raises. `u_max` and `tol_rel` must be finite and strictly
positive — NaN passes a plain `<= 0` check and then makes every later
comparison `False` — `c_bounds` must be finite, and `max_iter` must be
an `int` of at least 1.

For the rho-f variant — useful when the bus impedance carries a
frequency-coupling term whose magnitude should also be probed —
see :func:`find_max_rho_f_scaling`. It reports the same four
diagnostic keys with the same meaning.

## Conductor thermal limits (IEC 60949 / IEC 60909-0)

The inverse-rho routines answer a *person-safety* question (does the
EPR stay below a touch-voltage limit). `check_conductor_limits`
answers the complementary *equipment-integrity* question: does the
grounding conductor survive the fault thermally? It compares the
thermally equivalent short-time current

$$ I_{th} = I_{s,\text{RMS}} \sqrt{m + n} $$

against the adiabatic limit of IEC 60949,

$$ I_{adm} = \frac{k \, S}{\sqrt{t_k}}, \qquad
   k = K \sqrt{\ln\!\frac{\theta_f + \beta}{\theta_i + \beta}} , $$

per grounding branch, and reports the utilisation and a pass/fail
flag. A branch is checked only when its `BranchType` carries both
`conductor_material` and `cross_section_mm2`.

### What is superposed and what is not

This is the modelling rule the whole short-circuit side is built on.
The frequency-domain solve superposes the **linear** AC-RMS currents
as always. The IEC 60909 factors $\kappa$ and $m$ are **non-linear**
in the fault-loop $R/X$, so they are applied *once*, to the already
aggregated branch current — $i_p$ and $I_{th}$ are never superposed
directly.

With several infeeds the DC components add, so the largest possible
peak of the total current is the sum of the individual peaks. Written
as a single factor on the aggregate that is exactly the
current-weighted mean

$$ \kappa_{\text{eff}} =
   \frac{\sum_i \kappa_i I_i}{\sum_i I_i} , $$

which `resolve_fault_sc_characteristics` uses by default
(`aggregation="weighted"`); it reproduces the sum of the individual
peaks identically. `aggregation="max"` is the strictly conservative
variant. Reusing one source's $\kappa$ for all of them is simply
wrong and errs in either direction. Where the simultaneous-peak
assumption is too crude — strongly mixed $R/X$ infeeds — the
transient solver remains the exact fallback, because it integrates
the actual waveforms instead of applying a standard factor.

```python
import groundinsight as gi

# The characteristics can be set by hand ...
net.sources["src"].r_to_x = 0.1
net.faults["F"].t_k_s = 0.5

# ... or imported from a solved pandapower case, see the I/O page:
# gi.apply_shortcircuit_characteristics(net, net_pp, "F")

gi.run_fault(net, "F")
gi.check_conductor_limits(net, "F").select(
    "branch_name", "I_s_rms_A", "i_p_A", "I_th_A",
    "I_admissible_A", "utilization", "within_limit",
)
```

Explicit `t_k`, `kappa` / `r_to_x` and `n` arguments override whatever
is stored on the model, so sensitivity studies stay possible.

## Node thermal limits — earthing conductor vs earth electrode

`check_conductor_limits` assesses the shield / earth wire **between**
buses. `check_node_limits` assesses the two grounding elements **at** a
bus, which EN 50522 / IEC 61936-1 keep strictly apart because they carry
different currents. Confusing them is the classic sizing error, and in a
meshed system it is an order-of-magnitude error in both directions.

| element | German | current | column |
|---|---|---|---|
| earthing conductor | *Erdungsleiter* | full injected earth-fault current | `ResultBus.i_inj` |
| earth electrode | *Erder* | share dissipated into the soil, $u_\text{EPR}/Z_B$ | `ResultBus.ia` |

Three physically distinct currents meet at a grounding bus: the lumped
injection `i_inj`, the electrode current `ia`, and the branch shield
currents reported per branch on `ResultBranch`. The nodal balance that
ties them together is

$$ i_a = i_\text{vector} +
   \sum_\text{branches} (u_\text{other} - u_\text{self})\, Y_\text{self} . $$

`i_inj` deliberately **excludes** the mutual Norton-equivalent injections
of the inductively coupled branches: those model a *distributed* induced
EMF along the line, not a current entering the node through a lumped
conductor. It is non-zero only at source buses (the infeed) and at the
fault bus (the total fault current).

### Data model and current split

A bus is assessed per element, and only once its `BusType` carries both
the material and the cross-section for that element. The two elements are
independent — declaring one does not imply the other.

```python
bt = gi.BusType(
    name="tower",
    system_type="Tower",
    voltage_level=110.0,
    impedance_formula="rho * 0.1",
    # Erdungsleiter — sized for the full earth-fault current
    earthing_conductor_material="Cu",
    earthing_conductor_cross_section_mm2=50.0,
    earthing_conductor_theta_final_C=gi.final_temperature("Cu", "bare"),
    # Erder — sized for what actually reaches the soil, four legs
    electrode_material="Steel",
    electrode_cross_section_mm2=95.0,
    electrode_current_split=0.25,
)
```

Each element carries a free factor `current_split` in $(0, 1]$, applied as
$I_\text{conductor} = I_\text{RMS}\cdot\texttt{current\_split}$. It
expresses how the bus current divides between physically parallel paths
that the nodal model lumps into one: `1.0` (default) for a single
conductor, `1/N` for $N$ parallel legs, `0.5` for a ring fed at one point,
or an IEEE Std 80 division factor. It is deliberately **not** derived
automatically — the split depends on geometry the nodal model does not
carry — and a value above 1 is rejected at the model level, because that
would not be a split but an error.

```python
gi.run_fault(net, "F")
gi.check_node_limits(net, "F", t_k=0.5, r_to_x=0.1).select(
    "bus_name", "element", "I_rms_A", "current_split",
    "I_th_A", "I_admissible_A", "utilization", "within_limit",
)
```

The IEC 60909 excitation (`t_k`, `kappa`, `m`, `n`) is resolved by the
same helper the branch check uses, so both views of one fault always
agree. Buses whose type declares neither element still appear in the
frame, with the currents filled in and `within_limit = None` — enough to
size by hand without re-running anything.

### Final temperatures

`FINAL_TEMPERATURES` and `final_temperature(material, covering)` provide
$\theta_f$ values with the source named inline per entry (National Grid
ETS Table 5a for bare buried conductors, IEC 60364-5-54 Table 54.2 for
PVC / XLPE). The catalog is deliberately **incomplete rather than filled
with plausible-looking numbers**; `final_temperature` raises for anything
missing and names EN 50522 Table 2 as the source to consult.

!!! warning "Steel default"

    The `IEC60949_MATERIALS["Steel"]` default of 400 °C is *higher* — i.e.
    more permissive, the unsafe direction for a limit check — than the
    300 °C the National Grid table gives for bare buried steel. It is left
    unchanged because moving a default silently would move every existing
    study. Pass `theta_final_C` explicitly until the value has been checked
    against EN 50522 Table 2.

!!! note "Results computed before this feature"

    `ResultBus.i_inj` defaults to `0.0` on results stored by an earlier
    version, so the earthing-conductor rows would read as unstressed.
    Re-run `run_fault` after upgrading.

### Incomplete results are an error, not a pass

Both checks build their frame from the *stored* result of the fault, so a
missing entry would produce no row — and a missing row is indistinguishable
from a passing one. They therefore verify first that the stored result covers
every branch and every active bus, and raise `ValueError` otherwise rather
than reporting the missing elements as free of violations.

This matters because the gap is reachable through ordinary use.
`ElectricalNetwork.solve_network()` replaces `network.results[fault]` with a
result carrying bus rows only — `compute_branch_currents()` fills in the
branches, which is why `run_fault` calls both — so solving a hand-built
`ElectricalNetwork` to look at $Y$, $i$ or $u$ discards the branch results.
Adding a branch after `run_fault` leaves the same gap. To inspect the nodal
system without disturbing anything, build the `ElectricalNetwork` (its
constructor has no side effects) and read the voltages back from
`network.results[fault].buses` instead of re-solving.

Both frames also carry an explicit schema, so a network genuinely without
branches returns an empty frame that is still selectable and filterable,
rather than a schema-less `(0, 0)` frame on which `pl.col(...)` raises.

## API reference

::: groundinsight.analysis
