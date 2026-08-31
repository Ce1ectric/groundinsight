# Parameter sweeps

Solve one fault once per parameter combination and stack the results into
long-format frames that carry the parameters as columns. This is what the
statistics below operate on — until such a frame exists there is nothing to
summarise, because every accessor on `Network` reports a single solve.

`rho_f_points` builds the points from a catalogue of five-parameter rho-f
vectors, which is the form `groundfield` exports.

::: groundinsight.simulation.sweep
    options:
      members:
        - SweepPoint
        - SweepResult
        - rho_f_points
        - run_sweep
