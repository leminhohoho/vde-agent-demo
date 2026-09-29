"""Significance helpers (spec §5.5): the `significant` flag of a candidate.

Pure, `Decimal` throughout, deterministic: the bootstrap uses its own `random.Random(seed)` with
the seed from config (luật 11), never the global generator.

- Rates: Wilson score interval.
- Median DOM: percentile bootstrap interval.
- `significant` = the two intervals do not overlap (touching counts as overlap) and, for DOM, the
  median gap is at least `min_effect_size_days`. Rates have no effect-size bound in the spec, so
  only the interval test applies to them (docs/OPEN_QUESTIONS.md Q13).
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from ..settings import SemanticParams

Z_BY_CONFIDENCE_PCT: dict[Decimal, Decimal] = {
    Decimal(90): Decimal("1.6448536269514722"),
    Decimal(95): Decimal("1.9599639845400542"),
    Decimal(99): Decimal("2.5758293035489004"),
}
"""Two-sided standard normal quantiles (mathematical constants, not business thresholds)."""


@dataclass(frozen=True)
class Interval:
    low: Decimal
    high: Decimal


def median(values: Sequence[Decimal]) -> Decimal:
    if not values:
        raise ValueError("median of an empty sample")
    xs = sorted(values)
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2


def _z(confidence_pct: Decimal) -> Decimal:
    try:
        return Z_BY_CONFIDENCE_PCT[confidence_pct]
    except KeyError:
        supported = ", ".join(str(c) for c in Z_BY_CONFIDENCE_PCT)
        raise ValueError(f"unsupported confidence {confidence_pct}%; supported: {supported}") from None


def wilson_interval(successes: int, n: int, confidence_pct: Decimal) -> Interval:
    if n <= 0:
        raise ValueError("Wilson interval of an empty sample")
    z = _z(confidence_pct)
    nd = Decimal(n)
    p = Decimal(successes) / nd
    z2 = z * z
    denom = 1 + z2 / nd
    center = (p + z2 / (2 * nd)) / denom
    half = z * (p * (1 - p) / nd + z2 / (4 * nd * nd)).sqrt() / denom
    low = Decimal(0) if successes == 0 else max(Decimal(0), center - half)
    high = Decimal(1) if successes == n else min(Decimal(1), center + half)
    return Interval(low, high)


def bootstrap_median_interval(values: Sequence[Decimal], iterations: int, seed: int, confidence_pct: Decimal) -> Interval:
    if not values:
        raise ValueError("bootstrap of an empty sample")
    rng = random.Random(seed)
    xs = list(values)
    n = len(xs)
    medians = sorted(median([xs[rng.randrange(n)] for _ in range(n)]) for _ in range(iterations))
    tail = (1 - confidence_pct / 100) / 2
    lo = int(tail * iterations)
    hi = max(lo, math.ceil((1 - tail) * iterations) - 1)
    return Interval(medians[lo], medians[min(hi, iterations - 1)])


def intervals_overlap(a: Interval, b: Interval) -> bool:
    return not (a.high < b.low or b.high < a.low)


def significant_median_difference(group: Sequence[Decimal], rest: Sequence[Decimal], params: SemanticParams) -> bool:
    if not group or not rest:
        return False
    if abs(median(group) - median(rest)) < params.min_effect_size_days:
        return False
    conf = params.significance_confidence_pct
    a = bootstrap_median_interval(group, params.bootstrap_iterations, params.bootstrap_seed, conf)
    b = bootstrap_median_interval(rest, params.bootstrap_iterations, params.bootstrap_seed, conf)
    return not intervals_overlap(a, b)


def significant_rate_difference(group_hits: int, group_n: int, rest_hits: int, rest_n: int, params: SemanticParams) -> bool:
    if group_n <= 0 or rest_n <= 0:
        return False
    conf = params.significance_confidence_pct
    return not intervals_overlap(wilson_interval(group_hits, group_n, conf), wilson_interval(rest_hits, rest_n, conf))
