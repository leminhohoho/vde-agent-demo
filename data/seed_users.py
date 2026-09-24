"""Apply the backend schema and insert the demo users (idempotent).

Usage: uv run python data/seed_users.py [PATH]
PATH defaults to `backend_db` from the backend config (`VDAGENT_BACKEND_DB` / `VDAGENT_CONFIG` honoured).
"""

from __future__ import annotations

import sqlite3
import sys

from vdagent_backend.config import load_config
from vdagent_backend.db.database import apply_schema

DEMO_USERS = [("u_000000000001", "Alice"), ("u_000000000002", "Bob")]


def main(argv: list[str]) -> int:
    path = argv[1] if len(argv) > 1 else load_config().backend_db
    apply_schema(path)
    conn = sqlite3.connect(path)
    try:
        with conn:
            conn.executemany("INSERT OR IGNORE INTO users (id, name) VALUES (?, ?)", DEMO_USERS)
        total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        conn.close()
    print(f"backend db: {path}  demo users ensured: {', '.join(n for _, n in DEMO_USERS)}  users total={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
