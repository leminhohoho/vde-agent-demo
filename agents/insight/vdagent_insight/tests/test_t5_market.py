"""T5 Market Context (spec §6.2, BR-09, D-72): context only, never a cause, never a recommendation.

D-72: the data pack holds one 12-month window, so trend metrics (mortgage rate, absorption) are the
latest month against the first month of that window; inferred metrics (MOI, PIR, household income)
are stated at their latest level only, with a limitation.
"""

from __future__ import annotations

from decimal import Decimal

from ..candidates.t5_market import t5_candidates
from ..contracts import DatasetPayload, MacroRow
from .builders import context, dataset, inventory, macro, market, semantic, unit

TASKS = ("T1", "T5", "T6", "T7")


def data() -> DatasetPayload:
    return dataset([unit(1)], [inventory(1, 120)])


def window() -> list[MacroRow]:
    rates = ["10.5", "10.77", "11.05", "11.32", "11.59", "11.86", "12.14", "12.41", "12.68", "12.95", "13.23", "13.5"]
    months = [
        20250701,
        20250801,
        20250901,
        20251001,
        20251101,
        20251201,
        20260101,
        20260201,
        20260301,
        20260401,
        20260501,
        20260601,
    ]
    absorption = ["85.0", "83.78", "82.52", "81.31", "80.09", "78.88", "77.62", "76.41", "75.19", "73.98", "72.72", "71.5"]
    return [macro(d, rate=r, absorption=a, moi="2.5", pir="8.5") for d, r, a in zip(months, rates, absorption, strict=True)]


def test_trend_is_the_latest_month_against_the_start_of_the_window() -> None:
    (c,) = t5_candidates(context(data(), tasks=TASKS, market_payload=market(window()))).candidates
    assert c.candidate_id == "C-T5-MKT-EAST-HCM-MID_HIGH"
    assert (c.task, c.insight_type, c.level) == ("T5", "MARKET_CONTEXT", "MARKET")
    assert c.cause_code is None and c.action_code is None
    assert (c.slots["interest_rate_start"].value, c.slots["interest_rate"].value) == (Decimal("10.5"), Decimal("13.5"))
    assert (c.slots["absorption_rate_start"].display, c.slots["absorption_rate"].display) == ("85%", "71,5%")
    assert c.slots["window_months"].display == "12 tháng"
    assert c.slots["interest_rate"].metric_ref == "ART-MARKET#/fact_market_macro_monthly/11/floating_mortgage_rate_pct"
    assert c.slots["interest_rate_start"].metric_ref == "ART-MARKET#/fact_market_macro_monthly/0/floating_mortgage_rate_pct"
    assert c.priority == Decimal("0.3") and c.significant is False and c.confidence == "MEDIUM"


def test_inferred_metrics_are_levels_only_with_a_limitation() -> None:
    (c,) = t5_candidates(context(data(), tasks=TASKS, market_payload=market(window()))).candidates
    assert (c.slots["moi"].display, c.slots["pir"].display) == ("2,5 tháng", "8,5 lần")
    assert c.slots["household_income"].unit == "VND"
    assert not {"moi_start", "pir_start", "household_income_start"} & set(c.slots)
    assert c.dq_flags == ["INFERRED_MARKET_METRIC"]


def test_market_metrics_come_from_config() -> None:
    trend = [m.slot for m in semantic().market_metrics if m.trend]
    level = [m.slot for m in semantic().market_metrics if not m.trend]
    assert (trend, level) == (["interest_rate", "absorption_rate"], ["moi", "pir", "household_income"])


def test_a_single_month_has_levels_but_no_trend() -> None:
    (c,) = t5_candidates(context(data(), tasks=TASKS, market_payload=market([macro(20260601, moi=None)]))).candidates
    assert "interest_rate_start" not in c.slots and "moi" not in c.slots
    assert c.slots["window_months"].value == 1


def test_other_markets_and_segments_are_ignored() -> None:
    rows = [macro(20260601, market_id="MKT-WEST-HCM"), macro(20260601, segment="LUXURY")]
    assert t5_candidates(context(data(), tasks=TASKS, market_payload=market(rows))).candidates == []


def test_no_market_artifact_or_no_t5_means_no_candidate() -> None:
    assert t5_candidates(context(data(), tasks=TASKS)).candidates == []
    assert t5_candidates(context(data(), tasks=("T1",), market_payload=market(window()))).candidates == []
