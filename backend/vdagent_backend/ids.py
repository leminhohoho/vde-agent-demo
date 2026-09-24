"""IDs (`<prefix>_<12 hex>`) and UTC ISO-8601 timestamps (§3.2)."""

import secrets
from datetime import UTC, datetime


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
