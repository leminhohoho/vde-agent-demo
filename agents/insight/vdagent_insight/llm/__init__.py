"""LLM provider layer (spec §6.3–6.4, §7.1, §7.5): steps 5–6 and [LLM-R].

Phase 0: the `LlmClient` interface and `FakeLlmClient`. Phase P3 adds `gemini_client.py`
(google-genai), `openai_client.py` (openai Responses), `preflight.py` and `usage.py` (LlmUsage + cost).
"""

from .base import LlmClient, LlmError, LlmSchemaError, LlmTransientError, Reasoning
from .fake import FakeLlmClient, FakeReply, RecordedCall

__all__ = [
    "FakeLlmClient",
    "FakeReply",
    "LlmClient",
    "LlmError",
    "LlmSchemaError",
    "LlmTransientError",
    "Reasoning",
    "RecordedCall",
]
