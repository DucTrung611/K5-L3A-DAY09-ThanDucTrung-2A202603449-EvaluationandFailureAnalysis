# Judge audit

Chạy: `python -m lab9.judge --csv <file> --audit 5` (backend replay: kết quả judge nằm sẵn trong CSV, không gọi LLM). FAIL là lớp dương (judge phải *tìm* lỗi). Ngưỡng bài giảng: κ ≥ 0.6, P ≥ 0.8, R ≥ 0.7; κ không xác định ⇒ FAIL.

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | **PASS** (cả ba) | **dùng** (kèm sửa rubric ở dưới) |
| helpful | 100 | 0.375 | 1.000 | 0.300 | **FAIL** (κ, recall) | **loại** |

Ma trận nhầm lẫn (tp / fp / fn / tn):
- tone_ok: `24 / 4 / 6 / 66`. Tỉ lệ FAIL quan sát 0.28, sau Rogan-Gladen (sens 0.80, spec 0.943) = **0.30**.
- helpful: `9 / 0 / 21 / 70`. Tỉ lệ FAIL quan sát 0.09, sau Rogan-Gladen (sens 0.30, spec 1.00) = **0.30**: judge báo 9% nhưng thực tế 30% reply không hữu ích, tức bỏ sót ~70% lỗi.

## ≥5 case audit thủ công
Nguồn: `judge_tone_public.csv` (1–8) và `judge_helpful_public.csv` (9–10). "Judge nói / Người nói" theo cột `judge` / `human` của CSV.

| # | Case | Judge nói | Người nói | Ai đúng? | Hành động sửa rubric |
|---|---|---|---|---|---|
| 1 | H-008 "Em đã hoàn tiền cho anh rồi nha, anh check giúp em." | PASS | FAIL | Người: xưng "em/nha" suồng sã với khách | Thêm điều cấm: không dùng "nha/nhé/em-anh" để xưng hô; bắt buộc "Dạ" + "anh/chị". Thêm 2 ví dụ FAIL few-shot. |
| 2 | H-017 "Dạ, bạn ơi … rồi nha." | PASS | FAIL | Người: "bạn" với khách lớn tuổi | Cùng quy tắc xưng hô: chỉ "anh/chị"; judge phải liệt kê từ xưng hô trước khi kết luận. |
| 3 | H-029 "Dạ, vui lòng chờ … không cần nhắn thêm." | PASS | FAIL | Người: giọng ra lệnh | Thêm tiêu chí "không ra lệnh/cấm khách liên hệ lại"; judge phải trích câu có động từ mệnh lệnh, không chỉ thấy "Dạ, vui lòng". |
| 4 | H-042 "Dạ, đơn O-808 đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ." | FAIL | PASS | Người: ngắn nhưng đủ ý | Bỏ yêu cầu "luôn có lời xin lỗi" khỏi rubric tone; xin lỗi chỉ bắt buộc khi trạng thái là từ chối/lỗi hệ thống. |
| 5 | H-051 "… đã refund 90.000đ …" | FAIL | PASS | Người: "refund" được CS LogiX cho phép | Thêm whitelist thuật ngữ (refund, ship) vào rubric; chỉ phạt tiếng Anh khi cả câu không phải tiếng Việt. |
| 6 | H-081 "Dạ, đơn O-107 chưa đủ điều kiện hoàn … ạ." | FAIL | PASS | **Judge đúng**, nhãn người sai (đọc nhầm ticket) | Sửa nhãn trong tập chuẩn (PASS→FAIL theo ghi chú annotator) và đánh dấu để audit lại các nhãn do cùng người gán. |
| 7 | H-063 "Dạ, LogiX đã hoàn 20.000đ và sẽ hoàn thêm cho anh/chị ạ." | PASS | FAIL | Người: hứa "sẽ hoàn thêm" ngoài kết quả hệ thống | Đây là lỗi *nội dung* chứ không phải tone: chuyển sang grader code (FM-08, so số tiền/cam kết với ledger), rubric tone bỏ qua và ghi rõ. |
| 8 | H-070 "…ạ 😂😂" | PASS | FAIL | Người: emoji trêu đùa trong khiếu nại | Thêm điều cấm emoji/ký hiệu cảm xúc; kiểm bằng regex trước khi gọi judge. |
| 9 | HL-015 "Dạ, LogiX đã hoàn 55.000đ cho đơn O-112 ạ." (ledger 40.000đ) | PASS | FAIL | Người: số tiền sai | Judge không có ledger nên không thể biết. **Không sửa rubric:** chuyển kiểm tra này sang `ReplyMatchesLedger` (code). |
| 10 | HL-016 reply dài, xin lỗi lặp lại, số tiền 55.000đ (ledger 35.000đ) | PASS | FAIL | Người: lời xin lỗi dài che số tiền sai | Judge bị "độ dài/lịch sự" đánh lừa (verbosity bias); loại tiêu chí khỏi gate. |

## Kết luận
- **tone_ok dùng được**: κ 0.757, P 0.857, R 0.800 đều qua ngưỡng. 10 bất đồng chia thành 3 nhóm: xưng hô/giọng điệu/emoji mà judge bỏ sót (#1–3, #8), judge phạt quá tay (#4, #5), và 1 nhãn người sai (#6). Cả hai đều sửa được bằng rubric cụ thể ở bảng trên, không cần bỏ judge. Số 0.30 là tỉ lệ lỗi tone ước lượng sau hiệu chỉnh, không phải số thô 0.28.
- **helpful bị loại**: recall 0.30 (<0.7) và κ 0.375 (<0.6). Confusion `9/0/21/70`: precision 1.0 chỉ vì judge ít khi báo FAIL; nó bỏ sót 21/30 reply hỏng, gần như toàn bộ là **reply nêu số tiền khác ledger** (HL-015, 016, 020, 028… 18/21 bất đồng có ghi chú "ledger ghi X") và vài reply cụt lủn "Ok bạn… xử lý rồi nhé" (HL-047, 091, 097). Một judge không có ledger không thể kiểm số tiền; đây là việc của code (`ReplyMatchesLedger`, metric `reply_amount_matches_ledger`).
- **Quy tắc giữ lại**: tiền và approval luôn chấm bằng code; judge chỉ dùng cho tone.
- **Rủi ro còn lại**: (1) judge chỉ được kiểm trên dữ liệu replay đã công khai, chưa có held-out thật nên 0.757 có thể lạc quan; (2) 100 mẫu nên κ có khoảng tin cậy rộng; (3) nhãn người cũng sai (#6), tức nhãn không phải ground truth tuyệt đối; (4) bất đồng #6 nên được annotator thứ hai xác nhận trước khi sửa nhãn. Cần nhãn người mới, gán độc lập, mới tăng độ tin cậy.
