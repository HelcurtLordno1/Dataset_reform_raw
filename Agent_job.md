• Tôi hiểu yêu cầu của bạn như sau:

  - Giữ nguyên tuyệt đối AGENT.md, iDecide_Wireframe.html và northwind.db.
  - Biến hai bản sao northwind_tester1.db và northwind_tester2.db thành hai bộ dữ liệu “raw/messed-up” có chủ đích.
  - Mỗi lỗi phải có ground truth để biết Agent có:
      - phát hiện đúng vấn đề;
      - đề xuất đúng thao tác;
      - làm sạch đúng dữ liệu;
      - tránh sửa nhầm dữ liệu vốn hợp lệ.

  - Sau preprocessing, cần kiểm tra được kết quả có khôi phục đúng về trạng thái logic của northwind.db hay không.
  - Hiện tại bạn chỉ yêu cầu thiết kế phương pháp, chưa viết hoặc chạy code.

  ## Phương pháp tôi sẽ thực hiện

  ### 1. Khóa baseline sạch

  northwind.db được coi là nguồn sự thật và chỉ đọc, không chỉnh sửa.

  Trước khi tạo dữ liệu lỗi, tôi sẽ khảo sát:

  - Các bảng, cột và kiểu dữ liệu.
  - Primary key, foreign key, unique constraint.
  - Số dòng và checksum logic của từng bảng.
  - Phân bố giá trị, min/max, null, distinct.
  - Các quy tắc nghiệp vụ suy ra được, chẳng hạn:
      - giá và số lượng không âm;
      - ngày giao hàng không hợp lý nếu trước ngày đặt;
      - foreign key phải tham chiếu đến bản ghi tồn tại;
      - mã khách hàng, sản phẩm và đơn hàng phải đúng định dạng.

  Đây sẽ là baseline để đối chiếu sau khi Agent xử lý.

  ### 2. Không hiểu “valid 50%” đơn giản là 50% ô bị lỗi

  Nếu làm hỏng ngẫu nhiên một nửa database, dữ liệu có thể trở nên phi thực tế hoặc không thể sửa một cách xác định.

  Tôi sẽ định nghĩa validity theo nhiều tầng:

  - Cell validity: tỷ lệ ô hợp lệ.
  - Row validity: tỷ lệ dòng không chứa lỗi.
  - Column quality: null, sai kiểu, sai miền giá trị, outlier.
  - Relational validity: PK, FK, duplicate và tính nhất quán giữa các bảng.
  - Business-rule validity: dữ liệu đúng kiểu nhưng vô lý về nghiệp vụ.

  Mục tiêu 40–50% nên được đo bằng một quality score có trọng số, thay vì buộc đúng 50% tổng số ô phải bị phá.

  ### 3. Tạo hai biến thể có tính chất khác nhau

  northwind_tester1.db sẽ là bộ lỗi phổ biến, phù hợp để kiểm tra khả năng profiling và gợi ý preprocessing cơ bản:

  - Null ở các cột quan trọng và không quan trọng.
  - Chuỗi có khoảng trắng thừa.
  - Khác biệt hoa/thường.
  - Giá trị categorical viết không nhất quán.
  - Ngày ở nhiều định dạng.
  - Số được lưu dưới dạng text.
  - Decimal dùng dấu phẩy/dấu chấm không đồng nhất.
  - Dòng trùng lặp.
  - Một số outlier rõ ràng.
  - Giá trị placeholder như N/A, unknown, -, chuỗi rỗng.

  Agent dự kiến phải gợi ý các tác vụ như điền null, đổi kiểu, chuẩn hóa text/ngày, loại duplicate và xử lý outlier.

  northwind_tester2.db sẽ khó hơn, dùng để kiểm tra reasoning và tính nhất quán nhiều bảng:

  - Orphan foreign key.
  - Primary key sai định dạng hoặc trùng.
  - Giá trị bị chuyển nhầm giữa các cột tương thích kiểu.
  - Ngày hợp lệ về cú pháp nhưng sai logic.
  - Giá, số lượng, discount sai miền.
  - Outlier khó nhận biết hơn.
  - Typo trong category, city, country hoặc product.
  - Đơn vị không đồng nhất.
  - Một số dòng bị thiếu tổ hợp nhiều trường.
  - Mâu thuẫn giữa order, order detail, customer, employee hoặc product.
  - Một lượng nhỏ lỗi cơ bản để Agent vẫn phải kết hợp nhiều phép xử lý.

  Hai tester không nên chỉ khác random seed; chúng cần đại diện cho hai cấp độ và hai nhóm lỗi khác nhau.

  ### 4. Mọi thay đổi đều phải có corruption manifest

  Đây là phần quan trọng nhất để biến database thành benchmark thật sự.

  Mỗi lỗi được chèn sẽ có một bản ghi ground truth, chẳng hạn:

   Thông tin          Ý nghĩa
  ━━━━━━━━━━━━━━━━━  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Dataset            tester1 hoặc tester2
  ─────────────────  ──────────────────────────────────────
   Table              Bảng bị tác động
  ─────────────────  ──────────────────────────────────────
   Row identity       Primary key của dòng
  ─────────────────  ──────────────────────────────────────
   Column             Cột bị sửa
  ─────────────────  ──────────────────────────────────────
   Clean value        Giá trị gốc
  ─────────────────  ──────────────────────────────────────
   Corrupted value    Giá trị raw
  ─────────────────  ──────────────────────────────────────
   Error type         null, typo, outlier, duplicate…
  ─────────────────  ──────────────────────────────────────
   Severity           Mức độ lỗi
  ─────────────────  ──────────────────────────────────────
   Expected action    Điền null, chuẩn hóa, xóa dòng…
  ─────────────────  ──────────────────────────────────────
   Recoverable        Có thể khôi phục chính xác hay không
  ─────────────────  ──────────────────────────────────────
   Rule ID            Quy tắc preprocessing tương ứng

  Manifest nên ở định dạng JSONL hoặc CSV để con người và chương trình đều đọc được.

  Với lỗi cấp dòng hoặc quan hệ, manifest sẽ ghi thêm:

  - ID dòng được nhân bản.
  - Foreign key gốc và foreign key sai.
  - Dòng nào bị thêm hoặc xóa.
  - Quan hệ nào bị vi phạm.

  ### 5. Tách “khôi phục dataset” và “đánh giá Agent”

  Có hai cách revert khác nhau:

  1. Revert tuyệt đối để chạy lại test
     Tạo lại tester từ northwind.db với cùng cấu hình và random seed. Cách này bảo đảm mỗi lần test nhận đúng cùng một dữ liệu raw.

  2. Revert bằng preprocessing
     Áp dụng ground-truth repair lên tester rồi so sánh kết quả với baseline. Cách này kiểm tra liệu toàn bộ lỗi được tạo có thể sửa ngược hay không.

  Không nên so sánh trực tiếp byte của hai file SQLite vì metadata, page layout hoặc thứ tự ghi có thể khác dù dữ liệu giống hệt. Benchmark nên so sánh logic:

  - Schema và constraint.
  - Tập bản ghi sau khi sắp xếp theo khóa.
  - Kiểu dữ liệu chuẩn hóa.
  - Giá trị từng ô.
  - PK/FK và business rules.
  - Row count và checksum theo bảng.

  ### 6. Chuẩn bị một bộ specification cho mỗi tester

  Mỗi tester sẽ có hồ sơ riêng, ví dụ về mặt khái niệm:

  - Mục tiêu quality score.
  - Random seed.
  - Danh sách bảng/cột được phép làm lỗi.
  - Danh sách lỗi và tỷ lệ.
  - Các cột không được phá.
  - Quy tắc clean kỳ vọng.
  - Số lỗi thực tế đã tạo.
  - Kết quả profiling trước/sau.
  - Expected recommendations từ Agent.

  Nhờ vậy, tester có thể tái lập được, thay vì là một database bị chỉnh ngẫu nhiên không rõ nguồn gốc.

  ### 7. Benchmark Agent ở ba cấp độ

  Tôi sẽ không chỉ kiểm tra kết quả cuối cùng, mà chia thành:

  - Detection: Agent có nhận ra đúng cột/dòng/vấn đề không?
  - Recommendation: Agent có đề xuất đúng loại preprocessing không?
  - Repair: sau khi áp dụng, dữ liệu có trở về baseline logic không?

  Các chỉ số chính:

  - Precision: bao nhiêu cảnh báo của Agent là đúng.
  - Recall: Agent phát hiện được bao nhiêu lỗi đã cài.
  - False-positive rate: Agent sửa nhầm bao nhiêu giá trị sạch.
  - Repair accuracy: bao nhiêu giá trị lỗi được phục hồi đúng.
  - Schema/constraint pass rate.
  - Final logical match với northwind.db.
  - Quality score trước và sau xử lý.

  Điều này rất quan trọng: một Agent xóa hết các dòng có vấn đề có thể làm “validity” tăng cao, nhưng không được coi là clean đúng vì gây mất dữ liệu quá mức.

  ### 8. Danh sách yêu cầu preprocessing sẽ là ground truth, không phải script duy nhất

  Tôi sẽ chuẩn bị song song:

  - Human-readable checklist: để bạn biết đúng ra phải xử lý những gì.
  - Machine-readable manifest: để chương trình chấm kết quả.
  - Deterministic corruption specification: để tái tạo tester.
  - Reference repair process: để chứng minh các lỗi có thể phục hồi.
  - Validation report: để so tester sau preprocessing với baseline.

  CSV phù hợp cho lỗi theo từng ô; JSON/JSONL phù hợp hơn cho duplicate, row-level và lỗi liên bảng. Thực tế nên dùng cả hai hoặc dùng JSONL làm nguồn chính rồi xuất CSV để xem thuận tiện.

  ## Một giới hạn cần xác định rõ

  Không phải lỗi raw nào cũng có thể suy luận ngược 100% chỉ từ dữ liệu lỗi. Ví dụ, nếu thay một tên khách hàng bằng NULL, Agent không thể tự biết chắc tên ban đầu là gì nếu không có nguồn tham
  chiếu.

  Vì vậy tôi sẽ phân lỗi thành:

  - Automatically inferable: chuẩn hóa khoảng trắng, format ngày, duplicate rõ ràng.
  - Rule-repairable: sửa bằng constraint hoặc mapping đã định nghĩa.
  - Reference-repairable: cần đối chiếu bảng khác.
  - Irrecoverable without ground truth: chỉ benchmark được khả năng phát hiện/gợi ý, không yêu cầu Agent đoán đúng giá trị gốc.

  Nếu yêu cầu đầu ra phải giống northwind.db 100%, mọi lỗi mất thông tin phải có mapping hoặc repair oracle bí mật dùng khi chấm điểm. Không nên đưa oracle này cho Agent, vì như vậy bài test sẽ bị
  “lộ đáp án”.

  Tóm lại, tôi sẽ tạo hai benchmark có lỗi được kiểm soát, tái lập và đo lường được—không chỉ phá dữ liệu cho bẩn. tester1 kiểm tra preprocessing cơ bản; tester2 kiểm tra lỗi phức hợp và quan hệ.
  Mỗi thay đổi đều có ground truth, expected recommendation và phương pháp đối chiếu logic với northwind.db, trong khi ba file bạn yêu cầu giữ nguyên sẽ không bị chỉnh sửa.


• Tôi đã khảo sát toàn bộ project ở chế độ chỉ đọc. Chưa có file nào được tạo, sửa hoặc xóa; đặc biệt không đụng vào Agent_job.md, wireframe và ba database.

  ## 1. Hiện trạng project

  10-9_create_datasets/
  ├── Agent_job.md
  ├── README.md
  ├── iDecide_Wireframe.html
  ├── northwind.db
  ├── Problem_set_for_Agent/
  ├── data_raw_tester1/
  │   ├── README_tester1.md
  │   ├── northwind_tester1.db
  │   ├── revert_clean_tester1.json
  │   ├── revert_clean_tester1.py
  │   └── revert_clean_tester1.ipynb
  └── data_raw_tester2/
      ├── README_tester2.md
      ├── northwind_tester2.db
      ├── revert_clean_tester2.json
      ├── revert_clean_tester2.py
      └── revert_clean_tester2.ipynb

  Các phát hiện quan trọng:

  - Agent_job.md chứa đầy đủ mục tiêu benchmark chúng ta vừa thống nhất. Tôi sẽ coi đây là tài liệu yêu cầu được bảo vệ.
  - README.md đang rỗng.
  - iDecide_Wireframe.html là wireframe tĩnh, dữ liệu và hành vi Agent hiện đang được mock bằng JavaScript, không kết nối trực tiếp với SQLite.
  - northwind.db là baseline 24 MB, integrity check thành công.
  - Hai tester có SHA-256 giống hệt baseline. Nghĩa là hiện tại chúng là bản sao byte-for-byte, chưa có corruption.
  - Sáu file .json, .py, .ipynb của hai tester đang hoàn toàn rỗng.
  - Hai README con mới có bốn dòng placeholder. README_tester1.md còn nhắc nhầm sang data_raw_tester2.
  - Problem_set_for_Agent/ đang rỗng.
  - Project hiện không phải Git repository, do đó chưa có cơ chế phục hồi qua Git. Việc bảo vệ baseline và ghi checksum càng quan trọng.
  - Không tìm thấy file AGENT.md hay AGENTS.md; file thực tế hiện có là Agent_job.md. Tôi sẽ bảo vệ cả tên bạn đã nói lẫn file thực tế này.

  ## 2. Tôi hiểu gì từ database

  Baseline có:

  - 13 bảng dữ liệu.
  - 17 view.
  - Không có trigger hoặc index tùy chỉnh ngoài index tự sinh cho primary key.
  - Hai bảng đang rỗng: CustomerCustomerDemo và CustomerDemographics.
  - Các bảng chính:
      - Orders: 16.282 dòng.
      - Order Details: 609.283 dòng.
      - Customers: 93 dòng.
      - Products: 77 dòng.
      - Suppliers: 29 dòng.
      - Các bảng nhân viên, vùng và vận chuyển có kích thước nhỏ hơn.

  - Các foreign key được khai báo trong schema và baseline hiện không có vi phạm.
  - SQLite không bật foreign-key enforcement mặc định khi mở kết nối. Vì vậy script tạo lỗi sau này phải quản lý việc bật/tắt foreign key một cách rõ ràng.
  - Một số null đã tồn tại hợp lệ trong baseline:
      - Customers.Fax: 24/93.
      - Một số địa chỉ, region, postal code của customer.
      - Employees.ReportsTo: một giá trị null hợp lý cho cấp cao nhất.
      - Orders.ShippedDate: 21 dòng chưa có ngày giao.
      - Orders.ShipPostalCode: 172 dòng null.

  Do đó, “clean 100%” không thể được định nghĩa là “không còn null”. Nó phải có nghĩa là khớp với trạng thái logic của northwind.db, đồng thời không sửa nhầm những null hợp lệ này.

  Tôi sẽ không làm lỗi trực tiếp lên:

  - Các cột BLOB như ảnh category và employee.
  - Hai bảng rỗng.
  - Các view — view sẽ tự phản ánh lỗi từ bảng nguồn.
  - Baseline northwind.db.

  ## 3. Tôi hiểu gì từ wireframe iDecide

  Wireframe mô tả pipeline 8 bước, trong đó benchmark này tập trung trực tiếp vào bước 3: làm sạch từng bảng.

  UI hỗ trợ hoặc mô phỏng:

  - Profiling theo cột.
  - Valid/error/empty percentage.
  - Chọn bảng và cột.
  - Đổi kiểu dữ liệu.
  - Điền null.
  - Loại outlier.
  - Chuẩn hóa/scale.
  - Encode.
  - Chuẩn hóa ngày.
  - Khử trùng lặp.
  - SQL nâng cao.
  - Agent đề xuất thao tác dựa trên profiling.
  - Preview sau từng bước.
  - Revert/xóa một bước.
  - Profiling cập nhật sau mỗi bước.

  Như vậy, hai tester không chỉ cần “bẩn”, mà phải tạo ra tín hiệu profiling đủ rõ để các nút gợi ý của UI có ý nghĩa. Đồng thời tester2 cần có các lỗi mà sáu thao tác dựng sẵn chưa giải quyết hoàn
  toàn, buộc Agent phải reasoning hoặc đề xuất SQL nâng cao.

  Wireframe là tài liệu tham khảo về hành vi cần benchmark; tôi không có ý định sửa nó.

  ## 4. Ý định đối với từng khu vực

   Khu vực                   Ý định
  ━━━━━━━━━━━━━━━━━━━━━━━━  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   northwind.db              Baseline chỉ đọc, nguồn sự thật duy nhất
  ────────────────────────  ────────────────────────────────────────────────────────────────
   Agent_job.md              Tài liệu yêu cầu, không chỉnh sửa
  ────────────────────────  ────────────────────────────────────────────────────────────────
   iDecide_Wireframe.html    Tham chiếu UI/luồng Agent, không chỉnh sửa
  ────────────────────────  ────────────────────────────────────────────────────────────────
   README.md                 Tài liệu điều hướng và quy chuẩn toàn benchmark
  ────────────────────────  ────────────────────────────────────────────────────────────────
   data_raw_tester1/         Benchmark cấp cơ bản: profiling và preprocessing từng cột
  ────────────────────────  ────────────────────────────────────────────────────────────────
   data_raw_tester2/         Benchmark cấp nâng cao: lỗi đa cột, nghiệp vụ và liên bảng
  ────────────────────────  ────────────────────────────────────────────────────────────────
   Problem_set_for_Agent/    Chứa đề bài Agent được phép nhìn thấy, không chứa đáp án
  ────────────────────────  ────────────────────────────────────────────────────────────────
   JSON revert               Ground truth/corruption manifest máy đọc được
  ────────────────────────  ────────────────────────────────────────────────────────────────
   Python revert             Reference repair và validation engine
  ────────────────────────  ────────────────────────────────────────────────────────────────
   Notebook revert           Hướng dẫn chạy trực quan, tái sử dụng Python thay vì lặp logic

  ## 5. Thiết kế cho tester1

  Mục tiêu dự kiến: quality score khoảng 48–50%.

  Tester1 giữ nguyên schema và tập trung vào lỗi phổ biến mà UI đã có thao tác tương ứng:

  - Null được chèn có chủ đích.
  - Placeholder như N/A, unknown, -, chuỗi rỗng.
  - Khoảng trắng đầu/cuối và nhiều khoảng trắng.
  - Hoa/thường không nhất quán.
  - Category typo hoặc biến thể tương đương.
  - Ngày ở nhiều định dạng.
  - Số được lưu dưới dạng text.
  - Decimal separator không đồng nhất.
  - Outlier rõ ràng.
  - Near-duplicate với khóa mới hợp lệ.
  - Một số lỗi type-affinity mà SQLite cho phép lưu.

  Các bảng trọng tâm:

  - Customers
  - Orders
  - Order Details
  - Products
  - Có thể thêm Suppliers nếu cần đủ độ đa dạng.

  Expected recommendations chủ yếu:

  - Fill null.
  - Change type.
  - Normalize text/date.
  - Detect/remove outlier.
  - Deduplicate.
  - Chuẩn hóa categorical values.

  ## 6. Thiết kế cho tester2

  Mục tiêu dự kiến: quality score khoảng 40–45%.

  Tester2 khó hơn nhưng vẫn tái lập và phục hồi được:

  - Orphan foreign key.
  - ID hợp lệ về kiểu nhưng không có trong bảng cha.
  - Semantic duplicate với primary key khác.
  - Ngày hợp lệ cú pháp nhưng sai thứ tự nghiệp vụ.
  - Discount, freight, quantity, stock hoặc price ngoài miền.
  - City/country/postal code mâu thuẫn.
  - Product liên kết sai category hoặc supplier.
  - Employee hierarchy không hợp lý.
  - Các cột tương thích kiểu bị tráo dữ liệu.
  - Lỗi phối hợp nhiều cột trên cùng một dòng.
  - Outlier có vẻ hợp lệ nếu chỉ nhìn riêng một cột.
  - Một lượng lỗi cơ bản để Agent phải kết hợp nhiều thao tác.

  Expected recommendations gồm:

  - Các thao tác preprocessing thông thường.
  - Kiểm tra PK/FK và integrity.
  - Cross-table validation.
  - Business-rule validation.
  - SQL nâng cao hoặc mapping theo bảng tham chiếu.
  - Cảnh báo lỗi không thể tự động suy luận giá trị gốc.

  Tôi không dự định phá constraint tùy tiện hoặc bỏ primary key chỉ để tạo duplicate. Tester1 sẽ giữ schema; tester2 ưu tiên orphan key và semantic duplicate trước khi cân nhắc thay đổi schema.

  ## 7. Cách sử dụng các file hiện có

  Trong mỗi tester, tôi dự kiến phân trách nhiệm như sau:

  - northwind_testerN.db
      - Database raw được đưa cho Agent.
      - Chỉ được sinh từ baseline, không chỉnh tay không có log.

  - README_testerN.md
      - Mục tiêu của scenario.
      - Quality score.
      - Những bảng nằm trong scope.
      - Hướng dẫn chạy.
      - Không công khai giá trị sạch cụ thể nếu dùng để blind test.

  - revert_clean_testerN.json
      - Manifest/oracle chính.
      - Ghi seed, rule ID, bảng, khóa dòng, cột, clean value, raw value, expected action và khả năng phục hồi.
      - Có cả lỗi cell-level, row-level và relationship-level.

  - revert_clean_testerN.py
      - Đọc manifest.
      - Revert tester về bản repaired riêng, không ghi đè baseline.
      - Chạy validation logic.
      - So sánh kết quả theo schema, dữ liệu, constraint và checksum logic.

  - revert_clean_testerN.ipynb
      - Giao diện hướng dẫn cho người test.
      - Chỉ gọi lại chức năng trong .py.
      - Không chứa một bản implementation thứ hai vì dễ lệch logic.

  Còn thiếu một thành phần tạo corruption. Tôi dự định bổ sung một công cụ deterministic dùng config và seed để tái tạo hai tester từ baseline. Không nên sửa database thủ công vì sẽ không tái lập
  hoặc audit được.

  ## 8. Phân tách đề bài và đáp án

  Tôi dự định dùng Problem_set_for_Agent cho nội dung Agent được nhìn thấy:

  Problem_set_for_Agent/
  ├── README.md
  ├── tester1_task.md
  ├── tester2_task.md
  └── results/

  Nó sẽ chứa:

  - Mục tiêu Agent cần thực hiện.
  - Database nào phải profiling.
  - Yêu cầu xuất recommendations.
  - Format ghi kết quả.
  - Điều kiện không được dùng baseline hoặc repair oracle.
  - Nơi lưu kết quả từng lần chạy.

  Ground truth và clean values không nên nằm trong prompt hoặc folder được đưa trọn cho Agent. Nếu Agent đọc được revert_clean_*.json, benchmark sẽ lộ đáp án. Khi test thực tế, chỉ nên upload
  database raw và task description cần thiết.

  ## 9. README gốc sẽ được cập nhật thế nào

  Tôi chưa sửa README.md vì bạn yêu cầu chưa chỉnh file. Khi được phép, README sẽ có:

  1. Mục tiêu project.
  2. Protected files.
  3. Cấu trúc thư mục.
  4. Thống kê baseline.
  5. Định nghĩa “clean”, “raw” và quality score.
  6. Khác biệt giữa tester1 và tester2.
  7. Corruption taxonomy.
  8. Quy tắc manifest và ground truth.
  9. Quy trình:
      - build;
      - profile;
      - test Agent;
      - repair;
      - validate;
      - reset.

  10. Benchmark metrics.
  11. Cách tránh oracle leakage.
  12. Guardrails chống ghi đè baseline.
  13. Reproducibility bằng seed và checksum.

  Hai README con sẽ được viết lại hoàn toàn, không giữ nội dung placeholder hiện tại.
  5. Viết README gốc và README từng tester.
  6. Tạo công cụ sinh corruption deterministic.
  7. Sinh hai database raw từ bản sao baseline.
  8. Sinh manifest và báo cáo profiling trước/sau.
  9. Viết reference repair/validation.
  10. Làm notebook thành giao diện chạy thử, không chứa logic trùng lặp.
  11. Tạo hai problem sets cho Agent.
  12. Chạy kiểm tra:
      - baseline không đổi checksum;
      - tester đạt quality target;
      - oracle repair khớp baseline logic;
      - foreign-key check;
      - integrity check;
      - chạy lại với cùng seed tạo cùng kết quả.

  Hiện tại mọi thứ vẫn nguyên trạng. Bước tiếp theo hợp lý là bạn duyệt thiết kế này; sau đó tôi mới bắt đầu cập nhật README và triển khai các artifact.
