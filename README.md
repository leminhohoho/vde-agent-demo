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
- First time only:

  ```
  uv sync
  uv run python proto/scripts/gen.py      # gRPC stubs (gitignored)
  for a in orchestrator data compare insight report; do cp -n agents/$a/.env.example agents/$a/.env; done
  ```

  Then fill in `OPENAI_API_KEY`, `OPENAI_BASE_URL` and `LLM_MODEL` in each `agents/<name>/.env`
  (see [Environment variables](#environment-variables)).

## Environment variables

Each component reads its own `.env` file. Every `.env` is gitignored and excluded from Docker
images; commit changes to the `.env.example` next to it instead.

| File | Needed? | Create it with |
|---|---|---|
| `agents/<name>/.env`, one per agent | **Yes**, for each of the five agents | `cp agents/<name>/.env.example agents/<name>/.env`, then fill in the LLM settings |
| `backend/.env` | No: the Backend runs on `backend/config.yaml` alone | `cp backend/.env.example backend/.env`, then uncomment what you need |
| `agents/_template/.env` | Only to run the template's echo agent | `cp agents/_template/.env.example agents/_template/.env` |

### Agents (`agents/<name>/.env`)

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `OPENAI_API_KEY` | yes | — | API key for the model endpoint. |
| `OPENAI_BASE_URL` | yes | — | Base URL of an OpenAI-compatible endpoint, e.g. `https://…/v1`. |
| `LLM_MODEL` | yes | — | Model name served by that endpoint. It **must support tool calling**. |
| `LLM_TIMEOUT_S` | no | `120` | Timeout per LLM call, in seconds (must be > 0). |
| `VDAGENT_BACKEND` | no | `localhost:50050` | The Backend's agent hub, `host:port`. Docker compose sets `backend:50050`. |

The five agents need the same variables. They may share one model or each use their own.

- **Missing required variable:** the agent exits with code 2, naming the variable.
- **Precedence**, highest first: `agents/<name>/.env`, then the process environment, then any
  `.env` in a folder above the agent (it only fills variables still unset). So a key exported in
  your shell cannot shadow the one in the agent's file.
- **Identity:** an agent identifies itself only by its name (`NAME` in its `agent.py`). There is no
  credential. If the Backend does not list the name in `backend/config.yaml`, the agent exits
  with code 2.

### Backend (`backend/.env`, optional)

Each variable overrides the matching key of `backend/config.yaml`. The process environment wins
over the file.

| Variable | Default (`config.yaml`) | Meaning |
|---|---|---|
| `VDAGENT_AGENT_LISTEN` | `127.0.0.1:50050` | Address the agent hub listens on. `0.0.0.0:50050` accepts agents from other machines. |
| `VDAGENT_MCP_PUBLIC_URL` | `http://localhost:8000/mcp` | MCP URL handed to agents; it must be reachable from every agent machine. |
| `VDAGENT_BACKEND_DB` | `./var/backend.db` | Backend SQLite file (relative to where the backend is started). |
| `VDAGENT_WAREHOUSE_DB` | `./var/warehouse.db` | Warehouse SQLite file. |
| `VDAGENT_FRONTEND_DIST` | `./frontend/dist` | Built frontend, served at `/` if it exists. |
| `VDAGENT_MAX_DEPTH` | `4` | Maximum agent-call depth. |
| `VDAGENT_MAX_STEPS` | `12` | LLM calls per turn, sent to agents. |
| `VDAGENT_CONFIG` | `backend/config.yaml` | Which YAML to load. Set it in the shell: it has no effect inside `backend/.env`, which is looked up next to the chosen config file. |

`HOST` (for `make backend HOST=0.0.0.0`) is a make variable, not an environment setting: it is the
interface the HTTP server (UI, API, MCP) binds to.

### By setup

- **Everything on one machine:** create the five `agents/<name>/.env` files and fill in the LLM
  settings. No Backend file is needed.
- **Docker compose:** the same five agent files; they must exist or `docker compose up` fails.
  Compose overrides `VDAGENT_BACKEND` with `backend:50050` and configures the backend with
  `backend/config.compose.yaml`; `backend/.env` is not used.
- **Remote agents:** on the Backend machine set `VDAGENT_AGENT_LISTEN=0.0.0.0:50050` and
  `VDAGENT_MCP_PUBLIC_URL=http://<backend-host>:8000/mcp` (shell or `backend/.env`) and run
  `make backend HOST=0.0.0.0`. On each agent machine set `VDAGENT_BACKEND=<backend-host>:50050` in
  that agent's `.env`. See [Remote agent](#remote-agent).

## Makefile usage

| Target | Does |
|---|---|
| `make` / `make help` | Lists the targets. |
| `make backend` | Starts the backend (API, SSE, MCP, built UI) on http://localhost:8000 and the agent hub on `127.0.0.1:50050` (`agent_listen`). `HOST=0.0.0.0` serves HTTP to other machines. |
| `make agent-<name>` | Starts one agent (`orchestrator`, `data`, `compare`, `insight`, `report`) from its folder: `cd agents/<name> && uv run python -m vdagent_<name>`, configured by `agents/<name>/.env`. It dials the hub at `VDAGENT_BACKEND` and reconnects with backoff when the session drops. |
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

2. On the agent machine, in a checkout, create the agent's `.env` from its example and point it at
   the Backend:

   ```
   uv sync && uv run python proto/scripts/gen.py
   cp agents/<name>/.env.example agents/<name>/.env
   # edit agents/<name>/.env: VDAGENT_BACKEND=<backend-host>:50050, OPENAI_API_KEY, OPENAI_BASE_URL, LLM_MODEL
   make agent-<name>
   ```

### Notes on connecting from anywhere

- **The Backend knows who, not where.** It never dials agents and stores no agent addresses. It
  still lists agent names in `backend/config.yaml` (`agents:`): that list is the allowlist the
  hub accepts, the peer roster sent to every agent, and the key for MCP permissions. An agent can
  connect from any machine, but only under a listed name. Self-registration of new names is not
  supported.
- **Hub address.** The hub listens on `127.0.0.1:50050` by default; set
  `VDAGENT_AGENT_LISTEN=0.0.0.0:50050` (or `agent_listen` in the config) to accept agents from
  other machines. Keep it on `127.0.0.1` when every agent is local.
- **MCP must be reachable too.** Agents call the Backend's MCP server over HTTP on every turn, so
  `mcp_public_url` (`VDAGENT_MCP_PUBLIC_URL`) must be an address the agent machines can reach, and
  the backend must serve HTTP beyond localhost (`make backend HOST=0.0.0.0`).
- **Security.** Agent sessions carry no credential (demo): anyone who can reach the hub can
  connect as any listed agent and receive users' messages and MCP tokens. The hub has no TLS
  either. Expose the hub and port 8000 only on networks you trust.
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

Then open http://localhost:8000. The backend service is configured by
`backend/config.compose.yaml` alone; each agent service reads its own `agents/<name>/.env`, with
`VDAGENT_BACKEND` overridden to `backend:50050` on the compose network, which is not published.
All five agent `.env` files must exist before `docker compose up`.

## Tests

```
uv run pytest                      # backend + every agent
uv run pytest agents/data          # one agent
cd frontend && npm test            # frontend
```
