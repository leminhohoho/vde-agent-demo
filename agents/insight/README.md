# insight agent

Insight Agent v2 ([docs/insight_agent_spec.md](../../docs/insight_agent_spec.md)): explains why units,
towers or projects sell slowly — root causes per unit, cause distribution, patterns, market context,
data limitations — from a frozen data-pack snapshot. Every number in an answer comes from the data
(the LLM only writes `{{slot}}` templates); every insight carries evidence and lineage.

A Backend plugin: `vdagent_insight/__init__.py` exports `setup(api, opts)`, which reads the plugin's
`.env` (runtime.py) and registers `insight`. One turn = one deterministic pipeline run (agent.py,
steps 0–10) with at most two LLM calls (Gemini, OpenAI fallback), no tool calling, no LangChain.

- `bridge.py`: the inbound message is a JSON `InsightTaskRequest` (bare or in a ```json block), or —
  compat mode until the Orchestrator sends JSON (D-10) — free text with a unit code, tower or project
  name, turned into a request by fixed rules (`config/bridge.yaml`). The reply is a Vietnamese
  summary, the `artifact_id` and a compact JSON block of the KEY insights (≤ 6 000 chars).
- `store.py`: artifacts, idempotency keys and LLM usage in `var/insight_artifacts.db`.
- `memory.py` (`CtxMemory`): insight references per conversation in `ctx.memory`, never numbers.

## Run

`make backend` (or `uv run uvicorn vdagent_backend.app:app --port 8000`) loads it with the other
plugins. Without the Backend:

```
uv run python agents/insight/scripts/ask.py "Vì sao tòa Sapphire 1 có nhiều căn bán chậm?"
uv run python agents/insight/scripts/ask.py --no-llm "Vì sao căn SAPPHIRE1-16.231 bán chậm?"
```

Configure it in `agents/insight/.env` (gitignored; see `.env.example`). The plugin reads the file
itself with `dotenv_values()`; its values win over the Backend's process environment.

| Variable | |
|---|---|
| `GEMINI_API_KEY`, `OPENAI_API_KEY` | LLM keys (Gemini primary, OpenAI fallback). None → TEMPLATE mode. |
| `OPENAI_BASE_URL` | Optional OpenAI-compatible endpoint. |
| `INSIGHT_FORCE_PROVIDER` | `gemini` or `openai`: one provider, no fallback. |
| `INSIGHT_LLM` | `off`: TEMPLATE mode even with keys. |
| `INSIGHT_ARTIFACT_SOURCE`, `INSIGHT_EXPORT_DIR` | `export` (data pack in `<repo>/export`, default when present) or `fixtures`. |
| `INSIGHT_STORE_PATH` | SQLite store, default `<repo>/var/insight_artifacts.db`. |
| `INSIGHT_AS_OF` | Task clock for freshness: `snapshot` (default), `now`, or an ISO datetime. |

Model ids, limits and prices: `config/llm.yaml`. Thresholds and catalogues: `config/semantic_insight.yaml`.

## Test

```
uv run pytest agents/insight                      # unit + TC tests (+ data-pack golden if export/ exists)
INSIGHT_LIVE=1 uv run pytest agents/insight -m live -s   # real providers, a few cents
```
