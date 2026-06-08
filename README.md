# Bot Khải Hoàn Derma (Telegram WooCommerce & Notion Bot)

Hệ thống Bot Telegram chuyên nghiệp tích hợp quản lý WooCommerce, đồng bộ sản phẩm tự động từ Notion và báo cáo SEO Google Analytics / Search Console.

---

## 🚀 Tính Năng Chính

### 1. Đồng Bộ & Đăng Sản Phẩm Từ Notion (Tự Động Hóa 100%)
*   Quét database Notion, lọc các bài viết có trạng thái **"Báo IT đăng"**.
*   Tự động tải toàn bộ ảnh từ thư mục Google Drive (ở cột **Media sản phẩm**).
*   Đọc nội dung từ cột **Bài content Tây**, tự động bóc tách **Danh mục**, **Giá bán** và **Giá khuyến mãi** tại dòng đầu tiên:
    `Danh mục sản phẩm: [Tên danh mục] - Giá: [Giá thường] - Giá khuyến mãi: [Giá khuyến mãi]`
*   Upload ảnh lên WordPress Media, tạo sản phẩm WooCommerce kèm cấu hình từ khóa SEO Rank Math (lấy tối đa 3 từ khóa đầu tiên).
*   Cập nhật ngược lại Notion: Đổi trạng thái sang **"Đã đăng web"**, tích chọn **"IT đã đăng"** và lưu **"Link web"**.

### 2. Quản Lý Đơn Hàng & Doanh Thu
*   Kiểm tra nhanh đơn hàng và doanh thu hôm nay, tháng này.
*   Xem chi tiết thông tin đơn hàng cụ thể (sản phẩm, khách hàng).
*   Xuất file báo cáo Excel chi tiết đơn hàng (hôm nay, tháng này hoặc theo khoảng ngày tùy chọn).

### 3. Cập Nhật Sản Phẩm & Tồn Kho Nhanh
*   Sửa giá thường / giá khuyến mãi của sản phẩm (hỗ trợ cả sản phẩm biến thể).
*   Thay đổi trạng thái kho hàng nhanh (`còn hàng` hoặc `hết hàng`).
*   Xóa sản phẩm trực tiếp từ khung chat Telegram.

### 4. Báo Cáo SEO Google (GA4 & Search Console)
*   Xem báo cáo lưu lượng truy cập (Traffic 28 ngày qua).
*   Thống kê từ khóa hàng đầu trên Google Search Console.

---

## 🛠️ Hướng Dẫn Triển Khai (Chạy 24/24 trên Server)

### 1. Các File Cần Thiết
Để chạy bot trên máy tính hoặc server khác, bạn chỉ cần copy các file sau:
*   `telegram_woocommerce_bot.py` (File chạy chính)
*   `notion_sync.py` (Module đồng bộ Notion)
*   `telegram_bot.env` (File cấu hình Token API & Mật khẩu)
*   `bot_modules/` (Thư mục chứa code báo cáo Google)
*   `google-oauth-client.json` (Xác thực Google API nếu có)
*   `start_telegram_bot.bat` & `run_telegram_bot.ps1` (Script khởi chạy nhanh)

### 2. Cài Đặt Môi Trường
Yêu cầu máy chạy đã cài đặt **Python 3.10 trở lên**. Chạy lệnh sau để cài đặt các thư viện bổ trợ:
```bash
pip install requests gdown google-auth google-auth-oauthlib
```

### 3. Cấu Hình Biến Môi Trường (`telegram_bot.env`)
Tạo hoặc chỉnh sửa file `telegram_bot.env` ở thư mục gốc:
```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_ALLOWED_CHAT_IDS=chat_id_1,chat_id_2

WORDPRESS_SITE_URL=https://khaihoanderma.com
WORDPRESS_USERNAME=your_wp_username
WORDPRESS_PASSWORD=your_wp_application_password

WOOCOMMERCE_CONSUMER_KEY=ck_...
WOOCOMMERCE_CONSUMER_SECRET=cs_...

NOTION_TOKEN=ntn_...
NOTION_DATABASE_ID=your_notion_database_id
```

### 4. Khởi Chạy
*   **Windows:** Click đúp chuột vào file `start_telegram_bot.bat`.
*   **Chạy tự động cùng Windows:** Nhấn `Win + R` -> gõ `shell:startup` -> tạo shortcut của file `start_telegram_bot.bat` bỏ vào thư mục Startup.

---

## 💬 Danh Sách Lệnh Nhanh (Slash Commands)
Cấu hình danh sách lệnh nhanh này qua `@BotFather` để tiện thao tác:
*   `/start` - Xem hướng dẫn sử dụng bot chi tiết.
*   `/ping` - Kiểm tra trạng thái hoạt động của bot.
*   `/dong_bo_notion` - Quét và đăng tự động các sản phẩm đang chờ từ Notion lên web.
*   `/dong_bo_san_pham` - Đồng bộ danh sách sản phẩm từ website WooCommerce vào bộ nhớ đệm của bot.
*   `/whoami` - Xem Chat ID Telegram cá nhân của bạn.
