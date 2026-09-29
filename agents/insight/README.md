# insight agent

Insight Agent v2 ([docs/insight_agent_spec.md](../../docs/insight_agent_spec.md), v2.1) explains why
units, towers or projects sell slowly: root causes per unit, cause distribution, patterns, market
context and data limitations. It works from a frozen data-pack snapshot. The LLM only writes
`{{slot}}` templates, and the code fills every number from the data. Each insight carries evidence
and lineage.

It is a Backend plugin. `vdagent_insight/__init__.py` exports `setup(api, opts)`, which reads the
plugin's `.env` (`runtime.py`) and registers the agent `insight`. One turn is one deterministic
pipeline run (`agent.py`, steps 0–10) with at most two LLM calls: Gemini, with OpenAI as fallback.
There is no tool calling and no LangChain.

- `bridge.py`: the inbound message is either a JSON `InsightTaskRequest` (bare or in a ```json
  block), or free text containing a unit code, a tower name or "dự án <name>". Free text is the
  compat mode, kept until the Orchestrator sends JSON (D-10); it is turned into a request by fixed
  rules in `config/bridge.yaml`. The reply is a Vietnamese summary that states the data date, then
  the `artifact_id`, then a compact JSON block of the KEY insights (≤ 6 000 chars). Prompt
  injection in the message is logged as `INSIGHT_SECURITY_EVENT` and changes nothing.
- `store.py`: artifacts, idempotency keys and LLM usage, kept in `var/insight_artifacts.db`.
- `memory.py` (`CtxMemory`): insight references per conversation, kept in `ctx.memory`. It never
  stores numbers.

## Run

With the Backend, run `make backend` (or `uv run uvicorn vdagent_backend.app:app --port 8000`). It
loads this plugin along with the others. You can then chat with the agent directly:
`POST /api/agents/insight/messages` with the header `X-User-Id`.

Without the Backend, use the terminal demo. It follows the same path as an Orchestrator message:
bridge, then the data pack, then the pipeline, then the store, then the reply.

```
uv run python agents/insight/scripts/ask.py "Vì sao căn SAPPHIRE1-16.231 bán chậm?"
uv run python agents/insight/scripts/ask.py "Tại sao tòa Sapphire 1 có nhiều căn bán chậm?"
uv run python agents/insight/scripts/ask.py "Dự án The Beverly đang bán chậm vì lý do gì?"
uv run python agents/insight/scripts/ask.py --no-llm "DOM trung vị theo hướng ban công ở Sapphire 1?"
uv run python agents/insight/scripts/ask.py --events "…"      # also print the INSIGHT_* events
```

`ask.py` prints the reply, then the artifact id, status, LLM calls, cost and latency. Its options:

- `--no-llm`: TEMPLATE mode, no cost.
- `--provider gemini|openai`: use one provider only.
- `--source export|fixtures`: choose the data source.
- `--invocation <id>`: rerun the same task, which returns the stored artifact (TC-29).

Two other scripts: `scripts/tower_eval.py` measures how many drafted items of a tower question fall
back to templates (live, a few cents per run). `scripts/llm_probe.py` checks the keys and models.

## Configure

Put the variables in `agents/insight/.env` (gitignored; see `.env.example`). The plugin reads the
file itself with `dotenv_values()`, and its values win over the Backend's process environment.

| Variable | |
|---|---|
| `GEMINI_API_KEY`, `OPENAI_API_KEY` | LLM keys: Gemini primary, OpenAI fallback. No key → TEMPLATE mode. |
| `OPENAI_BASE_URL` | Optional OpenAI-compatible endpoint. |
| `INSIGHT_FORCE_PROVIDER` | `gemini` or `openai`: one provider, no fallback. |
| `INSIGHT_LLM` | `off`: TEMPLATE mode even with keys. |
| `INSIGHT_ARTIFACT_SOURCE`, `INSIGHT_EXPORT_DIR` | `export` (data pack in `<repo>/export`, the default when present) or `fixtures` (81-unit cut). |
| `INSIGHT_STORE_PATH` | SQLite store, default `<repo>/var/insight_artifacts.db`. |
| `INSIGHT_AS_OF` | Task clock for freshness: `snapshot` (default, 08:00 the day after the snapshot), `now`, or an ISO datetime with its offset. |

Model ids, limits, prices and `prompt_version` are in `config/llm.yaml`. Thresholds and catalogues
are in `config/semantic_insight.yaml`. Compat-mode keywords are in `config/bridge.yaml`. Changing a
prompt means raising `prompt_version` and rerunning TC-01→TC-33.

## Test

```
uv run pytest agents/insight                              # unit + TC-01→33 (+ data-pack golden if export/ exists)
INSIGHT_LIVE=1 uv run pytest agents/insight -m live -s    # real providers, about 0.01 USD
```

The data-pack golden test runs every one of the 1 139 overdue units through the pipeline and checks
the stated causes against the bridge table. The `export/` data pack is not in git.
