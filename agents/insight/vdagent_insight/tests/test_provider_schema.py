"""Provider schema (D-34): a lowered JSON Schema both Gemini and OpenAI strict accept."""

from __future__ import annotations

import json
from typing import Any

from ..contracts import LlmInsightDraft
from ..llm.schema import provider_schema

DROPPED = {"$ref", "$defs", "title", "default", "maxLength", "minLength", "maxItems", "minItems"}


def walk(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        found.append(node)
        for value in node.values():
            found += walk(value)
    elif isinstance(node, list):
        for item in node:
            found += walk(item)
    return found


def test_the_draft_schema_is_inlined_closed_and_fully_required() -> None:
    schema = provider_schema(LlmInsightDraft)
    nodes = walk(schema)
    assert not {k for n in nodes for k in n} & DROPPED
    objects = [n for n in nodes if n.get("type") == "object"]
    assert objects
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert sorted(obj["required"]) == sorted(obj["properties"])


def test_optional_fields_become_nullable_and_the_answer_still_validates() -> None:
    schema = provider_schema(LlmInsightDraft)
    item = schema["properties"]["selected"]["items"]
    assert {"type": "null"} in item["properties"]["limitation_text"]["anyOf"]
    answer = {
        "selected": [
            {
                "candidate_ids": ["C1"],
                "template": "Căn {{unit}}.",
                "slots": [{"slot": "unit", "ref": "C1.unit"}],
                "limitation_text": None,
            }
        ],
        "skipped": [],
    }
    assert LlmInsightDraft.model_validate_json(json.dumps(answer)).selected[0].limitation_text is None


def test_the_schema_is_stable_byte_for_byte() -> None:
    assert json.dumps(provider_schema(LlmInsightDraft), sort_keys=True) == json.dumps(
        provider_schema(LlmInsightDraft), sort_keys=True
    )
