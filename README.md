# vdagent

A proof-of-concept multi-agent analytics assistant. A user asks questions about a retail sales
warehouse; five LLM agents (orchestrator, data, compare, insight, report) collaborate by messaging
each other through the Backend, which also serves the React UI and an MCP tool server.

- Design: [`docs/superpowers/specs/2026-09-24-vdagent-design.md`](docs/superpowers/specs/2026-09-24-vdagent-design.md)
- Agent template design: [`docs/superpowers/specs/2026-09-24-agent-template-design.md`](docs/superpowers/specs/2026-09-24-agent-template-design.md)
- Building an agent: [`agents/_template/README.md`](agents/_template/README.md)

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python 3.12 is picked up from `.python-version`), GNU make, Node 22 for the frontend.
- A repo-root `.env` for the agents' model endpoint (any OpenAI-compatible server with tool calling):

  ```
  OPENAI_API_KEY=…
  OPENAI_BASE_URL=https://…/v1
  LLM_MODEL=…
  ```

- First time only:

  ```
  uv sync
  uv run python proto/scripts/gen.py      # gRPC stubs (gitignored)
  ```

## Makefile usage

| Target | Does |
|---|---|
| `make` / `make help` | Lists the targets. |
| `make backend` | Starts the backend (API, SSE, MCP, built UI) on http://localhost:8000. |
| `make agent-<name>` | Starts one agent: `orchestrator` (:50051), `data` (:50052), `compare` (:50053), `insight` (:50054), `report` (:50055). |
| `make agents` | Starts all five agents in one terminal; Ctrl-C stops them all. |
| `make reset-db` | Deletes `var/backend.db` and `var/warehouse.db` and reseeds them (demo users Alice and Bob, the deterministic warehouse). |

A typical local session:

```
make reset-db          # first run, or to start again from clean data
make backend           # terminal 1
make agents            # terminal 2 (or one `make agent-<name>` per terminal)
cd frontend && npm install && npm run dev   # terminal 3 → http://localhost:5173
```

Stop the backend and agents before `make reset-db`: it deletes the SQLite files, and a running
backend would keep writing to the deleted ones. The database paths can be overridden
(`make reset-db BACKEND_DB=/tmp/b.db WAREHOUSE_DB=/tmp/w.db`); start the backend with the matching
`VDAGENT_BACKEND_DB` / `VDAGENT_WAREHOUSE_DB` to use them.

## Docker

```
docker compose up --build
```

Then open http://localhost:8000. The agent services read `.env`.

## Tests

```
uv run pytest                      # backend + every agent
uv run pytest agents/data          # one agent
cd frontend && npm test            # frontend
```
