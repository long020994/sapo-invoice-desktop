# Sapo Invoice Desktop

Ứng dụng Windows độc lập để đọc ảnh/PDF hóa đơn, đối chiếu sản phẩm Sapo, kiểm tra
giá đã gồm VAT và xuất Excel. Ứng dụng không dùng Tampermonkey và không
kết nối tới `C:\Sapo_AI_Agent\server.py`.

## Mở bản thử nghiệm độc lập

Mở `dist-v17\SapoInvoiceDesktop\SapoInvoiceDesktop.exe`, chọn **Cấu hình** ở thanh bên trái,
nhập OpenAI API key rồi lưu. Sau đó chọn **Hóa đơn mới** và chọn ảnh/PDF; ứng dụng tự động
đọc ngay sau khi chọn.
Khung bên phải hiển thị ảnh hóa đơn hoặc trang PDF đang chọn. Với PDF nhiều trang, dùng
nút mũi tên hai bên số trang để chuyển trang; với nhiều file, bấm vào tên file để xem lại.
Thanh công cụ nền tối hỗ trợ xoay trái/phải, zoom 10%-500%, vừa khung và kéo ảnh để xem
vùng khuất. Có thể dùng con lăn chuột để zoom; khi khung xem có focus, dùng Ctrl +/- để
zoom, phím R để xoay và phím 0 để trở về vừa khung.

Thư mục `dist\SapoInvoiceDesktop` là một bộ hoàn chỉnh. Có thể chép cả thư mục sang vị trí
khác trên máy; chương trình không cần Python, Tampermonkey hoặc server cũ để chạy.

## Dữ liệu

Dữ liệu cá nhân được lưu trong `%LOCALAPPDATA%\SapoInvoiceDesktop`:

- `settings.json`: cấu hình; API key được Windows mã hóa.
- `price_history.json`: lịch sử giá nhập.
- `learning_rules.json`: các lựa chọn sản phẩm đã học.
- `sapo_database.json`: danh mục sản phẩm riêng của ứng dụng.

Khi ba gợi ý đều không đúng, nhấp **Tìm trong toàn bộ 14.854 sản phẩm**, nhập tên,
SKU hoặc barcode rồi chọn đúng sản phẩm. Ứng dụng sẽ ghi nhớ lựa chọn đó cho lần sau.

File Excel xuất ra giữ nguyên cấu trúc 13 cột của mẫu Sapo `receive_inventories_import_template_with_lot_date`.
Giá nhập đã bao gồm VAT nên ứng dụng để trống ô thuế, tránh cộng thuế hai lần.
Nếu hóa đơn ghi tiền theo đơn vị nghìn, ví dụ đơn giá `42`, `25`, ứng dụng tự hiểu là
`42.000đ`, `25.000đ` và đồng thời quy đổi đúng thành tiền của dòng. Giá đã ghi đầy đủ
không bị nhân thêm; dòng hàng tặng `0đ` vẫn được giữ nguyên.

Sapo bắt buộc mọi dòng phải có SKU. Với sản phẩm đang thiếu SKU, ứng dụng tự tạo mã ngắn
không dấu theo tên sản phẩm (ví dụ `head&shoulderday`) và đánh dấu **SKU mới** để người dùng
nhận biết. Khi xuất, ứng dụng tạo thêm file kiểm tra `cap-nhat-sku-gia-ban-...xlsx`.
Trước khi xuất, ứng dụng kiểm tra chéo SKU với cả SKU và barcode của mọi sản phẩm khác.
Nếu một SKU cũ đụng barcode của sản phẩm khác, ứng dụng đề xuất SKU an toàn từ barcode
riêng của đúng sản phẩm và chỉ đồng bộ thay đổi lên Sapo khi người dùng bật đồng bộ lúc xuất.

SKU mới chưa tồn tại trong Sapo nên không thể dùng ngay trong đơn nhập hàng. Khi có SKU
mới hoặc giá bán thay đổi, ứng dụng yêu cầu chọn file danh sách sản phẩm `.csv` mới nhất
vừa xuất từ Sapo và tạo `BUOC-1-cap-nhat-san-pham-sapo.csv`. Cần nhập file BƯỚC 1 vào
danh sách sản phẩm, chờ Sapo cập nhật xong, rồi mới nhập file `BUOC-2-don-nhap-hang-...xlsx`.

Cột **Đơn giá** trong file đơn nhập được để trống nếu giá nhập trên hóa đơn bằng giá vốn
hiện có. Nếu khác, giá được làm tròn về số nguyên trước khi ghi để đáp ứng giới hạn của Sapo.
Trong **Sửa dòng**, người dùng có thể xem giá vốn, giá bán hiện tại và nhập giá bán mới.

Trong tab **Cấu hình**, nút **Cập nhật SKU trực tiếp lên Sapo** đọc toàn bộ phiên bản sản
phẩm từ Sapo và hiển thị số lượng thay đổi trước khi yêu cầu xác nhận. Ứng dụng chỉ điền
SKU cho sản phẩm đang trống SKU; mọi SKU đã có từ trước luôn được giữ nguyên. SKU mới lấy
từ barcode, trường hợp trùng được thêm hậu tố `1`, `2`, `3`... Sản phẩm trống cả SKU lẫn
barcode được bỏ qua. Sau khi hoàn tất, database nội bộ được làm mới
tự động. Nếu bị ngắt giữa chừng, chạy lại sẽ bỏ qua các SKU đã đúng và tiếp tục phần còn lại.

Nút **Bật cho phép bán âm cho toàn bộ** kiểm tra mọi phiên bản sản phẩm và chỉ cập nhật
những phiên bản chưa được phép bán khi hết hàng. Thao tác này gửi
`inventory_policy = continue` lên Sapo; không thay đổi SKU, barcode, giá hoặc số lượng tồn.

Lần đầu sử dụng, nhập địa chỉ cửa hàng, API Key và API Secret của **Ứng dụng riêng** Sapo.
Ứng dụng riêng phải có quyền **Sản phẩm, phiên bản và danh mục: Đọc và ghi**. Thông tin bí
mật được mã hóa bằng tài khoản Windows hiện tại. Nút **Tạo CSV dự phòng** vẫn được giữ lại
nhưng không cần dùng trong quy trình thông thường.

Nếu cần hoàn tác một lần cập nhật sai, chọn **Khôi phục SKU gốc từ file sao lưu** và dùng
file danh sách sản phẩm đã xuất trước khi SKU bị thay đổi. Ứng dụng đối chiếu theo **Id phiên
bản**, chỉ khôi phục những dòng từng có SKU và không đụng tới các dòng vốn để trống SKU.

## Đồng bộ khi xuất hóa đơn

Trong cửa sổ sửa dòng, có thể đánh dấu **Đây là sản phẩm mới** rồi nhập tên, SKU, barcode,
giá nhập và giá bán. Khi bật **Đồng bộ sản phẩm và giá lên Sapo khi xuất**, ứng dụng sẽ:

- Tạo sản phẩm mới trên Sapo trước khi tạo file đơn nhập.
- Cập nhật SKU, barcode và giá bán đã thay đổi của sản phẩm hiện có.
- Ghi Id phiên bản mới vào kết quả và làm mới database cục bộ.
- Sau đó mới xuất file Excel đơn nhập hàng với SKU hợp lệ.

Giá vốn không được cập nhật qua Product API vì API công khai của Sapo không cung cấp trường
giá vốn cho Variant. Giá nhập đã thuế vẫn được ghi vào file Excel và được Sapo xử lý khi
người dùng nhập file đơn nhập hàng. Sapo hiện không công bố API cho thao tác tải file đơn
nhập, vì vậy ứng dụng không gọi endpoint nội bộ không ổn định.

Nhấp đúp vào cột SKU, số lượng, giá nhập hoặc giá bán trong bảng kết quả sẽ mở đúng trường
tương ứng và bôi đen toàn bộ giá trị để nhập thay thế ngay. Nhấp đúp trực tiếp trong mọi ô
nhập liệu cũng chọn toàn bộ nội dung.

Chọn một dòng rồi bấm **Xóa dòng** hoặc phím **Delete** để bỏ các mục không cần nhập như
chiết khấu toàn đơn. Trong cửa sổ tìm sản phẩm, nội dung tìm kiếm được bôi đen sẵn; bấm
**Enter** để chọn kết quả đầu tiên. Trong cửa sổ sửa, bấm **Enter** để lưu thay đổi.
Sau khi xuất Excel thành công, danh sách file và kết quả hiện tại được tự động xóa để sẵn
sàng xử lý hóa đơn tiếp theo.

Đây là bản nền tảng độc lập. Việc tạo đơn trực tiếp trên Sapo chỉ được bổ sung sau khi
endpoint đơn nhập được kiểm chứng an toàn; hiện tại ứng dụng xuất `.xlsx` để người dùng
kiểm tra trước khi nhập vào Sapo.
