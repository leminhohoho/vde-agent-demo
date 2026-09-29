@../../AGENTS.md

# Insight Agent (spec v2.0: pipeline tất định 0–10, 2 lần gọi LLM)

Nguồn sự thật: [docs/insight_agent_spec.md](../../docs/insight_agent_spec.md) · tên bảng/cột: [docs/dw_schema_v3.1.0.md](../../docs/dw_schema_v3.1.0.md) · quyết định & câu hỏi mở: [docs/OPEN_QUESTIONS.md](../../docs/OPEN_QUESTIONS.md).
Chỉ sửa trong `agents/insight/` (ngoại lệ đã duyệt: `uv.lock`, target Makefile `insight-*`). Spec mơ hồ/mâu thuẫn → DỪNG, ghi OPEN_QUESTIONS, hỏi.

## Luật bất biến
1. LLM không sinh số: mọi số đi qua slot `{{...}}`, claim_binder điền giá trị thật.
2. Ngưỡng, giá, model, danh mục chỉ ở `config/*.yaml` có version. Không hard-code.
3. `Decimal` cho mọi số nghiệp vụ và chi phí; cấm float (YAML: decimal viết dạng chuỗi có ngoặc).
4. Không đọc Comparison Artifact, không gọi agent khác, không query DW.
5. Gọi LLM qua SDK trực tiếp (`google-genai`, `openai` Responses) sau `LlmClient`. Không LangChain, tool calling, MCP; không import `tools.py`, `mcp_client.py`.
6. Chỉ 2 điểm gọi LLM ([LLM-1], [LLM-R]); reasoning tắt, repair tối đa `low`. Job memory: `call_type = MEMORY`.
7. Mỗi lần gọi LLM ghi `LlmUsage` + `cost_usd` (spec 7.5).
8. Validator GR-01→08 chạy trên mọi output LLM; lỗi → repair 1 lần → TEMPLATE theo item.
9. Memory (9.5): chỉ tham chiếu + sở thích, cấm số liệu/kết luận; memory lỗi không làm hỏng task.
10. Artifact bất biến, SHA-256 trên canonical JSON (`artifacts.content_hash`).
11. Gate, candidates, validation, render, assess là hàm thuần: không I/O, không LLM, bootstrap seed cố định.
12. Đổi `prompts/v2/system.md`/`repair.md` → tăng `prompt_version` (ghi trong file + `config/llm.yaml`, có test), chạy lại TC-01→TC-33.
13. Contract: Pydantic v2 `extra="forbid"`, giữ nguyên tên field/enum của spec (`contracts.py`).
14. TDD: test đỏ trước, rồi mới code. Interface I/O là `async` (sdk R10).

## Cây thư mục
```
config/semantic_insight.yaml  ngưỡng 5.4 + danh mục 7.6 (version = semantic_config_version)   [bước 2]
config/llm.yaml               insight_llm_config 7.5 + prompt_version                          [bước 2]
vdagent_insight/
  __init__.py   setup(): đăng ký agent        bridge.py  ctx → run_task → emit (P4)          [0, trả kết quả]
  agent.py      pipeline 0–10 (P4)            settings.py  .env + loader YAML có validate    [2]
  contracts.py  model Pydantic                artifacts.py  canonical JSON, Reader/Writer    [1, 2, 10]
  view.py       join dataset theo căn, BR-01  formatting.py  hiển thị số vi-VN               [3, 4, 8]
  gate.py       Sufficiency Gate 5.5          candidates/   T1 T2 T3 T5 T7, stats, priority   [3, 4, 5]
  llm/          LlmClient, Fake, gemini, openai, schema, calls (retry/fallback), preflight, prompt, steps, usage [5, 6, R]
  validation.py GR-01→08 + quét injection     render.py  claim_binder, nhãn, TEMPLATE        [7, 8]
  narrate.py    áp draft từng item, fallback TEMPLATE, LLM_SKIPPED                              [7, 8]
  assess.py     confidence, KEY, status       memory.py  InsightMemory, NoOpMemory           [2, 9, 10]
  prompts/      v2/system, v2/repair (bản cũ system/compact/extract giữ tới P4)
  tests/        test_*.py, builders.py, fixtures/tcNN/ (request.json + artifacts/*.json [+ config/])
```
`agent.py`, `bridge.py`, `MemoryMiddleware` và `tests/test_agent.py` là bản LangChain cũ, giữ tới P4 rồi thay.

## Lệnh (từ gốc repo)
- `make insight-test` = `uv run pytest agents/insight`
- `make insight-lint` = `uvx ruff@0.16.9 check agents/insight`, `... format --check agents/insight`,
  `uv run --with basedpyright==1.40.1 python -m basedpyright agents/insight`
  (không có `make` thì chạy thẳng các lệnh trên). Live: `INSIGHT_LIVE=1 uv run pytest agents/insight -m live -s`.

## Giả định đang dùng (chi tiết: OPEN_QUESTIONS)
- Input: `InsightTaskRequest` JSON trong message; lõi `run_task(request, deps)` không biết ctx.
- Artifact đầu vào đọc qua `ArtifactReader`; P0–P4 dùng `FixtureArtifactReader`. Payload `metric`/`dq`/`dataset`
  là shape tự đặt: `TODO(data-agent-contract)`.
- Mart có thêm cột `is_peer_sample_constrained`, `peer_count` (không có trong DW v3.1.0).
- Key thiếu trong 5.4 (`max_units_in_context`, `min_cause_share_pct`, `conflict_tolerance_pct`, bootstrap) = PENDING.
- `insight_templates` (TEMPLATE ngoài ROOT_CAUSE) và `recommendation_text` là bản nháp, cần Sales Ops duyệt.
