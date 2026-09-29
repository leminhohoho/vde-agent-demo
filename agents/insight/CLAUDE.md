@../../AGENTS.md

# Insight Agent (spec v2.0: pipeline tất định 0–10, 2 lần gọi LLM)

Nguồn sự thật: [docs/insight_agent_spec.md](../../docs/insight_agent_spec.md) · tên bảng/cột: [docs/dw_schema_v3.1.0.md](../../docs/dw_schema_v3.1.0.md) · quyết định & câu hỏi mở: [docs/OPEN_QUESTIONS.md](../../docs/OPEN_QUESTIONS.md).
Chỉ sửa trong `agents/insight/` (ngoại lệ đã duyệt: `uv.lock`, target Makefile `insight-*`, `.gitignore` gốc, `docs/*`). Spec mơ hồ/mâu thuẫn → DỪNG, ghi OPEN_QUESTIONS, hỏi.

## Luật bất biến
1. LLM không sinh số: mọi số đi qua slot `{{...}}`, claim_binder điền giá trị thật.
2. Ngưỡng, giá, model, danh mục, từ khóa chỉ ở `config/*.yaml` có version. Không hard-code.
3. `Decimal` cho mọi số nghiệp vụ và chi phí; cấm float (YAML: decimal viết dạng chuỗi có ngoặc).
4. Không đọc Comparison Artifact, không gọi agent khác, không query DW.
5. Gọi LLM qua SDK trực tiếp (`google-genai`, `openai` Responses) sau `LlmClient`. Không LangChain, tool calling, MCP; không import `tools.py`, `mcp_client.py`.
6. Chỉ 2 điểm gọi LLM ([LLM-1], [LLM-R]); reasoning tắt, repair tối đa `low`. Không có job LLM `MEMORY` (D-42).
7. Mỗi lần gọi LLM ghi `LlmUsage` + `cost_usd` (spec 7.5), lưu bảng `insight_llm_usage`.
8. Validator GR-01→08 chạy trên mọi output LLM; lỗi → repair 1 lần → TEMPLATE theo item.
9. Memory (9.5): chỉ tham chiếu + sở thích, cấm số liệu/kết luận; memory lỗi không làm hỏng task.
10. Artifact bất biến, SHA-256 trên canonical JSON (`artifacts.content_hash`).
11. Gate, candidates, validation, render, assess là hàm thuần: không I/O, không LLM, bootstrap seed cố định.
12. Đổi `prompts/system.md`/`repair.md` → tăng `prompt_version` (ghi trong file + `config/llm.yaml`, có test), chạy lại TC-01→TC-33.
13. Contract: Pydantic v2 `extra="forbid"`, giữ nguyên tên field/enum của spec (`contracts.py`).
14. TDD: test đỏ trước, rồi mới code. Interface I/O là `async` (sdk R10).

## Cây thư mục
```
config/semantic_insight.yaml  ngưỡng 5.4 + danh mục 7.6    config/llm.yaml  model, giá, prompt_version
config/bridge.yaml            từ khóa intent, task theo intent, giới hạn reply (chế độ tương thích)
vdagent_insight/
  __init__.py   setup() → runtime + InsightAgent       runtime.py  .env → nguồn data, store, provider, as_of
  bridge.py     ctx → InsightTaskRequest → run_task → reply ≤ 6.000 ký tự; compact() tất định
  agent.py      run_task: bước 0–10, idempotency, E01–E04, deadline, persist, sự kiện INSIGHT_*
  store.py      SQLite var/insight_artifacts.db         export_reader.py  data pack CSV → artifact
  contracts.py  model Pydantic      artifacts.py  canonical JSON, Reader   settings.py  loader YAML
  view.py, gate.py, candidates/ (T1 T2 T3 T5 T7, stats, priority), formatting.py      [3–5]
  llm/          LlmClient, Fake, gemini, openai, schema, calls, preflight, prompt, steps, usage, providers
  validation.py, render.py, narrate.py, assess.py                                      [7–9]
  memory.py     InsightMemory, NoOpMemory, CtxMemory (ctx.memory)
  prompts/      system.md, repair.md
  tests/        test_*.py, builders.py, fixtures/tcNN/, fixtures/export_sample/
scripts/ask.py  demo terminal qua bridge (không cần backend)   scripts/llm_probe.py  probe D-30
```

## Lệnh (từ gốc repo)
- `make insight-test` = `uv run pytest agents/insight` (test `datapack` cần `export/`, test `live` cần `INSIGHT_LIVE=1`)
- `make insight-lint` = `uvx ruff@0.16.9 check agents/insight`, `... format --check agents/insight`,
  `uv run --with basedpyright==1.40.1 python -m basedpyright agents/insight`
- Demo: `uv run python agents/insight/scripts/ask.py "Vì sao tòa Sapphire 1 có nhiều căn bán chậm?"` (`--no-llm`, `--events`)

## Giả định đang dùng (chi tiết: OPEN_QUESTIONS, mục Phase 4)
- Orchestrator gửi câu hỏi tự do → chế độ tương thích (P4-1) tới khi chốt D-10; JSON request vẫn được nhận.
- Nguồn artifact: data pack `export/` qua `ExportArtifactReader` (D-77); `as_of` ghim theo snapshot (P4-5).
- Mart có thêm cột `is_peer_sample_constrained`, `peer_count` (không có trong DW v3.1.0).
- `insight_templates` và `recommendation_text` là bản nháp, cần Sales Ops duyệt.
