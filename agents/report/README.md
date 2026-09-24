# report agent

Builds charts (`create_chart`) and a saved markdown report (`save_report`) from the findings and datasets it is given; replies with the report id.

Built from [`agents/_template`](../_template/README.md): `host.py`, `contract.py`, `__main__.py` and
`tests/test_host.py` are template copies — do not edit them here. The brain is a thin tool-calling
loop over LiteLLM (`agent.py`, `llm.py`, `mcp_client.py`); its role prompt is
`vdagent_report/prompts/system.md`, the summariser prompt `prompts/compact.md`.

MCP tools granted by the Backend (`backend/vdagent_backend/mcp/tools.py`): `create_chart`, `save_report`, `describe_dataset`, `get_dataset_rows`;
plus `send_to_agent` to reach the other agents.

## Run

```
make agent-report                                   # from the repo root
cd agents/report && uv run python -m vdagent_report     # same, by hand
```

The agent connects out to the Backend and reconnects with backoff; start it before or after the
Backend. It needs no listening port.

Configure it in `agents/report/.env` (gitignored; it wins over the process env).

| Variable | |
|---|---|
| `VDAGENT_BACKEND` | Hub address `host:port`, default `localhost:50050`. |
| `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `LLM_MODEL` | Required. OpenAI-compatible endpoint with tool calling. |
| `LLM_TIMEOUT_S` | Per LLM call, default 120. |

## Test

```
uv run pytest agents/report
```
