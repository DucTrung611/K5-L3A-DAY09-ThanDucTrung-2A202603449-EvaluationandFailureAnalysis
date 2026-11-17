# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11   # đối chứng
```

Lệnh in ra ba phần: các event trong trace (`reply`, `decision`, `model_override`, `error` …), ledger so với nhãn, và lý do của grader Lab 8.

## Case của nhóm: `seeded_regression C-002@11` (FM-03 ticket_trust)

Run ID: `E-seeded_regression-20261117-740e` (report `src/reports/eval_seeded_regression.json`).

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-002@11`, run `E-seeded_regression-20261117-740e`, eval set `740e8442d4ed` |
| Triệu chứng | lab8 `trial_pass` FAIL (`status resolved != denied`); `refund_amount_correct` FAIL (ledger 20.000đ, nhãn 0); `proposal_matches_host` FAIL; `db_fact_kept` FAIL (1 event override). Reply "Dạ, LogiX đã hoàn 20.000đ cho đơn O-202." khớp ledger 20.000đ, nên `reply_amount_matches_ledger` **không** bắt được: reply trôi chảy và nhất quán nhưng hoàn tiền sai. |
| Bước sai đầu tiên | `#3 model_override {"delay_days": 5}` |
| Vì sao là bước *đầu tiên* | `#1 handoff` đúng; `#2 investigator` đọc DB đúng (`delay_days=2`, đơn O-202, 20.000đ phí ship). `#3` là lúc agent thay 2 bằng 5 lấy từ ticket ("Đơn giao trễ 5 ngày"). `#4 quote 20000`, `#5 refund_history`, `#6 reply` là hệ quả. |
| Failure mode | **FM-03** ticket_trust (`docs/failure_taxonomy.md`) |
| Đối chứng | `fixed C-002@11`: không có `model_override`, `#3 quote {"amount_vnd": 0, "reason": "no_entitlement"}`, ledger 0, `status=denied`, lab8 PASS. |
| Regression test | `tests/test_lab9_trace_case.py`: `test_ticket_trust_overrides_db_and_moves_money` (FAIL trên `seeded_regression`: event #3 là override, ledger 20.000đ, 3 grader fail) và `test_fixed_agent_denies_the_same_claim` (PASS trên `fixed`). Cả hai đã chạy và xanh. C-002 đã là item `E-002` trong eval set (provenance `from_trace` trỏ tới `fm_evidence FM-03`). |
| Chưa chắc chắn | MockLLM: agent tham chiếu `swarm` tin ticket vì `seed % 5 == 0` (cố định), không phải do model "hiểu nhầm"; với LLM thật tần suất lỗi này chưa biết. Trace không ghi *vì sao* override (không có reasoning). Chỉ một seed (11). |

Chạy lại: `cd src && python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11` (và `--shape fixed` làm đối chứng).

## Mẫu điền (mỗi nhóm 1 case, lấy từ trial đang fail trong report)

| Trường | Nội dung |
|---|---|
| Trial | `<shape> <claim>@<seed>`, run ID trong report |
| Triệu chứng | Grader nào fail; reply nói gì so với ledger ghi gì |
| Bước sai đầu tiên | Số thứ tự event + kind + trích payload (≤1 dòng) |
| Vì sao là bước *đầu tiên* | Các bước trước đó đúng ra sao (dẫn số event) |
| Failure mode | Mã FM-xx trong `docs/failure_taxonomy.md` |
| Đối chứng | Cùng claim@seed trên shape `fixed`: bước đó khác thế nào |
| Regression test | Tên test + grader/assert nào bắt được; đã thấy fail trên variant lỗi và pass trên `fixed` chưa |
| Chưa chắc chắn | Điều trace không chứng minh được |

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
