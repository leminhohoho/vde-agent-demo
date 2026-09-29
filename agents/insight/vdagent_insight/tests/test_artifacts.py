"""Canonical JSON + SHA-256 (spec §4.1, luật 10) and the fixture-backed `ArtifactReader`."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path

import pytest

from ..artifacts import ArtifactNotFound, FixtureArtifactReader, canonical_json, content_hash


def test_canonical_json_is_independent_of_key_order_and_whitespace() -> None:
    a = canonical_json({"b": 1, "a": {"y": [1, 2], "x": "Căn"}})
    b = canonical_json({"a": {"x": "Căn", "y": [1, 2]}, "b": 1})
    assert a == b == '{"a":{"x":"Căn","y":[1,2]},"b":1}'.encode()


def test_decimals_are_written_as_exact_strings() -> None:
    assert canonical_json({"v": Decimal("12.40")}) == b'{"v":"12.40"}'


def test_floats_are_refused() -> None:
    with pytest.raises(TypeError, match="float"):
        canonical_json({"v": 12.4})


def test_content_hash_is_sha256_hex_of_the_canonical_bytes() -> None:
    data = {"a": 1}
    assert content_hash(data) == hashlib.sha256(b'{"a":1}').hexdigest()
    assert len(content_hash(data)) == 64


def write_artifact(folder: Path, artifact_id: str, payload: Mapping[str, object]) -> None:
    doc = {
        "artifact_id": artifact_id,
        "artifact_type": "dq",
        "version": 1,
        "status": "VALID",
        "snapshot_id": "SNAP-1",
        "semantic_config_version": "sem-1",
        "payload": payload,
    }
    (folder / f"{artifact_id}.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


async def test_fixture_reader_returns_the_artifact_with_the_hash_of_what_it_read(tmp_path: Path) -> None:
    payload = {"overall_status": "PASS", "note": "Căn"}
    write_artifact(tmp_path, "ART-DQ-1", payload)
    art = await FixtureArtifactReader(tmp_path).read("ART-DQ-1")
    assert (art.artifact_id, art.artifact_type, art.status, art.snapshot_id) == ("ART-DQ-1", "dq", "VALID", "SNAP-1")
    assert art.payload == payload
    assert art.content_hash == content_hash(payload)


async def test_fixture_reader_reports_unknown_ids(tmp_path: Path) -> None:
    with pytest.raises(ArtifactNotFound, match="ART-X"):
        await FixtureArtifactReader(tmp_path).read("ART-X")
