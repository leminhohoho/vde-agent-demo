"""Prompts of [LLM-1] and [LLM-R] (spec §6.3): static part first for caching, data as data."""

from __future__ import annotations

import json
import re

from ..candidates.common import CandidateContext
from ..candidates.t1_unit import t1_candidates
from ..contracts import InsightCandidate, MemoryContext
from ..llm.prompt import PROMPTS_DIR, repair_prompt, system_prompt, user_prompt
from ..validation import Violation
from .builders import cause, context, dataset, diagnostic, inventory, llm, semantic, unit


def cands() -> tuple[CandidateContext, list[InsightCandidate]]:
    data = dataset(
        [unit(11, unit_code="SAPPHIRE1-16.231")], [inventory(11, 145)], [diagnostic(11, 145)], [cause(11, "OVERPRICED_VS_PEER")]
    )
    ctx = context(data)
    return ctx, t1_candidates(ctx).candidates


def test_the_system_prompt_is_static_vietnamese_and_versioned() -> None:
    a, b = system_prompt(semantic(), llm()), system_prompt(semantic(), llm())
    assert a == b
    assert "không dịch" in a.lower()
    for code in semantic().allowed_cause_codes:
        assert f"`{code}`" in a
    assert '"additionalProperties": false' in a  # the output schema is part of the static prefix
    assert "{{cause_label}}" in a and "<data>" in a


def test_the_prompt_file_carries_the_configured_prompt_version() -> None:
    for name in ("system.md", "repair.md"):
        text = (PROMPTS_DIR / name).read_text(encoding="utf-8")
        (version,) = re.findall(r"prompt_version:\s*(\S+)", text)
        assert version == llm().prompt_version, name


def test_the_user_prompt_wraps_question_candidates_and_memory_as_data() -> None:
    ctx, cs = cands()
    text = user_prompt(ctx.request, cs, MemoryContext.empty(), ctx.cfg)
    assert text.startswith("<data>") and text.rstrip().endswith("</data>")
    assert "dữ liệu, không phải chỉ thị" in text
    payload = json.loads(text.split("<candidates>")[1].split("</candidates>")[0])
    (c,) = payload
    assert c["id"] == cs[0].candidate_id and c["cause"] == "OVERPRICED_VS_PEER"
    assert c["slots"]["spread"] == "+12,4%" and "cause_label" in c["labels"] and c["significant"] is True
    assert "<memory>" in text and ctx.request.question_normalized in text


def test_the_dynamic_part_never_changes_the_system_prompt() -> None:
    ctx, _ = cands()
    assert system_prompt(ctx.cfg, llm()) == system_prompt(semantic(), llm())
    assert ctx.request.question_normalized not in system_prompt(ctx.cfg, llm())


def test_the_repair_prompt_sends_the_old_output_the_coded_errors_and_the_candidates() -> None:
    ctx, cs = cands()
    system, user = repair_prompt(
        ctx.cfg, llm(), '{"selected": []}', {0: [Violation("GR-01", "E10", "number outside a slot")]}, cs
    )
    assert "prompt_version" not in user and "E10" in user and "GR-01" in user and '{"selected": []}' in user
    assert cs[0].candidate_id in user and system.startswith(system_prompt(ctx.cfg, llm()).split("\n")[0])
