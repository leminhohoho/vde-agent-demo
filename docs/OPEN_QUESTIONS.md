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
| Q10 | `LlmInsightDraft.selected[].slot_map`: dạng dict hay list? | Dùng `slots: list[SlotRef]` (`slot`, `ref = "<candidate_id>.<slot>"`); Pydantic kiểm `ref` có đúng một dấu chấm, hai vế không rỗng, và `slot` không trùng trong một item. Khớp `{{slot}}` trong template ↔ `slots` thuộc GR-01/GR-03 (P2, validator theo item). Lý do: strict JSON schema của structured output OpenAI không chấp nhận object có `additionalProperties` động; Gemini responseSchema cũng xử lý kém kiểu map. Đổi ngay ở Phase 0 vì chưa có code nào phụ thuộc contract này. Ảnh hưởng: spec 6.5 (`LlmInsightDraft`) cần cập nhật; claim_binder ở P2 đọc `slots`. | CHỐT (spec: cần cập nhật) |

## Phase 1 (gate + candidates)

Trạng thái **TẠM** = đã code theo cách này, chờ nhóm xác nhận; đổi thì chỉ sửa config hoặc một hàm.

| # | Vấn đề | Cách đang làm | Trạng thái |
|---|---|---|---|
| Q11 | Freshness: 5.5 ghi ">24h cảnh báo, >72h gắn mọi insight", BR-12 lại dùng `freshness_max_days`. | `freshness_warn_hours`/`freshness_error_hours`: tuổi dữ liệu > 24h → STALE_SNAPSHOT, hạ 1 bậc; > 72h → STALE_SNAPSHOT và LOW (5.2 "snapshot cũ"). Tuổi = `as_of` của task − `dq.data_as_of`. Không áp cho số liệu vĩ mô (T5). | TẠM |
| Q12 | BR-07 nói peer < 5 thì "ẩn", nhưng TC-08 (3 peer) vẫn mong có insight OVERPRICED_VS_PEER. | Giữ candidate, gắn GROUP_TOO_SMALL (5–9: SMALL_SAMPLE), `significant = false`; `is_peer_sample_constrained` → trần MEDIUM + PEER_SAMPLE_CONSTRAINED. "Ẩn" hiểu là không được viết câu so sánh mạnh về peer. | MỞ |
| Q13 | `significant` cho tỷ lệ: spec chỉ có ngưỡng hiệu ứng theo ngày (`min_effect_size_days`). | Tỷ lệ: chỉ xét hai khoảng Wilson không chồng nhau. Trung vị DOM: bootstrap không chồng nhau **và** chênh ≥ 15 ngày. Hai khoảng chạm nhau tính là chồng nhau. | MỞ |
| Q14 | T3: spec ghi nguồn là "Metric Artifact theo dimension", nhưng cờ `significant` cần DOM từng căn và nhóm "phần còn lại"; mục 1.3 lại cấm Insight tự tính metric. | Tính trung vị DOM và tỷ lệ quá hạn theo nhóm từ các dòng dataset. Tập so sánh = căn chưa bán (AVAILABLE) trong scope, bỏ outlier > 3×IQR; tỷ lệ quá hạn = căn quá hạn / căn chưa bán. `metric_ref` trỏ vào artifact `insight_candidates` của task. | MỞ |
| Q15 | action_code: BR-10 (mapping trong config) hay 7.6/Phụ lục #5 ("ưu tiên `recommended_action` trong mart")? | `action_code` luôn lấy từ mapping trong config. Mart lệch mapping của `primary_cause_code` → CONFLICT `ACTION_CODE_MISMATCH` (T7). | MỞ |
| Q16 | LOW_SALES_INCENTIVE: DW kích hoạt khi `spiff_bonus_vnd = 0 HOẶC NULL`, nên NULL có nghĩa, không phải thiếu dữ liệu. | Bằng chứng bắt buộc chỉ gồm `base_commission_pct`; `spiff_bonus_vnd` là bằng chứng bổ sung (có thì bind). | TẠM |
| Q17 | 5.2: HIGH cần "≥ 2 evidence từ nguồn độc lập". | "Nguồn" = bảng DW có số được bind vào claim. `{{dom}}` luôn lấy từ `fact_unit_inventory_snapshot`, nên cause có số từ mart đủ 2 nguồn; LOW_SALES_INCENTIVE chỉ có số từ bảng tồn kho → trần MEDIUM. | TẠM |
| Q18 | Priority của T7 không được định nghĩa; mọi T2/T3 bằng nhau (0,5). | `priority_without_rank` trong config: T7 = 0 (luôn được giữ); hòa điểm thì xếp theo `candidate_id`. | TẠM |
| Q19 | BR-06: LEGAL_PERMIT_BARRIER ở cấp dự án, nhưng bridge ghi theo từng căn. | Một candidate PROJECT/dự án nếu có căn quá hạn mang mã này trong bridge (slot = số căn); không sinh candidate cấp căn. Dự án đủ cả hai cờ pháp lý mà vẫn có mã LEGAL → CONFLICT `LEGAL_FLAGS_MISMATCH`. | TẠM |
| Q20 | Coverage và n_eff chưa định nghĩa "hợp lệ"; outlier "theo peer group" nhưng dataset không có danh sách peer. | Căn hợp lệ = quá hạn, có dòng mart, tổng `attribution_score` = 1 ± 0,001. n_eff = số căn hợp lệ bỏ outlier DOM > 3×IQR trong cùng zone/project; nếu phải loại > 10% thì không loại căn nào. Với T1, outlier chỉ gắn cờ trong phạm vi zone. | TẠM |
| Q21 | Biên của bảng 5.5 ("5–10%", "70–90%") chưa rõ gồm hay không gồm hai đầu. | Missing rate, freshness, rào IQR: bằng đúng ngưỡng thì ở bậc nhẹ hơn (≤). Coverage, cỡ mẫu: bằng đúng ngưỡng thì đạt bậc đó (≥). MNAR: phải > 10 điểm %. Có test cho từng biên và ±1 đơn vị. | TẠM |
| Q22 | Payload `market_context` chưa có trong spec Data Agent; "cùng kỳ trước" chưa rõ. | Các dòng `fact_market_macro_monthly` (cột theo DW 3.1.0); so tháng mới nhất với cùng tháng năm trước; MOI hiển thị "tháng". `TODO(data-agent-contract)`. | TẠM |
| Q23 | Bảng "bằng chứng tối thiểu" (6.2) và danh sách dimension của T3 là danh mục, nhưng spec không nói đặt ở đâu. | Đưa vào `semantic_insight.yaml` (`required_evidence`, `supplementary_evidence`, `uses_peer_group`, `pattern_dimensions`); loader kiểm tra cột có thật trong bảng. | TẠM |
| Q24 | "Hai nguồn lệch quá `conflict_tolerance_pct`" (T7): so với giá trị nào? | Lệch tương đối so với giá trị trong dataset: DOM trong mart vs bảng tồn kho, giá trị metric artifact vs cột cùng tên trong dataset → CONFLICT `SOURCE_MISMATCH`. Ngưỡng 1% (PENDING). | TẠM |
| Q25 | `NumericBinding.display` bắt buộc ngay từ candidate, nhưng "formatter" thuộc bước 8. | Module thuần `formatting.py` (vi-VN, dấu phẩy thập phân, làm tròn half-up **chỉ ở display**), dùng chung cho candidate engine và claim_binder ở P2. | TẠM |

## Giả định đặt trong Phase 0

Xem `agents/insight/config/semantic_insight.yaml` (mọi giá trị có `status: PENDING`) và mục "Giả định"
trong `agents/insight/CLAUDE.md`.
