# Pathfinder

Depth-first search over the undirected bus/branch graph that enumerates every
simple path from a source bus to the active fault bus. The resulting ordered
branch lists are used by `ElectricalNetwork` to inject the mutual-coupling
Norton sources with the correct sign.

::: groundinsight.pathfinder
