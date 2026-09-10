# Northwind Data-Cleaning Agent Dataset

Bộ dữ liệu benchmark có giám sát để huấn luyện và đánh giá AI Agent ở nhiệm vụ **phát hiện vấn đề dữ liệu, đề xuất preprocessing và biến một SQLite database raw thành clean database**.

Project cung cấp ba problem set Northwind tăng dần độ khó. Mỗi tester là một gói độc lập, có raw input, lời giải Python chạy được, notebook Problem/Solution dành cho người đọc, ground truth JSON cho máy và clean output chuẩn để kiểm chứng.

## Mục tiêu của project

Agent được đánh giá ở ba năng lực riêng biệt:

1. **Detection** — nhận diện đúng rule, bảng, cột và candidate records có vấn đề.
2. **Recommendation** — chọn đúng loại xử lý: normalize, transform, delete duplicate, sửa quan hệ hoặc phục hồi từ supervised reference.
3. **Repair** — tạo database sạch khớp logic với baseline mà không sửa nhầm dữ liệu vốn đúng.

Clean trong project này không có nghĩa là xóa mọi `NULL`. Baseline Northwind chứa các missing value hợp lệ, ví dụ fax không có, nhân viên cấp cao nhất không có `ReportsTo`, hoặc đơn hàng chưa giao. Kết quả đúng là kết quả khớp trạng thái logic của `northwind.db`.

## Cấu trúc repository

```text
.
├── README.md
├── iDecide_Wireframe.html        # tham chiếu UI/behavior của Agent
├── northwind.db                  # clean logical baseline, chỉ đọc
├── protected_assets.json         # SHA-256 của các nguồn bất biến
├── CHECKSUMS.sha256              # checksum của release hiện tại
├── benchmark_scenarios.json      # seed và cấu hình tái tạo ba tester
├── benchmark_tool.py             # build, profile, validate và chấm điểm
├── data_raw_tester1/
├── data_raw_tester2/
└── data_raw_tester3/
```

Mỗi `data_raw_testerN/` chỉ chứa sáu artifact:

| File | Vai trò |
|---|---|
| `northwind_testerN.db` | Raw/messed-up SQLite database; input cần làm sạch và không được sửa trực tiếp. |
| `northwind_cleanedN.db` | Generated reference output sau preprocessing. |
| `revert_clean_testerN.json` | Machine-readable knowledge: profile, detection SQL, preprocessing plan, exact repair records, clean controls và validation expectations. |
| `revert_clean_testerN.py` | Ground-truth executable độc lập; copy raw, chạy các hàm preprocessing theo thứ tự, validate và publish clean output. |
| `revert_clean_testerN.ipynb` | Notebook tự chứa theo cấu trúc Problem → Detect → Solution → before/after examples → validation. |
| `README_testerN.md` | Hướng dẫn riêng, danh sách rule, cách chạy và tiêu chí pass của tester. |

Notebook không import file `.py`; người học có thể mở notebook và chạy tuần tự để thấy vấn đề, code phát hiện, code sửa cùng các ví dụ trước/sau. File `.py` là reference answer phù hợp để Agent đọc, chạy và tái lập toàn bộ pipeline bằng một lệnh.

## Ba cấp độ benchmark

| Tester | Độ khó | Target raw quality | Problem sets | Fault assertions | Clean controls | Trọng tâm |
|---|---|---:|---:|---:|---:|---|
| 1 | Foundational | 50% | 12 | 5,170 | 5,170 | mixed type/date, placeholder, whitespace, text normalization, outlier, duplicate |
| 2 | Advanced | 45% | 15 | 8,580 | 7,020 | orphan FK, column swap, invalid numeric domain, inconsistent business dates |
| 3 | Expert | 40% | 18 | 14,052 | 9,368 | contextual drift, valid-but-wrong relations, hidden Unicode, scale drift, coherent wrong blocks |

Target quality là tỷ lệ assertion có chủ đích, không phải tỷ lệ mọi cell vật lý trong SQLite:

```text
quality = 100 × passed(fault assertions + clean controls) / all assertions
```

Clean controls phạt các pipeline sửa nhầm dữ liệu đúng. Vì vậy, một Agent xóa hàng loạt candidate rows sẽ không thể đạt điểm cao.

## Hai loại tri thức trong solution

- **Deterministic repair:** clean value có thể tính trực tiếp từ raw, ví dụ parse số, chuẩn hóa Unicode, lấy trị tuyệt đối hoặc đảo lại hai cột.
- **Reference/oracle repair:** lỗi có thể được phát hiện nhưng raw đã mất thông tin, hoặc giá trị sai vẫn hợp lệ về kiểu/FK. Exact restoration phải dùng `clean_value` đã công khai trong JSON.

Candidate SQL phục vụ phát hiện và có thể cố ý trả về tập rộng để review. Chỉ `repair_records` mới là exact supervised labels. Project không giả vờ rằng mọi lỗi contextual đều có thể suy luận chắc chắn chỉ từ raw data.

## Quick start

Yêu cầu duy nhất là Python 3.10+; code runtime chỉ dùng Python standard library.

Chạy reference solution của từng tester từ project root:

```bash
python3 data_raw_tester1/revert_clean_tester1.py --force
python3 data_raw_tester2/revert_clean_tester2.py --force
python3 data_raw_tester3/revert_clean_tester3.py --force
```

Validate clean output hiện có:

```bash
python3 data_raw_tester1/revert_clean_tester1.py --validate-only
python3 data_raw_tester2/revert_clean_tester2.py --validate-only
python3 data_raw_tester3/revert_clean_tester3.py --validate-only
```

Hoặc mở `revert_clean_testerN.ipynb`, chọn **Restart Kernel** rồi **Run All**. Notebook tạo lại `northwind_cleanedN.db` ngay trong folder tương ứng.

Các lệnh project-level:

```bash
# Kiểm tra protected assets và logical output của cả ba tester
python3 benchmark_tool.py verify-project

# Profile một SQLite database
python3 benchmark_tool.py profile --db data_raw_tester1/northwind_tester1.db

# Chấm một clean database do Agent tạo
python3 benchmark_tool.py validate \
  --tester 1 \
  --candidate path/to/agent_cleaned.db

# Chấm recommendation JSON của Agent
python3 benchmark_tool.py score-recommendations \
  --tester 1 \
  --response path/to/agent_recommendations.json

# Tái tạo raw tester và reference output bằng seed cố định
python3 benchmark_tool.py build --tester 1 --force
python3 benchmark_tool.py build-all --force
```

Xem toàn bộ option bằng `python3 benchmark_tool.py --help` hoặc `python3 data_raw_testerN/revert_clean_testerN.py --help`.

## Kết quả reference đã xác thực

| Tester | Quality | Repaired faults | Remaining faults | Preserved controls | False positives | Logical tables |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 100.0 | 5,170 | 0 | 5,170 | 0 | 13/13 |
| 2 | 100.0 | 8,580 | 0 | 7,020 | 0 | 13/13 |
| 3 | 100.0 | 14,052 | 0 | 9,368 | 0 | 13/13 |

Cả ba reference output phải đồng thời đạt:

- `PRAGMA integrity_check = ok`;
- `PRAGMA foreign_key_check` không có violation;
- schema và `sqlite_sequence` khớp baseline;
- logical fingerprint khớp đủ 13/13 bảng;
- không còn fault assertion và không làm hỏng clean control.

Không so sánh hai SQLite database chỉ bằng file hash: metadata hoặc page layout có thể khác dù schema và dữ liệu hoàn toàn giống nhau. `benchmark_tool.py` thực hiện so sánh logic theo bảng, khóa và giá trị.

## Cách dùng cho training và evaluation

Trong **supervised training**, nên đọc theo thứ tự:

1. `README_testerN.md` — hiểu mục tiêu và toàn bộ problem set.
2. `revert_clean_testerN.ipynb` — học evidence, detection và solution qua từng bài.
3. `revert_clean_testerN.py` — xem reference pipeline chạy độc lập.
4. `revert_clean_testerN.json` — đọc `learning_guide`, `preprocessing_plan`, sau đó chỉ lấy `repair_records` của rule đang học.
5. `northwind_testerN.db` — tự profile và thực hành tạo clean output.

Trong **blind evaluation**, không cung cấp Python reference, notebook solution, clean output hoặc exact `repair_records` cho Agent. Chỉ cung cấp raw database và task description, sau đó dùng benchmark tool/ground truth ở phía evaluator để chấm.

## An toàn và khả năng tái lập

- `northwind.db` và `iDecide_Wireframe.html` là protected sources, chỉ đọc và được kiểm tra SHA-256.
- Mỗi build dùng seed cố định trong `benchmark_scenarios.json`.
- Raw database không được sửa in-place; output được tạo qua working copy rồi atomic replace.
- Mỗi pipeline chạy rule theo `execution_order` và chỉ publish output sau khi validation đạt.
- `--force` chỉ thay generated output được chỉ định.
- BLOB, view và các bảng baseline rỗng không bị corrupt trực tiếp.
- Sau khi thay đổi artifact có chủ đích, `CHECKSUMS.sha256` phải được phát hành lại.

Kiểm tra tính toàn vẹn của release:

```bash
sha256sum -c CHECKSUMS.sha256
```

Chi tiết của từng problem, detection query, solution mode và output kỳ vọng nằm trong README của từng tester.
