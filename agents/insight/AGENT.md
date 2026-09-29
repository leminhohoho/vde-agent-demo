# Insight Agent — hướng dẫn cho coding agent

Tài liệu này dành cho Codex, Claude, Gemini và người phát triển Insight Agent. Đọc trước khi sửa code trong `agents/insight/`.

## Trạng thái hiện tại

Repo chứa **prototype Python có thể chạy demo end-to-end**, dựa trên data pack và SQLite. Đây chưa phải kiến trúc production của VDAgent. Contract nghiệp vụ hiện hành là `insight.v2` theo spec v2.1; một số lựa chọn kỹ thuật trong prototype vẫn là mặc định tạm, chờ các nhóm ghi trong bảng [Cần chốt sau prototype](#cần-chốt-sau-prototype).

Phân biệt ba nguồn thông tin:

- **Hành vi đang chạy:** code và YAML trong `agents/insight/`.
- **Contract và quy tắc nghiệp vụ:** [`docs/insight_agent_spec.md`](../../docs/insight_agent_spec.md), phiên bản 2.1.
- **Quyết định, giả định và việc còn mở:** [`docs/OPEN_QUESTIONS.md`](../../docs/OPEN_QUESTIONS.md) và [`docs/INSIGHT_P2_P5_DECISIONS.md`](../../docs/INSIGHT_P2_P5_DECISIONS.md).

Nếu câu hỏi là “code hiện làm gì”, kiểm tra code/config trước. Nếu code và spec khác nhau, không tự chọn một bên: xác định đó là khác biệt giữa prototype và contract, rồi cập nhật tài liệu/quyết định liên quan.

## Insight Agent làm gì

Agent biến metric, kết quả chẩn đoán đã tính sẵn và kiểm tra chất lượng dữ liệu thành các nhận định có số liệu, evidence, lineage và giới hạn. Agent không tự quyết định thay người dùng.

- Hỗ trợ scope `MARKET`, `PROJECT`, `ZONE`, `UNIT` và ba intent: `SLOW_MOVING_INVESTIGATION`, `PEER_GROUP_COMPARISON`, `PERFORMANCE_METRIC_LOOKUP`.
- Sinh các nhóm nhận định T1 Unit Diagnosis, T2 Cause Distribution, T3 Pattern Detection, T5 Market Context, T7 Limitation Reporting; T6 Recommendation Drafting là bước gắn đề xuất. T4 không thuộc Insight; Compare Agent phụ trách chọn peer và diễn giải so sánh.
- Cho phép 8 mã nguyên nhân từ `allowed_cause_codes` trong `config/semantic_insight.yaml`.
- Chỉ đọc artifact đầu vào. Prototype dựng các artifact đó từ data pack qua `ExportArtifactReader`; agent không query Data Warehouse hay chạy SQL.
- Không suy luận nhân quả, dự báo, mô phỏng what-if, thay đổi giá/chính sách/trạng thái căn, gửi thông báo hay publish báo cáo.

## Trạng thái triển khai

| Hạng mục | Hiện trạng trong repo |
| --- | --- |
| Plugin Backend | Có `setup(api, opts)` và đăng ký agent `insight`. Có thể gọi trực tiếp qua Backend nếu plugin được nạp. |
| Request bridge | Nhận JSON `InsightTaskRequest` (trần hoặc trong code fence) và chế độ tương thích câu hỏi tự do; luật parse nằm trong `config/bridge.yaml`. |
| Data | Có reader cho fixture và export CSV. Data pack hiện có snapshot `SNAP-20260630-01`, 2 dự án, 9 tòa, 3.000 căn, 1.139 căn quá hạn. `export/` không commit. |
| Pipeline | Có các bước tất định 0–10, candidate engine T1/T2/T3/T5/T7, gate, validation, render, assessment, artifact persistence và sự kiện `INSIGHT_*`. |
| LLM | Có Gemini chính, OpenAI Responses dự phòng, structured output, preflight, retry/fallback, tối đa một lượt repair và fallback theo từng item sang TEMPLATE. Không có LangChain hoặc tool calling trong pipeline. |
| Artifact và usage | Lưu artifact, idempotency và usage trong SQLite, mặc định `var/insight_artifacts.db`; payload có hash SHA-256 canonical JSON. |
| Memory | Lưu/tham chiếu insight theo conversation qua `ctx.memory`; không lưu số hay kết luận cũ để dùng làm bằng chứng. Lỗi memory không làm hỏng task. |
| Regression coverage | Có test unit, fixture, contract và các case TC-01→TC-33; có golden test cho data pack và live test có điều kiện. Việc có test trong repo **không đồng nghĩa** chúng đã chạy thành công trong lần sửa hiện tại. |
| Production integration | Chưa có PostgreSQL queue/lease, Supabase Shared Artifact Store, quyền dự án/zone thật, contract artifact do Data Agent sinh, hoặc Report đọc artifact store của Insight. |

Vì vậy, không mô tả prototype là “đã hoàn thiện production”. Backend route của Insight có thể chạy độc lập; theo ghi nhận trong `OPEN_QUESTIONS.md`, các agent khác có thể chưa load nếu thiếu cấu hình `.env` của chúng, nên demo hiện không bảo đảm luồng qua Orchestrator.

## Luồng chạy

`vdagent_insight/bridge.py` chuyển invocation thành `InsightTaskRequest`, hoàn thiện snapshot/config/artifact refs khi cần, quét prompt injection và gọi lõi `run_task(request, deps)` trong `agent.py`. Lõi không phụ thuộc SDK context.

Pipeline hiện tại:

1. Kiểm idempotency; nếu đã có artifact cho cùng khóa thì trả artifact cũ, không gọi LLM.
2. Đọc artifact và kiểm type, status, content hash, snapshot, semantic config; yêu cầu `metric`, `dq`, `dataset`. Kiểm phạm vi so với `authorized_scope`.
3. Nạp config và memory; nếu memory lỗi thì dùng context rỗng.
4. Sufficiency Gate tính độ đủ dữ liệu, freshness, coverage, missingness, outlier và confidence ban đầu.
5. Candidate Engine sinh ứng viên có số liệu, evidence, lineage, priority và limitation.
6. Preflight giới hạn candidate/token; candidate T7 cần được giữ theo luật priority.
7. LLM chọn/diễn đạt bằng template và slot được phép. Lỗi tạm thời retry một lần rồi thử provider dự phòng; lỗi schema/ngôn ngữ được repair tối đa một lần. Item còn lỗi rơi về TEMPLATE.
8. Validator GR-01→GR-08 kiểm claim, slot, số, nhãn, scope, khuyến nghị và tiếng Việt; claim binder lấy giá trị thật từ candidate rồi format vi-VN.
9. Code tính confidence, `KEY`/`SUPPORTING`, `eligible_for_conclusion`, status và summary.
10. Persist candidate artifact và Insight Artifact; ghi usage/chi phí, event và memory refs.

Các phép tính nghiệp vụ, gate, candidate generation, validation, render và assessment phải tất định, thuần và không gọi LLM/I/O. I/O bất đồng bộ nằm ở adapter/ranh giới pipeline.

## Contract rút gọn

### Input

`InsightTaskRequest` dùng Pydantic v2 (`extra="forbid"`). Các trường chính:

- `run_id`, `task_id`, `attempt`, `fencing_token`;
- `intent`, `tasks`, `question_normalized`;
- `analysis_scope` (level và project/zone/unit IDs);
- `snapshot_id`, `semantic_config_version`;
- `user_context` (`user_id`, `role`, `authorized_scope`);
- `input_artifact_refs` (id/type/version/status/hash), `constraints`;
- tùy chọn `parent_insight_ref`, `conversation_id`.

Mọi artifact phải cùng snapshot và semantic config, hash phải khớp khi đọc lại, scope phải nằm trong quyền được cấp. Prototype hiện kiểm bắt buộc đủ `metric`, `dq`, `dataset`; lỗi đầu vào E01–E04 dừng task.

### Output

Một immutable Artifact Envelope, `artifact_type="insight"`, `schema_version="insight.v2"`, status `VALID`/`PARTIAL`/`INVALID`, cùng payload gồm summary, insights, rejected candidates, chart hints và limitations. Mỗi insight gắn claim/template, numeric bindings, evidence refs, lineage, confidence, materiality, conclusion eligibility và limitation. Khuyến nghị là đề xuất cần người duyệt.

`PARTIAL` thể hiện fallback TEMPLATE, evidence/dữ liệu thiếu, conflict hoặc limitation theo policy. Không có kết quả render được có thể thành `INVALID`. Số trong câu luôn do code bind từ artifact/candidate; LLM chỉ chọn template/slot hợp lệ.

## Luật bất biến khi sửa code

1. Không để LLM tạo, sửa hoặc làm tròn số. Dùng `Decimal`; số nghiệp vụ và giá trong YAML phải là chuỗi decimal, không dùng float. Chỉ formatter được làm tròn khi tạo chuỗi hiển thị.
2. Không phát biểu nhân quả. Dùng ngôn ngữ tương quan/khả năng liên quan; không dùng “chắc chắn do”.
3. Không có evidence/lineage thì không đưa insight vào kết luận. Thiếu dữ liệu phải nói rõ, không suy đoán.
4. Không đọc Comparison Artifact, không gọi agent khác, không query DW, không import/gọi `tools.py` hoặc `mcp_client.py`; không thêm LangChain, MCP hay tool calling.
5. Ngưỡng, taxonomy, nhãn, từ khóa, model, giá, prompt version và luật bridge ở `config/*.yaml`, có version. Không hard-code giá trị nghiệp vụ.
6. Các call LLM đi qua `LlmClient`/adapter SDK (`google-genai`, OpenAI Responses); chỉ có bước draft và một repair. Mọi lần gọi ghi `LlmUsage` và chi phí nếu có bảng giá.
7. Chạy GR-01→GR-08 trên output LLM. Repair tối đa một lần; sau đó dùng TEMPLATE cho item lỗi.
8. Memory chỉ giữ reference/topic/preference được cho phép; không làm nguồn cho số hay kết luận. Memory lỗi không làm fail task.
9. Artifact bất biến sau khi persist; content hash là SHA-256 canonical JSON. Sửa nội dung phải tạo version/artifact mới.
10. Thay `prompts/system.md` hoặc `prompts/repair.md` thì tăng version trong prompt và `config/llm.yaml`, rồi chạy bộ regression TC-01→TC-33.
11. I/O interface dùng `async`. Theo hướng dẫn repo, phát triển theo TDD: viết/điều chỉnh test trước khi thay đổi hành vi.
12. Giữ thay đổi trong `agents/insight/` khi nhiệm vụ nằm trong scope này; chỉ sửa ngoài thư mục khi cần cho yêu cầu. Tuân theo `AGENTS.md` ở repo root: TDD và không dùng sub-agent.

## Quyết định đã chốt sau prototype (spec v2.1)

- **D-00:** đây là prototype demo; dùng SQLite, không queue, ưu tiên chạy end-to-end trên data pack. `attempt`/`fencing_token` hiện không phải lease của worker.
- **D-70:** semantic config version `3.1.0`; tên key/đơn vị theo DW.
- **D-71:** peer từ 5 trở lên được so sánh; 3–4 chỉ mô tả; dưới 3 ẩn. Loại candidate dưới 3 chỉ áp dụng T3; T1/T2 giữ candidate, kèm cờ. Peer constrained giới hạn confidence tối đa MEDIUM.
- **D-72:** T5 dùng xu hướng 12 tháng sẵn có; MOI/PIR/thu nhập chỉ nêu mức kèm limitation, không diễn giải xu hướng.
- **D-73/D-74/D-75:** regex mã căn ở config; `evidence_artifact_id` chỉ phục vụ lineage; GR-08 kiểm template trước khi điền slot.
- **D-76/D-77:** role enum `SALES_OPS`, `SALES_MANAGER`, `EVALUATOR`; có `ExportArtifactReader`, chọn fixture/export bằng env.
- **D-78:** ở scope ZONE/PROJECT, candidate cùng cấp (T2/T3) được ưu tiên trước candidate căn lẻ.
- **D-30/D-35:** Gemini `gemini-3.5-flash-lite` là chính; OpenAI `gpt-6-luna` Responses là dự phòng. Lỗi tạm thời: retry một lần, rồi provider dự phòng, sau đó TEMPLATE. Lỗi schema: repair một lần với provider vừa trả lời, rồi TEMPLATE.
- **P3/P5:** explicit cache không dùng; prompt hiện `insight-prompt-1.5.0`; `max_output_tokens=4000`; khuyến nghị lấy tất định từ `action_texts`; nhãn số và `scope_noun` do code cấp; có luật `DOM_MISSING`, `SLOT_LABEL_WRITTEN`, `SCOPE_NOUN_WRITTEN`, `MEDIAN_AS_MEAN` và ngoại lệ `tỷ lệ`/`tỷ trọng`.
- **P4:** bridge hiện còn chế độ tương thích câu tự do; mặc định `as_of` ghim theo snapshot; reply có snapshot date, artifact id và JSON rút gọn; memory không có LLM job.

## Cần chốt sau prototype

Các dòng dưới đây là **mặc định đang chạy**, không phải mặc nhiên đã được chủ sở hữu nghiệp vụ phê chuẩn. Khi đổi ngưỡng/câu chữ, cập nhật config, test và `OPEN_QUESTIONS.md`.

| Câu hỏi/nhóm | Mặc định hiện tại | Owner / trạng thái |
| --- | --- | --- |
| Q13 — `significant` | Tỷ lệ: hai khoảng Wilson 95% không chồng nhau. Trung vị DOM: bootstrap 1.000 lần seed cố định, khoảng không chồng nhau và chênh ít nhất 15 ngày; chạm biên tính là chồng. | DA — MỞ |
| Q14 — nguồn T3 | Tự tính trung vị DOM và tỷ lệ quá hạn theo nhóm từ dataset; `metric_ref` trỏ vào `insight_candidates` của task. | DA + DATA — MỞ |
| Q15 — action code | Dùng mapping config; mart khác mapping thì phát CONFLICT `ACTION_CODE_MISMATCH`. | OPS — đề xuất đóng |
| Q11 — freshness | Trên 24 giờ: `STALE_SNAPSHOT`, hạ confidence một bậc; trên 72 giờ: LOW. Demo ghim `as_of` theo snapshot. | DA — MỞ |
| Q17 — HIGH confidence | “Nguồn độc lập” là bảng DW có số được bind; chỉ nguồn inventory thì confidence tối đa MEDIUM. | DA — TẠM |
| Q18 — priority | `priority_without_rank`: T2/T3 = 0,5; T5 = 0,3; T7 = 0; hòa xếp theo `candidate_id`. | DA — TẠM |
| Q19 — LEGAL ở cấp dự án | Một candidate PROJECT cho mỗi dự án, slot là số căn; có mã LEGAL khi dự án đủ giấy tờ thì CONFLICT. | DA — TẠM |
| Q20 — coverage, n_eff, outlier | Căn hợp lệ là quá hạn, có mart và tổng attribution score = 1 ± 0,001. Bỏ outlier >3×IQR trong zone/project trừ khi phải bỏ >10%. | DA — MỞ |
| Q21 — biên ngưỡng | Missing/freshness/IQR đúng ngưỡng vào bậc nhẹ hơn; coverage/cỡ mẫu đúng ngưỡng được tính đạt; MNAR khi >10 điểm %. | DA — MỞ |
| Q22 — `market_context` | Đọc các dòng `fact_market_macro_monthly` theo DW 3.1.0; contract Data Agent còn TODO. | DATA — MỞ |
| Q23–Q25 — evidence, source mismatch, format | Catalog evidence/dimension ở config; `SOURCE_MISMATCH` khi lệch >1%; format vi-VN half-up chỉ ở display. | DA — MỞ |
| Ngưỡng `PENDING` | Dùng giá trị khởi tạo trong P0/P1 và semantic config. | DA — cần xác nhận |
| D-20/21/24/25 — câu chữ | Catalog limitation/TEMPLATE/quantity words/từ mệnh lệnh/cụm so sánh đang là bản nháp. | OPS + DA — cần duyệt |
| D-22/23/26 — validator | Slot labels, injection patterns, từ cấm/whitelist tiếng Việt và `MEDIAN_AS_MEAN` theo config/validator hiện tại. | OPS — cần xác nhận |
| D-27/28/31/32, P2-1/2/4 — output policy | Gộp candidate, KEY, evidence kind, `LLM_SKIPPED`/`SELECTION_LIMIT`, `narrative_mode`, status và limitations theo implementation hiện tại. | PO — cần xác nhận |
| D-53/D-52 — replay/task clock | Lưu prompt hash/raw output tối đa 20 KB; `as_of` lưu trong replay, demo ghim theo snapshot. | PO — cần xác nhận |
| D-10 — giao thức Orchestrator | JSON request được hỗ trợ; free text vẫn parse bằng luật cố định trong bridge đến khi tích hợp JSON hoàn tất. | PLAT — MỞ |
| D-11/D-12 — consumer artifact | Reply tóm tắt + artifact id + JSON KEY tối đa 6.000 ký tự. Report chưa đọc được SQLite store của Insight. | PLAT — MỞ |
| D-13 — chart | `chart_hints` có mapping loại chart; chưa có Chart Agent trong repo. | PLAT — MỞ |
| D-14 — authorization | Chế độ tương thích prototype dùng `SALES_MANAGER` và mọi project trong data pack; JSON request vẫn qua E04. Chưa có nguồn `role_project_access`. | PLAT — tạm |
| D-15/D-16 — queue/deadline | Không có queue; attempt/fencing chỉ validate. Deadline 60 giây có thể tạo PARTIAL. | PLAT — prototype |
| D-17 — tài liệu ngoài plugin | README gốc và `agents/_template/README.md` còn ví dụ Insight/LangChain; prompt Orchestrator cần cập nhật khi D-10 chốt. | PLAT — MỞ |
| D-41 — conversation id | Bridge tạo UUID5 từ `ctx.task_id`. | PLAT — tạm |
| D-01/D-02 — Data Agent | Reader prototype tự dựng artifact từ data pack; contract `metric`/`dq` do Data Agent cấp chưa tích hợp. | DATA — MỞ |
| D-60/D-62 — đánh giá/vận hành | Golden hiện so cause code với bridge; 1.139 căn khớp 100% theo ghi nhận dự án. | OPS + DA — cần chốt vận hành |
| P3-6/7/8 — LLM ops | Model để `skipped` rỗng; live test cần `INSIGHT_LIVE=1` và key; cảnh báo chi phí chỉ log WARNING. | TL — TẠM |
| `action_texts`, `slot_labels` | Câu khuyến nghị và nhãn số nằm trong config nhưng còn là bản nháp cần Sales Ops duyệt. | OPS — cần duyệt |

## Những phần chưa có để đạt production

- PostgreSQL queue, lease/heartbeat/fencing thực sự và Supabase/Application DB dùng chung.
- Luồng Orchestrator gửi request contract ổn định; tích hợp artifact store để Report đọc được payload đầy đủ.
- Data Agent cung cấp artifact metric/DQ/dataset theo contract production; data pack hiện là adapter demo.
- Nguồn phân quyền project/zone thực; mặc định mọi project chỉ dành cho demo.
- Chart Agent và luồng sử dụng `chart_hints`.
- Duyệt chính thức các ngưỡng PENDING, catalog câu chữ, action text, slot labels và policy còn mở.

## Chạy demo và cấu hình

Từ thư mục gốc repo:

```powershell
uv run python agents/insight/scripts/ask.py "Vì sao căn SAPPHIRE1-16.231 bán chậm?"
uv run python agents/insight/scripts/ask.py "Tại sao tòa Sapphire 1 có nhiều căn bán chậm?"
uv run python agents/insight/scripts/ask.py "Dự án The Beverly đang bán chậm vì lý do gì?"
uv run python agents/insight/scripts/ask.py --no-llm "DOM trung vị theo hướng ban công ở Sapphire 1?"
uv run python agents/insight/scripts/ask.py --events "Vì sao căn SAPPHIRE1-16.231 bán chậm?"
```

`ask.py` chạy bridge → reader → pipeline → SQLite store → reply; in reply, artifact id, status, LLM calls, cost và latency. Tùy chọn: `--provider gemini|openai`, `--source export|fixtures`, `--invocation <id>` (gọi lại cùng invocation để kiểm idempotency), `--no-llm` (TEMPLATE).

Để nạp plugin vào Backend: `make backend` hoặc `uv run uvicorn vdagent_backend.app:app --port 8000`; route gọi trực tiếp là `POST /api/agents/insight/messages` với `X-User-Id`.

Đặt secret trong `agents/insight/.env` (không commit):

| Biến | Ý nghĩa |
| --- | --- |
| `GEMINI_API_KEY`, `OPENAI_API_KEY` | Provider keys; không có key thì chạy TEMPLATE. |
| `OPENAI_BASE_URL` | Endpoint tương thích OpenAI tùy chọn. |
| `INSIGHT_FORCE_PROVIDER` | `gemini` hoặc `openai`, không fallback provider. |
| `INSIGHT_LLM=off` | Tắt LLM, kể cả có key. |
| `INSIGHT_ARTIFACT_SOURCE` | `export` hoặc `fixtures`; mặc định chọn export khi có. |
| `INSIGHT_EXPORT_DIR` | Thư mục data pack export. |
| `INSIGHT_STORE_PATH` | SQLite path; mặc định `<repo>/var/insight_artifacts.db`. |
| `INSIGHT_AS_OF` | `snapshot` (mặc định), `now`, hoặc datetime ISO có timezone. |

Model, giá, giới hạn token và prompt version: `config/llm.yaml`. Threshold/catalog: `config/semantic_insight.yaml`. Intent, role tương thích, reply limit và scope limitation: `config/bridge.yaml`. Plugin nạp `.env` bằng `dotenv_values()`; giá trị trong file có ưu tiên cao hơn process environment.

## Test và kiểm tra chất lượng

```powershell
uv run pytest agents/insight
INSIGHT_LIVE=1 uv run pytest agents/insight -m live -s
make insight-lint
```

Test datapack tự skip khi không có export phù hợp; live test cần provider key và có thể tốn phí. Đừng báo “tests pass” trừ khi đã chạy và xem exit status trong đúng workspace. Prompt hoặc validator language thay đổi cần chạy regression TC-01→TC-33; thay prompt phải bump `prompt_version` trước.

### Chỉ mục acceptance cases TC-01→TC-33

| TC | Phạm vi và điều kiện đạt |
| --- | --- |
| 01 | Happy path căn thật `SAPPHIRE1-16.231`: KEY root-cause insight có evidence; DOM 143 và chênh peer +19,82% khớp artifact; đề xuất cần duyệt. |
| 02 | Nhiều cause giữ `severity_rank`, rank 1 khớp primary cause, tổng attribution score bằng 1.000. |
| 03 | Cause distribution ở zone: hai cách đếm, tỷ trọng weighted cộng 100%, claim nêu rõ mẫu số/phương pháp. |
| 04 | The Beverly chưa có permit: insight LEGAL cấp PROJECT đứng đầu headline, action vẫn là đề xuất. |
| 05 | AVAILABLE DOM=90, SOLD DOM=150, BOOKED DOM=120 không sinh root-cause signal. |
| 06 | Đổi `overdue_threshold_days` sang 60: căn DOM=75 được chẩn đoán và artifact ghi đúng config version. |
| 07 | Scope không có căn quá hạn: status VALID, không bịa cause. |
| 08 | Peer constrained: có limitation, confidence tối đa MEDIUM và được hiển thị. |
| 09 | Compare thành công/lỗi không làm thay đổi Insight Artifact; hash giống nhau vì Insight không đọc Comparison Artifact. |
| 10 | DQ cho biết 30% `asking_price_vnd` thiếu: không có KEY insight dựa trên giá; candidate bị loại/giảm mức và có DATA_LIMITATION. |
| 11 | Artifact khác snapshot: dừng E03, không có artifact VALID, phát `INSIGHT_INPUT_REJECTED`. |
| 12 | Draft bịa chênh peer 20% khi thật là 12,4%: validator chặn; kết quả cuối chỉ bind 12,4%. |
| 13 | Cause code ngoài allow-list (ví dụ `BAD_LOCATION`) không xuất hiện trong output. |
| 14 | “Chắc chắn do…” bị E12 chặn, repair hoặc TEMPLATE chuyển về ngôn ngữ không nhân quả. |
| 15 | Prompt injection trong text dữ liệu không đổi kết quả và phát `INSIGHT_SECURITY_EVENT`. |
| 16 | Scope vượt `authorized_scope` dừng E04; không lộ unit ngoài quyền. |
| 17 | Mart và bridge mâu thuẫn sinh CONFLICT; cause mâu thuẫn không KEY và `eligible_for_conclusion=false`. |
| 18 | Provider lỗi sau retry/fallback: TEMPLATE vẫn giữ số/evidence đúng, status PARTIAL. |
| 19 | Pattern của nhóm hướng Tây (25 căn) được giữ, nhóm NE (2 căn) bị loại; ngôn ngữ chỉ nói association. |
| 20 | Market context chỉ làm bối cảnh; không gán làm nguyên nhân chính của unit và không có recommendation. |
| 21 | Coverage 28/40: claim nêu mẫu số, confidence giảm một bậc, có PARTIAL_COVERAGE. |
| 22 | Khoảng tin cậy chồng lấp: `significant=false`; validator chặn cách nói “khác biệt rõ”. |
| 23 | Field thiếu 45% bị FIELD_EXCLUDED (>40%); chênh missing 25 điểm % phát MISSING_NOT_RANDOM. |
| 24 | 75 candidates: prompt giữ tối đa 40, giữ cả T7; 35 candidates còn lại mang CONTEXT_BUDGET. |
| 25 | Gemini usage 10.000 prompt (3.000 cached), 1.200 candidate + 800 thoughts: cost 0,00719 USD; hidden-thinking alert (>500). |
| 26 | Chuẩn hóa OpenAI usage; 1.500 output token, reasoning 0; cost theo bảng giá config. |
| 27 | JSON bị cắt do hết token đi theo E09: repair một lần rồi TEMPLATE; cộng cost cả hai call. |
| 28 | Model chưa có trong pricing: task vẫn chạy, `cost_usd=null`, phát cảnh báo PRICING_MISSING. |
| 29 | Cùng request chạy lại dùng artifact cũ, không gọi LLM, cost lần chạy lại bằng 0. |
| 30 | Draft sai tiếng Việt/label cause: LANGUAGE_MISMATCH → repair/TEMPLATE; output dùng `cause_label_vi` từ config. |
| 31 | Memory ref cũ cố đưa DOM 150 ngày của A-05.03 nhưng candidate hiện tại không có số đó: validator chặn; memory không chứa rendered text/numeric bindings. |
| 32 | Ref từ snapshot cũ bị bỏ (E19), đếm `stale_refs_dropped`; kết quả giống chạy không memory. |
| 33 | Memory lỗi đọc/ghi hoặc thiếu conversation id không làm hỏng task; ghi event lỗi ghi memory khi cần, không tính phí LLM MEMORY. |

## Bản đồ thư mục

```text
agents/insight/
├── AGENT.md                         hướng dẫn hợp nhất cho coding agent
├── README.md                        chạy demo và cấu hình nhanh
├── CLAUDE.md                        entry point của Claude, trỏ tới file này
├── config/
│   ├── semantic_insight.yaml        threshold, catalog, câu chữ, guardrail
│   ├── llm.yaml                     provider/model/giá/prompt version/limits
│   └── bridge.yaml                  parse câu tự do, intent, scope, reply
├── scripts/                         ask.py, tower_eval.py, llm_probe.py
└── vdagent_insight/
    ├── agent.py                     pipeline run_task, idempotency, persist/events
    ├── bridge.py                    JSON/free-text bridge, reply và memory adapter
    ├── contracts.py                 Pydantic contracts
    ├── runtime.py, settings.py      env, config, dependency assembly
    ├── export_reader.py, artifacts.py, view.py
    ├── gate.py, candidates/         gate và T1/T2/T3/T5/T7
    ├── llm/                         clients, prompt, schema, calls, usage, preflight
    ├── validation.py, render.py, formatting.py, assess.py, narrate.py
    ├── store.py, memory.py          SQLite store và conversation memory
    └── tests/                       unit, TC fixtures, data-pack golden, live tests
```

## Test case và thay đổi code nên tìm ở đâu

- Contract/schema: `vdagent_insight/contracts.py`; luật/hash artifact: `artifacts.py`.
- Bridge request, compat mode, reply: `bridge.py`, `config/bridge.yaml`.
- Ngưỡng và danh mục: `settings.py`, `config/semantic_insight.yaml`.
- Luồng orchestration/idempotency/persist: `agent.py`; pure business rules: `gate.py`, `candidates/`, `validation.py`, `render.py`, `assess.py`.
- Prompt/structured output/provider: `prompts/`, `llm/`, `config/llm.yaml`.
- Data/export/store/memory: `export_reader.py`, `artifacts.py`, `store.py`, `memory.py`.
- Khi lỗi nghiệp vụ, tìm TC fixture và test tương ứng trước; cập nhật test trước code theo TDD.

`CLAUDE.md` là entry point tương thích trỏ Claude tới file này. Nếu agent không tự đọc `AGENT.md`, hãy nêu rõ đường dẫn `agents/insight/AGENT.md` trong yêu cầu. Không khôi phục LangChain hoặc giả định queue production chỉ vì chúng còn xuất hiện trong tài liệu khác.
