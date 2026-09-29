"""T5 Market Context (spec §6.2, BR-09): context only, never a cause, never a recommendation."""

from __future__ import annotations

from decimal import Decimal

from ..candidates.t5_market import t5_candidates
from ..contracts import DatasetPayload
from .builders import context, dataset, inventory, macro, market, unit

TASKS = ("T1", "T5", "T6", "T7")


def data() -> DatasetPayload:
    return dataset([unit(1)], [inventory(1, 120)])


def test_latest_month_is_set_against_the_same_month_a_year_earlier() -> None:
    rows = [
        macro(20250630, rate="7.00", absorption="40.00", moi="10.0", pir="16.0"),
        macro(20260531, rate="8.20", absorption="32.00"),
        macro(20260630, rate="8.50", absorption="30.00", moi="14.5", pir="18.2"),
    ]
    (c,) = t5_candidates(context(data(), tasks=TASKS, market_payload=market(rows))).candidates
    assert c.candidate_id == "C-T5-MKT-EAST-HCM-MID_HIGH"
    assert (c.task, c.insight_type, c.level) == ("T5", "MARKET_CONTEXT", "MARKET")
    assert (c.subject.type, c.subject.id) == ("market", "MKT-EAST-HCM")
    assert c.cause_code is None and c.action_code is None
    assert (c.slots["interest_rate"].value, c.slots["interest_rate_prior"].value) == (Decimal("8.50"), Decimal("7.00"))
    assert (c.slots["absorption_rate"].display, c.slots["absorption_rate_prior"].display) == ("30%", "40%")
    assert (c.slots["moi"].display, c.slots["pir"].display) == ("14,5 tháng", "18,2 lần")
    assert c.slots["interest_rate"].metric_ref == "ART-MARKET#/fact_market_macro_monthly/2/floating_mortgage_rate_pct"
    assert c.priority == Decimal("0.3") and c.significant is False
    assert c.confidence == "MEDIUM"


def test_other_markets_and_segments_are_ignored() -> None:
    rows = [macro(20260630, market_id="MKT-WEST-HCM"), macro(20260630, segment="LUXURY")]
    assert t5_candidates(context(data(), tasks=TASKS, market_payload=market(rows))).candidates == []


def test_missing_prior_period_and_null_metrics_leave_their_slots_out() -> None:
    (c,) = t5_candidates(context(data(), tasks=TASKS, market_payload=market([macro(20260630, moi=None)]))).candidates
    assert set(c.slots) == {"interest_rate", "absorption_rate", "pir"}


def test_no_market_artifact_or_no_t5_means_no_candidate() -> None:
    assert t5_candidates(context(data(), tasks=TASKS)).candidates == []
    rows = market([macro(20260630)])
    assert t5_candidates(context(data(), tasks=("T1",), market_payload=rows)).candidates == []
