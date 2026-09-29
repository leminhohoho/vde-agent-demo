"""claim_binder and TEMPLATE mode (pipeline step 8, spec §6.1 step 8, §8.2).

Pure. Numbers never come from the LLM (luật 1):
- `bind_claim` fills every `{{slot}}` of a template from `<candidate_id>.<slot>` refs: a numeric
  slot of the candidate becomes a `NumericBinding` (value exact, display vi-VN, formatting.py), a
  label slot (`language.label_slots`) becomes text: the subject label, `cause_label_vi`, what a
  LEGAL project lacks (`permit_status`), or the limitation message(s) of the candidate.
- `render_template` is the fallback sentence of one candidate: the cause template for
  ROOT_CAUSE_SIGNAL (the peer-free variant when there are too few peers, D-71), else the
  template of its insight type. Candidate slot names are the template slot names by design.
- `recommendation_text`: the config sentence of the candidate's action code (`action_texts`).

A slot that cannot be resolved raises `RenderError`: the caller drops the item (or falls back).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from .contracts import InsightCandidate, NumericBinding
from .settings import SemanticConfig
from .view import DatasetView

SLOT = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
SUBJECT_LABEL_SLOTS = frozenset({"unit", "project", "zone", "scope", "subject", "group", "market"})
NO_OVERDUE = "NO_OVERDUE_UNITS"


class RenderError(ValueError):
    """A `{{slot}}` has no ref, points to an unknown candidate or to nothing the candidate holds."""


@dataclass(frozen=True)
class RenderedClaim:
    template: str
    rendered_text: str
    numeric_bindings: list[NumericBinding]


def slot_names(template: str) -> list[str]:
    """Slots in order of first appearance."""
    return list(dict.fromkeys(SLOT.findall(template)))


def _permit_status(candidate: InsightCandidate, cfg: SemanticConfig, view: DatasetView) -> str | None:
    project = next((p for p in view.projects if p.project_id == candidate.subject.id), None)
    if project is None:
        return None
    labels = cfg.language.permit_status_labels
    missing_permit, missing_guarantee = not project.is_sales_permit_issued, not project.is_bank_guarantee_issued
    if missing_permit and missing_guarantee:
        return labels["both"]
    if missing_permit:
        return labels["permit"]
    if missing_guarantee:
        return labels["guarantee"]
    return None


def labelled(slot: str, display: str, cfg: SemanticConfig) -> str:
    """A number as the reader sees it: with its fixed label when the slot has one (`slot_labels`)."""
    label = cfg.language.slot_labels.get(slot)
    return label.replace("{value}", display) if label else display


def label_for(slot: str, candidate: InsightCandidate, cfg: SemanticConfig, view: DatasetView) -> str | None:
    if slot not in cfg.language.label_slots:
        return None
    if slot in SUBJECT_LABEL_SLOTS:
        return candidate.subject.label
    if slot == "cause_label":
        code = candidate.cause_code
        return cfg.cause(code).cause_label_vi if code in cfg.allowed_cause_codes and code is not None else None
    if slot == "permit_status":
        return _permit_status(candidate, cfg, view)
    if slot == "limitation":
        messages = [cfg.language.limitation_messages[f] for f in candidate.dq_flags if f in cfg.language.limitation_messages]
        return "; ".join(dict.fromkeys(messages)) or None
    return None


def bind_claim(
    template: str,
    refs: Mapping[str, str],
    candidates: Mapping[str, InsightCandidate],
    cfg: SemanticConfig,
    view: DatasetView,
) -> RenderedClaim:
    texts: dict[str, str] = {}
    bindings: list[NumericBinding] = []
    for name in slot_names(template):
        ref = refs.get(name)
        if ref is None:
            raise RenderError(f"slot {{{{{name}}}}} has no ref")
        candidate_id, _, slot = ref.partition(".")
        candidate = candidates.get(candidate_id)
        if candidate is None:
            raise RenderError(f"slot {{{{{name}}}}}: unknown candidate {candidate_id!r}")
        numeric = candidate.slots.get(slot)
        if numeric is not None:
            bound = numeric.model_copy(update={"slot": name})
            bindings.append(bound)
            texts[name] = labelled(slot, bound.display, cfg)
            continue
        label = label_for(slot, candidate, cfg, view)
        if label is None:
            raise RenderError(f"slot {{{{{name}}}}}: candidate {candidate_id} has no value for {slot!r}")
        texts[name] = label
    return RenderedClaim(
        template=template, rendered_text=_fill(template, texts, {b.slot for b in bindings}), numeric_bindings=bindings
    )


def _fill(template: str, texts: Mapping[str, str], numeric: set[str]) -> str:
    """Fill the slots; a word right after a slot that repeats the value's own last word ("{{peers}} căn"
    with "12 căn") is written once."""
    out: list[str] = []
    pos = 0
    for m in SLOT.finditer(template):
        if m.start() < pos:
            continue
        out.append(template[pos : m.start()])
        text = texts[m.group(1)]
        out.append(text)
        pos = m.end()
        last = text.rsplit(" ", 1)[-1]
        if m.group(1) in numeric and " " in text and last.isalpha():
            repeat = re.match(rf" {re.escape(last)}(?!\w)", template[pos:])
            if repeat:
                pos += repeat.end()
    out.append(template[pos:])
    text = "".join(out)
    return text[:1].upper() + text[1:]  # a sentence may start with a slot ("{{cause_label}} …")


def fallback_template(candidate: InsightCandidate, cfg: SemanticConfig) -> str:
    if candidate.insight_type == "ROOT_CAUSE_SIGNAL" and candidate.cause_code in cfg.allowed_cause_codes:
        assert candidate.cause_code is not None
        cause = cfg.cause(candidate.cause_code)
        if cause.uses_peer_group and "GROUP_TOO_SMALL" in candidate.dq_flags:
            return cfg.language.peer_hidden_template
        return cause.template
    if candidate.insight_type == "DATA_LIMITATION" and NO_OVERDUE in candidate.dq_flags:
        return cfg.insight_templates[NO_OVERDUE]
    return cfg.insight_templates[candidate.insight_type]


def render_template(candidate: InsightCandidate, cfg: SemanticConfig, view: DatasetView) -> RenderedClaim:
    template = fallback_template(candidate, cfg)
    refs = {name: f"{candidate.candidate_id}.{name}" for name in slot_names(template)}
    return bind_claim(template, refs, {candidate.candidate_id: candidate}, cfg, view)


def recommendation_text(candidate: InsightCandidate, cfg: SemanticConfig) -> str | None:
    if candidate.action_code is None or candidate.cause_code not in cfg.allowed_cause_codes:
        return None
    assert candidate.cause_code is not None
    return cfg.action_texts.get(candidate.action_code)
