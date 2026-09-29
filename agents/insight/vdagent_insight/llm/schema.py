"""Provider schema (spec §6.3, D-34): the JSON Schema sent as Gemini `response_json_schema` and as
OpenAI `text.format` (json_schema, strict).

Generated from the Pydantic model and "lowered" to the subset both providers accept: `$ref` inlined,
every object closed (`additionalProperties: false`) with all its properties `required` (optional
fields stay nullable through `anyOf … null`), and `title`, `default` and length/count bounds removed.
Pydantic validates the answer in full afterwards, bounds included.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

DROPPED_KEYS = frozenset({"title", "default", "maxLength", "minLength", "maxItems", "minItems"})


def _lower(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, list):
        return [_lower(item, defs) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        return _lower(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
    out = {k: _lower(v, defs) for k, v in node.items() if k not in DROPPED_KEYS and k != "$defs"}
    if out.get("type") == "object" and "properties" in out:
        out["additionalProperties"] = False
        out["required"] = list(out["properties"])
    return out


def provider_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    lowered = _lower(schema, schema.get("$defs", {}))
    assert isinstance(lowered, dict)
    return lowered
