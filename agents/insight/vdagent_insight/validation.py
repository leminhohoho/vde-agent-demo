"""Validator (pipeline step 7, spec §5.3 GR-01→GR-08 and the related BR). Pure.

`validate_item` checks one draft item of [LLM-1]/[LLM-R] against the candidates the model was
given and returns coded violations; the caller repairs once, then falls back to TEMPLATE for that
item only. Texts checked: the template with its `{{slots}}` removed (labels are never checked, so
English tower names pass, D-75), and the optional limitation and recommendation texts.

| Rule | Code | Check |
|---|---|---|
| GR-01 | E10 | a digit, or a `language.quantity_words` phrase, outside a slot |
| GR-02 | E12 | a `forbidden_phrases` phrase or a URL |
| GR-03 | E11 | unknown candidate, a ref outside the item's candidates, a slot the candidate lacks, template slots ≠ slot refs |
| GR-04 | SCOPE_VIOLATION | a literal unit code (`language.unit_code_pattern`): codes only come through slots |
| GR-03 | E11 | unknown candidate, ref outside the item, slot the candidate lacks, template slots ≠ refs |
| GR-04 | SCOPE_VIOLATION | a literal unit code (`language.unit_code_pattern`): codes come through slots |
| GR-08 | LANGUAGE_MISMATCH | no Vietnamese diacritic, an English word, a cause code, or a cause item without `{{cause_label}}` |
| 6.3 | SENTENCE_TOO_LONG | more than `language.max_words` words |

`scan_injection` (GR-05, E13) looks for instruction-like text in inputs (question, DW labels); the
pipeline logs INSIGHT_SECURITY_EVENT and keeps treating the text as data.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .contracts import DraftItem, InsightCandidate, InsightTaskRequest
from .render import SLOT, slot_names
from .settings import SemanticConfig, normalize_phrase

VI_DIACRITIC = re.compile(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", re.IGNORECASE)
DIGIT = re.compile(r"\d")
URL = re.compile(r"https?://|www\.", re.IGNORECASE)
WORD = re.compile(r"[^\W\d_]+")
PEER_SLOTS = frozenset({"spread", "peers"})
CAUSE_TYPES = frozenset({"ROOT_CAUSE_SIGNAL", "CAUSE_DISTRIBUTION"})


@dataclass(frozen=True)
class Violation:
    rule: str
    code: str
    detail: str


def _has_phrase(text: str, phrase: str) -> bool:
    """Whole-word match on NFC-lowercased text."""
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalize_phrase(text)) is not None


def _prose(item: DraftItem) -> list[str]:
    texts = [SLOT.sub(" ", item.template)]
    texts += [t for t in (item.limitation_text, item.recommendation_text) if t]
    return texts


def _references(item: DraftItem, candidates: Mapping[str, InsightCandidate], cfg: SemanticConfig) -> list[Violation]:
    out: list[Violation] = []
    unknown = [c for c in item.candidate_ids if c not in candidates]
    if unknown:
        out.append(Violation("GR-03", "E11", f"candidates not given to the model: {', '.join(unknown)}"))
    in_template, bound = set(slot_names(item.template)), {s.slot for s in item.slots}
    if in_template != bound:
        out.append(Violation("GR-03", "E11", f"template slots {sorted(in_template)} ≠ slot refs {sorted(bound)}"))
    for ref in item.slots:
        candidate_id, _, slot = ref.ref.partition(".")
        candidate = candidates.get(candidate_id)
        if candidate_id not in item.candidate_ids or candidate is None:
            out.append(Violation("GR-03", "E11", f"{ref.slot}: {candidate_id} is not a candidate of this item"))
        elif slot not in candidate.slots and slot not in cfg.language.label_slots:
            out.append(Violation("GR-03", "E11", f"{ref.slot}: candidate {candidate_id} has no slot {slot!r}"))
    return out


def _numbers_and_phrases(item: DraftItem, cfg: SemanticConfig) -> list[Violation]:
    out: list[Violation] = []
    lang = cfg.language
    for text in _prose(item):
        if DIGIT.search(text) or any(_has_phrase(text, w) for w in lang.quantity_words):
            out.append(Violation("GR-01", "E10", f"number outside a slot: {text!r}"))
        if URL.search(text) or any(p in normalize_phrase(text) for p in cfg.forbidden_phrases):
            out.append(Violation("GR-02", "E12", f"forbidden language: {text!r}"))
        if re.search(lang.unit_code_pattern, text):
            out.append(Violation("GR-04", "SCOPE_VIOLATION", f"unit code outside a slot: {text!r}"))
    return out


def _recommendation(
    item: DraftItem, given: list[InsightCandidate], cfg: SemanticConfig, request: InsightTaskRequest
) -> list[Violation]:
    text = item.recommendation_text
    if not text:
        return []
    out: list[Violation] = []
    actionable = any(c.action_code for c in given) and not all(c.task == "T5" for c in given)
    if not actionable or request.intent == "PERFORMANCE_METRIC_LOOKUP":
        out.append(Violation("GR-06", "RECOMMENDATION_NOT_ALLOWED", "no action code for this item, or a metric lookup"))
    lowered = normalize_phrase(text)
    prefixed = any(lowered.startswith(p) for p in cfg.language.recommendation_prefixes)
    if not prefixed or any(_has_phrase(text, p) for p in cfg.language.imperative_phrases):
        out.append(Violation("GR-06", "IMPERATIVE_RECOMMENDATION", f"not a suggestion: {text!r}"))
    return out


def _comparisons(item: DraftItem, given: list[InsightCandidate], cfg: SemanticConfig) -> list[Violation]:
    out: list[Violation] = []
    strong = [p for p in cfg.language.strong_comparison_phrases if any(_has_phrase(t, p) for t in _prose(item))]
    if strong and not all(c.significant for c in given):
        out.append(Violation("GR-07", "STRONG_CLAIM_NOT_SIGNIFICANT", f"{strong} on a non-significant candidate"))
    hidden = {
        c.candidate_id
        for c in given
        if "GROUP_TOO_SMALL" in c.dq_flags
        and c.cause_code in cfg.allowed_cause_codes
        and c.cause_code is not None
        and cfg.cause(c.cause_code).uses_peer_group
    }
    if any(s.ref.partition(".")[0] in hidden and s.ref.partition(".")[2] in PEER_SLOTS for s in item.slots):
        out.append(Violation("GR-07", "PEER_HIDDEN", "peer numbers with fewer than describe_min peers (D-71)"))
    return out


def _language(item: DraftItem, given: list[InsightCandidate], cfg: SemanticConfig) -> list[Violation]:
    out: list[Violation] = []
    whitelist = {normalize_phrase(w) for w in cfg.english_whitelist}
    stopwords = set(cfg.language.english_stopwords) - whitelist
    for text in _prose(item):
        words = {normalize_phrase(w) for w in WORD.findall(text)}
        english = sorted(words & stopwords)
        if not VI_DIACRITIC.search(text) or english:
            out.append(Violation("GR-08", "LANGUAGE_MISMATCH", f"not Vietnamese ({english}): {text!r}"))
        if any(code in text for code in cfg.allowed_cause_codes):
            out.append(Violation("GR-08", "LANGUAGE_MISMATCH", f"cause code instead of {{{{cause_label}}}}: {text!r}"))
    names_cause = any(c.insight_type in CAUSE_TYPES and c.cause_code for c in given)
    if names_cause and "cause_label" not in slot_names(item.template):
        out.append(Violation("GR-08", "LANGUAGE_MISMATCH", "a cause is named without {{cause_label}}"))
    return out


def _length(item: DraftItem, cfg: SemanticConfig) -> list[Violation]:
    words = len(SLOT.sub("X", item.template).split())
    if words > cfg.language.max_words:
        return [Violation("6.3", "SENTENCE_TOO_LONG", f"{words} words > {cfg.language.max_words}")]
    return []


def validate_item(
    item: DraftItem, candidates: Mapping[str, InsightCandidate], cfg: SemanticConfig, request: InsightTaskRequest
) -> list[Violation]:
    given = [candidates[c] for c in item.candidate_ids if c in candidates]
    return (
        _references(item, candidates, cfg)
        + _numbers_and_phrases(item, cfg)
        + _recommendation(item, given, cfg, request)
        + _comparisons(item, given, cfg)
        + _language(item, given, cfg)
        + _length(item, cfg)
    )


def scan_injection(texts: Iterable[str], cfg: SemanticConfig) -> list[str]:
    """The inputs that look like instructions (GR-05); they stay data, the pipeline logs an event."""
    patterns = [re.compile(p, re.IGNORECASE) for p in cfg.language.injection_patterns]
    return [t for t in texts if any(p.search(t) for p in patterns)]
