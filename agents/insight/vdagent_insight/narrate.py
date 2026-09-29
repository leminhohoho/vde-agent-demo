"""Narration (pipeline steps 7–8): turn the LLM draft into rendered claims, item by item.

Pure. `check_draft` runs the validator (validation.py) on every item, plus two draft-level rules:
an item mixes no insight types and uses no candidate another item already used (E11). The
pipeline sends those violations to the one repair call ([LLM-R], phase P3).

`narrate` then renders:
- a clean item with its own template and slot refs (source LLM); if binding still fails it is
  treated like a violation;
- a failing item as the TEMPLATE sentence of each of its candidates (source TEMPLATE, E09–E12);
- a T7 candidate the model did not use, as TEMPLATE: limitations are never dropped (GR-07);
- every other candidate the model left out → rejected LLM_SKIPPED (D-32).
Without a draft (LLM unavailable, E08) every candidate is rendered as TEMPLATE: all T7 plus at
most `max_items` others in the given (priority) order, the rest rejected SELECTION_LIMIT.
`narrative_mode` is TEMPLATE when there is no draft or any drafted item fell back.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from .contracts import InsightCandidate, InsightTaskRequest, LlmInsightDraft, RejectedCandidate
from .render import RenderedClaim, RenderError, bind_claim, render_template
from .settings import SemanticConfig
from .validation import Violation, validate_item
from .view import DatasetView

Source = Literal["LLM", "TEMPLATE"]


@dataclass(frozen=True)
class NarratedItem:
    candidate_ids: tuple[str, ...]
    claim: RenderedClaim
    source: Source
    limitation_text: str | None = None
    recommendation_text: str | None = None


@dataclass
class Narration:
    items: list[NarratedItem] = field(default_factory=list)
    violations: dict[int, list[Violation]] = field(default_factory=dict)
    rejected: list[RejectedCandidate] = field(default_factory=list)
    narrative_mode: Literal["LLM", "TEMPLATE"] = "LLM"


def check_draft(
    draft: LlmInsightDraft, candidates: Mapping[str, InsightCandidate], cfg: SemanticConfig, request: InsightTaskRequest
) -> dict[int, list[Violation]]:
    found: dict[int, list[Violation]] = {}
    used: set[str] = set()
    for i, item in enumerate(draft.selected):
        violations = validate_item(item, candidates, cfg, request)
        types = {candidates[c].insight_type for c in item.candidate_ids if c in candidates}
        if len(types) > 1:
            violations.append(Violation("GR-03", "E11", f"one item mixes insight types {sorted(types)}"))
        reused = sorted(set(item.candidate_ids) & used)
        if reused:
            violations.append(Violation("GR-03", "E11", f"candidates already used by another item: {reused}"))
        used |= set(item.candidate_ids)
        if violations:
            found[i] = violations
    return found


def _template(candidate: InsightCandidate, cfg: SemanticConfig, view: DatasetView, out: Narration) -> None:
    try:
        out.items.append(NarratedItem((candidate.candidate_id,), render_template(candidate, cfg, view), "TEMPLATE"))
    except RenderError:
        out.rejected.append(RejectedCandidate(candidate_id=candidate.candidate_id, reason_code="RENDER_FAILED"))


def narrate(
    candidates: list[InsightCandidate],
    draft: LlmInsightDraft | None,
    cfg: SemanticConfig,
    view: DatasetView,
    request: InsightTaskRequest,
    max_items: int,
) -> Narration:
    out = Narration()
    by_id = {c.candidate_id: c for c in candidates}
    if draft is None:
        out.narrative_mode = "TEMPLATE"
        others = [c for c in candidates if c.task != "T7"]
        for c in others[:max_items]:
            _template(c, cfg, view, out)
        for c in [c for c in candidates if c.task == "T7"]:
            _template(c, cfg, view, out)
        out.rejected += [
            RejectedCandidate(candidate_id=c.candidate_id, reason_code="SELECTION_LIMIT") for c in others[max_items:]
        ]
        return out

    out.violations = check_draft(draft, by_id, cfg, request)
    used: set[str] = set()
    for i, item in enumerate(draft.selected):
        item_candidates = [by_id[c] for c in item.candidate_ids if c in by_id and c not in used]
        if i not in out.violations:
            try:
                claim = bind_claim(item.template, {s.slot: s.ref for s in item.slots}, by_id, cfg, view)
            except RenderError as exc:
                out.violations[i] = [Violation("GR-03", "E11", str(exc))]
            else:
                out.items.append(
                    NarratedItem(tuple(item.candidate_ids), claim, "LLM", item.limitation_text, item.recommendation_text)
                )
                used |= set(item.candidate_ids)
                continue
        out.narrative_mode = "TEMPLATE"
        for c in item_candidates:
            _template(c, cfg, view, out)
            used.add(c.candidate_id)
    for c in candidates:
        if c.candidate_id in used:
            continue
        if c.task == "T7":
            _template(c, cfg, view, out)
        else:
            out.rejected.append(RejectedCandidate(candidate_id=c.candidate_id, reason_code="LLM_SKIPPED"))
    return out
