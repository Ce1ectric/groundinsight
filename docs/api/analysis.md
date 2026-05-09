# Analysis routines

Higher-level analysis workflows that orchestrate repeated
`run_fault` calls and answer parametric questions about an existing
network — currently the **inverse rho problem** at the bus-grounding
side.

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
# rho_max (per-bus dict), iterations.
print(f"c_max = {result['c_max']:.3f}")
print(f"EPR at c_max = {result['u_epr_rms_at_c_max']:.1f} V")
for bus, rho_max in result["rho_max"].items():
    print(f"  {bus}: rho_max = {rho_max:.0f} Ω·m")
print(f"converged in {result['iterations']} iterations")
```

For the rho-f variant — useful when the bus impedance carries a
frequency-coupling term whose magnitude should also be probed —
see :func:`find_max_rho_f_scaling`.

## API reference

::: groundinsight.analysis
