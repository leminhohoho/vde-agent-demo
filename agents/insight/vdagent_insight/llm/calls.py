"""One LLM call point with retry and provider fallback (spec §6, E08/E09, D-35).

`call_with_fallback`: the primary provider; a transient error (E08) is retried `retries` times after
`backoff_s`; still failing → the fallback provider once; still failing → E08 (the caller renders
TEMPLATE). A schema error (E09: 400, safety, truncation, invalid output) returns at once with the
provider that answered and its raw text, so the single repair goes to that provider. Every call that
produced usage (errors included) is kept, so the task cost counts it.

Retries and fallback belong to the same call point: a task still makes at most two LLM calls in the
sense of the spec ([LLM-1] and [LLM-R]).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from ..contracts import CallType, LlmUsage
from .base import LlmClient, LlmSchemaError, LlmTransientError, Reasoning


@dataclass
class CallOutcome:
    output: BaseModel | None = None
    client: LlmClient | None = None
    """The provider that answered (success or E09); None after E08."""
    error: Literal["E08", "E09"] | None = None
    raw: str | None = None
    usages: list[LlmUsage] = field(default_factory=list)
    events: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Call:
    system: str
    user: str
    schema: type[BaseModel]
    call_type: CallType
    reasoning: Reasoning


async def _attempt(client: LlmClient, out: CallOutcome, label: str, call: _Call) -> bool:
    """True when the call point is settled (answer or E09); False after a transient error."""
    try:
        output, usage = await client.generate_structured(
            system=call.system, user=call.user, schema=call.schema, call_type=call.call_type, reasoning=call.reasoning
        )
    except LlmTransientError as exc:
        if exc.usage is not None:
            out.usages.append(exc.usage)
        out.events.append(f"{label}_E08")
        return False
    except LlmSchemaError as exc:
        if exc.usage is not None:
            out.usages.append(exc.usage)
        out.events.append(f"{label}_E09")
        out.error, out.client, out.raw = "E09", client, exc.raw
        return True
    out.usages.append(usage)
    out.output, out.client, out.error = output, client, None
    return True


async def call_with_fallback(
    primary: LlmClient,
    fallback: LlmClient | None,
    *,
    system: str,
    user: str,
    schema: type[BaseModel],
    call_type: CallType,
    reasoning: Reasoning,
    retries: int,
    backoff_s: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> CallOutcome:
    out = CallOutcome()
    call = _Call(system, user, schema, call_type, reasoning)
    for attempt in range(retries + 1):
        if attempt:
            out.events.append("RETRY")
            await sleep(backoff_s)
        if await _attempt(primary, out, "PRIMARY", call):
            return out
    if fallback is not None:
        out.events.append("FALLBACK")
        if await _attempt(fallback, out, "FALLBACK", call):
            return out
    out.error = "E08"
    return out
