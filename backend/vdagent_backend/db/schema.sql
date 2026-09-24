CREATE TABLE IF NOT EXISTS users (
  id          TEXT PRIMARY KEY,               -- 'u_…'
  name        TEXT NOT NULL,
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS tasks (
  id          TEXT PRIMARY KEY,               -- 't_…'
  user_id     TEXT NOT NULL REFERENCES users(id),
  root_agent  TEXT NOT NULL,                  -- agent whose chat the human posted in
  status      TEXT NOT NULL CHECK (status IN ('running','completed','failed','cancelled')),
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_tasks_user ON tasks(user_id, created_at);

CREATE TABLE IF NOT EXISTS invocations (
  id           TEXT PRIMARY KEY,              -- 'inv_…'
  task_id      TEXT NOT NULL REFERENCES tasks(id),
  user_id      TEXT NOT NULL REFERENCES users(id),
  agent        TEXT NOT NULL,                 -- callee = stack owner
  caller       TEXT NOT NULL,                 -- 'user' or agent name
  parent_id    TEXT REFERENCES invocations(id),
  tool_call_id TEXT,                          -- parent's send_to_agent tool call id
  depth        INTEGER NOT NULL,
  inbound_text TEXT NOT NULL,
  status       TEXT NOT NULL CHECK (status IN ('queued','running','completed','failed','cancelled','rejected')),
  result_text  TEXT,
  error        TEXT,
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  started_at   TEXT,
  finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_inv_task  ON invocations(task_id);
CREATE INDEX IF NOT EXISTS ix_inv_stack ON invocations(user_id, agent, status);

CREATE TABLE IF NOT EXISTS messages (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id         TEXT NOT NULL REFERENCES users(id),
  agent           TEXT NOT NULL,              -- stack = (user_id, agent)
  seq             INTEGER NOT NULL,           -- per-stack order, starts at 1
  task_id         TEXT NOT NULL REFERENCES tasks(id),
  invocation_id   TEXT NOT NULL REFERENCES invocations(id),
  role            TEXT NOT NULL CHECK (role IN ('user','assistant','tool')),
  sender          TEXT,                       -- role=user: 'user' or agent name
  content         TEXT NOT NULL DEFAULT '',   -- raw text (no [from:] prefix)
  tool_calls_json TEXT,                       -- role=assistant: [{"id","name","arguments_json"}]
  tool_call_id    TEXT,                       -- role=tool
  compacted       INTEGER NOT NULL DEFAULT 0 CHECK (compacted IN (0,1)),
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE (user_id, agent, seq)
);
CREATE INDEX IF NOT EXISTS ix_msg_task ON messages(task_id);

CREATE TABLE IF NOT EXISTS stack_summaries (
  user_id    TEXT NOT NULL REFERENCES users(id),
  agent      TEXT NOT NULL,
  summary    TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (user_id, agent)
);

CREATE TABLE IF NOT EXISTS datasets (
  id            TEXT PRIMARY KEY,             -- 'ds_…'; also its table name in query_datasets
  user_id       TEXT NOT NULL REFERENCES users(id),
  invocation_id TEXT NOT NULL REFERENCES invocations(id),
  name          TEXT,
  source_sql    TEXT NOT NULL,
  columns_json  TEXT NOT NULL,                -- [{"name","type"}]
  rows_json     TEXT NOT NULL,                -- [[…], …], ≤ 10 000 rows
  row_count     INTEGER NOT NULL,
  truncated     INTEGER NOT NULL DEFAULT 0 CHECK (truncated IN (0,1)),
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS ix_ds_user ON datasets(user_id);

CREATE TABLE IF NOT EXISTS charts (
  id            TEXT PRIMARY KEY,             -- 'ch_…'
  user_id       TEXT NOT NULL REFERENCES users(id),
  invocation_id TEXT NOT NULL REFERENCES invocations(id),
  dataset_id    TEXT NOT NULL REFERENCES datasets(id),
  title         TEXT NOT NULL,
  spec_json     TEXT NOT NULL,                -- Vega-Lite v5, data inlined
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS reports (
  id            TEXT PRIMARY KEY,             -- 'rp_…'
  user_id       TEXT NOT NULL REFERENCES users(id),
  invocation_id TEXT NOT NULL REFERENCES invocations(id),
  title         TEXT NOT NULL,
  markdown      TEXT NOT NULL,
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS ix_rp_user ON reports(user_id, created_at);
