# Northwind Tester 3 — Data Cleaning Training Package

Folder này là một bài thực hành Data Cleaning cấp **expert** để con người hoặc AI Agent học cách biến raw SQLite database thành clean database chuẩn.

- Input: `northwind_tester3.db`
- Output: `northwind_cleaned3.db`
- Problems: 18
- Ground-truth faults: 14,052
- Clean controls: 9,368
- Mục tiêu: quality 100%, false positive 0, integrity OK và 13/13 bảng khớp expected fingerprints

## Vai trò của từng file

### `northwind_tester3.db`

Raw input chứa các lỗi được tạo có chủ đích. Không sửa trực tiếp file này.

### `revert_clean_tester3.json`

Machine-readable knowledge cho AI Agent: learning guide, preprocessing plan, detection SQL, exact repair records, clean controls và validation expectations. AI nên đọc `preprocessing_plan` trước khi dùng exact labels.

### `revert_clean_tester3.ipynb`

Tài liệu dễ đọc nhất. Mỗi bài có Problem, code Detect, giải thích Solution, code preprocessing thật và 5 after-cleaning examples. Notebook tự chứa code, không import `.py`.

### `revert_clean_tester3.py`

Executable ground-truth answer. Mỗi problem có một hàm riêng; script copy raw, repair theo JSON, validation và tạo clean output bằng một lệnh.

### `northwind_cleaned3.db`

Generated artifact do notebook hoặc Python reference answer tạo ra.

## Problem/Solution set

| # | Rule | Problem | Vị trí | Solution chuẩn |
|---:|---|---|---|---|
| 1 | `T3-HIDDEN-UNICODE-CITY` | ShipCity chứa ký tự Unicode vô hình | `Orders.ShipCity` | NFKC-normalize rồi loại bỏ zero-width và BOM; clean value được suy ra từ raw. |
| 2 | `T3-HIDDEN-UNICODE-UNIT` | QuantityPerUnit chứa ký tự Unicode vô hình | `Products.QuantityPerUnit` | NFKC-normalize và xóa các ký tự vô hình đã được nhận diện. |
| 3 | `T3-DUPLICATE-CUSTOMER` | Customer bị clone dưới ID khác | `Customers.CustomerID` | Kết hợp ID anomaly với business identity rồi chỉ xóa exact injected rows. |
| 4 | `T3-TYPO-SHIP-NAME` | ShipName có typo rất nhỏ | `Orders.ShipName` | Fuzzy matching tạo candidate; supervised canonical name quyết định đáp án cuối cùng. |
| 5 | `T3-COHERENT-WRONG-SHIPPING-BLOCK` | Shipping address block hợp lệ nhưng thuộc sai entity | `Orders.ShipAddress, ShipCity, ShipPostalCode, ShipCountry` | Đối chiếu ownership theo customer và ship identity, sau đó phục hồi toàn bộ affected fields bằng labels. |
| 6 | `T3-MIXED-ORPHAN-CUSTOMER` | CustomerID GHOST tạo orphan relation | `Orders.CustomerID` | Anti-join phát hiện GHOST; ground truth phục hồi customer ownership chính xác. |
| 7 | `T3-VALID-WRONG-SUPPLIER` | Product trỏ tới supplier tồn tại nhưng sai | `Products.SupplierID` | Dùng semantic/reference knowledge để phát hiện và supervised labels để phục hồi supplier đúng. |
| 8 | `T3-VALID-WRONG-CATEGORY` | Product trỏ tới category tồn tại nhưng sai | `Products.CategoryID` | Kiểm tra product-category semantics và phục hồi exact category từ labels. |
| 9 | `T3-PLAUSIBLE-CUSTOMER-REASSIGNMENT` | Order bị gán sang customer hợp lệ khác | `Orders.CustomerID` | Dùng bằng chứng chéo ShipName, address và geography; exact ownership lấy từ supervised labels. |
| 10 | `T3-PLAUSIBLE-EMPLOYEE-REASSIGNMENT` | Employee assignment bị xoay vòng | `Orders.EmployeeID` | Đây là contextual problem; exact historical assignment phải dùng oracle labels. |
| 11 | `T3-PLAUSIBLE-SHIPPER-REASSIGNMENT` | Shipper assignment bị xoay vòng | `Orders.ShipVia` | Phân tích assignment pattern để phát hiện; phục hồi chính xác bằng supervised labels. |
| 12 | `T3-ONE-YEAR-DATE-SHIFT` | OrderDate bị dịch đúng 365 ngày | `Orders.OrderDate` | Trừ chính xác 365 ngày và bảo toàn độ chính xác date/time của clean label. |
| 13 | `T3-FREIGHT-SCALE-DRIFT` | Freight bị sai thang đo 1/100 | `Orders.Freight` | Phát hiện scale cluster theo context, nhân affected values với 100 và làm tròn về precision chuẩn. |
| 14 | `T3-CONTEXTUAL-DETAIL-PRICE` | Order-line price hợp lệ nhưng thuộc dòng khác | `Order Details.UnitPrice` | Dùng product/time context để tạo candidate; exact historical line price cần supervised labels. |
| 15 | `T3-CONTEXTUAL-DETAIL-QUANTITY` | Quantity hợp lệ nhưng bị hoán vị giữa các dòng | `Order Details.Quantity` | Univariate profiling chỉ cho dấu hiệu; exact row quantities phải được phục hồi từ labels. |
| 16 | `T3-DISCOUNT-LABEL-DRIFT` | Discount level hợp lệ nhưng sai context | `Order Details.Discount` | Kiểm tra discount theo order context và dùng supervised labels cho exact repair. |
| 17 | `T3-CUSTOMER-CONTACT-MISALIGNMENT` | ContactName và ContactTitle thuộc sai customer | `Customers.ContactName, ContactTitle` | Validate identity fields như một nhóm và phục hồi cả pair bằng ground truth. |
| 18 | `T3-PLAUSIBLE-PRODUCT-PRICE` | Product price hợp lệ nhưng thuộc sản phẩm khác | `Products.UnitPrice` | Đối chiếu product identity và lịch sử; exact UnitPrice được phục hồi từ labels. |

## Cách đọc solution

- Deterministic repair: clean value được tính từ raw rồi đối chiếu supervised label.
- Reference/oracle repair: raw giúp phát hiện nhưng không đủ khôi phục exact value; code công khai dùng JSON labels.
- Candidate rows không luôn bằng exact faults. Contextual detection thường tạo một tập rộng để review; chỉ exact verified rows mới được reference answer sửa.

## Cách chạy

Từ project root: `python3 data_raw_tester3/revert_clean_tester3.py --force`.

Từ ngay trong folder: `python3 revert_clean_tester3.py --force`.

Chỉ validation output hiện có: `python3 revert_clean_tester3.py --validate-only`.

Với notebook, mở `revert_clean_tester3.ipynb`, chọn Restart Kernel rồi Run All.

## Output thành công

- `quality_score = 100.0`
- `repaired_faults = 14052`
- `remaining_faults = 0`
- `preserved_clean_controls = 9368`
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
