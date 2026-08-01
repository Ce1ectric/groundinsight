# Plotting

Matplotlib bar-plot helpers that consume a `Result` instance and
visualise earth potential rise, branch currents and bus currents
either for a specific frequency or as RMS across all frequencies.

## Physical / modelling context

A fault calculation produces three families of derived
quantities that are routinely inspected:

- **Earth potential rise (EPR)** — the magnitude of
  $\underline{u}(f)$ per bus, both per frequency and as the
  RMS $\sqrt{\sum_f |u(f)|^2}$ across all frequencies. The
  RMS view is the touch-voltage proxy under EN 50522.
- **Branch currents** — the per-frequency phasor of the
  current flowing through each grounding-conductor branch,
  signed in the branch traversal direction. This is the
  shield-current pattern that determines reduction factor
  and EMC effects.
- **Bus currents** — the *injected* current per bus, i.e.
  the residual that returns through the local earth path
  $1/Z_E$. The sum over all buses equals the fault current
  scaled by $r$.

For the time-domain transient solver
([`groundinsight.simulation.transient`](transient.md)) the same
plot helpers are mirrored with a `_transient` suffix and consume
a `ResultTransient` instead.

## Example

```python
import groundinsight as gi

# Assume `net` has been solved via gi.run_fault(...).
result = net.results["fault1"]

# EPR bar plot at 50 Hz and as RMS over all frequencies
gi.plot_bus_voltages(result=result, frequencies=[50.0],
                     title="EPR @ 50 Hz")
gi.plot_bus_voltages(result=result,
                     title="EPR (RMS over all frequencies)")

# Branch and bus currents
gi.plot_branch_currents(result=result, frequencies=[50.0],
                        title="Shield I")
gi.plot_bus_currents(result=result, title="Bus injection (RMS)")

# Transient counterpart (after a TransientStudy.solve(...))
gi.plot_epr_transient(result=result_t, title="EPR transient")
gi.plot_branch_current_transient(result=result_t, title="Shield I(t)")
```

All helpers return the `matplotlib.figure.Figure` they create, so
they integrate cleanly with notebook display and with
`fig.savefig(...)` calls.

## Only computed frequencies can be plotted

`frequencies=` selects from the frequencies the result actually
contains; asking for one that was never computed raises a
`KeyError` naming both the requested and the available values:

```python
net = gi.create_network(name="net", frequencies=[50.0])
...
gi.plot_bus_voltages(result=result, frequencies=[250.0])
# KeyError: Frequency [250.0] not present in 'uepr_freq' of any bus;
#           the result was computed for [50.0] Hz. ...
```

This is not pedantry about arguments. A missing frequency used to
be substituted with `0.0`, and a bar of height zero on an EPR plot
is a *statement*: "the fifth harmonic causes no earth potential
rise at this station". Nothing in the figure distinguished that
from "250 Hz was never part of the calculation". A bar of height
zero now always means a measured zero.

The same check covers the partial case — a frequency present on
some buses and missing on others — because a bar group that mixes
measured values with substituted zeros is worse still.

## Figure ownership

By default every helper creates its figure through `pyplot` and
leaves it open; the caller owns it. In a notebook that is what you
want. In a loop — a soil-resistivity sweep, one plot per scenario —
it is not, and matplotlib warns after the twentieth figure. Pass
`close=True` and the helper releases the figure it created before
returning:

```python
for rho in (50.0, 100.0, 500.0, 1000.0):
    ...
    fig = gi.plot_bus_voltages(result=result, title=f"rho = {rho}",
                               close=True)
    fig.savefig(f"epr_{rho:.0f}.png")
```

The returned figure is still complete — every axis, bar and label
is on it, and `savefig` works exactly as before. `close=True` only
unregisters it from `pyplot`, so it is collected once the last
reference goes out of scope. `plt.close(fig)` after the call
remains equivalent.

## Drawing into an existing axis

Pass `ax=` to draw into an axis you created yourself. The helper
then leaves the surrounding figure alone: it applies no
`tight_layout`, closes nothing, and returns *your* figure rather
than a new one. This is what makes two scenarios comparable side
by side:

```python
import matplotlib.pyplot as plt

fig, axes = plt.subplots(1, 2, figsize=(16, 5), sharey=True)
gi.plot_bus_voltages(result=base,   ax=axes[0], title="base case")
gi.plot_bus_voltages(result=outage, ax=axes[1], title="cable out")
fig.tight_layout()
```

Two combinations are rejected rather than silently ignored:

| Combination | Why it raises |
|---|---|
| `ax=` with `figsize=` | The figure already exists and may hold other panels, so the size cannot be honoured. Size it yourself: `plt.subplots(figsize=...)`, or `ax.figure.set_size_inches(...)`. |
| `ax=` with `close=True` | `close=` releases the figure *this call* created. With `ax=` the figure belongs to you, and closing it would take every sibling panel with it. |

A `figsize` that cannot be used raises as well, rather than being
replaced by the default: matplotlib accepts `figsize=(0, 0)` when
the figure is created and only fails much later, when it is drawn
or saved, so the traceback would point at `savefig` instead of at
the call responsible.

## API reference

::: groundinsight.plotting
