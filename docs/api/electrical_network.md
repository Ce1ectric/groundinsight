# Electrical network

Numerical core of `groundinsight`. `ElectricalNetwork` assembles the nodal
admittance matrix $Y(f)$ per frequency, fills the right-hand side with source
and mutual-coupling Norton currents, solves the linear system via sparse LU
and derives branch currents, reduction factors and grounding impedances.

::: groundinsight.electrical_network
