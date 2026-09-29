"""Artifact ports (pipeline steps 1–2: read + hash check; step 10: immutable write).

- `canonical_json` / `content_hash`: SHA-256 over canonical JSON (sorted keys, no whitespace,
  UTF-8, decimals as exact strings, floats refused) — spec §4.1, luật 10.
- `ArtifactReader`: input artifacts (`metric`, `dq`, `dataset`, `market_context`) by id. The hash it
  reports is computed from what it actually read, so step 1 can compare it with the request's
  `content_hash` (pre-condition 3.4). `FixtureArtifactReader` reads JSON files (phases P0–P4;
  the real source is open, docs/OPEN_QUESTIONS.md Q2).
- `ArtifactWriter` (SQLite `var/insight_artifacts.db`, phase P4; Q3).

Never reads Comparison Artifacts, never queries the DW (luật 4).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from .contracts import ArtifactStatus, Contract, InputArtifact, InputArtifactType


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _plain(value.model_dump(mode="python"))
    if isinstance(value, float):
        raise TypeError(f"float {value!r} cannot be hashed canonically; use Decimal")
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"{type(value).__name__} is not JSON-canonicalisable")


def canonical_json(value: Any) -> bytes:
    return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


class ArtifactNotFound(LookupError):
    """Maps to E02 REQUIRED_ARTIFACT_MISSING."""


class ArtifactReader(Protocol):
    async def read(self, artifact_id: str) -> InputArtifact: ...


class _FixtureDoc(Contract):
    artifact_id: str
    artifact_type: InputArtifactType
    version: int
    status: ArtifactStatus
    snapshot_id: str
    semantic_config_version: str
    payload: dict[str, Any]


class FixtureArtifactReader:
    """`<folder>/<artifact_id>.json` files: the input envelope without a hash, plus its payload."""

    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def _read(self, artifact_id: str) -> InputArtifact:
        path = self._folder / f"{artifact_id}.json"
        if not path.is_file():
            raise ArtifactNotFound(f"artifact {artifact_id} not found")
        doc = _FixtureDoc.model_validate_json(path.read_text(encoding="utf-8"))
        return InputArtifact(**doc.model_dump(), content_hash=content_hash(doc.payload))

    async def read(self, artifact_id: str) -> InputArtifact:
        return await asyncio.to_thread(self._read, artifact_id)
