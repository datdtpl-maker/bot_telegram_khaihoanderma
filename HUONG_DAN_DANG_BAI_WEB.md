# Hướng dẫn đăng sản phẩm từ Notion lên Khải Hoàn Derma

## 1. Chuẩn bị ảnh trên Google Drive

Tạo một thư mục riêng cho từng sản phẩm và đặt quyền Google Drive `Bất kỳ ai có đường liên kết` ở chế độ xem để `gdown` tải được.

Đặt ảnh trực tiếp ở thư mục gốc theo thứ tự:

- `1.jpg` hoặc `1.png` hoặc `1.webp`: ảnh đại diện bắt buộc.
- `2.jpg`, `3.jpg`, ...: ảnh gallery.
- Có thể có `10.jpg`, `11.jpg`; bot đã sắp xếp đúng theo số.

Quy tắc:

- Mỗi số chỉ có một ảnh; không để đồng thời `1.jpg` và `1.png`.
- Không để file hỏng hoặc đổi đuôi giả, ví dụ nội dung WebP nhưng đặt tên `.jpg`.
- Nên dùng ảnh vuông, rõ nét và không chèn thông tin sai sản phẩm.
- Không đặt ảnh của nhiều sản phẩm trong cùng một thư mục Drive.

## 2. Chuẩn bị dữ liệu trên Notion

Điền đủ các cột:

- `Tên sản phẩm`: bắt buộc.
- `Từ khóa SEO Rank Math`: tối đa ba từ khóa đầu, cách nhau bằng dấu phẩy.
- `Media sản phẩm`: URL thư mục Google Drive vừa chuẩn bị.
- `Bài content Tây`: relation tới page chứa nội dung chi tiết.

Trong page nội dung, thêm các dòng metadata sau. Có thể đặt chung một dòng hoặc tách riêng:

```text
Danh mục sản phẩm: Chăm sóc da
Giá: 450000
Giá khuyến mãi: 399000
```

`Giá khuyến mãi` là tùy chọn nhưng nếu có phải nhỏ hơn `Giá`. Các đoạn văn còn lại trở thành mô tả sản phẩm.

## 3. Gửi bài sang bot

1. Kiểm tra lại ảnh số `1`, tên, giá, danh mục và nội dung.
2. Chuyển cột `Trạng thái` của Notion sang `Báo IT đăng`.
3. Chờ tối đa 15 phút để bot gửi thông báo, hoặc gửi `đồng bộ notion` trên Telegram để kiểm tra ngay.
4. Bot hiển thị danh sách sản phẩm chờ đăng. Chọn `Xác nhận` để bắt đầu hoặc `Hủy` để dừng.

## 4. Bot xử lý những gì

1. Đọc toàn bộ dữ liệu và content blocks từ Notion.
2. Tải ảnh vào thư mục tạm riêng của từng sản phẩm.
3. Sắp xếp ảnh theo số và xác thực PNG/JPG/WebP.
4. Upload toàn bộ ảnh, gán Alt/Title và kiểm tra lỗi.
5. Tạo sản phẩm WooCommerce ở trạng thái nháp.
6. Kiểm tra ID và thứ tự ảnh; ảnh số `1` phải là ảnh đại diện.
7. Publish sản phẩm rồi cập nhật Notion thành `Đã đăng web`, tích `IT đã đăng` và ghi `Link web`.

## 5. Khi bot báo lỗi

- Không chuyển Notion sang `Đã đăng web` bằng tay nếu chưa kiểm tra sản phẩm thực tế.
- Sửa đúng dữ liệu hoặc ảnh được bot nêu lỗi, giữ trạng thái `Báo IT đăng`, rồi đồng bộ lại.
- Nếu WooCommerce đã tạo sản phẩm nhưng Notion cập nhật bị timeout, bot dùng SKU theo Notion Page ID để tìm lại sản phẩm; không tạo thêm bản trùng.
- Nếu bot báo đang có lượt đồng bộ khác chạy, chờ lượt đó hoàn tất rồi thử lại.
- Nếu Telegram báo lỗi `409`, tắt bot đang chạy trên máy cũ trước khi chạy máy mới.

## 6. Kiểm tra sau khi đăng

Mở link bot trả về và kiểm tra:

- Tên, giá thường và giá khuyến mãi.
- Danh mục.
- Ảnh đại diện đúng ảnh số `1`.
- Gallery đúng thứ tự `2, 3, 4...`.
- Nội dung mô tả và từ khóa SEO.
- Notion đã chuyển `Đã đăng web` và có `Link web`.
