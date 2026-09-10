# Northwind Tester 1 — Data Cleaning Training Package

Folder này là một bài thực hành hoàn chỉnh để con người hoặc AI Agent học cách biến một SQLite database có lỗi thành database sạch.

- Input: `northwind_tester1.db`
- Output: `northwind_cleaned1.db`
- Mức độ: foundational
- Số ground-truth faults: 5.170
- Số clean controls phải được giữ nguyên: 5.170
- Mục tiêu cuối: 100% ground truth, không sửa nhầm dữ liệu sạch, integrity OK và 13/13 bảng khớp fingerprint chuẩn

## Vai trò của từng file

### `northwind_tester1.db`

Database raw dùng làm đầu vào. File này chứa 12 tình huống data-quality đã được tạo có chủ đích. Không sửa trực tiếp file này.

### `revert_clean_tester1.json`

Knowledge và ground truth dành cho AI Agent:

- `learning_guide`: cách sử dụng package.
- `preprocessing_plan`: mô tả từng problem, vị trí, cách phát hiện và solution chuẩn.
- `repair_records`: 5.170 exact labels để phục hồi đúng các dòng bị làm lỗi.
- `clean_controls`: dữ liệu sạch dùng để phát hiện false positive.
- `validation_expectations`: row count và fingerprint của kết quả chuẩn.

AI nên đọc `preprocessing_plan` trước, sau đó mới dùng `repair_records` khi cần exact supervised repair.

### `revert_clean_tester1.ipynb`

Problem/solution workbook thân thiện nhất cho người đọc. Notebook đi lần lượt từ Problem 1 đến Problem 12. Mỗi bài có:

1. Mô tả vấn đề.
2. Code phát hiện và hiển thị ví dụ raw.
3. Giải thích solution.
4. Code preprocessing thật.
5. Kết quả kiểm tra trước và sau.

Toàn bộ code học tập nằm trong code cell. Markdown chỉ dùng để giải thích; notebook không import hoặc che giấu lời giải trong file Python.

### `revert_clean_tester1.py`

Executable reference answer. File này đọc raw DB và JSON trong cùng folder, chạy 12 solution theo đúng thứ tự, tạo clean DB rồi tự validation. Mỗi problem có một function riêng để người đọc có thể đối chiếu trực tiếp với notebook và JSON.

### `northwind_cleaned1.db`

Generated artifact. File này được sinh bởi notebook hoặc Python reference answer; không phải input và có thể tạo lại.

## Mười hai problem và solution chuẩn

| # | Problem | Vị trí | Cách phát hiện | Solution chuẩn |
|---:|---|---|---|---|
| 1 | Freight là text có tiền tố tiền tệ | `Orders.Freight` | Kiểm tra SQLite storage type | Bỏ tiền tố và chuyển về số |
| 2 | Product price là text có tiền tố tiền tệ | `Products.UnitPrice` | Kiểm tra SQLite storage type | Bỏ tiền tố và chuyển về số |
| 3 | Discount trộn phần trăm text với số thập phân | `Order Details.Discount` | Tìm text kết thúc bằng `%` | Bỏ `%` và chia 100 |
| 4 | Order date có nhiều format | `Orders.OrderDate` | Tìm ngày không theo ISO | Parse đúng format; dùng label cho phần giây đã mất |
| 5 | Phone bị thay bằng placeholder | `Customers.Phone` | Tìm `N/A`, `unknown`, `-` và chuỗi rỗng | Phát hiện từ raw, phục hồi chính xác bằng supervised label |
| 6 | Ship name có whitespace thừa | `Orders.ShipName` | Tìm đầu/cuối, tab hoặc nhiều space | Trim và gom mỗi chuỗi whitespace thành một space |
| 7 | Product name có whitespace thừa | `Products.ProductName` | Tìm đầu/cuối, tab hoặc nhiều space | Trim và gom mỗi chuỗi whitespace thành một space |
| 8 | Ship city sai hoa/thường | `Orders.ShipCity` | Tìm cách viết upper/lower bất thường | Khớp không phân biệt hoa/thường và trả về canonical label |
| 9 | Country dùng alias không thống nhất | `Customers.Country` | So với vocabulary alias | Map alias về country label chuẩn |
| 10 | Ship postal code bị thay bằng NULL | `Orders.ShipPostalCode` | Tìm NULL rồi phân biệt NULL hợp lệ và NULL được chèn | Phục hồi exact supervised label, giữ nguyên NULL hợp lệ |
| 11 | Quantity bị nhân 100 | `Order Details.Quantity` | Dùng threshold để tạo candidate và đối chiếu ground truth | Chia đúng injected values cho 100 |
| 12 | Customer bị clone dưới ID mới | `Customers` | Kết hợp ID pattern với business identity | Chỉ xóa đúng injected duplicate rows |

## Hai kiểu solution cần phân biệt

### Có thể suy ra từ raw

Các lỗi như currency text, percentage, whitespace và quantity nhân 100 vẫn giữ đủ thông tin. Code tự tính clean value từ raw rồi đối chiếu với JSON để chứng minh phép biến đổi đúng.

### Cần supervised ground truth

Placeholder, NULL và timestamp bị mất giây đã phá hủy thông tin gốc. Raw data chỉ giúp phát hiện vấn đề, không thể cho biết duy nhất giá trị trước khi bị xóa. Trong các trường hợp này, reference answer công khai dùng `clean_value` từ JSON. Đây là supervised repair, không phải suy luận giả.

## Cách chạy

Từ bất kỳ working directory nào, chạy `python3 data_raw_tester1/revert_clean_tester1.py --force`.

Nếu terminal đang đứng ngay trong folder này, chạy `python3 revert_clean_tester1.py --force`.

Để chỉ kiểm tra output hiện có, chạy `python3 revert_clean_tester1.py --validate-only`.

Với notebook, mở `revert_clean_tester1.ipynb`, chọn Restart Kernel rồi Run All. Notebook xử lý một bản sao trong RAM và chỉ ghi `northwind_cleaned1.db` sau khi validation thành công.

## Output thành công phải có

- `quality_score = 100.0`
- `repaired_faults = 5170`
- `remaining_faults = 0`
- `preserved_clean_controls = 5170`
- `false_positive_controls = 0`
- `foreign_key_violations = 0`
- `integrity_check = ok`
- `matching_tables = 13/13`
- `sqlite_sequence_match = true`

## Nguyên tắc an toàn

- Không mở raw DB ở chế độ ghi.
- Không dùng `northwind.db` hoặc code ngoài folder để repair.
- Không sửa tất cả candidate một cách mù quáng vì candidate có thể gồm giá trị hợp lệ.
- Chỉ publish output sau khi toàn bộ validation đạt.
- Ground truth là tài liệu training. Nếu cần blind evaluation, chỉ cung cấp raw DB và đề bài, không cung cấp JSON, notebook hoặc reference Python.
