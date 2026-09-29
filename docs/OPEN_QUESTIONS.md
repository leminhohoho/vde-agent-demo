# Insight Agent — câu hỏi mở và quyết định

Đối chiếu `docs/insight_agent_spec.md` (v2.1) với code hiện có. Trạng thái: **CHỐT** = đã có quyết định,
**MỞ** = chờ nhóm.

## Cần chốt sau prototype (cập nhật 29/09, cuối P5)

Mọi mục dưới đây đang **chạy theo mặc định** trong code/config; đổi thì chỉ sửa config hoặc một hàm. Quyết định đã chốt nằm trong spec v2.1 (phụ lục "Thay đổi v2.1").

| Câu hỏi | Đang chạy theo | Người chốt |
|---|---|---|
| Q13: `significant` cho tỷ lệ và trung vị DOM | Tỷ lệ: hai khoảng Wilson 95% không chồng nhau; trung vị DOM: bootstrap (1.000 lần, seed cố định) không chồng nhau **và** chênh ≥ 15 ngày; chạm nhau = chồng nhau | DA |
| Q14: nguồn tính của T3 (spec: Metric Artifact; mục 1.3 cấm Insight tự tính) | Insight tự tính trung vị DOM và tỷ lệ quá hạn theo nhóm từ dòng dataset; `metric_ref` trỏ vào `insight_candidates` của task | DA + DATA |
| Q15: `action_code` lấy từ mapping config hay `recommended_action` của mart | Mapping trong config; mart lệch → CONFLICT `ACTION_CODE_MISMATCH` (data pack khớp 100%, đề xuất đóng) | OPS |
| Q11: freshness theo giờ (5.5) hay `freshness_max_days` (BR-12) | > 24 h: STALE_SNAPSHOT, hạ 1 bậc; > 72 h: LOW. Demo ghim `as_of` theo snapshot (P4-5) | DA |
| Q17: "≥ 2 nguồn độc lập" cho HIGH | Nguồn = bảng DW có số được bind; chỉ bảng tồn kho → trần MEDIUM | DA |
| Q18: priority của T7 và hòa điểm | `priority_without_rank` (T2/T3 0,5; T5 0,3; T7 0); hòa theo `candidate_id` | DA |
| Q19: LEGAL_PERMIT_BARRIER ở cấp dự án | 1 candidate PROJECT, slot = số căn; dự án đủ giấy tờ mà có mã LEGAL → CONFLICT | DA |
| Q20: coverage, n_eff, outlier | Căn hợp lệ = quá hạn + có mart + tổng score = 1 ± 0,001; bỏ outlier > 3×IQR trong zone/project (không loại nếu > 10%) | DA |
| Q21: biên của bảng 5.5 | Missing/freshness/IQR: bằng ngưỡng → bậc nhẹ; coverage/cỡ mẫu: bằng ngưỡng → đạt; MNAR > 10 điểm % | DA |
| Q22: payload `market_context` | Các dòng `fact_market_macro_monthly` theo DW 3.1.0 (`TODO(data-agent-contract)`) | DATA |
| Q23, Q24, Q25 | Bảng bằng chứng + dimension T3 trong config; SOURCE_MISMATCH lệch > 1% so với dataset; `formatting.py` vi-VN làm tròn half-up chỉ ở display | DA |
| Ngưỡng `PENDING` trong `config/semantic_insight.yaml` | Giá trị mặc định đặt ở P0–P1 | DA |
| D-20, D-21, D-24, D-25: danh mục câu chữ (limitation, TEMPLATE, `quantity_words` + ngoại lệ "tỷ lệ/tỷ trọng", từ mệnh lệnh, so sánh mạnh) | Bản nháp trong config | OPS (+ DA) |
| D-22, D-23, D-26: slot nhãn, quét injection (câu hỏi, nhãn, và message đến ở bridge), luật tiếng Việt, `MEDIAN_AS_MEAN` | Như config và validator hiện tại | OPS |
| D-27, D-28, D-31, D-32, D-33, P2-1, P2-2, P2-4 | Gộp candidate, luật KEY, `kind` của evidence, LLM_SKIPPED/SELECTION_LIMIT, khuyến nghị, `narrative_mode`, status, `Insight.limitations` là câu tiếng Việt | PO |
| D-53: lưu prompt hash + output thô trong `producer.replay` | Đang lưu (tối đa 20 KB mỗi output) | PO |
| D-52: đồng hồ của task | `as_of` = lúc nhận task, lưu trong replay; demo ghim theo snapshot | PO |
| D-10: Orchestrator gửi JSON `InsightTaskRequest` | Chế độ tương thích: câu tự do → request bằng luật cố định (`config/bridge.yaml`) | PLAT |
| D-11, D-12: Report dùng kết quả Insight | Reply = tóm tắt + `artifact_id` + JSON KEY insight ≤ 6.000 ký tự; Report chưa đọc được store của Insight | PLAT |
| D-13: `chart_hints` / Chart Agent | kpi_card, stacked_bar, bar, line (chưa có Chart Agent) | PLAT |
| D-14: role và phạm vi quyền | Prototype: SALES_MANAGER + mọi dự án của data pack (đã duyệt cho prototype); cần nguồn `role_project_access` | PLAT |
| D-15, D-16: queue/lease, deadline | Không có queue; `attempt`/`fencing_token` chỉ validate; deadline 60 s → PARTIAL | PLAT |
| D-17: README gốc và `_template` vẫn lấy Insight làm ví dụ LangChain | Chưa sửa (ngoài phạm vi) | PLAT |
| D-41: `conversation_id` | Bridge: UUID5 của `ctx.task_id` | PLAT |
| D-01, D-02: dữ liệu thật và hợp đồng `dq`/`metric` với Data Agent | `ExportArtifactReader` tự dựng artifact từ data pack | DATA |
| D-60, D-62: bộ chấm điểm và vận hành | Golden = mã nguyên nhân của bridge (1.139 căn khớp 100%) | OPS, DA |
| P3-6, P3-7, P3-8 | `skipped` rỗng (hệ thống tự ghi LLM_SKIPPED); test live chỉ khi `INSIGHT_LIVE=1`; cảnh báo chi phí chỉ log WARNING | TL |
| P5-2: `max_output_tokens = 2500` (spec 6.4) | Giữ theo spec. Câu hỏi cấp tòa chọn đủ 12 item đôi khi bị cắt (E09, 2/5 lần đo) → 1 lần repair, kết quả vẫn đúng nhưng chi phí gấp đôi. Đề xuất nâng lên 4.000 | TL + PO |
| P5-3: câu chữ của LLM đúng số nhưng diễn giải chưa chuẩn (vd "tỷ trọng 13,3% trong tổng số căn chậm" cho `weighted_share`; "tồn DOM" không có số) | Chưa có luật chặn; đề xuất thêm nghĩa của từng slot vào prompt (bump prompt_version) | OPS + TL |

## Phase 0

| # | Vấn đề | Quyết định | Trạng thái |
|---|---|---|---|
| Q1 | Spec giả định PostgreSQL queue + `InsightTaskRequest`; sdk chỉ có `Agent.invoke(ctx)` với message text. | Lõi là `run_task(request, deps)` thuần, không biết `ctx`. `bridge.py` parse JSON `InsightTaskRequest` từ message (chấp nhận khối ```json trong text); lỗi → trả lời E01 tiếng Việt kèm schema mong đợi. Không sửa Orchestrator. | CHỐT (phía Orchestrator: MỞ) |
| Q2 | Không có artifact store `metric`/`dq`/`dataset`; warehouse hiện là dữ liệu bán lẻ, không phải DW BĐS v3.1.0; luật cấm MCP/DW. | `ArtifactReader` (Protocol) + `FixtureArtifactReader` cho P0–P4. | Nguồn thật: MỞ |
| Q3 | Không có `shared_artifacts`. | SQLite riêng `var/insight_artifacts.db` sau `ArtifactWriter`, lưu cả idempotency key. Câu trả lời emit = tóm tắt tiếng Việt + `artifact_id` + khối JSON payload. | CHỐT |
| Q4 | `ctx.memory` scope (user, agent), chỉ có text, không expiry/snapshot/conversation, hết hiệu lực khi `invoke` return. | Payload JSON có schema trong `text`, lọc expiry/snapshot/conversation bằng code. Job extract/compact chạy sau emit, trước khi `invoke` return, timeout `memory.job_timeout_s = 5`. P0 chỉ `NoOpMemory`, bản thật ở P4. | CHỐT |
| Q5 | Spec viết `LlmClient`/`InsightMemory` đồng bộ; sdk R10 cấm block event loop. | Giữ tên và chữ ký, đổi sang `async def`. | CHỐT |
| Q6 | Sửa ngoài `agents/insight/`. | Được sửa `agents/insight/pyproject.toml`, `uv.lock`, thêm target Makefile `insight-test`, `insight-lint`. Type-check bằng basedpyright (config sẵn ở root). Repo chưa có linter → thêm ruff (chạy qua `uvx`, config trong `agents/insight/pyproject.toml`). | CHỐT |
| Q7 | `tests/test_agent.py` test hành vi LangChain; `README.md` gốc và `agents/_template/README.md` lấy insight làm recipe LangChain. | `test_agent.py` đã xóa ở P4 (P4-13). **Không** sửa hai README: nhóm cần chọn agent khác (ví dụ report) làm recipe LangChain. | README: MỞ |
| Q8a | `is_peer_sample_constrained`, số peer không có trong bảng DW nào. | Data pack có sẵn hai cột này trong mart (D-03); đã bỏ `TODO`. | ĐÓNG |
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

## Phase 5 (chất lượng câu cấp tòa, demo, tổng kết)

| # | Quyết định / kết quả | Trạng thái |
|---|---|---|
| P5-1 | P4-10: nguyên nhân thật của E10 cấp tòa là **validator bắt nhầm** "tỷ lệ"/"tỷ trọng" (từ "tỷ" trong `quantity_words`), không phải prompt. Sửa: `quantity_word_exceptions` trong config + thông báo lỗi nêu đúng từ bị bắt. Prompt 1.3.0/1.4.0: chọn đủ mọi CAUSE_DISTRIBUTION rồi mới tới căn ví dụ; `limitation_text`/`recommendation_text` không có slot; không viết đơn vị sau slot số; `group_dom`/`rest_dom` là trung vị. Không cấp slot số liệu peer cho candidate có nhóm < 3 (PEER_HIDDEN). Đo (`scripts/tower_eval.py`, Sapphire 1): trước 4/22 item rơi về mẫu (18%); sau 0/46 (5 lần chạy, 1.4.0). | ĐÃ KIỂM |
| P5-4 | Bridge: "dự án <tên>" → phạm vi PROJECT (`project_keywords`), kể cả khi trùng tên tòa (The Beverly); message đến được quét injection → INSIGHT_SECURITY_EVENT, kết quả không đổi; không có KEY → trả tối đa 5 ý và đưa vào JSON. | ĐÃ CHỐT (kỹ thuật) |
| P5-5 | Validator: slot trong `limitation_text`/`recommendation_text` → E11 nêu rõ trường; gọi trung vị là "trung bình" → `MEDIAN_AS_MEAN` (`median_slots`, `mean_words` trong config). | ĐÃ CHỐT (kỹ thuật) |

## Phase 4 (run_task, store, bridge, memory, bỏ LangChain; demo trên data pack)

| # | Quyết định / giả định | Trạng thái |
|---|---|---|
| P4-1 | **Chế độ tương thích (tạm tới D-10).** Orchestrator hiện gửi câu hỏi tự do. `bridge.py` dựng `InsightTaskRequest` bằng luật cố định, không gọi LLM: mã căn theo regex của config (D-73), tên tòa/dự án so với danh mục data pack (bỏ dấu, bỏ tiền tố "The"), intent theo từ khóa (`config/bridge.yaml`: "so sánh" → PEER_GROUP, "vì sao/tại sao/bán chậm…" → SLOW_MOVING, "trung bình/thống kê" → LOOKUP, không có từ khóa → SLOW_MOVING). Không nhận ra phạm vi → trả E01 kèm hướng dẫn. JSON (trần hoặc trong ```json) vẫn được ưu tiên; trường thiếu (run_id, task_id, snapshot, version, refs, user_context) được điền từ data pack. | TẠM (D-10 MỞ) |
| P4-2 | `run_id`/`task_id` = UUID5 của `invocation_id` (gọi lại cùng invocation → artifact cũ, TC-29); `conversation_id` = UUID5 của `ctx.task_id` của backend (D-41, để drill-down trong cùng task dùng được memory). | TẠM |
| P4-3 | D-14 (**duyệt tạm cho prototype**, 29/09): ở chế độ tương thích `role = SALES_MANAGER` (`config/bridge.yaml`) và `authorized_scope` = mọi dự án trong data pack, vì chưa có dữ liệu phân quyền dự án/zone. JSON request mang `user_context` riêng thì dùng của request (E04 vẫn kiểm). | TẠM (prototype) |
| P4-4 | D-11: trả lời = tóm tắt tiếng Việt + `artifact_id` + khối JSON rút gọn các KEY insight (`id`, `rendered_text`, `eligible_for_conclusion`, `limitations`, `recommendation`), tối đa 6.000 ký tự (cắt bớt item, `truncated: true`). Artifact đầy đủ nằm trong `var/insight_artifacts.db`. Report chưa đọc được store này (D-12). | TẠM (D-11, D-12 MỞ) |
| P4-5 | D-52 bổ sung: data pack là snapshot đóng băng (30/06), nên đồng hồ thật (29/09) làm mọi lần chạy bị STALE_SNAPSHOT và mất KEY. `INSIGHT_AS_OF=snapshot` (mặc định) ghim `as_of` = 08:00 ngày sau snapshot; `now` = đồng hồ thật; hoặc một ISO datetime có múi giờ. `as_of` lưu trong `producer.replay`. Câu trả lời ghi rõ "dữ liệu tính đến <ngày snapshot>" (P5). | ĐÃ CHỐT (29/09) |
| P4-6 | D-78 (mới): ở phạm vi ZONE/PROJECT, candidate phân tích cùng cấp phạm vi (T2 phân bố, T3 pattern; không tính T7) đứng trước căn lẻ, cả khi cắt context lẫn khi xếp KEY. Lý do (data pack): căn lẻ có priority tới 1,0 > mặc định T2 0,5, nên câu hỏi "vì sao tòa X…" trả 5 căn lẻ thay vì phân bố nguyên nhân của tòa. | ĐÃ CHỐT (29/09) |
| P4-7 | T7 ở phạm vi ZONE/PROJECT: PEER_SAMPLE_CONSTRAINED / PEER_DATA_MISSING gộp 1 candidate cho mỗi zone/project kèm số căn (slot `units`); phạm vi UNIT vẫn từng căn. Lý do (data pack): 52 căn bị giới hạn peer ở The Sapphire 1; T7 luôn được giữ nên chiếm hết 40 chỗ context. | ĐÃ CHỐT (29/09) |
| P4-8 | `narrative_mode = TEMPLATE` khi có dù chỉ 1 item rơi về mẫu (P2). `producer.model_id` vẫn ghi model khi có item do LLM viết, và câu trả lời ghi "LLM (…), một phần dùng mẫu cố định". | CHỐT (kỹ thuật) |
| P4-9 | claim_binder bỏ từ đơn vị lặp ngay sau slot số (model viết `{{peers}} căn`, giá trị đã là "12 căn"). Thấy trong live 29/09. | CHỐT (kỹ thuật) |
| P4-10 | Câu hỏi cấp tòa trên data pack (40 candidate): 2/10 item vẫn vi phạm E10 sau repair → 2 item đó dùng mẫu. Sửa prompt cần tăng `prompt_version` và chạy lại TC-01→TC-33, nên để sau P4. | ĐÓNG ở P5 (P5-1) |
| P4-11 | D-18 `compact()` tất định: câu hỏi + `artifact_id`, cắt ở 2.000 ký tự. D-40/D-42: chỉ ghi INSIGHT_REF và TOPIC_SUMMARY tất định (gom subject + mã nguyên nhân khi quá 20 ref), không ghi USER_PREF, không có job LLM `MEMORY`. | CHỐT (theo đề xuất) |
| P4-12 | D-50: sự kiện `INSIGHT_*` là 1 dòng JSON qua logger `vdagent.plugin.vdagent_insight` (= `api.log`); `LlmUsage` lưu thêm trong bảng `insight_llm_usage`. | CHỐT (theo đề xuất) |
| P4-13 | D-17: đã xóa agent LangChain (`legacy_agent`/`CtxBridge`/`MemoryMiddleware`, `tests/test_agent.py`, prompt cũ), bỏ `langchain*`, `mcp` khỏi `pyproject.toml`; `tools.py`, `mcp_client.py` giữ nguyên, không import. **Chưa sửa** `README.md` gốc và `agents/_template/README.md` (vẫn lấy insight làm ví dụ LangChain) và prompt Orchestrator (D-10). | README + Orchestrator: MỞ |
| P4-14 | Backend thật (`uv run uvicorn vdagent_backend.app:app`): plugin insight load được. Orchestrator, data, compare, report không load vì thiếu `.env` của chúng, nên demo gửi thẳng `POST /api/agents/insight/messages`, không qua Orchestrator. | Ghi nhận |

## Phase 3 (LLM adapters, pre-flight, repair, cost)

| # | Quyết định / giả định | Trạng thái |
|---|---|---|
| P3-1 | Schema gửi provider (D-34): sinh từ Pydantic, inline `$ref`, mọi object đóng + mọi field `required` (optional → nullable), bỏ `title`/`default`/giới hạn độ dài; Pydantic kiểm đầy đủ sau khi nhận. | CHỐT (kỹ thuật) |
| P3-2 | Phân loại lỗi (D-35): timeout, 429, 5xx, lỗi mạng → E08 (retry 1 lần sau 1 s → OpenAI → TEMPLATE); 400, safety, hết token, JSON sai schema → E09 → 1 lần repair tới **provider vừa trả lời** (reasoning low) → TEMPLATE theo item. | CHỐT |
| P3-3 | TC-18 thực hiện theo D-35: Gemini lỗi 2 lần (1 + 1 retry), OpenAI 503 → TEMPLATE (spec ghi "3 lần"). | Chờ cập nhật spec |
| P3-4 | Prompt v2 ở `prompts/` (P4 đã chuyển từ `prompts/v2/`). `prompt_version` ghi trong file prompt phải khớp `config/llm.yaml` (có test). Hiện `insight-prompt-1.2.0`. | CHỐT |
| P3-5 | (Chuẩn hóa ngoặc được chấp nhận, **đếm và log** `INSIGHT_SLOT_NAME_NORMALISED`.) Trong prompt, candidate mang mã ngắn `c1`, `c2`… kèm danh sách `refs` hợp lệ; câu trả lời được đổi về id thật trước khi validate. Lý do (live 29/09): id dài làm Gemini vượt `max_output_tokens`; model đoán sai ref (`.cause` thay cho `.cause_label`) và ghi tên slot có ngoặc `{{…}}` (được chuẩn hóa). | CHỐT (kỹ thuật) |
| P3-6 | Model được yêu cầu để `skipped` rỗng; hệ thống tự ghi LLM_SKIPPED (giảm output token). | TẠM |
| P3-7 | Test live chỉ chạy khi `INSIGHT_LIVE=1` **và** có key (tránh tốn tiền/không ổn định trong test thường, commit gate và CI). | TẠM |
| P3-8 | `LlmUsage` đi qua interface `UsageSink`; store SQLite làm ở P4. Cảnh báo PRICING_MISSING / HIDDEN_THINKING / vượt ngân sách ngày chỉ log WARNING. | TẠM |
| P3-9 | Cache hit = 0 trong các lần chạy live. **Quyết định: không dùng explicit cache.** | ĐÃ CHỐT |
| P3-10 | Live ép OpenAI (`INSIGHT_FORCE_PROVIDER=openai`, TC-01, 29/09): MAIN + 1 REPAIR (145 reasoning token ở `low`), 2/2 item LLM, VALID, 0,00086 USD, 8,1 s. | ĐÃ KIỂM |

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
