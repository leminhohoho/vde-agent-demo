# Insight Agent — các điểm cần chốt trước P2–P5

Tài liệu gom những gì còn thiếu hoặc mơ hồ trong `docs/insight_agent_spec.md` (v2.0) khi đối chiếu với
code hiện tại (P0 + P1 trên `feat/insight-p1`) và hệ thống vdagent đang chạy. Các câu Q12–Q15 đã có
trong `docs/OPEN_QUESTIONS.md` nên không nhắc lại ở đây.

**Cách đọc.** Mỗi mục `D-xx` gồm: câu hỏi, lý do phải chốt, **đề xuất mặc định**, người chốt, và
phase bị chặn. Mục nào team không phản hồi thì coding agent làm theo đề xuất mặc định và ghi lại
trong `OPEN_QUESTIONS.md`. Riêng các mục đánh dấu **CHẶN** thì không có mặc định an toàn: phải có
câu trả lời trước khi bắt đầu phase đó.

Viết tắt người chốt: **PO** (product owner), **DATA** (phụ trách Data Agent / DW), **PLAT**
(backend, Orchestrator, Report), **OPS** (Sales Ops, duyệt câu chữ), **DA** (Data Analyst, ngưỡng),
**TL** (tech lead: LLM, hạ tầng, chi phí).

**Cập nhật 29/09.** Đã đối chiếu thêm với `docs/PRD_VDAgent.md` và data pack `export/` (20 CSV +
`load.sql`, snapshot `SNAP-20260630-01`: 2 dự án, 9 tòa, 3.000 căn, 1.139 căn quá hạn). Kết quả nằm ở
mục 0b; các mục D bị ảnh hưởng có thêm dòng **Cập nhật (data pack)**.

---

## Quyết định đã chốt (29/09)

| Mục | ĐÃ CHỐT |
|---|---|
| D-00 | Repo là **prototype để demo**. P4 làm gọn: SQLite, không queue, ưu tiên chạy end-to-end trên data pack. |
| D-70 | `semantic_config_version = 3.1.0`. Tên key và đơn vị theo DW (`10` = 10%). Các ngưỡng công thức DW đã có được đưa vào config với trạng thái APPROVED (nguồn: DW). |
| D-71 | Peer: ≥ 5 được so sánh, 3–4 chỉ mô tả, < 3 ẩn. Riêng việc **loại hẳn** candidate khi < 3 chỉ áp cho T3; T1/T2 giữ candidate kèm cờ và không so sánh. `constrained` vẫn giới hạn confidence ≤ MEDIUM. **Thay thế Q12.** |
| D-72 | T5 dùng xu hướng trong 12 tháng hiện có (lãi suất, tỷ lệ hấp thụ), không so cùng kỳ năm trước. MOI, PIR, thu nhập chỉ nêu mức, kèm limitation, không nói xu hướng. |
| D-73 | Regex mã căn đặt trong config, theo định dạng của data (`SAPPHIRE1-13.001`). |
| D-74 | `evidence_artifact_id` chỉ ghi vào lineage. |
| D-75 | GR-08 kiểm `template` trước khi điền slot. |
| D-76 | Enum role = `SALES_OPS`, `SALES_MANAGER`, `EVALUATOR`. |
| D-77 | Làm `ExportArtifactReader` (ở P4). Chọn nguồn qua `INSIGHT_ARTIFACT_SOURCE=fixtures\|export`. Dùng làm nguồn demo và golden set tự động (mã nguyên nhân khớp bridge). |
| TC | TC-01 dùng căn `SAPPHIRE1-16.231`; TC-04 dùng The Beverly. |
| Data pack | `export/` không commit (thêm vào `.gitignore`). Mẫu nhỏ nằm ở `tests/fixtures/export_sample/`. Test trên data pack đầy đủ đánh dấu `@pytest.mark.datapack`, tự skip khi không có `export/`. |
| Tiến độ | Đầu P2 làm D-70/D-71 (commit riêng), rồi làm P2. **Dừng trước P3** để xác nhận D-30 (key/model). |

Các mục khác chưa chốt: coding agent làm theo **đề xuất mặc định** và ghi lại trong `OPEN_QUESTIONS.md`.

---

## 0. Tóm tắt: các điểm chặn lớn nhất (sau cập nhật)

| # | Điểm | Chặn | Người chốt | Trạng thái |
|---|---|---|---|---|
| D-00 | Repo Python hiện tại có phải là bản POC theo PRD hay chỉ là prototype? PRD mô tả TypeScript + Supabase + PostgreSQL Queue | P4 | PO, PLAT | Mới |
| D-01 | Dữ liệu BĐS: **đã có data pack**, nhưng chưa nạp vào hệ thống và Data Agent chưa sinh artifact | P4 chạy thật, P5 | PO, DATA | Giảm nhẹ |
| D-02 | Hợp đồng payload `dq`/`metric` (**`dataset` đã khớp cột với data pack**) | P4 | DATA | Giảm nhẹ |
| D-70 | `semantic_config_version` phải là `3.1.0`; một số key trùng với `semantic_config` của DW nhưng lệch đơn vị | P2 | DA, DATA | Mới |
| D-71 | Bậc peer: 96% dòng dùng so sánh peer có dưới 10 peer, 85% bị `constrained` (dữ liệu cho Q12) | P2 | DA | Mới |
| D-10 | Orchestrator phải gửi `InsightTaskRequest` (JSON) | P4 | PLAT | Giữ nguyên |
| D-11 | Report/Orchestrator nhận kết quả Insight thế nào | P4 | PLAT | Giữ nguyên |
| D-30 | Model ID, API key, endpoint của Gemini và OpenAI Responses | P3 | TL | Giữ nguyên |
| D-40 | Nguồn text để trích `USER_PREF` | P4 | PO | Giữ nguyên |
| D-20 | Danh mục câu chữ tiếng Việt | P2 (merge) | OPS | Giữ nguyên |
| D-60 | Golden set: **data pack đã có ground truth mã nguyên nhân** cho 1.139 căn | P5 | OPS, DA | Giảm nhẹ |

---

## 0b. Cập nhật sau PRD + data pack

### Đã có câu trả lời từ dữ liệu

Kiểm tra trên toàn bộ data pack:

| Mục | Phát hiện | Hệ quả cho Insight |
|---|---|---|
| D-03 | `dm_unit_friction_diagnostics` **có** `is_peer_sample_constrained` và `peer_count` | Bỏ `TODO` cho hai cột này; `contracts.DiagnosticRow` đã khớp |
| D-04 | Mart có đúng 1.139 dòng = 1.139 căn AVAILABLE có DOM > 90; không có căn nào khác | Grain = căn quá hạn đã chẩn đoán. BR-01 vẫn kiểm từ bảng tồn kho |
| D-05 | Bridge: rank 1 = `primary_cause_code` ở 100% căn; tổng score = 1 ở 100%; DOM của mart = DOM tồn kho ở 100% | Pipeline DW đã tự nhất quán; CONFLICT chỉ xuất hiện khi dữ liệu hỏng. Giữ kiểm tra như lưới an toàn |
| Q15 | `recommended_action` khớp mapping `cause → action` ở 100% dòng | `ACTION_CODE_MISMATCH` không bao giờ bật với dữ liệu này; có thể chốt "mart = mapping" |
| Q16 | `spiff_bonus_vnd`: 958 căn = 0, 255 căn = NULL (8,5%) | Đúng như giả định: NULL có nghĩa. DQ **không nên** tính NULL của cột này là "thiếu" |
| E06 | 37 căn thiếu `price_spread_vs_peer_pct`, đều có `peer_count = 0` và không căn nào mang cause dựa trên peer. 147 căn thiếu `funnel_dropoff_rate_pct`, không căn nào mang DEEP_FUNNEL_DROP_OFF | Với dữ liệu này bằng chứng bắt buộc không bao giờ thiếu. NULL ở đây nghĩa là "không áp dụng" → DQ nên xếp là N/A, không phải missing |
| TC-04 | 99 căn LEGAL_PERMIT_BARRIER đều thuộc The Beverly, dự án `is_sales_permit_issued = False` | Có sẵn ca thật cho TC-04 |
| TC-01 | Chỉ 1 căn "giống TC-01": `SAPPHIRE1-16.231`, DOM 143, chênh peer 19,82%, 12 peer, không bị giới hạn. Căn A-05.03 (145 ngày, 12,4%) trong spec không có trong dữ liệu | Golden TC-01 nên dùng căn thật này; fixture `tc01` hiện tại vẫn giữ để unit test |
| TC-15 | `cancellation_reason` là **mã** (`PRICE_TOO_HIGH` hoặc rỗng), không phải text tự do. Text tự do chỉ có ở `unit_objections.specific_reason`, `unit_showing_logs.notes`, `unit_policy_adjustments.policy_value_desc` | Khẳng định D-23: TC-15 phải viết lại |
| D-14 | `users.csv` có `role` = `sales_ops`, `sales_manager`, `evaluator` | Có nguồn cho role, nhưng xem D-76 về enum; vẫn thiếu phạm vi dự án/zone (`role_project_access` có trong PRD 5.4 nhưng không có trong data pack) |
| D-13 | PRD yêu cầu **6 agent, có Chart Agent** (Recharts, KPI Card); repo hiện chưa có | `chart_hints` là cho Chart Agent chứ không cho Report; PLAT cần chốt có làm Chart Agent không |
| D-41, D-50, D-51 | PRD 5.4 có `conversations`, `runs`, `agent_task_logs`, `shared_artifacts` trong Application DB (Supabase) | Đó là kiến trúc đích; repo chưa có. Xem D-00 |

### Mục mới

**D-00 — Repo này là bản POC hay prototype? CHẶN P4.**
PRD 4.1–4.5 mô tả stack TypeScript/Next.js, Supabase PostgreSQL 17, PostgreSQL Queue với SKIP LOCKED
và lease/fencing, Shared Artifact Store, và Chart Agent riêng. Spec Insight v2 viết theo kiến trúc
này. Repo `vde-agent-demo` lại là backend Python + SQLite + plugin, không có queue và không có
artifact store. Đây là gốc của D-10, D-15, D-41, D-50, D-51.
- Nếu **repo là POC chính thức**: giữ các quyết định Q1–Q4 (JSON qua message, SQLite riêng,
  `ctx.memory`), và cập nhật spec cho khớp.
- Nếu **repo chỉ là prototype**, bản POC sẽ viết lại bằng TypeScript: Insight nên đóng gói lõi
  thuần (gate, candidates, validation, render) sao cho dễ port. P4 chỉ làm adapter tối thiểu.
- Cần PO + PLAT chốt, vì câu trả lời quyết định bao nhiêu công sức đổ vào P4.

**D-70 — Phiên bản semantic config và các key trùng với DW. Chốt trước P2.**
- `snapshot_manifest.semantic_version` = `3.1.0`. Mọi artifact và request phải mang
  `semantic_config_version = "3.1.0"`, trong khi `config/semantic_insight.yaml` đang có version
  `sem-2026-09-29`. Đề xuất đổi version của config Insight thành `3.1.0`, và lấy tên file theo
  version này.
- `semantic_config` của DW có 47 key. Các key trùng với config Insight:

  | Key DW | Giá trị DW | Key Insight | Giá trị Insight | Ghi chú |
  |---|---|---|---|---|
  | `overdue_threshold_days` | 90 (APPROVED) | cùng tên | 90 | Khớp |
  | `peer_area_tolerance_pct` | **10** (đơn vị %) | cùng tên | **0.10** (tỷ lệ) | **Lệch đơn vị**: đổi Insight thành `"10"` |
  | `min_peer_count` | 5 (APPROVED) | `peer_tiers.describe_min` / `compare_min` | 5 / 10 | Xem D-71 |
  | `severe_defect_penalty_min` | 25 | `physical_defect_trigger` | 25 | Khớp, khác tên |
  | `thermal_penalty_min` | 40 | `thermal_penalty_trigger` | 40 | Khớp, khác tên |
  | `subsidy_support_min_mo` | 24 | `subsidy_min_months` | 24 | Khớp, khác tên |
  | `funnel_dropoff_threshold_pct` | 60 | `funnel_dropoff_trigger_pct` | 60 | Khớp, khác tên |
  | `low_commission_threshold_pct` | 1.5 | `low_commission_max_pct` | 1.5 | Khớp, khác tên |

- DW **đã có** các ngưỡng công thức mà spec (Phụ lục #3) từng ghi là "trống":
  `peer_spread_threshold_pct` 10, `secondary_gap_threshold_pct` 10, `lump_sum_ticket_ratio_threshold`
  15, `defect_neutral_max` 24, cùng trọng số attribution 0,60/0,25/0,15. Insight vẫn không dùng
  chúng, vì Insight chỉ đọc kết quả của pipeline.
- Đề xuất: đổi tên các key của Insight theo tên DW. Thêm một test so config Insight với
  `export/semantic_config.csv` cho các key trùng, để config không lệch nữa. Insight không query DW
  (luật 4), nên đây chỉ là bản sao được kiểm tra.
- Cần DA chốt.

**D-71 — Bậc peer so với dữ liệu thật: số liệu cho Q12. Chốt trước P2.**
Trên 310 dòng bridge của hai cause dựa trên peer (OVERPRICED_VS_PEER 165, SEVERE_PHYSICAL_DEFECT 145):

| Nhóm | < 5 peer | 5–9 peer | ≥ 10 peer | `constrained` |
|---|---|---|---|---|
| OVERPRICED_VS_PEER | 56 | 99 | 10 | 140/165 |
| SEVERE_PHYSICAL_DEFECT | 74 | 67 | 4 | 133/145 |

Toàn mart: `peer_count` từ 0 đến 15; 445 căn < 5, 644 căn 5–9, 50 căn ≥ 10; 968/1.139 bị
`constrained`. Có căn `constrained` với 10 peer, và căn không `constrained` với 5 peer: cờ này có
nghĩa "đã phải mở rộng sang tầng lân cận", không phải "ít peer".

Với luật hiện tại (so sánh từ 10, mô tả từ 5, ẩn dưới 5), gần như **mọi** nhận định về peer đều bị
giới hạn ở MEDIUM hoặc không được so sánh mạnh, và 1/3 thuộc diện "ẩn".
- Đề xuất: `peer_tiers.compare_min` = `min_peer_count` của DW (5, đã APPROVED theo PRD 5.6);
  `describe_min` = 3; dưới 3 thì không được nói về peer. Giữ trần MEDIUM cho `constrained`.
- DA chốt, rồi đóng Q12.

**D-72 — Dữ liệu vĩ mô không có "cùng kỳ năm trước". Chốt trước khi dùng T5.**
`fact_market_macro_monthly` chỉ có 12 tháng (2025-07 → 2026-06) cho một market
(`MKT-HN-GIALAM`, MID_HIGH). Không có 2025-06, nên T5 "so với cùng kỳ" không bao giờ có kỳ trước.
Thêm vào đó:
- `date_key` là **ngày đầu tháng** (`20250701`), trong khi DW doc ghi "ngày cuối tháng". Code Insight
  so theo YYYYMM nên không bị ảnh hưởng.
- `months_of_inventory_moi` (2,5), `macro_price_to_income_ratio` (8,5) và
  `median_household_income_vnd` là **hằng số suy luận** (DW `semantic_config` ghi PENDING, "suy luận
  D17"). Lãi suất thả nổi và tỷ lệ hấp thụ thì có biến động thật (10,5 → 13,5% và 85 → 71,5%).

Đề xuất: T5 so tháng mới nhất với **tháng xa nhất trong cửa sổ 12 tháng** (ghi rõ "so với 11 tháng
trước"), và bỏ các chỉ số hằng số/suy luận, hoặc gắn limitation `INFERRED_METRIC`. DA/DATA chốt.

**D-73 — Định dạng mã căn.**
`unit_code` = `SAPPHIRE1-13.001` (tên tòa-tầng.số thứ tự), `unit_id` = `U00001`, `unit_key` từ
100000. Mọi ví dụ `A-05.03` trong spec đều lỗi thời. GR-04 (quét mã căn ngoài quyền trong câu) cần
regex theo định dạng mới: đề xuất `[A-Z0-9]+-\d{2}\.\d{3}`. DATA xác nhận định dạng này cố định.

**D-74 — `evidence_artifact_id` trong bridge.**
Cả 2.243 dòng đều có giá trị, dạng `ART-<CAUSE>-<unit_id>`, trỏ tới `shared_artifacts`, nhưng
artifact đó không có trong data pack.
- Đề xuất: Insight ghi id này vào `lineage.source_refs`, không coi là evidence (vì không mở ra được).
  DATA cho biết khi nào artifact này thật sự tồn tại.

**D-75 — Nhãn từ DW không dấu hoặc tiếng Anh, ảnh hưởng GR-08.**
`project_name` ("Vinhomes Ocean Park"), `zone_name` ("The Sapphire 1", "Masteri Waterfront"),
`market_name` ("Gia Lam - Dong Ha Noi") đều là ASCII hoặc tiếng Anh; text tự do trong DW cũng là
tiếng Việt không dấu ("Phong ngu khong co cua so truc tiep"). Nếu GR-08 kiểm trên câu **đã điền
slot**, mọi câu nhắc tên tòa đều bị báo `LANGUAGE_MISMATCH`.
- Đề xuất: GR-08 chỉ kiểm `template` (trước khi điền slot); nhãn đi qua slot được miễn. Cập nhật
  cách hiểu GR-08 trong spec.

**D-76 — Enum vai trò lệch giữa data và spec.**
Data: `sales_ops`, `sales_manager`, `evaluator` (chữ thường). Spec 3.3: `SALES_OPS`, `SALES_MANAGER`,
`PROJECT_DIRECTOR`, `DATA_ANALYST`. `evaluator` không có trong spec; `PROJECT_DIRECTOR` và
`DATA_ANALYST` không có trong data.
- Đề xuất: Orchestrator đổi sang chữ hoa khi dựng request; thêm `EVALUATOR` vào enum (quyền xem như
  `DATA_ANALYST`). PO chốt.

**D-77 — Nạp data pack vào đâu để P4/P5 chạy được.**
`load.sql` dùng `\copy` của PostgreSQL. Warehouse của repo là SQLite (bán lẻ), và Data Agent không
sinh artifact.
- Đề xuất cho Insight (không phụ thuộc D-01): viết một **reader chỉ dùng khi dev/test**,
  `ExportArtifactReader`. Reader đọc các CSV trong `export/`, dựng payload `dataset`, `dq`, `metric`,
  `market_context` theo đúng contract hiện tại, và tự tính DQ: missing rate, missing theo nhóm quá
  hạn/đã bán, coi NULL "có nghĩa" là N/A theo mục trên.
- Nhờ reader này, P4 chạy được pipeline thật trên 1.139 căn, P5 có golden set tự động cho mã nguyên
  nhân (so với bridge). Reader đánh dấu rõ là dev-only; khi Data Agent sinh artifact thật thì bỏ.
- PO/DATA chốt có chấp nhận cách này không, và phía nào sở hữu reader.

### Thông tin khác từ PRD (không chặn)

- PRD 3.4 bắt buộc có Report "6 phần chuẩn + completeness validation" và "KPI Card + chart". Insight
  chỉ cần cung cấp insight có `eligible_for_conclusion` và `chart_hints`, nên không ảnh hưởng P2–P3.
- PRD 4.4: `status` của artifact có thêm `DRAFT`, `SUPERSEDED`. Contract Insight hiện chỉ có
  `VALID`/`PARTIAL`/`INVALID` cho status lúc ghi. `SUPERSEDED` sẽ được đặt khi có version mới (P4,
  D-51).
- PRD 4.7 Failure Matrix: Insight thuộc nhóm Analytical (lỗi thì run PARTIAL), đúng như spec 8.
- PRD 5.3 vẫn liệt kê 12 bảng tên cũ (`dim_units`, `unit_deal_events`…). Data pack dùng tên DW
  v3.1.0, nên PRD 5.3 cần cập nhật (Phụ lục #1 của spec).
- Data pack có 3 bảng **không có trong DW doc** (`unit_objections`, `unit_showing_logs`,
  `unit_policy_adjustments`). Insight không dùng. DATA cập nhật DW doc nếu chúng là chính thức.

---

## 1. Hợp đồng dữ liệu với Data Agent (DATA)

**D-01 — Nguồn dữ liệu thật. CHẶN P4 (chạy thật), P5.**
Warehouse của repo (`data/seed_warehouse.py`) là dữ liệu bán lẻ (`dim_product`, `fact_sales`).
16 bảng BĐS của DW v3.1.0 không tồn tại, và Data Agent chỉ trả dataset SQL (`ds_…`), không trả
artifact `metric`/`dq`/`dataset` có `snapshot_id`, `semantic_config_version`, `content_hash`.
- Đề xuất: (a) DATA seed một warehouse BĐS mock theo "Quy chuẩn giả lập" ở mục 4 của DW v3.1.0,
  gồm 1–2 dự án POC; (b) cho đến khi có, P4 chạy bằng `FixtureArtifactReader`, còn P5 chỉ kiểm được
  luồng docker-compose với fixture, không chạy end-to-end với dữ liệu thật.
- Cần chốt: có làm (a) không, ai làm, xong khi nào.
- **Cập nhật (data pack):** đã có `export/` (20 bảng, 3.000 căn, 2 dự án Vinhomes Ocean Park, 9 tòa), nên phương án (a) coi như đã xong phần dữ liệu. Còn thiếu: nạp vào đâu (`load.sql` là PostgreSQL, warehouse của repo là SQLite), và ai sinh artifact. Xem D-77.

**D-02 — Payload của artifact đầu vào. CHẶN P4.**
Shape hiện tại nằm trong `contracts.py`, mục "Input artifact payloads", đánh dấu
`TODO(data-agent-contract)`. Cần DATA xác nhận hoặc sửa:
- `dataset`: các mảng theo tên bảng DW (`dim_project_profile`, `dim_zone_master`,
  `dim_unit_master`, `fact_unit_inventory_snapshot`, `dm_unit_friction_diagnostics`,
  `unit_diagnostic_causes`), mỗi mảng chỉ chứa các cột Insight dùng.
- `dq`: `overall_status`, `snapshot_date`, `data_as_of`, và `fields[]` gồm `table`, `field`,
  `status`, `is_primary`, `missing_rate_pct`, `missing_rate_overdue_pct`, `missing_rate_sold_pct`.
- `metric`: `metrics[]` gồm `metric_id`, `calculation_ref`, `subject`, `dimension`, `group`,
  `value`, `unit`, `n`.
- `market_context`: các dòng `fact_market_macro_monthly`.
- Quy ước chung: số thập phân dạng **chuỗi** (Insight từ chối float), và `content_hash` = SHA-256
  trên canonical JSON của `payload`. DATA phải hash đúng cách này (xem `artifacts.canonical_json`).
- **Cập nhật (data pack):** tên cột của `dataset` khớp 100% với data pack (`contracts.py` dùng tập con). Vẫn thiếu: không có bảng nào cho kết quả DQ (`is_primary`, missing rate, `data_as_of`) hay cho `metric`. Đề xuất D-77 tự tính các phần này khi dev.

**D-03 — Cột không có trong DW v3.1.0.**
- `is_peer_sample_constrained` và `peer_count`: spec dùng, nhưng không bảng nào có. Đề xuất thêm
  vào `dm_unit_friction_diagnostics`.
- `data_as_of` (thời điểm nạp tồn kho/giá, dùng cho freshness): đề xuất lấy từ DQ Engine.
- Missing rate theo nhóm quá hạn / đã bán (cho MNAR): đề xuất DQ Engine tính sẵn.
- **Cập nhật (data pack):** `is_peer_sample_constrained`, `peer_count` **đã có** trong mart. `data_as_of`: không có; đề xuất dùng `snapshot_manifest.snapshot_date` (23:59 giờ `Asia/Ho_Chi_Minh`). Missing rate theo nhóm: tính được từ bảng tồn kho (D-77).

**D-04 — Grain của mart `dm_unit_friction_diagnostics`.**
DW ghi "1 dòng / căn AVAILABLE", nhưng `primary_cause_code` lại NOT NULL. Vậy căn AVAILABLE chưa quá
hạn có dòng không, và mang mã gì?
- Đề xuất: mart chỉ chứa căn quá hạn đã chẩn đoán. Insight không dựa vào mart để xác định quá hạn
  (BR-01 kiểm từ `fact_unit_inventory_snapshot`).
- **Cập nhật (data pack):** đã rõ: mart = đúng 1.139 căn quá hạn đã chẩn đoán. **Đóng.**

**D-05 — Ai đảm bảo nhất quán giữa mart và bridge.**
BR-03/BR-04 hiện sinh CONFLICT ở phía Insight. Cần DATA xác nhận pipeline DW có tự kiểm tra hay
không (Phụ lục #4 của spec). Nếu có, Insight chỉ báo khi lệch; nếu không, CONFLICT sẽ xuất hiện
thường xuyên.
- **Cập nhật (data pack):** pipeline DW nhất quán 100% (rank 1 = primary, tổng score = 1, DOM khớp, action khớp mapping). Chỉ còn xác nhận: pipeline có **đảm bảo** điều này cho các snapshot sau không. Lưu ý: độ chính xác của score không đều (`0.60` và `0.400`); Insight dùng Decimal nên không ảnh hưởng.

**D-06 — Nhãn hiển thị.**
`project_name`, `zone_name`, `unit_code` là text từ DW và sẽ đi vào prompt qua slot nhãn (`{{unit}}`,
`{{project}}`). GR-05 lại cấm text tự do từ DW trong prompt. Đề xuất: các cột mã/tên được coi là
"nhãn", không phải text tự do; Insight vẫn quét mẫu injection trên chúng (D-23).
- **Cập nhật (data pack):** nhãn là ASCII/tiếng Anh, nên kiểm GR-08 cần tách riêng: xem D-75.

---

## 2. Tích hợp hệ thống (PLAT)

**D-10 — Orchestrator gửi `InsightTaskRequest`. CHẶN P4.**
`agents/orchestrator/prompts/system.md` hiện bảo Orchestrator gửi "dataset ids và câu hỏi" bằng lời,
và vẫn mô tả warehouse bán lẻ. Insight v2 chỉ nhận JSON `InsightTaskRequest`; parse lỗi thì trả E01.
Cần PLAT chốt:
- Ai sửa prompt/tool của Orchestrator để dựng request: chọn `intent` và `tasks`, `analysis_scope`,
  `snapshot_id`, `semantic_config_version`, `input_artifact_refs` (id + hash).
- `run_id`/`task_id` phải là UUID. Backend dùng id dạng `t_…`, nên đề xuất Orchestrator tự sinh
  UUID và ghi id của backend vào log.
- Trong lúc chờ: có giữ agent Insight cũ (LangChain) song song dưới tên khác, ví dụ `insight_legacy`,
  để demo không gãy không?

**D-11 — Trả kết quả cho Orchestrator và Report. CHẶN P4.**
Đã chốt (Q3): câu trả lời = tóm tắt tiếng Việt + `artifact_id` + khối JSON payload. Nhưng một payload
tới 12 insight kèm evidence và lineage có thể rất dài, làm Orchestrator tốn token. Hơn nữa Report
không đọc được SQLite riêng của Insight (plugin không được đụng DB của nhau).
- Đề xuất: reply chỉ chứa tóm tắt + `artifact_id` + danh sách **KEY insight rút gọn** (id,
  `rendered_text`, `eligible_for_conclusion`, limitation, `chart_hints`), giới hạn khoảng 6.000 ký
  tự. Payload đầy đủ đọc lại qua một tool MCP mới `get_insight_artifact(artifact_id)`, việc này cần
  PLAT thêm vào backend.
- Cách thay thế, không cần sửa backend: gửi JSON đầy đủ nhưng cắt các trường lớn (evidence, lineage).
- Cần chốt: chọn cách nào, và giới hạn kích thước reply.

**D-12 — Report dùng Insight thế nào.**
Spec 10.4 yêu cầu Report chỉ đưa insight có `eligible_for_conclusion = true` vào kết luận, và
Claim-Evidence Validator của Report đọc `evidence_refs`. Report hiện là agent LangGraph, không biết
gì về hợp đồng này. Cần PLAT xác nhận ai sửa Report, và sửa trong phase nào.

**D-13 — `chart_hints`.**
Hệ thống không có Chart Agent riêng; Report vẽ chart bằng `create_chart` với 3 loại `bar`, `line`,
`pie`, và chỉ vẽ từ dataset `ds_…`. Trong khi đó `chart_hints.metric_refs` của Insight trỏ vào
artifact Insight, không phải dataset.
- Đề xuất: `suggested_chart` ∈ {`bar`, `line`, `pie`}, theo loại insight: CAUSE_DISTRIBUTION → `pie`,
  PATTERN → `bar`, MARKET_CONTEXT → `line`, ROOT_CAUSE_SIGNAL → không có hint.
- Cần chốt: Report vẽ từ đâu. Hoặc Data Agent tạo dataset tương ứng, hoặc bỏ `chart_hints` trong POC.
- **Cập nhật (data pack):** PRD yêu cầu Chart Agent riêng (Recharts, KPI Card). Đề xuất bộ từ cho `suggested_chart` theo Recharts: `bar`, `stacked_bar`, `line`, `pie`, `kpi_card`. Ví dụ: CAUSE_DISTRIBUTION → `stacked_bar` (hai cách đếm), ROOT_CAUSE_SIGNAL → `kpi_card`. Việc Report hiện chỉ vẽ `bar`/`line`/`pie` là giới hạn của repo, không phải của contract.

**D-14 — Quyền truy cập (`authorized_scope`, `role`).**
Bảng `users` của backend chỉ có `id` và `name`, không có vai trò hay phạm vi dự án/zone.
- Đề xuất cho POC: Insight tin `user_context` do Orchestrator gửi.
- Cần chốt: ai là nguồn sự thật của vai trò và phạm vi (Orchestrator đọc từ đâu?).
- **Cập nhật (data pack):** `users.csv` có role (D-76). Phạm vi dự án/zone (`role_project_access`, PRD 5.4) vẫn chưa có dữ liệu.

**D-15 — Hàng đợi, lease, fencing token, heartbeat (spec 9.2, 10.4).**
Hệ thống không có PostgreSQL Queue; backend gọi thẳng `invoke(ctx)`.
- Đề xuất: `attempt` và `fencing_token` vẫn validate theo schema nhưng không có tác dụng; hủy task =
  `asyncio.CancelledError` (E15); không có heartbeat. Cần xác nhận POC chấp nhận điều này.

**D-16 — Deadline (E16).**
`constraints.deadline_ms` mặc định 60 s. Backend không có deadline cho mỗi turn; mỗi lần gọi LLM
timeout 20 s theo config.
- Đề xuất: hết deadline thì trả phần đã validate, nếu chưa có thì dùng TEMPLATE, status PARTIAL.
  Cần PLAT xác nhận 60 s phù hợp với độ sâu gọi giữa các agent (`max_depth = 4`).

**D-17 — Gỡ bản LangChain cũ (Q7 còn mở).**
P4 sẽ xóa `langchain*` khỏi `agents/insight/pyproject.toml`, xóa `MemoryMiddleware`, và viết lại
`bridge.py` và `agent.py`. `README.md` gốc và `agents/_template/README.md` đang lấy Insight làm ví
dụ mẫu cho LangChain. Cần PLAT chọn agent khác (đề xuất: report) và sửa hai README đó.

**D-18 — `compact()` của sdk.**
Mỗi agent phải có `compact(previous_summary, messages)`, nhưng spec v2 không nhắc tới.
- Đề xuất: làm tất định, không gọi LLM: giữ danh sách `artifact_id` + KEY insight id + câu hỏi,
  cắt ở 2.000 ký tự. Như vậy không phát sinh chi phí LLM ngoài 2 điểm gọi cho phép.

---

## 3. Validator và ngôn ngữ (P2)

**D-20 — Danh mục câu chữ. Cần OPS duyệt trước khi merge P2.**
- Thông điệp tiếng Việt cho **mọi mã limitation**, dùng cho `payload.limitations[].message` và slot
  `{{limitation}}`: DQ_NOTE, DQ_WARN, DQ_DESCRIBE_ONLY, FIELD_EXCLUDED, MISSING_NOT_RANDOM,
  EVIDENCE_FIELD_MISSING, PARTIAL_COVERAGE, LOW_COVERAGE, INSUFFICIENT_COVERAGE, SMALL_SAMPLE,
  GROUP_TOO_SMALL, OUTLIER_WARN, OUTLIER_EXCLUDED, STALE_SNAPSHOT, PEER_SAMPLE_CONSTRAINED,
  PEER_DATA_MISSING, NO_OVERDUE_UNITS, MARKET_CONTEXT_MISSING, CONFLICT và các lý do conflict
  (PRIMARY_CAUSE_MISMATCH, ATTRIBUTION_SUM_MISMATCH, ACTION_CODE_MISMATCH, SOURCE_MISMATCH,
  LEGAL_FLAGS_MISMATCH).
- Các câu TEMPLATE và `recommendation_text` trong `config/semantic_insight.yaml` hiện là bản nháp.
- Nhãn của slot `{{permit_status}}`, ví dụ "giấy phép mở bán", "bảo lãnh ngân hàng", "giấy phép mở
  bán và bảo lãnh ngân hàng".
- Đề xuất: coding agent viết bản nháp đầy đủ trong config ở đầu P2, OPS duyệt trên PR.

**D-21 — GR-01: phát hiện "số tự do".**
Quét chữ số thì đơn giản. Khó là "từ chỉ lượng": "một", "hai" thường là số từ bình thường ("một căn",
"hai nguyên nhân").
- Đề xuất: danh sách `quantity_words` trong config gồm "một nửa", "gấp đôi", "gấp ba", "phần tư",
  "phần ba", "chục", "trăm", "nghìn", "ngàn", "triệu", "tỷ", "hàng loạt", "đa số", "phần lớn"; và cho
  phép số đếm "một"…"mười" khi đứng trước danh từ đơn vị. Cần DA/OPS duyệt danh sách.

**D-22 — Danh sách slot nhãn và nguồn giá trị.**
Slot không phải số — `unit`, `project`, `zone`, `scope`, `subject`, `group`, `market`, `cause_label`,
`permit_status`, `limitation` — được điền từ subject của candidate, từ config, hoặc từ danh mục D-20.
- Đề xuất: khóa cứng danh sách này trong config (`label_slots`). LLM đặt một slot không có trong
  danh sách và không phải slot số của candidate → vi phạm GR-03.

**D-23 — GR-05 và E13: mẫu prompt injection.**
Candidate JSON gửi LLM không chứa text tự do, chỉ có số, mã và nhãn (TC-15 dựa vào
`cancellation_reason`, nhưng payload hiện tại không có cột này).
- Đề xuất: quét `question_normalized` và các nhãn với danh sách mẫu trong config (ví dụ "bỏ qua
  (mọi) hướng dẫn", "ignore previous", "system:", "bạn là", URL). Khớp → log INSIGHT_SECURITY_EVENT,
  dữ liệu vẫn giữ nguyên.
- Cần DATA xác nhận: text định tính như `cancellation_reason` có bao giờ tới Insight không? Spec
  5.5 nói chỉ tới dưới dạng nhãn + số đếm. Nếu đúng vậy, TC-15 cần viết lại.
- **Cập nhật (data pack):** `cancellation_reason` là mã (`PRICE_TOO_HIGH` hoặc rỗng). Text tự do chỉ nằm ở các bảng Insight không đọc. Đề xuất viết lại TC-15: đặt câu injection vào `question_normalized` và vào một nhãn (ví dụ `zone_name`), rồi kiểm output bằng golden và có INSIGHT_SECURITY_EVENT.

**D-24 — GR-06: câu mệnh lệnh / đã thực thi.**
- Đề xuất: `recommendation_text` phải bắt đầu bằng "Đề xuất" hoặc "Có thể cân nhắc"; cấm "hãy",
  "phải", "ngay lập tức", "đã giảm", "đã điều chỉnh" (bổ sung vào `forbidden_phrases` hoặc thêm
  `imperative_phrases`). OPS duyệt.

**D-25 — GR-07: từ "so sánh mạnh".**
Validator cần danh sách từ chỉ được dùng khi `significant = true`.
- Đề xuất `strong_comparison_phrases`: "cao hơn rõ", "thấp hơn rõ", "chậm hơn rõ", "nhanh hơn rõ",
  "vượt trội", "đáng kể", "khác biệt rõ", "nổi bật". DA/OPS duyệt.
- Về vế "mọi mã limitation của candidate phải xuất hiện trong output": đề xuất code tự gắn mã vào
  `insight.limitations`, không phụ thuộc LLM viết `limitation_text`.

**D-26 — GR-08: phát hiện tiếng Anh.**
Tiếng Việt không dấu cũng là ký tự ASCII ("gia", "can"), nên không thể coi mọi từ ASCII là tiếng Anh.
- Đề xuất: câu phải có ít nhất một ký tự có dấu tiếng Việt; và câu không được chứa từ nào trong danh
  sách từ tiếng Anh phổ biến (`english_stopwords`: the, is, are, unit, price, overpriced, peers,
  because, …) ngoài `english_whitelist`. Chấp nhận có thể bỏ sót.
- **Cập nhật (data pack):** kiểm trên `template`, không kiểm trên câu đã điền slot (D-75).

---

## 4. Confidence, KEY, status, payload (P2)

**D-27 — Gộp nhiều candidate thành một insight.**
LLM trả `candidate_ids: list`, còn `Insight.candidate_id` trong spec 4.2 chỉ là một chuỗi.
- Đề xuất: `candidate_id` = candidate đầu tiên; các id còn lại ghi vào `lineage.calculation_refs`
  dưới dạng `candidate:<id>`. Evidence là hợp; confidence lấy mức thấp nhất; chỉ gắn `cause_code`
  khi mọi candidate cùng một mã. Nếu muốn đổi thành `candidate_ids: list` trong contract `insight.v2`
  thì cần PO chốt ngay, vì đây là thay đổi contract.

**D-28 — Loại insight nào được là KEY / `eligible_for_conclusion`.**
- Đề xuất: chỉ ROOT_CAUSE_SIGNAL, CAUSE_DISTRIBUTION, PATTERN (và PATTERN phải có
  `significant = true`). MARKET_CONTEXT, DATA_LIMITATION, CONFLICT luôn là SUPPORTING. Mọi cờ
  CONFLICT chặn KEY; POC không có cơ chế "giải quyết" conflict.

**D-29 — Headline và coverage trong summary.**
- `headline_insight_ids`: đề xuất lấy các KEY insight theo thứ tự LEGAL (BR-06) → priority →
  `candidate_id`, tối đa `max_key_insights`.
- `coverage.units_explained`: đề xuất = số căn quá hạn có ít nhất một ROOT_CAUSE_SIGNAL được chọn,
  hoặc được đại diện trong một CAUSE_DISTRIBUTION.
- `constraints.max_key_insights` trong request (nếu có) ghi đè giá trị trong config.

**D-31 — Evidence và artifact `insight_candidates`.**
- Candidate hiện lưu evidence dạng `<artifact_id>#<path>`, còn `Insight.evidence_refs` cần `kind` ∈
  {METRIC, DIAGNOSTIC_ROW, DQ, MARKET}. Giá trị do Insight tự tính (tỷ trọng T2, trung vị T3) trỏ vào
  `insight_candidates`, và không có `kind` phù hợp.
- Đề xuất: thêm `kind = COMPUTED` (thay đổi contract, PO chốt), hoặc xếp chúng vào `METRIC`.
- Spec 4.1 coi `insight_candidates` là một artifact đầu vào. Đề xuất lưu nó như một artifact bất biến
  trong cùng store SQLite, có id và hash, để truy vết được con số. Cần xác nhận.

**D-32 — Candidate không được LLM chọn.**
- Đề xuất: candidate LLM bỏ qua → `rejected_candidates` với lý do `LLM_SKIPPED`.
- Ở chế độ TEMPLATE toàn phần, render tối đa `max_selected_insights` (12) candidate theo priority,
  giữ mọi T7.

**D-33 — Khuyến nghị.**
- Đề xuất: `recommendation.text` lấy từ LLM nếu qua được GR-06, không thì lấy câu trong config.
  `user_pref.show_recommendation = false` → bỏ phần khuyến nghị.
- Intent PERFORMANCE_METRIC_LOOKUP không bao giờ có khuyến nghị, kể cả khi request có T6.

---

## 5. LLM và chi phí (P3, TL)

**D-30 — Model, key, endpoint. CHẶN P3.**
- `gemini-3.5-flash-lite` và `gpt-6-luna` có đúng là model ID tài khoản đang dùng được không?
- Biến môi trường: đề xuất `GEMINI_API_KEY` (Google AI Studio hay Vertex?), `OPENAI_API_KEY`,
  `OPENAI_BASE_URL`.
- `.env` hiện trỏ tới một endpoint tương thích OpenAI (proxy). Proxy đó có hỗ trợ **Responses API**
  và structured output strict không?
- Container docker-compose có được gọi ra internet không?

**D-34 — Structured output của hai provider.**
Gemini `responseSchema` không hỗ trợ đủ `maxLength`/`maxItems`. OpenAI strict yêu cầu mọi field đều
`required` (field tùy chọn phải khai báo nullable).
- Đề xuất: schema gửi provider là bản "hạ cấp" (bỏ các ràng buộc không hỗ trợ); Pydantic kiểm đầy đủ
  sau khi nhận. TL xác nhận.

**D-35 — Retry: TC-18 lệch với config.**
TC-18 ghi "Gemini timeout 3 lần", nhưng `transient_retries = 1` nghĩa là chỉ gọi Gemini 2 lần rồi
chuyển sang OpenAI.
- Đề xuất: giữ 1 retry. Sửa TC-18 thành "Gemini timeout 2 lần (lần đầu + 1 retry), OpenAI trả 503 →
  TEMPLATE".
- Cần chốt thêm:
  - Lỗi nào là tạm thời: timeout, 429, 5xx, lỗi mạng.
  - 400 hay safety block của model: đề xuất coi là E09, đi repair rồi TEMPLATE, không retry.
  - Lệnh repair gọi provider nào: đề xuất provider vừa trả kết quả.

**D-36 — Prompt.**
- `prompts/system.md` và `repair.md` bản đầu do coding agent viết trong P3. Cần người duyệt (OPS +
  TL) trước khi chạy golden set.
- Nội dung gửi lệnh repair: đề xuất gồm output cũ, danh sách lỗi có mã, và candidate liên quan.

**D-37 — Giá, cảnh báo ngân sách, trung vị 7 ngày.**
- Bảng giá trong `config/llm.yaml` cần TL đối chiếu với trang giá chính thức.
- Cảnh báo `run_cost_alert_multiplier` cần trung vị chi phí 7 ngày, `budget.daily_usd` cần tổng chi
  phí theo ngày. Hệ thống không có bảng `agent_task_logs`.
- Đề xuất: lưu `LlmUsage` và tổng chi phí mỗi task vào store SQLite của Insight, tính trung vị từ đó.
  Cảnh báo chỉ ghi log WARNING (không gửi kênh ngoài).
- Cần TL chốt: có cần kênh cảnh báo ngoài (Slack/email) không, và vượt ngân sách ngày thì làm gì
  (chỉ cảnh báo, hay tự chuyển TEMPLATE)?

---

## 6. Memory (P4)

**D-40 — Nguồn trích `USER_PREF`. CHẶN P4.**
`extract.md` trích sở thích "từ lời người dùng", nhưng Insight chỉ nhận message từ Orchestrator
(`question_normalized`), không thấy câu gốc của người dùng. Các lựa chọn:
- (a) Orchestrator gửi sở thích tường minh trong request, ví dụ `user_context.preferences`: thay đổi
  contract, PO + PLAT chốt.
- (b) Trích từ `question_normalized`: dữ liệu nghèo, dễ trích sai.
- (c) Bỏ USER_PREF trong POC, chỉ giữ INSIGHT_REF + TOPIC_SUMMARY.

Đề xuất: (c) cho POC, (a) sau.

**D-41 — `conversation_id`.**
Backend không có khái niệm hội thoại; memory của sdk chỉ có scope (user, agent).
- Đề xuất: Orchestrator sinh `conversation_id` = UUID gắn với chuỗi hội thoại của nó. Insight lọc
  bản ghi theo trường này trong JSON payload. Không có trường này → bỏ phần memory hội thoại (spec
  đã cho phép).

**D-42 — Chi phí và nội dung job MEMORY.**
Job compact (TOPIC_SUMMARY) có thật cần LLM không? Nội dung chỉ là danh sách subject + mã nguyên nhân.
- Đề xuất: làm tất định, không gọi LLM, tránh chi phí `call_type = MEMORY`. Chỉ `extract` (nếu giữ
  USER_PREF) mới cần LLM.

---

## 7. Vận hành, log, lỗi (P4, P5)

**D-50 — Nơi ghi log và sự kiện.**
Spec 10.1–10.2 cần log JSON và bảng `events` (INSIGHT_*). Plugin không được ghi vào DB của backend.
- Đề xuất: log JSON một dòng qua `api.log` (logger `vdagent.plugin.vdagent_insight`); sự kiện
  INSIGHT_* là các dòng log có trường `event`; `LlmUsage` lưu thêm vào SQLite của Insight. Nếu cần
  hiện trên UI thì PLAT phải mở API ghi sự kiện cho plugin.

**D-51 — Store `var/insight_artifacts.db`.**
- Đề xuất bảng: `insight_artifacts` (id, version, idempotency_key UNIQUE, status, envelope_json,
  content_hash, created_at, superseded_by) và `insight_llm_usage`. Ghi trong transaction.
  Trùng idempotency key → trả artifact cũ (E17).
- Phiên bản mới / SUPERSEDED (9.3) chỉ xảy ra khi drill-down với `parent_insight_ref`.
- Cần PLAT xác nhận: thư mục `var/` được mount volume trong compose (đã có `./var:/app/var`), và có
  giữ dữ liệu khi deploy lại không.

**D-52 — Đồng hồ của task.**
Freshness phụ thuộc thời điểm chạy.
- Đề xuất: `as_of` = thời điểm nhận task, lưu vào artifact để replay (9.4) cho ra đúng kết quả cũ.
  Idempotency key không chứa `as_of`.

**D-53 — Lưu dữ liệu replay (9.4).**
Spec đòi lưu "hash của prompt và output thô của LLM". Đề xuất lưu trong envelope (`producer.replay`):
`prompt_sha256`, output thô (tối đa 20 KB) và tham số gọi LLM. Cần PO xác nhận được phép lưu output
thô, vì có thể chứa text do LLM viết.

---

## 8. Đánh giá (P5)

**D-60 — Golden set. CHẶN P5 cho các TC chạy LLM thật.**
TC-01→11, 16, 19, 20 cần "LLM thật + golden file (cho phép khác câu chữ, không cho khác số, mã,
evidence)". Hiện chưa có golden set (Phụ lục của spec).
- Đề xuất: bắt đầu bằng golden cho chính 10 fixture P1 (so số, mã, evidence, loại insight, KEY),
  rồi OPS + DA bổ sung 30–50 câu.
- Cần chốt: ai chấm tiêu chí "dễ hiểu ≥ 4,0", và chấm lúc nào.
- **Cập nhật (data pack):** bridge của data pack là ground truth mã nguyên nhân cho 1.139 căn, nên có thể sinh tự động golden cho "cause code khớp ≥ 95%" (11.1). Phần chấm câu chữ vẫn cần OPS.

**D-61 — TC chưa có fixture.**
- TC-08→20 và TC-24→33 sẽ được tạo trong P2–P4 theo cùng mẫu với `tests/fixtures/tcNN`.
- Cần chốt riêng cho TC-15 (xem D-23) và TC-16. TC-16: request có `unit_ids` ngoài quyền → E04. Nhưng
  kiểm tra quyền theo `unit_ids` đòi hỏi biết căn thuộc zone nào, mà thông tin này chỉ có trong
  dataset. Đề xuất: E04 kiểm tra trên `project_ids`/`zone_ids` của request, cộng thêm kiểm tra
  zone của các căn sau khi đọc dataset (bước 2).

**D-62 — Chạy thử trên docker-compose.**
- Cần TL/PLAT cung cấp key LLM thật cho môi trường thử và xác nhận egress.
- Nếu D-01 chưa xong thì P5 chỉ chạy với fixture: thêm cờ `.env` `INSIGHT_ARTIFACT_SOURCE=fixtures`.

---

## 9. Spec cần cập nhật sau khi chốt

Nên cập nhật một lần sau buổi chốt:
- 6.5: `slot_map` → `slots` (Q10).
- 4.2: `cost_usd` có thể null; D-27 (`candidate_id` hay `candidate_ids`); D-31 (`kind`).
- 5.4: bổ sung các key mới trong config: `unit_examples_per_cause`, `priority_without_rank`,
  `pattern_dimensions`, `required_evidence`/`supplementary_evidence`, và các danh mục của D-21,
  D-24, D-25, D-26.
- 5.5 / BR-12: freshness theo giờ (Q11); cách xử lý biên (Q21).
- 6.2 T3: nguồn tính toán (Q14).
- 7.5: `memory.job_timeout_s`, `transient_backoff_ms`, `prompt_version`.
- 11: đổi "20 test case" thành 33; sửa TC-11 (Q8b), TC-15 (D-23), TC-18 (D-35).
- 3.1 / 9: nhận task qua message JSON thay cho PostgreSQL Queue (Q1, D-15); memory trên `ctx.memory`
  (Q4).

---

## 10. Tổng hợp theo người chốt

| Người chốt | Mục |
|---|---|
| PO | D-01, D-27, D-31, D-40, D-53, D-60 (phần chấm điểm) |
| DATA | D-01, D-02, D-03, D-04, D-05, D-06, D-23 (text định tính) |
| PLAT | D-10, D-11, D-12, D-13, D-14, D-15, D-16, D-17, D-41, D-50, D-51, D-62 |
| OPS | D-20, D-21, D-24, D-25, D-36 (duyệt prompt), D-60 |
| DA | D-21, D-25, D-60; các ngưỡng PENDING trong `config/semantic_insight.yaml` |
| TL | D-30, D-34, D-35, D-36, D-37, D-62 |
| PO + PLAT (mới) | D-00, D-76, D-77 |
| DA + DATA (mới) | D-70, D-71, D-72, D-73, D-74, D-75 |

**Thứ tự nên chốt:**
1. Trước P2: D-70, D-71 (đóng Q12), D-75, rồi D-20 → D-26 (danh mục câu chữ và luật validator), D-27, D-28, D-31.
2. Trước P3: D-30, D-34, D-35, D-37.
3. Trước P4: **D-00**, D-77, D-02, D-10, D-11, D-40, D-41, D-51.
4. Trước P5: D-01, D-60, D-62.
