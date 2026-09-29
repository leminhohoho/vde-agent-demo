# Insight Agent — câu hỏi mở và quyết định

Đối chiếu `docs/insight_agent_spec.md` (v2.0) với code hiện có. Trạng thái: **CHỐT** = đã có quyết định,
**MỞ** = chờ nhóm.

| # | Vấn đề | Quyết định | Trạng thái |
|---|---|---|---|
| Q1 | Spec giả định PostgreSQL queue + `InsightTaskRequest`; sdk chỉ có `Agent.invoke(ctx)` với message text. | Lõi là `run_task(request, deps)` thuần, không biết `ctx`. `bridge.py` parse JSON `InsightTaskRequest` từ message (chấp nhận khối ```json trong text); lỗi → trả lời E01 tiếng Việt kèm schema mong đợi. Không sửa Orchestrator. | CHỐT (phía Orchestrator: MỞ) |
| Q2 | Không có artifact store `metric`/`dq`/`dataset`; warehouse hiện là dữ liệu bán lẻ, không phải DW BĐS v3.1.0; luật cấm MCP/DW. | `ArtifactReader` (Protocol) + `FixtureArtifactReader` cho P0–P4. | Nguồn thật: MỞ |
| Q3 | Không có `shared_artifacts`. | SQLite riêng `var/insight_artifacts.db` sau `ArtifactWriter`, lưu cả idempotency key. Câu trả lời emit = tóm tắt tiếng Việt + `artifact_id` + khối JSON payload. | CHỐT |
| Q4 | `ctx.memory` scope (user, agent), chỉ có text, không expiry/snapshot/conversation, hết hiệu lực khi `invoke` return. | Payload JSON có schema trong `text`, lọc expiry/snapshot/conversation bằng code. Job extract/compact chạy sau emit, trước khi `invoke` return, timeout `memory.job_timeout_s = 5`. P0 chỉ `NoOpMemory`, bản thật ở P4. | CHỐT |
| Q5 | Spec viết `LlmClient`/`InsightMemory` đồng bộ; sdk R10 cấm block event loop. | Giữ tên và chữ ký, đổi sang `async def`. | CHỐT |
| Q6 | Sửa ngoài `agents/insight/`. | Được sửa `agents/insight/pyproject.toml`, `uv.lock`, thêm target Makefile `insight-test`, `insight-lint`. Type-check bằng basedpyright (config sẵn ở root). Repo chưa có linter → thêm ruff (chạy qua `uvx`, config trong `agents/insight/pyproject.toml`). | CHỐT |
| Q7 | `tests/test_agent.py` test hành vi LangChain; `README.md` gốc và `agents/_template/README.md` lấy insight làm recipe LangChain. | Giữ `test_agent.py` tới P4 rồi thay. **Không** sửa hai README: nhóm cần chọn agent khác (ví dụ report) làm recipe LangChain. | README: MỞ |
| Q8a | `is_peer_sample_constrained`, số peer không có trong bảng DW nào. | Fixture tự thêm cột `is_peer_sample_constrained`, `peer_count` vào mart, đánh dấu `TODO(data-agent-contract)`. | CHỐT tạm |
| Q8b | TC-11 nhắc "Comparison snapshot" dù Insight không đọc Comparison. | Hiểu là lệch snapshot giữa các artifact đầu vào (metric vs dq/dataset). | CHỐT |
| Q8c | `LlmUsage.cost_usd` là string nhưng TC-28 cần `null`. | `cost_usd: str \| None`. | CHỐT |
| Q8d | "20 test case" vs 33; bước 0–10 vs "step 1–9" vs "9 bước". | 33 test; chuẩn là bước 0–10. | CHỐT |
| Q8e | Key dùng mà không có trong 5.4: `max_units_in_context`, `min_cause_share_pct`, `conflict_tolerance_pct`, `english_whitelist`, `freshness_max_days`. | Tự đặt mặc định, ghi `PENDING` trong yaml. Freshness dùng `freshness_warn_hours`/`freshness_error_hours` (24/72) của 5.5, bỏ `freshness_max_days`. | CHỐT tạm |
| Q8f | `InsightRef`, `AuthorizedScope` chưa định nghĩa. | `InsightRef` = payload INSIGHT_REF (9.5); `AuthorizedScope` = `user_context.authorized_scope` (3.3). | CHỐT |
| Q8g | Mart chỉ chứa căn AVAILABLE nên BR-01 không kiểm được từ mart. | Kiểm AVAILABLE + DOM > ngưỡng từ `fact_unit_inventory_snapshot` trong dataset artifact. | CHỐT |
| Q9 | `docs/PRD_VDAgent.md` không có trong repo. | Không dùng. | MỞ |

## Giả định đặt trong Phase 0

Xem `agents/insight/config/semantic_insight.yaml` (mọi giá trị có `status: PENDING`) và mục "Giả định"
trong `agents/insight/CLAUDE.md`.
