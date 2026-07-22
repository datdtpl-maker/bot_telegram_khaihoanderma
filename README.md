# Bot Khải Hoàn Derma

Bot Telegram quản trị WooCommerce và hỗ trợ quy trình đăng sản phẩm có kiểm soát từ Notion lên `khaihoanderma.com`.

## Các chức năng hiện có

- Đơn hàng, doanh thu và xuất báo cáo Excel.
- Tra cứu sản phẩm, cập nhật giá, giá khuyến mãi, tồn kho và biến thể.
- Tạo, sửa hoặc xóa sản phẩm có bước xác nhận trên Telegram.
- Duyệt đánh giá WooCommerce.
- Báo cáo Google Analytics và Search Console khi đã cấu hình OAuth.
- Đồng bộ sản phẩm từ Notion theo quy trình an toàn bên dưới.

## Điểm an toàn của luồng đăng sản phẩm

- Khóa thread và khóa file chỉ cho phép một lượt đồng bộ trên cùng máy; SKU Notion chống tạo trùng khi API timeout.
- Mỗi Notion Page có SKU ổn định `notion-{page_id}` để chống đăng trùng và phục hồi khi API timeout.
- Mỗi sản phẩm dùng thư mục ảnh tạm riêng; không trộn ảnh giữa hai lượt chạy.
- Ảnh được natural sort: `1, 2, 3, ... 10, 11`.
- Kiểm tra nội dung thật và MIME của PNG/JPG/WebP; tên file tiếng Việt được chuyển thành tên upload an toàn.
- Bắt buộc có tên, mô tả, giá lớn hơn 0, danh mục và ít nhất một ảnh.
- Bắt buộc toàn bộ ảnh upload và cập nhật Alt/Title thành công.
- Tạo sản phẩm ở trạng thái `draft`, kiểm tra đúng thứ tự ảnh rồi mới `publish`.
- Nếu lỗi trước khi publish, bot rollback sản phẩm nháp và media vừa upload.
- Notion database và content blocks được đọc có phân trang, không dừng ở 100 bản ghi.

## Cài đặt trên máy mới

1. Cài Python 3.10 trở lên và chọn `Add Python to PATH`.
2. Copy nguyên thư mục bot sang máy mới. Không gửi `telegram_bot.env` qua nơi công cộng vì file chứa khóa bí mật.
3. Mở PowerShell trong thư mục bot và chạy:

```powershell
python -m pip install -r requirements.txt
```

4. Nếu chưa có cấu hình, copy `telegram_bot.env.example` thành `telegram_bot.env`, rồi điền token/API key thực tế.
5. Click đúp `start_telegram_bot.bat`.
6. Mở Telegram và gửi `ping`. Bot phải phản hồi trước khi thao tác đăng bài.

Có thể kiểm tra cấu hình mà không chạy bot hoặc gọi API đăng bài:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_telegram_bot.ps1 -ValidateOnly
```

Không chạy cùng một Telegram Bot Token trên hai máy. Telegram sẽ báo xung đột `409`, đồng thời làm quy trình vận hành khó kiểm soát.

## File cần copy sang máy khác

- `telegram_woocommerce_bot.py`
- `notion_sync.py`
- `bot_modules/`
- `requirements.txt`
- `run_telegram_bot.ps1`
- `start_telegram_bot.bat`
- `telegram_bot.env.example`
- `telegram_bot.env` — copy riêng và bảo mật
- Các file Google OAuth nếu đang sử dụng báo cáo GA4/Search Console
- `HUONG_DAN_DANG_BAI_WEB.md`

Không cần copy: `__pycache__/`, `temp_notion_images/`, `bot.log`, `telegram_uploads/`, file báo cáo cũ và thư mục `tests/`.

## Khởi động tự động cùng Windows

Cách đơn giản:

1. Nhấn `Win + R` và nhập `shell:startup`.
2. Tạo shortcut trỏ tới `start_telegram_bot.bat` trong thư mục Startup.
3. Khởi động lại máy và kiểm tra bằng lệnh `ping` trên Telegram.

Script PowerShell sẽ kiểm tra Python, file cấu hình, biến bắt buộc và thư viện trước khi chạy. Bot lỗi liên tiếp 5 lần sẽ dừng thay vì lặp vô hạn; xem `bot.log` để chẩn đoán.

## Kiểm thử code

Các test không gọi API thật và không đăng sản phẩm:

```powershell
python -m unittest discover -s tests -v
```

Quy trình chi tiết: xem [HUONG_DAN_DANG_BAI_WEB.md](HUONG_DAN_DANG_BAI_WEB.md).
