# Warnings and diagnostics

`groundinsight` reports a condition that changes how a result must be read —
a heuristic phase-current split, a frequency the input does not cover, a
reactance that has no meaning at 0 Hz — as a **warning with a stable code**,
not as a log line. This page lists every code the package can produce: the
warning class it is raised with, its severity, the elements it names, when it
is raised and what to do about it. A test compares the page with the source
code in both directions, so a code cannot be added without a row here.

## How a diagnostic reaches you

Every package warning is an instance of a subclass of
`gi.GroundinsightWarning` (itself a `UserWarning`) and carries three fields
next to its message:

| Field | Meaning |
|---|---|
| `code` | stable upper-case identifier; programs branch on it, never on the message text |
| `severity` | `info`, `warning` or `error` (only the pre-flight check produces `error`) |
| `elements` | the network elements concerned, as `(kind, name)` pairs, e.g. `("branch", "K2")`; kinds are `bus`, `branch`, `source`, `fault`, `bus_type`, `branch_type`, `network` and `switch` (a pandapower switch, by its import label) |

Where you find them:

- **During a solve.** `gi.run_fault` and `gi.solve` collect every package
  warning of the run into `Result.diagnostics`, a list of `gi.Diagnostic`
  records with `code`, `severity`, `category`, `message` and `elements`. The
  record is made even when the warning is filtered out or shown only once, so
  the result always carries its full set; `Network.res_fault` counts them in
  `n_diagnostics`.
- **Around any other call** (importing, creating elements, a thermal check,
  a transient study), collect them yourself with
  `groundinsight.diagnostics.collecting()`:
  `with collecting() as records: ...`. The collection is per thread and per
  asyncio task, so two runs in parallel never see each other's records.
- **Before a solve.** `gi.check_network(net)` returns the problems of a
  network as `gi.Diagnostic` records without raising; its codes are listed
  [below](#codes-of-the-pre-flight-check).
- **In a front end.** The service layer `groundinsight.api` passes the
  records on unchanged as `DiagnosticDTO` (code, severity, category,
  elements, message).

A worked example of all four: [Diagnostics, logging and warnings](../howto/diagnostics.md).

As Python warnings they also follow the usual machinery: `warnings.simplefilter("error", gi.ModelAssumptionWarning)`
turns a category into an exception (useful in CI),
`python -W error::groundinsight.DCLimitWarning` does the same from the command
line, and `logging.captureWarnings(True)` routes them into logging. Renamed
parameters and deprecated attributes raise Python's own `DeprecationWarning`,
which has no code.

## Warning classes

| Code | Category | Severity | Elements | Raised when | What to do |
|---|---|---|---|---|---|
| `GROUNDINSIGHT` | `GroundinsightWarning` | warning | – | Default code of the base class (a `UserWarning`); every package warning is an instance of it. The package raises its subclasses with their own codes. | Filter or catch `gi.GroundinsightWarning` to handle all package warnings at once. |
| `MODEL_ASSUMPTION` | `ModelAssumptionWarning` | warning | – | Default code of the class for results that rest on a modelling assumption or heuristic; the package raises it with the specific codes below. | Read the specific code. |
| `INPUT` | `InputWarning` | warning | – | Default code of the class for input that is incomplete or inconsistent but can be solved; the package raises it with the specific codes below. | Read the specific code. |
| `FREQUENCY_ORDER` | `NetworkFrequencyOrderWarning` | warning | – | `Network.frequencies` is not strictly increasing. The order is kept, but the FFT transient solver maps spectral bins by position. | Sort the list, unless the order is intended. |
| `DC_LIMIT` | `DCLimitWarning` | warning | – | An impedance formula is evaluated at 0 Hz and the limit needs a decision: it has a **pole** (modelled as an open circuit — right for a series capacitance), it **converges only slowly** (the value reached is an approximation of the limit, e.g. `sqrt(f)` or `log(f)` terms), it carries a **finite reactance** (the 0 Hz bin takes the real part and drops the reactance), or it is **zero** (an ideal short: modelled with a stand-in of √ε times the smallest other impedance of the network). Only the 0 Hz bin is affected. | Write reactances as `j*2*pi*f*L` so they vanish at DC; state a known DC value as a term without `f`; give an ideal bond a small resistance or merge its two buses. See [Formula syntax: direct current](formula-syntax.md#direct-current-f-0). |
| `TRANSIENT_MODEL` | `TransientModelWarning` | warning | coupled branches (FFT coupling case) | The transient model is narrower than the input: the **FFT solver ignores mutual coupling** (the EPR comes out higher, i.e. conservative), the **FFT window ends while the response still rings** (the tail wraps around), the active fault carries **scalings other than 1** (both solvers use the waveform as the literal injection), or a branch has **\|M_mutual\| > L_self** (almost always a unit slip). | Use `solver="state_space"` for coupled networks; lengthen `t_end` or pass `zero_pad=True`; scale the waveform itself; check the units of the RLC formulas. |

## Codes raised during a solve

These are raised by `gi.run_fault` and `gi.solve` and stored in
`Result.diagnostics`.

| Code | Category | Severity | Elements | Raised when | What to do |
|---|---|---|---|---|---|
| `PHASE_PROXY` | `ModelAssumptionWarning` | warning | the branches of the mesh, the fault | The phase network is meshed (a ring, parallel cables) and branches of the mesh have no `phase_impedance_formula`, so the phase current is split with proxy impedances (the screen self impedance, or the length). Broken screens are named. | Declare `phase_impedance_formula` on the branch types — the reduced phase impedance, see [Phase impedance for rings and meshes](../howto/types-from-datasheets.md#phase-impedance-for-rings-and-meshes). |
| `THEVENIN_COUPLING` | `ModelAssumptionWarning` | warning | the voltage sources | A Thévenin (voltage) source away from the fault bus reaches the fault while a coupled branch has a mutual impedance. The coupling is driven by the source's Norton current instead of its loop current; in the reference cases the EPR came out 6–38 % too low, i.e. unconservative. | Model the infeed as a current source with the fault current of a short-circuit calculation, or treat the EPR as a lower bound. |
| `SCALINGS_INCOMPLETE` | `InputWarning` | warning | the fault | `Fault.scalings` is a non-empty mapping that misses network frequencies; those are scaled with 1.0 (full contribution). Once per run. Also reported by `gi.check_network`. | Give every network frequency a scaling; `0.0` excludes a frequency. Leaving `scalings` out means 1.0 everywhere, without a warning. |
| `SOURCE_FREQUENCY_GAP` | `InputWarning` | warning | the source | A source that reaches the fault has no value at some network frequencies and contributes 0 A there. Once per source and run. Also reported by `gi.check_network`. | Complete `Source.values`, or accept the gap deliberately. A source with a value at *no* network frequency is an error (`ValueError`). |
| `SOURCE_WITHOUT_PHASE_PATH` | `ModelAssumptionWarning` | warning | the source, the fault | A source reaches the fault through the earthing network but has no phase-conductor connection to the fault bus. Its current is still injected, as if it returned entirely through soil and screens (reduction factor 1 for its share, no coupling). | Check the topology and the `active` flags; a phase path needs branches that carry a phase conductor. |
| `EARTH_GROUP_UNDEFINED` | `ModelAssumptionWarning` | warning | the fault, the fault bus | No electrode carries more than numerical noise (1e-9 of the fault current), so there is no earth-return current: `i_earth` and `value_current` are `None`. | Usually a fault on the bus of the only source, or a network without electrodes; check the excitation. |
| `FAULT_AT_FEEDING_BUS` | `ModelAssumptionWarning` | info | the fault, the fault bus | The fault sits on the bus of every source that feeds it: the fault current closes through that station's own earthing back to the source, nothing is injected into the earthing network, EPR and earth-return current are zero, and both reduction factors are `None`. `U_E` is 0 V; `Z_E` is still the driving-point impedance. Recorded in `Result.diagnostics` only (`groundinsight.diagnostics.note`), never raised as a Python warning. | Nothing to fix for the station itself; to study what the surrounding network sees, place the fault at another bus or add the other infeeds. |
| `EARTH_GROUP_SPREAD` | `ModelAssumptionWarning` | warning | the fault, the fault bus | The electrode currents are spread in angle, so the split into the group that feeds the soil and the group that returns the current is less sharp than usual; `I_E` and `value_current` describe the dominant direction. | Read `r_current` with care; compare with the EPR itself. |
| `REDUCTION_FACTOR_UNDEFINED` | `ModelAssumptionWarning` | warning | the fault, the fault bus | The EPR of the fault bus without coupling is zero to numerical precision (or the solve without coupling fails), so the EPR-based factor `value` has no reference and is `None` at that frequency. | Expected for a fault without a path to earth at that frequency; the EN 50522 factor `value_current` is unaffected. |
| `EARTHING_IMPEDANCE_UNDEFINED` | `ModelAssumptionWarning` | warning | the fault, the fault bus, the buses of its earthing group | No bus connected to the fault bus through screens has an electrode, or the fault bus reaches the earthing network only through a Thévenin loop: `z_earthing` and `Z_G` are `None`. | Give the earthing group an electrode; a Thévenin loop is not an earthing impedance. |
| `PARALLEL_COEFFICIENT_DEFAULT` | `ModelAssumptionWarning` | warning | the sources | Legacy mode `phase_current_mode="paths"`: a source reaches the fault over several paths whose `parallel_coefficient` is left at the default, so each path carries the full current. | Use the default mode `"auto"`, or set the coefficients. |

`DC_LIMIT` and `FREQUENCY_ORDER` can appear in `Result.diagnostics` as well, when an
impedance is evaluated during the run; see [Warning classes](#warning-classes).

## Codes raised outside a solve

These are raised by the function named in the row; collect them with
`collecting()` around the call.

| Code | Category | Severity | Elements | Raised when | What to do |
|---|---|---|---|---|---|
| `SOURCE_FREQUENCY_OUTSIDE_NETWORK` | `InputWarning` | warning | the source | `create_source`, `create_voltage_source` or `Network.add_source` get values at frequencies the network does not solve; they are ignored. Also reported by `gi.check_network`. | Add the frequency to `Network.frequencies`, or drop the value. |
| `THERMAL_DATA_INCOMPLETE` | `InputWarning` | warning | the branches or buses, the fault | `check_conductor_limits` / `check_node_limits`: a type declares half of its thermal data (a material without a cross-section, or the reverse), so `within_limit` is null for it. | Declare both, or neither. |
| `THERMAL_DC_EXCLUDED` | `ModelAssumptionWarning` | warning | the branches or buses, the fault | The 0 Hz component is left out of the adiabatic check, because `m` already carries the DC content of an alternating fault current. A genuine direct current during T_k adds about I_dc² to I_th²; the message quotes the increase. | If a direct current really flows during the fault (DC railway, rectifier), check with the increased I_th. |
| `SC_KAPPA_MIXED` | `ModelAssumptionWarning` | warning | the sources, the fault | `resolve_fault_sc_characteristics`: the sources feeding the fault have different R/X; the non-linear IEC 60909 factors are applied once, with the aggregated κ. | Use the transient solver for an exact result with mixed R/X. |
| `SC_DATA_INCOMPLETE` | `InputWarning` | warning | the sources, the fault | `resolve_fault_sc_characteristics`: sources that carry part of the current have no IEC 60909 data; κ is that of the others. | Set `i_k_a`, `r_to_x` or `kappa` on every source. |
| `SC_KAPPA_MISSING` | `InputWarning` | warning | the buses | `read_shortcircuit_results`: neither `ip_ka` nor a usable R/X is available for a bus. | Complete the zero-sequence data in pandapower (`x0x_max`, `r0x0_max`, `r0_ohm_per_km`, …). |
| `SC_KAPPA_OUT_OF_RANGE` | `InputWarning` | warning | the bus | `read_shortcircuit_results`: pandapower's `ip_ka`/`ikss_ka` imply a κ outside (1, 2]. | Check the pandapower case. |
| `SC_ROW_AMBIGUOUS` | `InputWarning` | warning | the fault, the bus | `apply_shortcircuit_characteristics`: several rows match the fault bus; the first is used. | Pass `pp_bus=` or `bus_name=`. |
| `SC_SPLIT_EQUAL` | `InputWarning` | warning | the sources, the fault | `apply_shortcircuit_characteristics`: the sources inject nothing at the frequency, so I_k'' is split equally. | Set the relative infeed shares first. |
| `SC_VALUE_REPLACED` | `InputWarning` | warning | the fault | `apply_shortcircuit_characteristics` replaced a different `t_k_s` or `n_factor` of the fault with the value of the short-circuit case. | Pass the value explicitly if the previous one was intended. |
| `SC_VOLTAGE_SOURCE_SKIPPED` | `InputWarning` | warning | the source | `apply_shortcircuit_characteristics`: a voltage source keeps its values. | Model the infeed as a current source if its share should be written. |
| `IMPORT_BUS_SKIPPED` | `InputWarning` | warning | the bus | `from_pandapower` / `preview_pandapower_import`: a bus has no usable `vn_kv`. | Fix `vn_kv` in pandapower. |
| `IMPORT_LINE_SKIPPED` | `InputWarning` | warning | the branch | A line is a self-loop, or its length cannot be imported. | Fix the line in pandapower. |
| `IMPORT_LENGTH_DEFAULT` | `InputWarning` | warning | the branch | A line has no usable `length_km` and gets the fallback of 1 km. | Set `length_km`; every impedance of the branch scales with it. |
| `IMPORT_SWITCH_NOT_IMPORTED` | `InputWarning` | warning | the buses the switches would bond | Closed bus-to-bus switches were not imported because `ImportDefaults.switch_branch_type` is not set; a bus behind such a switch is isolated and reads 0 V. | Set `switch_branch_type`. |
| `IMPORT_LINE_SWITCH_OPEN` | `InputWarning` | info | the branch, the switches | `from_pandapower`: a line has an open line switch (`et="l"`) and is imported with an open phase conductor (`phase_impedance_formula = "nan"`); it carries no fault current, its screen stays connected at both ends. | Nothing, if the screen is bonded at both stations; set the branch inactive if it is interrupted as well. |
| `IMPORT_ELEMENT_NOT_IMPORTED` | `InputWarning` | warning | – | Transformers and impedance elements are not modelled and were left out. | Check the preview; model the connection by hand if it matters. |
| `IMPORT_VOLTAGE_LEVEL_MISMATCH` | `InputWarning` | warning | the bus type | The default bus type declares another voltage level than `voltage_level_kV`. | Use a bus type written for the imported level. |
| `IMPORT_EMPTY` | `InputWarning` | warning | the network | The import produced no bus or no branch — usually a wrong `voltage_level_kV`. | Check `voltage_level_kV` against `net.bus.vn_kv`. |
| `TRANSIENT_SOURCE_NOT_CONNECTED` | `InputWarning` | warning | the sources | `TransientStudy.solve(solver="fft")`: a source with a waveform has no galvanic connection to the fault bus and injects nothing. | Check the topology. |
| `TRANSIENT_BRANCH_SKIPPED` | `InputWarning` | warning | the branches | State-space solver: branches without `R_self`/`L_self` are left out. | Declare `R_self_formula` and `L_self_formula` on the branch types. |
| `TRANSIENT_COUPLING_SKIPPED` | `TransientModelWarning` | warning | the voltage sources | State-space solver: the coupling is modelled for current sources only. | Use current sources for coupled studies. |
| `TRANSIENT_RESISTIVE_COUPLING_IGNORED` | `TransientModelWarning` | warning | the branches | State-space solver: `R_mutual`/`M_mutual` on branches without `L_self` are ignored. | Give those branches an inductance. |
| `TRANSIENT_PHASE_SPLIT` | `ModelAssumptionWarning` | warning | the coupled branches | State-space solver on a meshed network: the phase split is taken at `f_eval` and depends on frequency here, so a DC offset and harmonics are split as at `f_eval` (below 1 % of the peak on a mixed cable/line ring). | Accept, or compare with `run_fault` at the frequencies of interest. |
| `PROJECT_FILE_LEGACY_ENCODING` | `InputWarning` | warning | the network | `load_network_from_json` read a file that is not UTF-8 (written by 0.6.0 or earlier on a non-UTF-8 system) with the platform encoding. | Save it again; the new file is UTF-8. |
| `LEGACY_EARTHING_DEFINITION` | `InputWarning` | info | the fault | A result stored before `U_E` and `Z_E` were redefined at the fault bus is loaded: its old values moved to `u_earthing_group` / `z_earthing_group`, and `U_E`, `Z_E` and `Z_G` stay empty. A record in `Result.diagnostics`, not a Python warning. | Run the fault again. |

## Codes of the pre-flight check

`gi.check_network(net, fault=None)` returns these as records of category
`InputWarning` (the three codes it shares with the solve — `SCALINGS_INCOMPLETE`,
`SOURCE_FREQUENCY_GAP`, `SOURCE_FREQUENCY_OUTSIDE_NETWORK` — are listed above).
An `error` means that `run_fault` would raise or would return an answer that
does not describe the network; a `warning` that the network solves but part of
the input has no effect.

| Code | Category | Severity | Elements | Raised when | What to do |
|---|---|---|---|---|---|
| `NOT_A_NETWORK` | `InputWarning` | error | – | The argument is not a `Network`. | Pass a network. |
| `CHECK_FAILED` | `InputWarning` | error | the network | A check raised on an unforeseen malformation; the other checks still ran. | Read the message; it names the check. |
| `ELEMENT_INVALID` | `InputWarning` | error | the entry | An entry of `buses`, `branches`, `sources` or `faults` is not an element of the expected type. | Rebuild the entry through the API. |
| `FREQUENCIES_INVALID` | `InputWarning` | error | the network | Empty frequency list, duplicates, non-finite or negative values (an in-place edit bypasses the validator). | Assign a valid list to `Network.frequencies`. |
| `NO_ACTIVE_BUS` | `InputWarning` | error | the network | No bus is active. | Activate the buses. |
| `NO_SOURCE` | `InputWarning` | error | the network | The network has no source. | Add a source. |
| `NO_FAULT` | `InputWarning` | error | the network | The network has no fault. | Add a fault. |
| `FAULT_UNKNOWN` | `InputWarning` | error | the fault | The `fault=` argument names no fault of the network. | Check the name. |
| `BRANCH_UNKNOWN_BUS` | `InputWarning` | error | the branch | A branch end names a bus that does not exist; the solver would drop the branch. | Fix the reference. |
| `SOURCE_UNKNOWN_BUS` | `InputWarning` | error | the source | A source names a bus that does not exist; the solver would ignore it. | Fix the reference. |
| `FAULT_UNKNOWN_BUS` | `InputWarning` | error | the fault | A fault names a bus that does not exist. | Fix the reference. |
| `FAULT_BUS_INACTIVE` | `InputWarning` | error | the fault, the bus | A fault sits on an inactive bus. | Activate the bus or move the fault. |
| `SOURCE_BUS_INACTIVE` | `InputWarning` | warning | the source, the bus | A source sits on an inactive bus and injects nothing. | Activate the bus if the source should feed. |
| `IMPEDANCE_MISSING` | `InputWarning` | error | the element | An element of the nodal system has no impedance at a network frequency. | Re-derive it (assign the type again) or complete the dictionary. |
| `FORMULA_ERROR` | `InputWarning` | error | the element | No stored impedance, and the type formula cannot be evaluated. | Fix the formula; see [Formula syntax](formula-syntax.md). |
| `IMPEDANCE_NOT_PASSIVE` | `InputWarning` | error | the element | An impedance that is inverted is zero, too small to invert or has a negative real part. | Check the formula at the lowest ρ, frequency and length of the model. |
| `IMPEDANCE_NOT_FINITE` | `InputWarning` | error | the element | An impedance is NaN at a network frequency. | Fix the formula or the stored value. |
| `SOURCE_FREQUENCY_NONE` | `InputWarning` | error | the source | A source has a value at none of the network frequencies (`run_fault` raises a `ValueError`). | Give it values at the network frequencies. |
| `SOURCE_VALUE_NOT_FINITE` | `InputWarning` | error | the source | A source value is NaN or infinite at a network frequency. | Fix the value. |
| `FAULT_NOT_REACHED` | `InputWarning` | warning | the fault | No source reaches the fault over active elements; every potential is 0 V. | Correct for an outage that islands the fault bus; otherwise check the topology. |
| `ISOLATED_BUS` | `InputWarning` | warning | the bus | An active bus has no active branch to another active bus. | Connect it or deactivate it. |
| `FLOATING_ISLAND` | `InputWarning` | error | the buses of the island | Buses joined by grounding conductors have no electrode and therefore no reference to earth; their potential is undefined and `run_fault` refuses the network. | Give one bus of the group an electrode. |

## Errors

Conditions that leave no meaningful answer raise instead of warning:

- `ValueError` — invalid input or a violated model rule: an unknown element
  name, a formula the guard refuses, a non-passive impedance, a source without
  a value at any network frequency, a group of buses without a path to earth
  ("Singular admittance matrix … no path to reference earth"), a project file
  with a newer `schema_version` than the running release supports.
- `RuntimeError` — a state that does not allow the call: no started database
  session for the module-level helpers, a database written by an older
  release opened with `migrate=False`, `res_fault` for a fault without stored
  result. `gi.StudyCancelled`, raised when a study's `should_cancel()`
  answers true, is a `RuntimeError` too.
- `TypeError` — a value of the wrong type for a model field, or a callback
  that is not callable.
- `ImportError` — a function of the optional `pandapower` extra without
  pandapower installed; the message names the extra.
