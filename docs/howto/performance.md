# Large and meshed networks

How large can a network get, what costs the time, and what to avoid in meshed
grids. This guide summarises the measurements of the current release and
shows the one setting that turns a fast solve into an endless one. The script
is [`performance.py`](../snippets/performance.py); its output is at the
[end of the page](#output).

## What a solve costs

`run_fault` assembles the nodal admittance matrix of the earthing network and
the reduced matrix of the phase-conductor network straight into sparse form
and factorises each once per frequency; the factorisation is reused for
every source and for the second solve without coupling that the EPR-based
reduction factor needs.
The cost grows almost linearly with the number of buses (exponent 1.12 from
1000 to 8000 buses), and so does the memory. Measured on the development
container (CPU time, median of three interleaved runs, seven frequencies;
from the change log):

| Network | Before → after | |
|---|---|---|
| ring, 1000 buses | 1.06 → 0.12 s | `run_fault` |
| ring, 2000 buses | 4.04 → 0.22 s | `run_fault` |
| ring, 2500 buses | 7.8 → 0.30 s | `run_fault` |
| radial, 2000 buses, three sources | 18.1 → 0.27 s | `run_fault` |
| 40 × 40 mesh, four sources | 7.0 → 0.35 s | `run_fault` |
| radial, 2000 buses, three sources | 17.3 s → 0.017 s | phase-conductor stage alone |
| ring, 2000 buses | 5.6 → 0.032 s | `bus_response` |
| ring, 2000 buses | 2.8 → 0.031 s | `analyze_cuts` |
| ring, 1000 buses, every bus scaled | 5.47 s → 0.59 s | `find_max_rho_scaling` |
| 40 × 40 mesh | 11.3 s → 1.86 s | `find_max_rho_scaling` |
| ring, 1000 buses, ten fault locations | 23.9 s → 1.42 s | `find_max_rho_f_scaling` |
| ring, 4000 buses | 0.21 s → 0.035 s | `res_buses` |
| ring, 5000 buses | 2.02 s → 1.51 s | building the network |

At 4000 buses a ring solves in 0.53 s, a radial feeder with three sources in
0.57 s and a 50 × 50 mesh in 0.59 s; the dense kernel of earlier releases did
not fit into 2 GB there. Peak memory of a 1000-bus ring went from 146 to 25 MB.
The times are for this machine; yours scale with it.

## What still costs time

- **Building a large network.** Every element evaluates its formulas at every
  frequency when it is created (with SymPy, cached per formula) and checks the
  values: a 5000-bus ring takes 1.5 s to build, a 50 000-bus ring 16.7 s.
  Build once, then edit (`net.update_bus`, `net.update_branch`) instead of
  rebuilding.
- **The result objects.** On the largest networks the per-element result
  objects are the largest remaining share of a run: 0.2–0.3 s of the 0.53 s
  of the 4000-bus ring. The frames `res_buses` and `res_branches` are built
  column-wise.
- **The FFT transient solver** solves a dense matrix per frequency bin; its
  memory is bounded by `fft_block_bytes`
  ([Transient studies](transient.md#memory-and-time)).
- **Path enumeration** — see below.

## Studies reuse their first solve

`find_max_rho_scaling` and `find_max_rho_f_scaling` evaluate the same fault
for twenty or more trial values of the electrode impedances. Only the first
trial per fault location is a full solve; every later one assembles the
matrix values again with the same code, factorises them and solves the
injection of the first solve — bit for bit what a full `run_fault` would give,
so the searches take the same steps and return the same answer
([Admissible soil resistivity](inverse-rho.md)). `run_sweep` and
`run_outage_study` solve each point in full. Different networks can be solved
in parallel threads, one network per thread; threads share one interpreter,
so for CPU-bound studies in parallel use processes — for example the process
backend of the [service layer's job runner](service-layer.md#jobs).

## Do not enumerate paths in meshed networks

The default `phase_current_mode="auto"` solves the phase-conductor network as
a sparse system: it only needs to know which sources reach the fault, a
breadth-first search, and `net.paths` stays empty after a solve. The legacy
mode `phase_current_mode="paths"`, the transient state-space solver and an
explicit `gi.create_paths(net)` instead **enumerate every simple path** from
each source to the fault — and their number grows exponentially with the
number of meshes, not with the number of buses: 2, 12, 184 and 8512 paths
between the opposite corners of a 2 × 2 to 5 × 5 grid, 1 262 816 for 6 × 6,
which took about a minute. Before the default mode stopped enumerating, a
6 × 6 grid spent 55–68 s on the paths and milliseconds on the solve; now it
solves in 0.09 s.

Use `create_paths` only for radial or lightly meshed networks, when you want
the paths as data. The search refuses to produce more than
`max_paths` paths (100 000 by default) and names the bus pair instead of
running for hours; set the limit per network with `PathfinderSettings`:
`net.pathfinder_settings = gi.PathfinderSettings(max_paths=...)`
([Pathfinder](../api/pathfinder.md#settings-and-memo-per-network)).

```python
--8<-- "docs/snippets/performance.py:types"
```

```python
--8<-- "docs/snippets/performance.py:ring"
```

```python
--8<-- "docs/snippets/performance.py:grid"
```

```python
--8<-- "docs/snippets/performance.py:cap"
```

## Output

```text title="Output of performance.py"
ring: 500 buses, EPR at S250 = 159.44 V, paths enumerated: 0
ring diagnostics: ['EARTH_GROUP_SPREAD']
2x2 grid: 2 simple paths from corner to corner
3x3 grid: 12 simple paths from corner to corner
4x4 grid: 184 simple paths from corner to corner
5x5 grid: 8512 simple paths from corner to corner
capped at 1000 paths: Enumerating the simple paths from bus 'B0_0' to bus 'B4_4' exceeded the limit of 1000 paths
```

The path counts are the known numbers of self-avoiding corner-to-corner
paths in a square grid (OEIS A007764); the test suite compares them. Along a
ring of 250 km the electrode currents turn in phase, so the split into the
stations that feed the soil and those that return the current is less sharp
than usual — the solve says so with `EARTH_GROUP_SPREAD`, and `I_E` and the
current-based reduction factor describe the dominant direction
([Warnings and diagnostics](../reference/warnings.md#codes-raised-during-a-solve)).
