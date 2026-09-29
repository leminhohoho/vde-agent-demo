"""Sufficiency Gate (spec §5.5): decision-table tiers at their boundaries, IQR, DQ fields, scopes."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from ..gate import (
    Tier,
    assess_field,
    coverage_tier,
    downgrade,
    exclude_outliers,
    freshness_tier,
    iqr_fences,
    missing_rate_tier,
    mnar_tier,
    outlier_flag,
    run_gate,
    sample_tier,
)
from ..view import DatasetView
from .builders import cause, dataset, diagnostic, dq, dq_field, inventory, scope, semantic, unit

P = semantic().params
AS_OF = datetime.fromisoformat("2026-07-01T00:00:00+07:00")


def d(x: str | int) -> Decimal:
    return Decimal(x)


# ---- missing rate of a secondary field: ≤5 / ≤10 / ≤20 / ≤40 / >40 ----------------------------


@pytest.mark.parametrize(
    ("pct", "expected"),
    [
        ("0", Tier()),
        ("4", Tier()),
        ("5", Tier()),
        ("6", Tier("DQ_NOTE")),
        ("9", Tier("DQ_NOTE")),
        ("10", Tier("DQ_NOTE")),
        ("11", Tier("DQ_WARN", downgrade=1)),
        ("19", Tier("DQ_WARN", downgrade=1)),
        ("20", Tier("DQ_WARN", downgrade=1)),
        ("21", Tier("DQ_DESCRIBE_ONLY", force_low=True, describe_only=True)),
        ("39", Tier("DQ_DESCRIBE_ONLY", force_low=True, describe_only=True)),
        ("40", Tier("DQ_DESCRIBE_ONLY", force_low=True, describe_only=True)),
        ("41", Tier("FIELD_EXCLUDED", excluded=True)),
    ],
)
def test_missing_rate_tiers_at_and_around_each_bound(pct: str, expected: Tier) -> None:
    assert missing_rate_tier(d(pct), P.missing_rate_tiers) == expected


def test_missing_rate_bound_is_exact_to_the_decimal() -> None:
    assert missing_rate_tier(d("5.00"), P.missing_rate_tiers) == Tier()
    assert missing_rate_tier(d("5.01"), P.missing_rate_tiers).flag == "DQ_NOTE"


# ---- coverage of a zone/project: ≥90 / ≥70 / ≥50 / <50 ------------------------------------------


@pytest.mark.parametrize(
    ("pct", "expected"),
    [
        ("100", Tier()),
        ("91", Tier()),
        ("90", Tier()),
        ("89", Tier("PARTIAL_COVERAGE", downgrade=1)),
        ("71", Tier("PARTIAL_COVERAGE", downgrade=1)),
        ("70", Tier("PARTIAL_COVERAGE", downgrade=1)),
        ("69", Tier("LOW_COVERAGE", describe_only=True)),
        ("51", Tier("LOW_COVERAGE", describe_only=True)),
        ("50", Tier("LOW_COVERAGE", describe_only=True)),
        ("49", Tier("INSUFFICIENT_COVERAGE", excluded=True)),
        ("0", Tier("INSUFFICIENT_COVERAGE", excluded=True)),
    ],
)
def test_coverage_tiers_at_and_around_each_bound(pct: str, expected: Tier) -> None:
    assert coverage_tier(d(pct), P.coverage_tiers) == expected


# ---- n_eff / peers: ≥10 / 5–9 / <5 ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (12, Tier()),
        (6, Tier()),
        (5, Tier()),
        (4, Tier("SMALL_SAMPLE", describe_only=True)),
        (3, Tier("SMALL_SAMPLE", describe_only=True)),
        (2, Tier("GROUP_TOO_SMALL", excluded=True)),
        (0, Tier("GROUP_TOO_SMALL", excluded=True)),
    ],
)
def test_sample_tiers_at_and_around_each_bound(n: int, expected: Tier) -> None:
    assert sample_tier(n, P.peer_tiers) == expected


# ---- MNAR gap (>10 points) and freshness (>24h, >72h) ------------------------------------------


@pytest.mark.parametrize(
    ("gap", "flagged"),
    [("9", False), ("10", False), ("11", True), ("25", True)],
)
def test_mnar_gap_strictly_above_the_bound_downgrades_once(gap: str, flagged: bool) -> None:
    expected = Tier("MISSING_NOT_RANDOM", downgrade=1) if flagged else Tier()
    assert mnar_tier(d(gap), P.mnar_gap_pct) == expected


@pytest.mark.parametrize(
    ("hours", "expected"),
    [
        (23, Tier()),
        (24, Tier()),
        (25, Tier("STALE_SNAPSHOT", downgrade=1)),
        (71, Tier("STALE_SNAPSHOT", downgrade=1)),
        (72, Tier("STALE_SNAPSHOT", downgrade=1)),
        (73, Tier("STALE_SNAPSHOT", force_low=True)),
    ],
)
def test_freshness_tiers_at_and_around_each_bound(hours: int, expected: Tier) -> None:
    assert freshness_tier(d(hours), P.freshness_warn_hours, P.freshness_error_hours) == expected


# ---- IQR outliers (1.5× warn, 3× exclude, stop cutting above 10 %) ------------------------------


def test_iqr_fences_use_linear_quartiles() -> None:
    f = iqr_fences([d(v) for v in (10, 20, 30, 40, 50)], P.outlier_iqr_warn, P.outlier_iqr_exclude)
    assert (f.q1, f.q3, f.iqr) == (d(20), d(40), d(20))
    assert (f.warn_low, f.warn_high, f.exclude_low, f.exclude_high) == (d(-10), d(70), d(-40), d(100))


@pytest.mark.parametrize(
    ("value", "flag"),
    [
        (69, None),
        (70, None),
        (71, "OUTLIER_WARN"),
        (99, "OUTLIER_WARN"),
        (100, "OUTLIER_WARN"),
        (101, "OUTLIER_EXCLUDED"),
        (-10, None),
        (-11, "OUTLIER_WARN"),
        (-40, "OUTLIER_WARN"),
        (-41, "OUTLIER_EXCLUDED"),
    ],
)
def test_outlier_flags_are_strictly_beyond_each_fence(value: int, flag: str | None) -> None:
    fences = iqr_fences([d(v) for v in (10, 20, 30, 40, 50)], P.outlier_iqr_warn, P.outlier_iqr_exclude)
    assert outlier_flag(d(value), fences) == flag


def test_extreme_outliers_are_excluded_up_to_the_share_limit() -> None:
    values = [d(v) for v in [100] * 9 + [110] * 9 + [5000] * 2]  # 2 of 20 = 10 %: allowed
    kept, excluded = exclude_outliers(values, lambda v: v, P)
    assert excluded == [d(5000), d(5000)] and len(kept) == 18


def test_cutting_stops_when_more_than_the_share_limit_would_be_excluded() -> None:
    values = [d(v) for v in [100] * 8 + [110] * 9 + [5000] * 3]  # 3 of 20 = 15 % > 10 %
    kept, excluded = exclude_outliers(values, lambda v: v, P)
    assert excluded == [] and kept == values


def test_fewer_than_four_values_have_no_outliers() -> None:
    values = [d(1), d(2), d(9999)]
    assert exclude_outliers(values, lambda v: v, P) == (values, [])


# ---- DQ field assessment --------------------------------------------------------------------------


def test_primary_field_fail_rejects_and_warn_forces_low() -> None:
    fail = assess_field(dq_field("dm_unit_friction_diagnostics", "price_spread_vs_peer_pct", "FAIL"), P)
    assert fail.reject and "EVIDENCE_FIELD_MISSING" in fail.flags
    warn = assess_field(dq_field("dm_unit_friction_diagnostics", "price_spread_vs_peer_pct", "WARN"), P)
    assert not warn.reject and warn.force_low and "DQ_WARN" in warn.flags


def test_tc23_secondary_field_45pct_missing_is_excluded_and_not_random() -> None:
    field = dq_field(
        "fact_unit_inventory_snapshot",
        "spiff_bonus_vnd",
        "WARN",
        primary=False,
        missing="45",
        missing_rate_overdue_pct="30",
        missing_rate_sold_pct="5",
    )
    a = assess_field(field, P)
    assert a.excluded and not a.reject
    assert a.flags == ("FIELD_EXCLUDED", "MISSING_NOT_RANDOM")
    assert a.mnar_gap_pct == d(25)


def test_secondary_warn_within_a_fine_missing_rate_still_downgrades_once() -> None:
    a = assess_field(dq_field("fact_unit_inventory_snapshot", "asking_price_vnd", "WARN", primary=False, missing="2"), P)
    assert (a.flags, a.downgrade, a.force_low, a.excluded) == (("DQ_WARN",), 1, False, False)


def test_downgrade_ladder_stops_at_low() -> None:
    assert downgrade("HIGH", 0) == "HIGH"
    assert downgrade("HIGH", 1) == "MEDIUM"
    assert downgrade("HIGH", 2) == "LOW"
    assert downgrade("MEDIUM", 5) == "LOW"


# ---- run_gate: freshness, fields and scope coverage / n_eff ------------------------------------


def zone_dataset(overdue: int, diagnosed: int) -> DatasetView:
    units = [unit(i) for i in range(1, overdue + 3)]
    inv = [inventory(i, 100 + i) for i in range(1, overdue + 1)] + [
        inventory(overdue + 1, 30),
        inventory(overdue + 2, 40, "SOLD"),
    ]
    diags = [diagnostic(i, 100 + i) for i in range(1, diagnosed + 1)]
    causes = [cause(i, "OVERPRICED_VS_PEER") for i in range(1, diagnosed + 1)]
    return DatasetView(dataset(units, inv, diags, causes))


def test_gate_reports_coverage_and_n_eff_per_zone_and_project() -> None:
    result = run_gate(zone_dataset(40, 28), dq(), scope("ZONE", zone_ids=["ZN-AQUA-01"]), semantic(), AS_OF)
    z = result.scope("ZONE", "ZN-AQUA-01")
    assert (z.units_in_scope, z.overdue_units, z.units_valid, z.n_eff) == (42, 40, 28, 28)
    assert z.coverage_pct == d(70)
    assert z.coverage == Tier("PARTIAL_COVERAGE", downgrade=1)
    assert z.confidence == "MEDIUM"
    assert result.scope("PROJECT", "PRJ-X").coverage_pct == d(70)


def test_units_whose_scores_do_not_sum_to_one_are_not_valid_for_coverage() -> None:
    units = [unit(1), unit(2)]
    inv = [inventory(1, 120), inventory(2, 130)]
    diags = [diagnostic(1, 120), diagnostic(2, 130)]
    causes = [cause(1, "OVERPRICED_VS_PEER"), cause(2, "OVERPRICED_VS_PEER", 1, "0.6")]
    result = run_gate(
        DatasetView(dataset(units, inv, diags, causes)), dq(), scope("PROJECT", project_ids=["PRJ-X"]), semantic(), AS_OF
    )
    assert result.scope("PROJECT", "PRJ-X").units_valid == 1


def test_no_overdue_units_means_no_coverage_figure() -> None:
    result = run_gate(zone_dataset(0, 0), dq(), scope("ZONE", zone_ids=["ZN-AQUA-01"]), semantic(), AS_OF)
    z = result.scope("ZONE", "ZN-AQUA-01")
    assert (z.overdue_units, z.coverage_pct, z.coverage) == (0, None, Tier())


def test_stale_data_is_flagged_for_every_scope() -> None:
    stale = dq(data_as_of="2026-06-27T23:00:00+07:00")  # 73 h before AS_OF
    result = run_gate(zone_dataset(10, 10), stale, scope("ZONE", zone_ids=["ZN-AQUA-01"]), semantic(), AS_OF)
    assert result.freshness_hours == d(73)
    assert result.freshness == Tier("STALE_SNAPSHOT", force_low=True)
    assert result.scope("ZONE", "ZN-AQUA-01").confidence == "LOW"
    assert "STALE_SNAPSHOT" in result.scope("ZONE", "ZN-AQUA-01").flags


def test_gate_indexes_dq_fields() -> None:
    fields = [dq_field("dm_unit_friction_diagnostics", "price_spread_vs_peer_pct", "FAIL")]
    result = run_gate(zone_dataset(1, 1), dq(fields), scope("MARKET"), semantic(), AS_OF)
    assessment = result.field("dm_unit_friction_diagnostics", "price_spread_vs_peer_pct")
    assert assessment is not None and assessment.reject
    assert result.field("dm_unit_friction_diagnostics", "unknown") is None
