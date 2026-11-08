# tests/test_audit_pass19_earth_current.py
"""The earth-return split: linear time (P3) and robustness against rounding (P9).

Finding IDs: P3 -- ``split_earth_currents`` rebuilt ``set(chosen)`` for every
bus and scanned the half-plane window with a scalar ``while`` loop, so a
20 000-bus network spent 12 s per frequency in the split alone. The fix must
not move a single bit of the result, so the previous implementation is kept
here verbatim as the oracle and compared with ``==``.

P9 / F14 -- the group, ``I_E`` and the factors built on it were decided by
rounding noise in named configurations; see the second half of the module.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

import numpy as np
import pytest

from groundinsight.utils.earth_current import EarthCurrentSplit, split_earth_currents


# ---------------------------------------------------------------------------
# Oracle: the implementation before P3, copied verbatim (do not "improve" it)
# ---------------------------------------------------------------------------


def _old_split_earth_currents(
    electrode_currents: Dict[str, complex],
    *,
    reference_bus: Optional[str] = None,
    feeding_buses: Optional[List[str]] = None,
) -> Optional[EarthCurrentSplit]:
    names = [name for name, value in electrode_currents.items() if value != 0]
    if not names:
        return None

    values = np.array([complex(electrode_currents[n]) for n in names], dtype=complex)
    total_magnitude = float(np.abs(values).sum())

    if feeding_buses is not None:
        chosen = [n for n in names if n in set(feeding_buses)]
        i_earth = complex(
            sum((complex(electrode_currents[n]) for n in chosen), 0.0 + 0.0j)
        )
    else:
        chosen, i_earth = _old_best_half_plane(names, values)
        rest_names = [n for n in names if n not in set(chosen)]
        if _old_should_swap(chosen, rest_names, reference_bus):
            chosen, rest_names = rest_names, chosen
            i_earth = -i_earth

    rest = [n for n in names if n not in set(chosen)]
    separation = (
        float(abs(i_earth) / (0.5 * total_magnitude)) if total_magnitude > 0 else 0.0
    )
    return EarthCurrentSplit(
        i_earth=i_earth,
        feeding_buses=sorted(chosen),
        returning_buses=sorted(rest),
        separation=separation,
    )


def _old_should_swap(
    chosen: List[str], rest: List[str], reference_bus: Optional[str]
) -> bool:
    if not rest:
        return False
    if reference_bus is not None:
        if reference_bus in chosen:
            return False
        if reference_bus in rest:
            return True
    if len(rest) != len(chosen):
        return len(rest) < len(chosen)
    return sorted(rest) < sorted(chosen)


def _old_best_half_plane(
    names: List[str], values: np.ndarray
) -> Tuple[List[str], complex]:
    n = len(values)
    if n == 1:
        return list(names), complex(values[0])

    angles = np.angle(values)
    order = np.argsort(angles)
    sorted_angles = angles[order]
    sorted_values = values[order]

    doubled = np.concatenate([sorted_values, sorted_values])
    doubled_angles = np.concatenate([sorted_angles, sorted_angles + 2.0 * np.pi])
    prefix = np.concatenate([[0.0 + 0.0j], np.cumsum(doubled)])

    best_sum = 0.0 + 0.0j
    best_span = (0, 0)
    j = 0
    for i in range(n):
        if j < i:
            j = i
        limit = sorted_angles[i] + np.pi
        while j < i + n and doubled_angles[j] < limit:
            j += 1
        candidate = complex(prefix[j] - prefix[i])
        if abs(candidate) > abs(best_sum):
            best_sum = candidate
            best_span = (i, j)

    start, stop = best_span
    chosen = [names[order[k % n]] for k in range(start, stop)]
    return chosen, best_sum


def _same(a: Optional[EarthCurrentSplit], b: Optional[EarthCurrentSplit]) -> bool:
    """Bit-level equality of two splits (``==`` on every field)."""
    if a is None or b is None:
        return a is None and b is None
    return (
        a.i_earth == b.i_earth
        and a.feeding_buses == b.feeding_buses
        and a.returning_buses == b.returning_buses
        and a.separation == b.separation
    )


def _random_sets(count: int, seed: int):
    """Random phasor sets with zeros, duplicate angles, anchors and pins."""
    rng = np.random.default_rng(seed)
    for _ in range(count):
        n = int(rng.integers(1, 200))
        values = rng.normal(size=n) + 1j * rng.normal(size=n)
        values[rng.random(n) < 0.1] = 0
        if n > 3 and rng.random() < 0.3:
            values[1] = values[0]                      # duplicate angle
        if n > 4 and rng.random() < 0.2:
            values[2] = -values[0]                     # exactly opposed pair
        names = [f"B{k}" for k in range(n)]
        mode = rng.random()
        if mode < 0.6:
            reference = names[int(rng.integers(n))]    # may carry zero current
        elif mode < 0.7:
            reference = "not-a-bus"
        else:
            reference = None
        pinned = None
        if rng.random() < 0.15:
            pinned = [nm for nm in names if rng.random() < 0.4]
        yield dict(zip(names, values)), reference, pinned


# ---------------------------------------------------------------------------
# P3 -- linear time, bit for bit the same result
# ---------------------------------------------------------------------------


def test_the_split_is_bit_identical_to_the_previous_implementation():
    """2000 random phasor sets: every field equal with ``==``."""
    mismatches = []
    for k, (currents, reference, pinned) in enumerate(_random_sets(2000, seed=20261108)):
        old = _old_split_earth_currents(currents, reference_bus=reference, feeding_buses=pinned)
        new = split_earth_currents(currents, reference_bus=reference, feeding_buses=pinned)
        if not _same(old, new):
            mismatches.append(k)
    assert mismatches == []


def test_the_documented_examples_keep_their_answers():
    """The hand-made cases of the module docstring, against the oracle."""
    cases = [
        {"A": 10 + 0j, "B": 5 + 0j, "C": -15 + 0j},
        {"A": 10 + 0j, "B": 10j, "C": -10 - 10j},
        {"A": 3 + 4j},
        {"A": 1 + 0j, "B": -1 + 0j},
        {"A": 0j, "B": 2 + 0j, "C": -2 + 0j},
    ]
    for currents in cases:
        for reference in (None, "A", "C"):
            assert _same(
                _old_split_earth_currents(currents, reference_bus=reference),
                split_earth_currents(currents, reference_bus=reference),
            )


def _two_clusters(n: int) -> Dict[str, complex]:
    """A feeding and a returning cluster, the realistic shape of the input."""
    rng = np.random.default_rng(1)
    magnitudes = np.abs(rng.normal(size=n) + 1j * rng.normal(size=n))
    values = np.where(
        np.arange(n) < n // 3,
        magnitudes * np.exp(1j * 0.3),
        -magnitudes * np.exp(1j * 0.25),
    )
    return {f"B{k}": complex(v) for k, v in enumerate(values)}


def test_a_large_split_is_still_bit_identical():
    """n = 4000 is the largest size at which the quadratic oracle still runs
    in well under a second."""
    currents = _two_clusters(4000)
    assert _same(
        _old_split_earth_currents(currents, reference_bus="B0"),
        split_earth_currents(currents, reference_bus="B0"),
    )


@pytest.mark.slow
def test_the_split_is_not_quadratic():
    """n = 20 000 took 12 s per call before P3; the bound leaves a factor of
    about fifty to the measured time so that load on the machine cannot
    make the test flaky."""
    currents = _two_clusters(20_000)
    start = time.perf_counter()
    split = split_earth_currents(currents, reference_bus="B0")
    elapsed = time.perf_counter() - start
    assert split is not None
    assert elapsed < 0.5
