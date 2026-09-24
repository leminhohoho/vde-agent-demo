# vdagent

A proof-of-concept multi-agent analytics assistant. A user asks questions about a retail sales
warehouse; five LLM agents (orchestrator, data, compare, insight, report) collaborate by messaging
each other through the Backend, which also serves the React UI and an MCP tool server. Each agent
process dials the Backend's agent hub (gRPC, port 50050) and keeps one session open, so agents can
run on any machine that can reach the Backend — no agent listens on a port.

- Design: [`docs/superpowers/specs/2026-09-24-vdagent-design.md`](docs/superpowers/specs/2026-09-24-vdagent-design.md)
- Agent template design: [`docs/superpowers/specs/2026-09-24-agent-template-design.md`](docs/superpowers/specs/2026-09-24-agent-template-design.md)
- Agents connect to the Backend: [`docs/superpowers/specs/2026-09-24-agent-connect-direction-design.md`](docs/superpowers/specs/2026-09-24-agent-connect-direction-design.md)
- Building an agent: [`agents/_template/README.md`](agents/_template/README.md)

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python 3.12 is picked up from `.python-version`), GNU make, Node 22 for the frontend.
- A repo-root `.env` with the agents' model endpoint (any OpenAI-compatible server with tool
  calling) and one hub token per agent. The Backend and each agent read the same
  `VDAGENT_AGENT_TOKEN_<NAME>` variable; the Backend accepts an agent's session only with its token.

  ```
  OPENAI_API_KEY=…
  OPENAI_BASE_URL=https://…/v1
  LLM_MODEL=…
  VDAGENT_AGENT_TOKEN_ORCHESTRATOR=…
  VDAGENT_AGENT_TOKEN_DATA=…
  VDAGENT_AGENT_TOKEN_COMPARE=…
  VDAGENT_AGENT_TOKEN_INSIGHT=…
  VDAGENT_AGENT_TOKEN_REPORT=…
  ```

  Generate each token with `python -c "import secrets; print(secrets.token_urlsafe(24))"`. An agent
  without a token on the Backend side can never connect (the Backend logs a warning at startup).

- Optional `agents/<name>/.env`: overrides both the process environment and the root `.env` for
  that agent only (e.g. a different `OPENAI_API_KEY`). Precedence: agent `.env` > process env >
  root `.env`.

- First time only:

  ```
  uv sync
  uv run python proto/scripts/gen.py      # gRPC stubs (gitignored)
  ```

## Makefile usage

| Target | Does |
|---|---|
| `make` / `make help` | Lists the targets. |
| `make backend` | Starts the backend (API, SSE, MCP, built UI) on http://localhost:8000 and the agent hub on `127.0.0.1:50050` (`agent_listen`). `HOST=0.0.0.0` serves HTTP to other machines. |
| `make agent-<name>` | Starts one agent (`orchestrator`, `data`, `compare`, `insight`, `report`); it dials the hub at `VDAGENT_BACKEND` (default `localhost:50050`) and reconnects with backoff when the session drops. |
| `make reset-db` | Deletes `var/backend.db` and `var/warehouse.db` and reseeds them (demo users Alice and Bob, the deterministic warehouse). |

A typical local session:

```
make reset-db              # first run, or to start again from clean data
make backend               # terminal 1
make agent-orchestrator    # one terminal per agent: orchestrator, data, compare, insight, report
make agent-data            # (before or after the backend: agents retry until it is up)
…
cd frontend && npm install && npm run dev   # another terminal → http://localhost:5173
```

An agent is healthy in the UI while its session is connected. Stopping an agent turns it
unhealthy (its in-flight turns fail with `UNAVAILABLE: agent disconnected`); starting it again
turns it healthy without restarting the backend.

Stop the backend and agents before `make reset-db`: it deletes the SQLite files, and a running
backend would keep writing to the deleted ones. The database paths can be overridden
(`make reset-db BACKEND_DB=/tmp/b.db WAREHOUSE_DB=/tmp/w.db`); start the backend with the matching
`VDAGENT_BACKEND_DB` / `VDAGENT_WAREHOUSE_DB` to use them.

## Remote agent

Run an agent on another machine (or another checkout) against this Backend:

1. On the Backend machine, accept agents and serve MCP on the network:

   ```
   VDAGENT_AGENT_LISTEN=0.0.0.0:50050 VDAGENT_MCP_PUBLIC_URL=http://<backend-host>:8000/mcp make backend HOST=0.0.0.0
   ```

2. On the agent machine, in a checkout, create `agents/<name>/.env`:

   ```
   VDAGENT_BACKEND=<backend-host>:50050
   VDAGENT_AGENT_TOKEN_<NAME>=…        # the same value the Backend has
   OPENAI_API_KEY=…
   OPENAI_BASE_URL=https://…/v1
   LLM_MODEL=…
   ```

   then `uv sync && uv run python proto/scripts/gen.py && make agent-<name>`.

### Notes on connecting from anywhere

- **The Backend knows who, not where.** It never dials agents and stores no agent addresses. It
  still lists agent names in `backend/config.yaml` (`agents:`): that list is the allowlist the
  hub accepts, the peer roster sent to every agent, and the key for MCP permissions. An agent can
  connect from any machine, but only under a listed name with that name's token. Self-registration
  of new names is not supported.
- **Hub address.** The hub listens on `127.0.0.1:50050` by default; set
  `VDAGENT_AGENT_LISTEN=0.0.0.0:50050` (or `agent_listen` in the config) to accept agents from
  other machines. Keep it on `127.0.0.1` when every agent is local.
- **MCP must be reachable too.** Agents call the Backend's MCP server over HTTP on every turn, so
  `mcp_public_url` (`VDAGENT_MCP_PUBLIC_URL`) must be an address the agent machines can reach, and
  the backend must serve HTTP beyond localhost (`make backend HOST=0.0.0.0`).
- **Security.** The hub has no TLS; the per-agent tokens are the only gate, and a leaked token
  lets someone impersonate that one agent. Do not expose the hub or port 8000 on networks you do
  not trust without adding TLS (for example behind a TLS-terminating proxy or VPN).
- **One process per agent name.** A new session for a connected name replaces the old one, and
  the old one's in-flight turns fail. The replaced process reconnects, so two running copies of
  the same agent keep replacing each other: run exactly one.
- **Health and failures.** An agent is healthy while its session is connected. A stopped or
  crashed agent turns unhealthy at once; a silently dropped network is detected by gRPC keepalive
  within about 30 s. Losing a session fails that agent's in-flight turns with
  `UNAVAILABLE: agent disconnected`; the agent reconnects with backoff (0.5 s doubling to 10 s),
  including after a Backend restart.

## Docker

```
docker compose up --build
```

Then open http://localhost:8000. The backend and agent services read `.env` (tokens, LLM
settings); agents dial `backend:50050` on the compose network, which is not published.

## Tests

```
uv run pytest                      # backend + every agent
uv run pytest agents/data          # one agent
cd frontend && npm test            # frontend
```
