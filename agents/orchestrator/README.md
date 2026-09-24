# orchestrator agent

Talks to the user, plans, delegates to the other agents with `send_to_agent`, and writes the final answer citing artifact ids. Never writes SQL.

Built from [`agents/_template`](../_template/README.md): `host.py`, `contract.py`, `__main__.py` and
`tests/test_host.py` are template copies — do not edit them here. The brain is a thin tool-calling
loop over LiteLLM (`agent.py`, `llm.py`, `mcp_client.py`); its role prompt is
`vdagent_orchestrator/prompts/system.md`, the summariser prompt `prompts/compact.md`.

MCP tools granted by the Backend (`backend/vdagent_backend/mcp/tools.py`): `describe_dataset`, `get_dataset_rows`;
plus `send_to_agent` to reach the other agents.

## Run

```
make agent-orchestrator                                   # from the repo root
cd agents/orchestrator && uv run python -m vdagent_orchestrator     # same, by hand
```

The agent connects out to the Backend and reconnects with backoff; start it before or after the
Backend. It needs no listening port.

Configure it in `agents/orchestrator/.env` (gitignored; it wins over the process env).

| Variable | |
|---|---|
| `VDAGENT_BACKEND` | Hub address `host:port`, default `localhost:50050`. |
| `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `LLM_MODEL` | Required. OpenAI-compatible endpoint with tool calling. |
| `LLM_TIMEOUT_S` | Per LLM call, default 120. |

## Test

```
uv run pytest agents/orchestrator
```
