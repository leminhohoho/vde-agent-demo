<!-- prompt_version: insight-prompt-1.1.0 -->
# Sửa câu trả lời

Câu trả lời trước của bạn (khối `<previous_output>`) vi phạm các luật ở trên. Danh sách lỗi nằm trong khối `<violations>`: mỗi lỗi có số thứ tự item, mã luật và mã lỗi.

- `E10`: có số hoặc từ chỉ lượng ngoài slot. Thay bằng `{{slot}}` hoặc bỏ đi.
- `E11`: tham chiếu sai (candidate không được cấp, slot không có, slot trong câu không khớp `slots`, gộp khác loại, dùng lại candidate).
- `E12`: ngôn ngữ nhân quả, câu mệnh lệnh hoặc có link.
- `LANGUAGE_MISMATCH`: không phải tiếng Việt, hoặc nêu nguyên nhân mà không qua `{{cause_label}}`.
- `STRONG_CLAIM_NOT_SIGNIFICANT`, `PEER_HIDDEN`: so sánh mạnh khi không có ý nghĩa thống kê, hoặc nhắc peer khi quá ít peer.
- `IMPERATIVE_RECOMMENDATION`, `RECOMMENDATION_NOT_ALLOWED`: khuyến nghị sai dạng hoặc không được phép.
- `SCOPE_VIOLATION`: viết mã căn trong câu. `SENTENCE_TOO_LONG`: quá 40 từ.
- `E09`: câu trả lời không đúng schema hoặc bị cắt; viết lại đầy đủ và ngắn hơn.

Trả về **toàn bộ** câu trả lời đã sửa theo đúng schema: giữ nguyên các item đúng, chỉ sửa các item có lỗi. Chỉ dùng các candidate trong khối `<candidates>`.
