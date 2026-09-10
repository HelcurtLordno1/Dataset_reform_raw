# Northwind Tester 2 — Data Cleaning Training Package

Folder này là một bài thực hành Data Cleaning cấp **advanced** để con người hoặc AI Agent học cách biến raw SQLite database thành clean database chuẩn.

- Input: `northwind_tester2.db`
- Output: `northwind_cleaned2.db`
- Problems: 15
- Ground-truth faults: 8,580
- Clean controls: 7,020
- Mục tiêu: quality 100%, false positive 0, integrity OK và 13/13 bảng khớp expected fingerprints

## Vai trò của từng file

### `northwind_tester2.db`

Raw input chứa các lỗi được tạo có chủ đích. Không sửa trực tiếp file này.

### `revert_clean_tester2.json`

Machine-readable knowledge cho AI Agent: learning guide, preprocessing plan, detection SQL, exact repair records, clean controls và validation expectations. AI nên đọc `preprocessing_plan` trước khi dùng exact labels.

### `revert_clean_tester2.ipynb`

Tài liệu dễ đọc nhất. Mỗi bài có Problem, code Detect, giải thích Solution, code preprocessing thật và 5 after-cleaning examples. Notebook tự chứa code, không import `.py`.

### `revert_clean_tester2.py`

Executable ground-truth answer. Mỗi problem có một hàm riêng; script copy raw, repair theo JSON, validation và tạo clean output bằng một lệnh.

### `northwind_cleaned2.db`

Generated artifact do notebook hoặc Python reference answer tạo ra.

## Problem/Solution set

| # | Rule | Problem | Vị trí | Solution chuẩn |
|---:|---|---|---|---|
| 1 | `T2-DUPLICATE-ORDER` | Order bị clone dưới khóa mới | `Orders.OrderID` | Dùng ID anomaly kết hợp các trường nhận dạng order, sau đó chỉ xóa những insert_row được ground truth xác nhận. |
| 2 | `T2-SWAPPED-SHIP-LOCATION` | ShipCity và ShipCountry bị tráo cột | `Orders.ShipCity, ShipCountry` | Đổi lại hai cột trong cùng một UPDATE cho từng order bị ảnh hưởng và kiểm tra cả hai clean labels. |
| 3 | `T2-SWAPPED-CUSTOMER-LOCATION` | Customers.City và Country bị tráo cột | `Customers.City, Country` | Hoán đổi City và Country nguyên tử trên đúng các customer rows đã được xác nhận. |
| 4 | `T2-ORPHAN-CUSTOMER` | Order tham chiếu CustomerID không tồn tại | `Orders.CustomerID` | Phục hồi quan hệ CustomerID bằng supervised labels sau khi phát hiện bằng anti-join hoặc foreign_key_check. |
| 5 | `T2-ORPHAN-EMPLOYEE` | Order tham chiếu EmployeeID không tồn tại | `Orders.EmployeeID` | Dùng anti-join để phát hiện và supervised label để khôi phục employee assignment chính xác. |
| 6 | `T2-ORPHAN-SHIPPER` | Order tham chiếu Shipper không tồn tại | `Orders.ShipVia` | Phát hiện bằng anti-join, sau đó phục hồi ShipVia gốc từ ground truth. |
| 7 | `T2-ORPHAN-DETAIL-PRODUCT` | Order detail tham chiếu ProductID không tồn tại | `Order Details.ProductID` | Dùng corrupted_primary_key để định vị raw row rồi phục hồi ProductID gốc từ supervised label. |
| 8 | `T2-ORPHAN-PRODUCT-SUPPLIER` | Product tham chiếu Supplier không tồn tại | `Products.SupplierID` | Phát hiện bằng anti-join và phục hồi supplier relationship từ ground truth. |
| 9 | `T2-ORPHAN-PRODUCT-CATEGORY` | Product tham chiếu Category không tồn tại | `Products.CategoryID` | Phát hiện bằng anti-join và phục hồi category relationship từ ground truth. |
| 10 | `T2-REQUIRED-BEFORE-ORDER` | RequiredDate xảy ra trước OrderDate | `Orders.RequiredDate` | Rule thời gian phát hiện lỗi nhưng không suy ra được ngày hẹn gốc; dùng supervised date để phục hồi chính xác. |
| 11 | `T2-SHIPPED-BEFORE-ORDER` | ShippedDate xảy ra trước OrderDate | `Orders.ShippedDate` | Dùng comparison giữa hai timestamp để phát hiện và supervised label để phục hồi ngày giao thực tế. |
| 12 | `T2-NEGATIVE-FREIGHT` | Freight có giá trị âm | `Orders.Freight` | Xác nhận Freight nhỏ hơn 0 rồi lấy trị tuyệt đối; phép sửa được suy ra trực tiếp từ raw. |
| 13 | `T2-NEGATIVE-QUANTITY` | Order quantity không dương | `Order Details.Quantity` | Lấy trị tuyệt đối nguyên của đúng affected rows và đối chiếu clean labels. |
| 14 | `T2-DISCOUNT-OUTSIDE-RANGE` | Discount nằm ngoài miền 0–1 | `Order Details.Discount` | Chia injected value cho 100 rồi kiểm tra kết quả nằm trong miền hợp lệ. |
| 15 | `T2-NEGATIVE-STOCK` | UnitsInStock có giá trị âm | `Products.UnitsInStock` | Phát hiện bằng điều kiện nhỏ hơn 0 và dùng supervised labels cho exact restoration. |

## Cách đọc solution

- Deterministic repair: clean value được tính từ raw rồi đối chiếu supervised label.
- Reference/oracle repair: raw giúp phát hiện nhưng không đủ khôi phục exact value; code công khai dùng JSON labels.
- Candidate rows không luôn bằng exact faults. Contextual detection thường tạo một tập rộng để review; chỉ exact verified rows mới được reference answer sửa.

## Cách chạy

Từ project root: `python3 data_raw_tester2/revert_clean_tester2.py --force`.

Từ ngay trong folder: `python3 revert_clean_tester2.py --force`.

Chỉ validation output hiện có: `python3 revert_clean_tester2.py --validate-only`.

Với notebook, mở `revert_clean_tester2.ipynb`, chọn Restart Kernel rồi Run All.

## Output thành công

- `quality_score = 100.0`
- `repaired_faults = 8580`
- `remaining_faults = 0`
- `preserved_clean_controls = 7020`
- `false_positive_controls = 0`
- `foreign_key_violations = 0`
- `integrity_check = ok`
- `matching_tables = 13/13`
- `sqlite_sequence_match = true`

## Nguyên tắc an toàn

- Chỉ đọc raw DB; luôn repair trên copy hoặc database trong RAM.
- Không dùng `northwind.db` hoặc code ngoài folder để repair.
- Không bulk-update toàn bộ candidates khi query có thể chứa clean rows.
- Chỉ publish output sau khi validation đạt.
- Đây là training ground truth. Khi blind evaluation, chỉ cung cấp raw DB và task description.
