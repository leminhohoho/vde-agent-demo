# compare agent

Compares datasets, periods and segments (deltas, % change, rankings) with `query_datasets`; may ask data for missing data.

Built from [`agents/_template`](../_template/README.md): `host.py`, `contract.py`, `__main__.py` and
`tests/test_host.py` are template copies — do not edit them here. The brain is a thin tool-calling
loop over LiteLLM (`agent.py`, `llm.py`, `mcp_client.py`); its role prompt is
`vdagent_compare/prompts/system.md`, the summariser prompt `prompts/compact.md`.

MCP tools granted by the Backend (`backend/vdagent_backend/mcp/tools.py`): `query_datasets`, `describe_dataset`, `get_dataset_rows`;
plus `send_to_agent` to reach the other agents.

## Run

```
make agent-compare                                   # GRPC_PORT=50053
GRPC_PORT=50053 uv run python -m vdagent_compare      # same, by hand
```

Environment (from the repo-root `.env`; process env wins):

| Variable | |
|---|---|
| `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `LLM_MODEL` | Required. OpenAI-compatible endpoint with tool calling. |
| `LLM_TIMEOUT_S` | Per LLM call, default 120. |
| `GRPC_PORT` | Default 50051; must match `backend/config.yaml`. |

## Test

```
uv run pytest agents/compare
```
