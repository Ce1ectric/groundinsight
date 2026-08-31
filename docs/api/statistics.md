# Statistics and classification

Two thin functions on top of a long-format result frame: named quantiles next to
the extremes, and a class column that turns a continuous quantity into the bands
a study argues in.

There is deliberately no built-in table of admissible values. Touch-voltage
limits depend on the clearing time, the standard edition and the additional
resistances assumed; a plausible-looking constant baked in here would be carried
into a result without ever being checked. `classify` takes its edges from the
caller, who can cite them.

::: groundinsight.analysis.statistics
    options:
      members:
        - summarize
        - classify
