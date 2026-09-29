"""Sufficiency Gate (pipeline step 3, spec §5.5).

Pure and deterministic (luật 11): no I/O, no LLM, `Decimal` throughout, every bound from
`SemanticParams`. The LLM never judges whether data is sufficient; this module does, and the
candidate engine (step 4) applies its verdicts.

- Decision-table tiers (`missing_rate_tier`, `coverage_tier`, `sample_tier`, `mnar_tier`,
  `freshness_tier`) return a `Tier`: the limitation code plus what it does to a candidate.
- IQR outliers per group: `iqr_fences`, `outlier_flag`, `exclude_outliers` (stop cutting when
  more than `outlier_max_excluded_pct` of the sample would go).
- `assess_field`: one DQ field result → flags, confidence effect, reject/exclude.
- `run_gate`: freshness, every DQ field, and coverage / n_eff per zone and project in scope.

Bounds: missing-rate, freshness and the IQR fences are inclusive on the "fine" side (a value
equal to the bound stays in the milder tier); coverage and sample tiers are lower bounds (a value
equal to the bound reaches that tier). Confidence starts HIGH and only goes down (spec §5.2).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal

from .contracts import AnalysisScope, ConfidenceLevel, DqFieldResult, DqPayload, Level
from .settings import CoverageTiers, MissingRateTiers, PeerTiers, SemanticConfig, SemanticParams
from .view import DatasetView, UnitView

LADDER: tuple[ConfidenceLevel, ...] = ("HIGH", "MEDIUM", "LOW")
HUNDRED = Decimal(100)


@dataclass(frozen=True)
class Tier:
    """One row of the §5.5 decision table: its limitation code (None = fine) and its effect."""

    flag: str | None = None
    downgrade: int = 0
    """Confidence steps down."""
    force_low: bool = False
    describe_only: bool = False
    """No comparison, ranking or attribution: `significant` must be false."""
    excluded: bool = False
    """Field dropped / no conclusion at this level / group suppressed."""


def downgrade(level: ConfidenceLevel, steps: int) -> ConfidenceLevel:
    return LADDER[min(LADDER.index(level) + max(steps, 0), len(LADDER) - 1)]


def cap(level: ConfidenceLevel, highest: ConfidenceLevel) -> ConfidenceLevel:
    """At most `highest` (e.g. MEDIUM when the peer sample is constrained, BR-07)."""
    return LADDER[max(LADDER.index(level), LADDER.index(highest))]


# ---- decision-table tiers -----------------------------------------------------------------------


def missing_rate_tier(missing_pct: Decimal, tiers: MissingRateTiers) -> Tier:
    """Secondary field: ≤5 fine, ≤10 DQ_NOTE, ≤20 DQ_WARN (−1), ≤40 describe-only + LOW, >40 dropped."""
    if missing_pct <= tiers.none_max:
        return Tier()
    if missing_pct <= tiers.note_max:
        return Tier("DQ_NOTE")
    if missing_pct <= tiers.warn_max:
        return Tier("DQ_WARN", downgrade=1)
    if missing_pct <= tiers.describe_only_max:
        return Tier("DQ_DESCRIBE_ONLY", force_low=True, describe_only=True)
    return Tier("FIELD_EXCLUDED", excluded=True)


def coverage_tier(coverage_pct: Decimal, tiers: CoverageTiers) -> Tier:
    """Zone/project: ≥90 full, ≥70 conclude with the denominator (−1), ≥50 describe only, <50 no conclusion."""
    if coverage_pct >= tiers.full_min:
        return Tier()
    if coverage_pct >= tiers.partial_min:
        return Tier("PARTIAL_COVERAGE", downgrade=1)
    if coverage_pct >= tiers.low_min:
        return Tier("LOW_COVERAGE", describe_only=True)
    return Tier("INSUFFICIENT_COVERAGE", excluded=True)


def sample_tier(n_eff: int, tiers: PeerTiers) -> Tier:
    """Peers / group size (D-71): ≥ compare_min may compare (if significant), ≥ describe_min describe
    only, below → GROUP_TOO_SMALL (only T3 drops the group; T1/T2 keep the candidate flagged)."""
    if n_eff >= tiers.compare_min:
        return Tier()
    if n_eff >= tiers.describe_min:
        return Tier("SMALL_SAMPLE", describe_only=True)
    return Tier("GROUP_TOO_SMALL", excluded=True)


def mnar_tier(gap_pct: Decimal, bound_pct: Decimal) -> Tier:
    """|missing among overdue − missing among sold| above the bound: one more step down."""
    return Tier("MISSING_NOT_RANDOM", downgrade=1) if gap_pct > bound_pct else Tier()


def freshness_tier(hours: Decimal, warn_hours: int, error_hours: int) -> Tier:
    """Inventory/price data age: >warn → STALE_SNAPSHOT (−1), >error → STALE_SNAPSHOT on everything, LOW."""
    if hours > error_hours:
        return Tier("STALE_SNAPSHOT", force_low=True)
    if hours > warn_hours:
        return Tier("STALE_SNAPSHOT", downgrade=1)
    return Tier()


# ---- IQR outliers -------------------------------------------------------------------------------


@dataclass(frozen=True)
class IqrFences:
    q1: Decimal
    q3: Decimal
    iqr: Decimal
    warn_low: Decimal
    warn_high: Decimal
    exclude_low: Decimal
    exclude_high: Decimal


def quantile(sorted_values: Sequence[Decimal], q: Decimal) -> Decimal:
    """Linear interpolation between order statistics (Hyndman–Fan type 7)."""
    pos = (len(sorted_values) - 1) * q
    lo = int(pos)
    if lo + 1 >= len(sorted_values):
        return sorted_values[lo]
    return sorted_values[lo] + (sorted_values[lo + 1] - sorted_values[lo]) * (pos - lo)


def iqr_fences(values: Sequence[Decimal], warn_k: Decimal, exclude_k: Decimal) -> IqrFences:
    xs = sorted(values)
    q1, q3 = quantile(xs, Decimal("0.25")), quantile(xs, Decimal("0.75"))
    iqr = q3 - q1
    return IqrFences(q1, q3, iqr, q1 - warn_k * iqr, q3 + warn_k * iqr, q1 - exclude_k * iqr, q3 + exclude_k * iqr)


def outlier_flag(value: Decimal, fences: IqrFences) -> str | None:
    if value < fences.exclude_low or value > fences.exclude_high:
        return "OUTLIER_EXCLUDED"
    if value < fences.warn_low or value > fences.warn_high:
        return "OUTLIER_WARN"
    return None


MIN_VALUES_FOR_IQR = 4


def exclude_outliers[T](items: Sequence[T], key: Callable[[T], Decimal], params: SemanticParams) -> tuple[list[T], list[T]]:
    """(kept, excluded): drop values beyond the 3×IQR fences, unless that would drop more than
    `outlier_max_excluded_pct` of the sample (then nothing is cut). Used by T2/T3 only; T1 keeps
    every unit and just flags it."""
    items = list(items)
    if len(items) < MIN_VALUES_FOR_IQR:
        return items, []
    fences = iqr_fences([key(i) for i in items], params.outlier_iqr_warn, params.outlier_iqr_exclude)
    excluded = [i for i in items if outlier_flag(key(i), fences) == "OUTLIER_EXCLUDED"]
    if Decimal(len(excluded)) * HUNDRED > params.outlier_max_excluded_pct * len(items):
        return items, []
    return [i for i in items if i not in excluded], excluded


# ---- DQ fields ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldAssessment:
    table: str
    field: str
    is_primary: bool
    status: str
    missing_rate_pct: Decimal
    mnar_gap_pct: Decimal | None
    flags: tuple[str, ...]
    downgrade: int = 0
    force_low: bool = False
    describe_only: bool = False
    excluded: bool = False
    reject: bool = False
    """Primary field DQ FAIL: every candidate that needs it is rejected (EVIDENCE_FIELD_MISSING)."""


def assess_field(result: DqFieldResult, params: SemanticParams) -> FieldAssessment:
    mnar_gap = None
    if result.missing_rate_overdue_pct is not None and result.missing_rate_sold_pct is not None:
        mnar_gap = abs(result.missing_rate_overdue_pct - result.missing_rate_sold_pct)
    base = FieldAssessment(
        table=result.table,
        field=result.field,
        is_primary=result.is_primary,
        status=result.status,
        missing_rate_pct=result.missing_rate_pct,
        mnar_gap_pct=mnar_gap,
        flags=(),
    )
    if result.is_primary:
        if result.status == "FAIL":
            return replace(base, flags=("EVIDENCE_FIELD_MISSING",), reject=True)
        if result.status == "WARN":
            return replace(base, flags=("DQ_WARN",), force_low=True)
        return base

    tier = missing_rate_tier(result.missing_rate_pct, params.missing_rate_tiers)
    if result.status == "FAIL":
        tier = Tier("FIELD_EXCLUDED", excluded=True)
    elif result.status == "WARN" and tier.flag in (None, "DQ_NOTE"):
        tier = Tier("DQ_WARN", downgrade=1)
    flags = [tier.flag] if tier.flag else []
    steps = tier.downgrade
    if mnar_gap is not None:
        mnar = mnar_tier(mnar_gap, params.mnar_gap_pct)
        if mnar.flag:
            flags.append(mnar.flag)
            steps += mnar.downgrade
    return replace(
        base,
        flags=tuple(flags),
        downgrade=steps,
        force_low=tier.force_low,
        describe_only=tier.describe_only,
        excluded=tier.excluded,
    )


# ---- scopes -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ScopeAssessment:
    level: Level
    id: str
    label: str
    units_in_scope: int
    overdue_units: int
    units_valid: int
    """Overdue units with a mart row and bridge rows whose scores sum to 1 (± tolerance)."""
    n_eff: int
    """`units_valid` minus extreme DOM outliers (3×IQR)."""
    coverage_pct: Decimal | None
    """None when the scope has no overdue unit."""
    coverage: Tier
    sample: Tier
    confidence: ConfidenceLevel
    flags: tuple[str, ...]
    valid_unit_keys: frozenset[int] = field(default_factory=frozenset)
    n_eff_unit_keys: frozenset[int] = field(default_factory=frozenset)


@dataclass(frozen=True)
class GateResult:
    fields: dict[str, FieldAssessment]
    freshness_hours: Decimal
    freshness: Tier
    scopes: dict[tuple[str, str], ScopeAssessment]

    def field(self, table: str, field_name: str) -> FieldAssessment | None:
        return self.fields.get(f"{table}.{field_name}")

    def scope(self, level: str, scope_id: str) -> ScopeAssessment:
        return self.scopes[(level, scope_id)]


def attribution_sum_ok(view: UnitView, tolerance: Decimal) -> bool:
    """BR-04: the unit's attribution scores add up to 1.000 (± tolerance)."""
    total = sum((row.attribution_score for row, _ in view.causes), Decimal(0))
    return bool(view.causes) and abs(total - 1) <= tolerance


def is_valid_diagnosis(view: UnitView, params: SemanticParams) -> bool:
    return view.diagnostic is not None and attribution_sum_ok(view, params.attribution_sum_tolerance)


def _dom(view: UnitView) -> Decimal:
    assert view.inventory is not None
    return Decimal(view.inventory.unsold_days_dom)


def _assess_scope(
    level: Level, scope_id: str, label: str, units: list[UnitView], params: SemanticParams, freshness: Tier
) -> ScopeAssessment:
    overdue = [u for u in units if DatasetView.is_overdue(u, params.overdue_threshold_days)]
    valid = [u for u in overdue if is_valid_diagnosis(u, params)]
    kept, _ = exclude_outliers(valid, _dom, params)
    if overdue:
        coverage_pct: Decimal | None = Decimal(len(valid)) * HUNDRED / len(overdue)
        coverage = coverage_tier(coverage_pct, params.coverage_tiers)
        sample = sample_tier(len(kept), params.peer_tiers)
    else:
        coverage_pct, coverage, sample = None, Tier(), Tier()
    confidence = downgrade("HIGH", coverage.downgrade + freshness.downgrade)
    if freshness.force_low:
        confidence = "LOW"
    flags = tuple(t.flag for t in (coverage, sample, freshness) if t.flag)
    return ScopeAssessment(
        level=level,
        id=scope_id,
        label=label,
        units_in_scope=len(units),
        overdue_units=len(overdue),
        units_valid=len(valid),
        n_eff=len(kept),
        coverage_pct=coverage_pct,
        coverage=coverage,
        sample=sample,
        confidence=confidence,
        flags=flags,
        valid_unit_keys=frozenset(u.unit.unit_key for u in valid),
        n_eff_unit_keys=frozenset(u.unit.unit_key for u in kept),
    )


def _hours_between(earlier: datetime, later: datetime) -> Decimal:
    delta = later - earlier
    return Decimal(delta.days * 86400 + delta.seconds) / Decimal(3600)


def run_gate(view: DatasetView, dq: DqPayload, scope: AnalysisScope, cfg: SemanticConfig, as_of: datetime) -> GateResult:
    """`as_of` is the task's clock, passed in so the gate stays pure and replayable."""
    params = cfg.params
    hours = _hours_between(datetime.fromisoformat(dq.data_as_of), as_of)
    freshness = freshness_tier(hours, params.freshness_warn_hours, params.freshness_error_hours)
    fields = {f"{r.table}.{r.field}": assess_field(r, params) for r in dq.fields}

    units = view.units_in_scope(scope)
    scopes: dict[tuple[str, str], ScopeAssessment] = {}
    for zone in view.zones:
        members = [u for u in units if u.zone is not None and u.zone.zone_key == zone.zone_key]
        if members:
            scopes[("ZONE", zone.zone_id)] = _assess_scope("ZONE", zone.zone_id, zone.zone_name, members, params, freshness)
    for project in view.projects:
        members = [u for u in units if u.project is not None and u.project.project_key == project.project_key]
        if members:
            scopes[("PROJECT", project.project_id)] = _assess_scope(
                "PROJECT", project.project_id, project.project_name, members, params, freshness
            )
    return GateResult(fields=fields, freshness_hours=hours, freshness=freshness, scopes=scopes)
