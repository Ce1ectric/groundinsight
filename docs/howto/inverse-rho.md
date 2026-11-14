# Admissible soil resistivity (inverse rho / rho-f)

The forward question is "what earth potential rise does this soil give?". The
inverse one is "how poor may the soil become before the earth potential rise
exceeds the limit?" — the margin of an installation against seasonal or
uncertain soil resistivity. This guide answers it with
`gi.find_max_rho_scaling` and, for a fitted rho-f characteristic, with
`gi.find_max_rho_f_scaling`. The script is
[`inverse_rho.py`](../snippets/inverse_rho.py); its output is at the
[end of the page](#output).

The example is the 20 kV feeder of the
[EN 50522 how-to](en50522-check.md#worked-example): earth fault at `S3`,
1000 A at 50 Hz and 50 A at 250 Hz, station electrodes of 5.90 Ω at
ρ = 100 Ω·m.

```python
--8<-- "docs/snippets/inverse_rho.py:network"
```

## Choose the limit

`u_limit` is a limit on the **earth potential rise** at the fault bus, RMS
over all frequencies — not the touch voltage. The earth potential rise
`U_E` is an upper bound of the touch voltage; EN 50522 compares it with the
permissible touch voltage `U_Tp(t_F)` of the fault duration in its design
procedure, and that procedure (with the recognised measures `M` of the
standard) says which value `U_E` may reach for your installation. Take
`u_limit` from there: the procedure, the clauses and the treatment of
harmonics are in [Compare results with EN 50522 limits](en50522-check.md#the-procedure).
The package contains no permissible values; the 500 V of the example are
illustrative.

## The largest admissible scaling of rho

`gi.find_max_rho_scaling(network, fault, bus_names, u_limit)` multiplies the
`specific_earth_resistance` of every bus in `bus_names` by one factor `c`,
evaluates the bus-type formulas again and finds by log-bisection over
`c_bounds` (default `(1e-3, 1e3)`) the largest `c` whose RMS earth potential
rise at the fault bus stays at or below `u_limit`.

- Only the **buses** are scaled. The `specific_earth_resistance` of the
  branches — the soil in the earth-return terms of a Carson-type cable formula
  — stays as it is.
- Before the bisection a scan of `monotonicity_grid` points (default 9) over
  `c_bounds` checks that the earth potential rise grows with `c`, which a
  bisection assumes; a formula whose reactance does not scale with ρ can turn,
  and the search then says so instead of returning a wrong maximum.
- The network is left as it was, also when the search fails or is cancelled
  (`should_cancel=`); `progress=` reports every solve.
- Every trial after the first one per fault location re-solves only the
  matrix values, bit for bit like a full `run_fault`, so a search costs little
  more than its first solve (see [Large and meshed networks](performance.md)).

```python
--8<-- "docs/snippets/inverse_rho.py:rho"
```

## Read the result

`c_max` is always a factor whose earth potential rise was computed and found
admissible. Whether it is also the **largest** one depends on how the search
ended — read `converged` (or `status`) before using it:

| `status` | `converged` | What `c_max` means |
|---|---|---|
| `"converged"` | `True` | The bracket closed to within `tol_rel`; the threshold lies in `c_bracket`. |
| `"bracket_within_tol_on_entry"` | `True` | The bracket was narrower than `tol_rel` from the start; same guarantee. |
| `"bracket_fully_admissible"` | `False` | Every factor up to `c_bounds[1]` was admissible: `c_max` is that bound, a lower bound of the true maximum, and `c_bracket` is `(c_hi, inf)`. Widen `c_bounds`. |
| `"max_iter_reached"` | `False` | The step cap ended the search; `c_max` is admissible but possibly far below the threshold. Raise `max_iter`. |
| `"non_monotone"` | `False` | The earth potential rise turns inside `c_bounds`; `c_max` is the largest admissible factor the scan measured, refined inside its interval — a lower bound. |

`c_bracket` is the interval that provably contains the threshold,
`bracket_rel_width` its relative width (compare it with `tol_rel`),
`rho_max` the scaled resistivity per bus and `u_epr_rms_at_c_max` the earth
potential rise at `c_max`. Do not branch on `iterations`: two different
statuses can both end with zero steps.

## A rho-f characteristic

When the electrodes are described by a fitted rho-f vector
$Z = k_1\rho + (k_2 + jk_3)f + (k_4 + jk_5)\rho f$ (the form `groundfield`
exports), `gi.find_max_rho_f_scaling(network, bus_names, u_limit, k_ref)`
scales the whole vector, $k = c \cdot k_\text{ref}$, gives it to every bus of
`bus_names` (evaluated with each bus's own ρ) and sweeps each of those buses
as the fault location: an existing fault at the bus is used, otherwise a
temporary one with `fault_scalings` (default 1.0) is created and removed again.
It finds the largest `c` for which no swept bus exceeds `u_limit`, and returns
`k_max`, `max_epr_rms_at_c_max`, `epr_rms_per_bus_at_c_max` and the same
`status`, `converged` and `c_bracket` as above.
`gi.evaluate_max_epr_under_k` evaluates one vector without a search, and
`gi.select_rho_f_from_catalog` ranks a whole catalogue against the limit.

```python
--8<-- "docs/snippets/inverse_rho.py:rho_f"
```

## Output

```text title="Output of inverse_rho.py"
rho search: status = converged, converged = True
c_max = 1.6939, bracket = (1.6939, 1.6953), EPR at c_max = 500.0 V
rho_max at S3 = 169.4 Ohm*m
rho-f search: status = converged, c_max = 0.6127, k1_max = 0.03615 Ohm/(Ohm*m)
largest EPR at c_max: 499.8 V, fault at S5
```

The station electrodes may see 1.69 times the assumed resistivity before the
earth potential rise at `S3` reaches 500 V. The rho-f search is stricter
because it also faults the other stations: with the fitted characteristic the
station at the end of the feeder, `S5`, exceeds the limit first, so the
characteristic may only be 0.61 times the fit. The test suite recomputes the
earth potential rise at both ends of the bracket with the independent
reference solver of the tests.
