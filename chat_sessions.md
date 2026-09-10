• Đã hoàn thiện lại toàn bộ project theo hướng mentor-first và source-code-first.

  ## Những thay đổi chính

  - Mỗi revert_clean_testerN.py giờ là pipeline độc lập, chứa toàn bộ:
      - SQLite profiling.
      - Candidate detection SQL.
      - Semantic transforms.
      - Oracle restoration có ghi rõ lý do.
      - UPDATE/DELETE transaction.
      - Pipeline order.
      - Fingerprint, assertion và clean validation.
      - CLI tạo output an toàn.

  - Tổng cộng có 45 hàm preprocessing trực tiếp:
      - Tester 1: 12 hàm.
      - Tester 2: 15 hàm.
      - Tester 3: 18 hàm.

  Ví dụ source tester 1 nằm trực tiếp tại data_raw_tester1/revert_clean_tester1.py:117, không còn phải mở shared runtime để tìm logic.

  ## Notebook đã trở thành tài liệu trung tâm

  Ba notebook hiện có:

  - Profiling chạy trực tiếp trên DB raw.
  - Problem/Solution matrix.
  - Một section riêng cho từng problem.
  - Candidate SQL.
  - Ground-truth examples.
  - Giải thích deterministic/oracle/hybrid.
  - Source infrastructure được nhúng nguyên văn.
  - Source semantic transforms được nhúng nguyên văn.
  - Source function của từng problem hiển thị ngay tại chỗ.
  - Cell chạy function tương ứng.
  - Làm việc trên SQLite trong RAM.
  - Chỉ publish clean DB sau khi validation đạt 100%.

  Notebook:

  - Tester 1 notebook (data_raw_tester1/revert_clean_tester1.ipynb) — 50 cells.
  - Tester 2 notebook (data_raw_tester2/revert_clean_tester2.ipynb) — 59 cells.
  - Tester 3 notebook (data_raw_tester3/revert_clean_tester3.ipynb) — 68 cells.

  Toàn bộ code cells đã được chạy thử thành công.

  ## Clean DB có thực sự do code sinh ra không?

  Có. Cả ba clean DB đã được tạo lại hai lần:

  1. Bằng chính revert_clean_testerN.py.
  2. Bằng toàn bộ code cells trong notebook.

  Kết quả:

   Tester    Faults repaired    Clean controls    Tables matched    Kết quả
  ━━━━━━━━  ━━━━━━━━━━━━━━━━━  ━━━━━━━━━━━━━━━━  ━━━━━━━━━━━━━━━━  ━━━━━━━━━
   1             5.170/5.170       5.170/5.170             13/13       100%
  ────────  ─────────────────  ────────────────  ────────────────  ─────────
   2             8.580/8.580       7.020/7.020             13/13       100%
  ────────  ─────────────────  ────────────────  ────────────────  ─────────
   3           14.052/14.052       9.368/9.368             13/13       100%

  Cả ba đều:

  - remaining_faults = 0
  - false_positive_controls = 0
  - Foreign-key violations = 0
  - Integrity = ok
  - Schema và SQLite sequence khớp.
  - Logical match với northwind.db.

  ## Phát hiện quan trọng khi chạy code thật

  - Rule mixed-date của tester 1 có 661 giá trị bị mất precision giây. Nó đã được sửa thành hybrid_transform_and_oracle, không còn tuyên bố sai rằng có thể suy ra hoàn toàn từ raw.
  - Date shift của tester 3 thực tế là +365 days, không đơn giản là thay đổi trường năm. Source đã đảo chính xác 365 ngày.
  - Các lỗi mất thông tin, valid-but-wrong và contextual permutation đều công khai dùng oracle; không giả vờ đó là preprocessing có thể tự suy luận.

  ## Dọn project

  Đã chuyển vào Thùng rác:


  - benchmark_tool.py: build lại raw và chấm candidate.
  - CHECKSUMS.sha256: kiểm tra release.

  Mỗi tester hiện có đúng 6 file: 2 DB và 4 file hỗ trợ thiết yếu, không có CSV, cache, journal hay file tạm.

  README tổng và README từng tester cũng đã viết lại:

  - README tổng (README.md)
  - Tester 1 README (data_raw_tester1/README_tester1.md)
  - Tester 2 README (data_raw_tester2/README_tester2.md)
  - Tester 3 README (data_raw_tester3/README_tester3.md)

  Toàn bộ 25 checksum cuối cùng đều đạt. northwind.db, Agent_job.md, wireframe và ba raw DB giữ nguyên hash.