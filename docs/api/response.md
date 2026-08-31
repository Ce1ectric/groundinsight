# Characterising a location without its electrode

What does the network do *at* a given station, independently of the electrode
installed there?

Adding an electrode is a rank-one change to the nodal matrix, so every nodal
voltage is a Möbius function of its admittance. Two solves with the electrode
removed therefore determine the response for **every** electrode, exactly and at
no further cost — measured against genuine solves from 0.05 Ω to 500 Ω and at a
complex value, the closed form agrees to `3e-15` relative.

The two extremes — no electrode and an ideal one — are the endpoints of that
curve, and both are exact limits rather than numerical stand-ins. `Z_network`,
the driving-point impedance with the local electrode removed, is the
site-independent number the whole analysis is built around.

See [Concepts](../concepts.md#characterising-a-location-without-its-electrode).

::: groundinsight.analysis.response
    options:
      members:
        - BusResponse
        - bus_response
