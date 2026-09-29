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
| Q12 | (Thay bằng D-71.) BR-07 nói peer < 5 thì "ẩn", nhưng TC-08 (3 peer) vẫn mong có insight OVERPRICED_VS_PEER. | Giữ candidate, gắn GROUP_TOO_SMALL (5–9: SMALL_SAMPLE), `significant = false`; `is_peer_sample_constrained` → trần MEDIUM + PEER_SAMPLE_CONSTRAINED. "Ẩn" hiểu là không được viết câu so sánh mạnh về peer. | ĐÓNG (D-71) |
| Q13 | `significant` cho tỷ lệ: spec chỉ có ngưỡng hiệu ứng theo ngày (`min_effect_size_days`). | Tỷ lệ: chỉ xét hai khoảng Wilson không chồng nhau. Trung vị DOM: bootstrap không chồng nhau **và** chênh ≥ 15 ngày. Hai khoảng chạm nhau tính là chồng nhau. | MỞ |
| Q14 | T3: spec ghi nguồn là "Metric Artifact theo dimension", nhưng cờ `significant` cần DOM từng căn và nhóm "phần còn lại"; mục 1.3 lại cấm Insight tự tính metric. | Tính trung vị DOM và tỷ lệ quá hạn theo nhóm từ các dòng dataset. Tập so sánh = căn chưa bán (AVAILABLE) trong scope, bỏ outlier > 3×IQR; tỷ lệ quá hạn = căn quá hạn / căn chưa bán. `metric_ref` trỏ vào artifact `insight_candidates` của task. | MỞ |
| Q15 | action_code: BR-10 (mapping trong config) hay 7.6/Phụ lục #5 ("ưu tiên `recommended_action` trong mart")? | `action_code` luôn lấy từ mapping trong config. Mart lệch mapping của `primary_cause_code` → CONFLICT `ACTION_CODE_MISMATCH` (T7). | ĐÓNG (data pack: khớp 100%) |
| Q16 | LOW_SALES_INCENTIVE: DW kích hoạt khi `spiff_bonus_vnd = 0 HOẶC NULL`, nên NULL có nghĩa, không phải thiếu dữ liệu. | Bằng chứng bắt buộc chỉ gồm `base_commission_pct`; `spiff_bonus_vnd` là bằng chứng bổ sung (có thì bind). | ĐÓNG (data pack xác nhận) |
| Q17 | 5.2: HIGH cần "≥ 2 evidence từ nguồn độc lập". | "Nguồn" = bảng DW có số được bind vào claim. `{{dom}}` luôn lấy từ `fact_unit_inventory_snapshot`, nên cause có số từ mart đủ 2 nguồn; LOW_SALES_INCENTIVE chỉ có số từ bảng tồn kho → trần MEDIUM. | TẠM |
| Q18 | Priority của T7 không được định nghĩa; mọi T2/T3 bằng nhau (0,5). | `priority_without_rank` trong config: T7 = 0 (luôn được giữ); hòa điểm thì xếp theo `candidate_id`. | TẠM |
| Q19 | BR-06: LEGAL_PERMIT_BARRIER ở cấp dự án, nhưng bridge ghi theo từng căn. | Một candidate PROJECT/dự án nếu có căn quá hạn mang mã này trong bridge (slot = số căn); không sinh candidate cấp căn. Dự án đủ cả hai cờ pháp lý mà vẫn có mã LEGAL → CONFLICT `LEGAL_FLAGS_MISMATCH`. | TẠM |
| Q20 | Coverage và n_eff chưa định nghĩa "hợp lệ"; outlier "theo peer group" nhưng dataset không có danh sách peer. | Căn hợp lệ = quá hạn, có dòng mart, tổng `attribution_score` = 1 ± 0,001. n_eff = số căn hợp lệ bỏ outlier DOM > 3×IQR trong cùng zone/project; nếu phải loại > 10% thì không loại căn nào. Với T1, outlier chỉ gắn cờ trong phạm vi zone. | TẠM |
| Q21 | Biên của bảng 5.5 ("5–10%", "70–90%") chưa rõ gồm hay không gồm hai đầu. | Missing rate, freshness, rào IQR: bằng đúng ngưỡng thì ở bậc nhẹ hơn (≤). Coverage, cỡ mẫu: bằng đúng ngưỡng thì đạt bậc đó (≥). MNAR: phải > 10 điểm %. Có test cho từng biên và ±1 đơn vị. | TẠM |
| Q22 | Payload `market_context` chưa có trong spec Data Agent; "cùng kỳ trước" chưa rõ. | Các dòng `fact_market_macro_monthly` (cột theo DW 3.1.0); so tháng mới nhất với cùng tháng năm trước; MOI hiển thị "tháng". `TODO(data-agent-contract)`. | TẠM |
| Q23 | Bảng "bằng chứng tối thiểu" (6.2) và danh sách dimension của T3 là danh mục, nhưng spec không nói đặt ở đâu. | Đưa vào `semantic_insight.yaml` (`required_evidence`, `supplementary_evidence`, `uses_peer_group`, `pattern_dimensions`); loader kiểm tra cột có thật trong bảng. | TẠM |
| Q24 | "Hai nguồn lệch quá `conflict_tolerance_pct`" (T7): so với giá trị nào? | Lệch tương đối so với giá trị trong dataset: DOM trong mart vs bảng tồn kho, giá trị metric artifact vs cột cùng tên trong dataset → CONFLICT `SOURCE_MISMATCH`. Ngưỡng 1% (PENDING). | TẠM |
| Q25 | `NumericBinding.display` bắt buộc ngay từ candidate, nhưng "formatter" thuộc bước 8. | Module thuần `formatting.py` (vi-VN, dấu phẩy thập phân, làm tròn half-up **chỉ ở display**), dùng chung cho candidate engine và claim_binder ở P2. | TẠM |

## Chốt sau khi profile data pack (29/09)

Chi tiết và số liệu: `docs/INSIGHT_P2_P5_DECISIONS.md` (mục "Quyết định đã chốt" và 0b).

| # | Quyết định | Trạng thái |
|---|---|---|
| D-00 | Repo là prototype để demo; P4 gọn (SQLite, không queue), chạy end-to-end trên data pack. | ĐÃ CHỐT |
| D-70 | `semantic_config_version = 3.1.0`; tên key + đơn vị theo DW (`peer_area_tolerance_pct = 10` nghĩa là 10%); các ngưỡng công thức DW vào config, APPROVED (nguồn DW). | ĐÃ CHỐT |
| D-71 | Peer ≥ 5 so sánh, 3–4 chỉ mô tả, < 3 ẩn; loại hẳn candidate chỉ ở T3 (T1/T2 giữ, gắn cờ, không so sánh); `constrained` → ≤ MEDIUM. **Thay thế Q12.** | ĐÃ CHỐT |
| D-72 | T5: xu hướng trong 12 tháng có sẵn (lãi suất, hấp thụ), không YoY; MOI/PIR/thu nhập chỉ nêu mức + limitation. | ĐÃ CHỐT |
| D-73 | Regex mã căn trong config theo định dạng data. | ĐÃ CHỐT |
| D-74 | `evidence_artifact_id` chỉ vào lineage. | ĐÃ CHỐT |
| D-75 | GR-08 kiểm template trước khi điền slot. | ĐÃ CHỐT |
| D-76 | Enum role = SALES_OPS, SALES_MANAGER, EVALUATOR. | ĐÃ CHỐT |
| D-77 | `ExportArtifactReader` (P4), `INSIGHT_ARTIFACT_SOURCE=fixtures\|export`, nguồn demo + golden set tự động. | ĐÃ CHỐT |
| Q12 | Thay bằng D-71. | ĐÓNG |
| Q15 | Data pack: `recommended_action` khớp mapping 100%; giữ mapping trong config + CONFLICT khi lệch. | ĐÓNG |
| Q16 | Data pack xác nhận NULL của `spiff_bonus_vnd` có nghĩa. | ĐÓNG |
| TC | TC-01 → `SAPPHIRE1-16.231`; TC-04 → The Beverly. | ĐÃ CHỐT |

## D-30: model, key, endpoint (probe 29/09, `agents/insight/scripts/llm_probe.py`)

| Provider | Kết quả | Trạng thái |
|---|---|---|
| Gemini `gemini-3.5-flash-lite` (google-genai 2.25, `GEMINI_API_KEY`) | Trả lời được; `response_json_schema` hợp lệ; `thinking_level=MINIMAL` → 0 thinking token; finish STOP; ~1,3 s; 21 in / 23 out token | ĐÃ CHỐT |
| OpenAI `gpt-6-luna` (openai 2.54, Responses API, `OPENAI_API_KEY`, endpoint mặc định api.openai.com) | Trả lời được; `json_schema` strict hợp lệ; `reasoning.effort=none` → 0 reasoning token; ~3,9 s | ĐÃ CHỐT |
| `OPENAI_BASE_URL` | Để trống → dùng api.openai.com (không đi qua proxy) | ĐÃ CHỐT |
| `LLM_MODEL` | Trống → agent LangChain cũ **không load** khi chạy backend cho tới P4 (không ảnh hưởng test) | Ghi nhận |

Chi phí probe: 0,00008 USD. Model ID trong `config/llm.yaml` giữ nguyên. Egress trong docker-compose kiểm ở P5.

## Phase 2 (validation, render, TEMPLATE, assess): mặc định đã áp dụng

Các mục D chưa được chốt riêng; coding agent làm theo **đề xuất mặc định** trong
`docs/INSIGHT_P2_P5_DECISIONS.md`. Câu chữ nằm trong `config/semantic_insight.yaml` (`language`) và là
**bản nháp chờ Sales Ops duyệt**.

| # | Mặc định đang chạy | Trạng thái |
|---|---|---|
| D-20 | Thông điệp tiếng Việt cho 25 mã limitation; câu TEMPLATE mỗi cause có `{{cause_label}}` (để TC-30 hiện đúng `cause_label_vi`); câu `peer_hidden_template` khi < 3 peer. | TẠM (OPS duyệt) |
| D-21 | GR-01: chữ số + danh sách `quantity_words`; không bắt "một", "hai"… đứng một mình. | TẠM (DA/OPS) |
| D-22 | Slot nhãn cố định (`label_slots`); slot khác phải là slot số của candidate (GR-03). | TẠM |
| D-23 | GR-05: quét `question_normalized` và nhãn theo `injection_patterns`; khớp → sự kiện bảo mật, dữ liệu giữ nguyên (TC-15: output không đổi). | TẠM |
| D-24 | GR-06: khuyến nghị phải bắt đầu "Đề xuất"/"Có thể cân nhắc", không chứa `imperative_phrases`. | TẠM (OPS) |
| D-25 | GR-07: `strong_comparison_phrases` chỉ khi mọi candidate của item `significant`; mã limitation do code tự gắn. | TẠM (DA/OPS) |
| D-26 | GR-08: câu phải có dấu tiếng Việt, không có từ trong `english_stopwords` (trừ whitelist), không có mã cause; item nói về nguyên nhân phải dùng `{{cause_label}}`. | TẠM |
| D-27 | Gộp nhiều candidate: `candidate_id` = cái đầu, còn lại `candidate:<id>` trong lineage; confidence thấp nhất; `cause_code` chỉ khi chung. | TẠM (PO nếu muốn đổi contract) |
| D-28 | KEY: ROOT_CAUSE_SIGNAL, CAUSE_DISTRIBUTION, PATTERN có `significant`; có evidence, đủ 2 nhánh lineage, confidence ≠ LOW, không CONFLICT; tối đa `max_key_insights`, LEGAL đứng đầu headline. | TẠM |
| D-31 | `kind`: dq → DQ, metric / số tự tính (`insight_candidates`) → METRIC, market → MARKET, dòng dataset → DIAGNOSTIC_ROW. | TẠM |
| D-32 | Candidate LLM bỏ qua → `LLM_SKIPPED`; T7 bị bỏ qua vẫn được render TEMPLATE; không có draft → TEMPLATE mọi T7 + tối đa 12 candidate khác, phần còn lại `SELECTION_LIMIT`. | TẠM |
| D-33 | Khuyến nghị: câu của LLM nếu qua GR-06, không thì câu trong config; không có với intent PERFORMANCE_METRIC_LOOKUP hoặc khi người dùng tắt. | TẠM |
| D-13 | `chart_hints` (KEY có số): ROOT_CAUSE_SIGNAL → `kpi_card`, CAUSE_DISTRIBUTION → `stacked_bar`, PATTERN → `bar`, MARKET_CONTEXT → `line`. | TẠM |
| P2-1 | `narrative_mode = TEMPLATE` khi không có draft hoặc có item phải fallback; T7 được bổ sung bằng TEMPLATE không tính. | TẠM |
| P2-2 | Status PARTIAL khi: TEMPLATE, candidate bị loại vì thiếu bằng chứng (EVIDENCE_FIELD_MISSING/PEER_DATA_MISSING), có CONFLICT, thiếu market_context. Candidate có nhưng không render được gì → INVALID. | TẠM |
| P2-3 | Id candidate không chứa dấu chấm (ref của draft là `<candidate_id>.<slot>`); id DQ trong T7 = `C-T7-DQ-<table>-<field>`. | CHỐT (kỹ thuật) |
| P2-4 | `Insight.limitations` = thông điệp tiếng Việt; mã nằm ở `payload.limitations[].code` kèm `affected`. | TẠM |

## Giả định đặt trong Phase 0

Xem `agents/insight/config/semantic_insight.yaml` (mọi giá trị có `status: PENDING`) và mục "Giả định"
trong `agents/insight/CLAUDE.md`.
