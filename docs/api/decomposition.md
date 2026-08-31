# Network decomposition at the fault location

Split the grounding network at the faulted station into named directions, and
quantify each of them: what parallel grounding impedance it contributes, and
what share of the fault current leaves through it.

```
parallel impedance left --- fault location --- parallel impedance right
```

The impedances come from source-free current division and add up to the
driving-point impedance of the whole network exactly, in a radial network and in
a ring alike. The currents come from a solved fault and describe how the fault
current actually divides. See [Concepts](../concepts.md#splitting-the-network-at-the-fault)
for the definitions and their limits.

::: groundinsight.analysis.decomposition
    options:
      members:
        - Cut
        - CutAnalysis
        - analyze_cuts
