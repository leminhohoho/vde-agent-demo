"""Significance helpers (spec §5.5): Wilson for rates, seeded bootstrap for median DOM."""

from __future__ import annotations

from decimal import Decimal

import pytest

from ..candidates.stats import (
    Interval,
    bootstrap_median_interval,
    intervals_overlap,
    median,
    significant_median_difference,
    significant_rate_difference,
    wilson_interval,
)
from .builders import semantic

P = semantic().params


def ds(*values: int) -> list[Decimal]:
    return [Decimal(v) for v in values]


def test_median_of_odd_and_even_samples() -> None:
    assert median(ds(5, 1, 3)) == Decimal(3)
    assert median(ds(4, 1, 3, 2)) == Decimal("2.5")
    with pytest.raises(ValueError):
        median([])


def test_wilson_interval_matches_the_textbook_values() -> None:
    ci = wilson_interval(5, 10, Decimal(95))
    assert round(ci.low, 4) == Decimal("0.2366") and round(ci.high, 4) == Decimal("0.7634")
    zero = wilson_interval(0, 20, Decimal(95))
    assert zero.low == 0 and round(zero.high, 4) == Decimal("0.1611")


def test_wilson_rejects_empty_samples_and_unsupported_confidence() -> None:
    with pytest.raises(ValueError):
        wilson_interval(0, 0, Decimal(95))
    with pytest.raises(ValueError, match="confidence"):
        wilson_interval(1, 10, Decimal(97))


def test_bootstrap_is_deterministic_for_a_seed() -> None:
    values = ds(10, 40, 70, 90, 120, 160, 200, 250, 300, 310, 400, 20)
    a = bootstrap_median_interval(values, P.bootstrap_iterations, P.bootstrap_seed, Decimal(95))
    b = bootstrap_median_interval(values, P.bootstrap_iterations, P.bootstrap_seed, Decimal(95))
    assert a == b
    assert a.low <= median(values) <= a.high


def test_bootstrap_of_a_constant_sample_is_that_constant() -> None:
    assert bootstrap_median_interval(ds(7, 7, 7), 200, 1, Decimal(95)) == Interval(Decimal(7), Decimal(7))


def test_touching_intervals_overlap() -> None:
    assert intervals_overlap(Interval(Decimal(1), Decimal(2)), Interval(Decimal(2), Decimal(3)))
    assert not intervals_overlap(Interval(Decimal(1), Decimal(2)), Interval(Decimal("2.01"), Decimal(3)))


def test_clearly_separated_medians_with_a_large_effect_are_significant() -> None:
    slow = ds(*range(200, 212))
    fast = ds(*range(100, 112))
    assert significant_median_difference(slow, fast, P)


def test_tc22_overlapping_intervals_are_not_significant_even_with_a_large_median_gap() -> None:
    west = ds(10, 30, 60, 90, 110, 130, 150, 170, 200, 240, 280, 320)
    east = ds(5, 20, 40, 70, 90, 110, 120, 140, 160, 200, 230, 260)
    assert abs(median(west) - median(east)) >= P.min_effect_size_days
    assert not significant_median_difference(west, east, P)


def test_a_separated_but_small_effect_is_not_significant() -> None:
    a = ds(*([110] * 12))
    b = ds(*([100] * 12))  # 10 days < min_effect_size_days (15)
    assert not significant_median_difference(a, b, P)


def test_rate_difference_is_significant_only_when_wilson_intervals_separate() -> None:
    assert significant_rate_difference(18, 20, 2, 20, P)
    assert not significant_rate_difference(6, 12, 4, 12, P)
