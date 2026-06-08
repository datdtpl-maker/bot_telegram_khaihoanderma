# Telegram WooCommerce Bot

Bot Telegram cho WooCommerce: xem doanh thu, đơn hàng, doanh thu sản phẩm, xuất danh sách sản phẩm/tồn kho, tìm kiếm web cơ bản, sửa giá sản phẩm, đổi trạng thái tồn kho, cập nhật mô tả sản phẩm và import nội dung sản phẩm từ file DOCX bằng chat tự nhiên.

## File cần copy sang máy chạy 24/24

- `telegram_woocommerce_bot.py`
- `telegram_bot.env`
- `run_telegram_bot.ps1`
- `start_telegram_bot.bat`
- `README_TELEGRAM_BOT.md`

## Yêu cầu máy chạy bot

- Windows 10/11 hoặc Windows Server.
- Python 3.10 trở lên.
- Internet ổn định.
- Máy không sleep nếu muốn chạy 24/24.

Kiểm tra Python:

```powershell
python --version
```

## Cấu hình

Mở `telegram_bot.env` và kiểm tra các dòng:

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_CHAT_IDS=...
WORDPRESS_SITE_URL=https://khaihoanderma.com
WORDPRESS_USERNAME=...
WORDPRESS_PASSWORD=...
WOOCOMMERCE_CONSUMER_KEY=ck_...
WOOCOMMERCE_CONSUMER_SECRET=cs_...
```

Nếu chỉ xem báo cáo, WooCommerce key quyền `Read` là đủ.
Nếu muốn sửa giá/tồn kho, WooCommerce key phải là `Read/Write`.
Bot cập nhật sản phẩm qua WooCommerce API. Nếu muốn sửa giá, tồn kho hoặc mô tả sản phẩm, WooCommerce key phải có quyền `Read/Write`.

## Chạy bot

Double-click:

```text
start_telegram_bot.bat
```

Hoặc chạy bằng PowerShell:

```powershell
.\run_telegram_bot.ps1
```

## Câu chat mẫu

```text
doanh thu tháng 5 2026 là bao nhiêu
hôm nay có đơn hàng không
kiểm tra đơn hàng tháng này
cho tôi xem chi tiết đơn hàng 2361
chi tiết đơn hàng hôm nay
chi tiết đơn hàng ngày 2026-05-08
chi tiết các đơn hàng tháng 5 năm 2026
sản phẩm nào bán được trong tháng 5 2026
có bao nhiêu sản phẩm đang bán
có bao nhiêu sản phẩm đang có hàng
xuất tất cả sản phẩm trên web
Kem chống nắng TreaMax Sunscreen sửa lại giá 350000
Kem Trị Mụn, Mờ Thâm, Trẻ Hóa Da Obagi Tretinoin 0.05% Cream loại Cream sửa giá 1400000
Kem Trị Mụn, Mờ Thâm, Trẻ Hóa Da Obagi Tretinoin 0.05% Cream phân loại Gel khuyến mãi 1200000
Kem chống nắng TreaMax Sunscreen chỉnh tồn kho hết hàng
Kem chống nắng TreaMax Sunscreen cho có hàng
Đăng bài viết sản phẩm Kem chống nắng TreaMax Sunscreen
Viết bài cho Gel Trị Mụn Deriva Bpo Gel
Xóa sản phẩm Gel Trị Mụn Deriva Bpo Gel
tìm thông tin serum trị nám
ping
```

Các thao tác sửa giá, tồn kho, cập nhật mô tả sản phẩm luôn cần xác nhận. Với sản phẩm có biến thể, hãy ghi rõ `loại`, `phân loại` hoặc `biến thể`, ví dụ `loại Cream` hoặc `phân loại Gel`. Nếu không ghi rõ, bot sẽ liệt kê các biến thể hiện có để chọn:

```text
xác nhận
```

Hủy thao tác:

```text
hủy
```

## Đồng bộ và đăng sản phẩm từ Notion

Quy trình đăng sản phẩm mới từ Notion:

1. **Chuẩn bị trên Notion:**
   - Tạo dòng sản phẩm trên Notion. Điền **Tên sản phẩm**, **Từ khóa SEO Rank Math**, và dán URL Google Drive chứa ảnh sản phẩm ở cột **Media sản phẩm** (phải share quyền xem công khai).
   - Chọn liên kết ở cột **Bài content Tây** trỏ đến trang con chứa nội dung bài viết.
   - Trong trang **Bài content Tây**, dòng đầu tiên phải ghi rõ thông tin danh mục, giá bán và giá khuyến mãi (nếu có) theo định dạng:
     `Danh mục sản phẩm: [Tên danh mục] - Giá: [Giá thường] - Giá khuyến mãi: [Giá khuyến mãi]`
     *(Ví dụ: Danh mục sản phẩm: Trị mụn - Giá: 750.000 - Giá khuyến mãi: 710.000)*
   - Chuyển **Trạng thái** (Status) của dòng sang **`Báo IT đăng`**.

2. **Kích hoạt đồng bộ:**
   - Nhắn tin lệnh: `đồng bộ notion` (hoặc `sync notion`, `dong bo notion`) cho bot Telegram.
   - Bot sẽ tự động tải các ảnh từ Google Drive, upload lên WordPress, tạo sản phẩm WooCommerce kèm SEO Rank Math (lấy tối đa 4 từ khóa đầu tiên), sau đó tự chuyển trạng thái Notion sang `Đã đăng web`, tích chọn `IT đã đăng` và cập nhật `Link web`.

## Báo cáo sản phẩm

Khi hỏi về tổng sản phẩm, sản phẩm có hàng, hết hàng, hoặc xuất tất cả sản phẩm, bot chỉ gửi file Excel `.xlsx` kèm caption ngắn. File gồm 4 cột:

- ID
- Tên sản phẩm
- Giá
- Tình trạng

## Chạy tự động khi mở Windows

Nhấn `Win + R`, nhập:

```text
shell:startup
```

Copy shortcut của `start_telegram_bot.bat` vào thư mục Startup.

## Kết nối Google Site Kit / GA4 / Search Console

Bot không đọc trực tiếp màn hình Site Kit trong WordPress. Bot đọc cùng nguồn dữ liệu bằng Google API:

- GA4 qua Google Analytics Data API.
- Search Console qua Search Console API.

### 1. Cài thư viện Google trên máy chạy bot

Chạy một lần trong thư mục bot:

```powershell
python -m pip install google-auth google-auth-oauthlib requests
```

Nếu GA4 không cho thêm email service account, dùng OAuth Gmail theo các bước dưới đây.

### Cách 1: OAuth Gmail cho GA4 và Search Console

1. Vào Google Cloud Console.
2. Chọn đúng Project.
3. Vào `APIs & Services` -> `Enabled APIs & services`.
4. Bật `Google Analytics Data API`.
5. Bật `Google Search Console API`.
6. Vào `APIs & Services` -> `OAuth consent screen`.
7. Chọn loại app `External`, điền thông tin bắt buộc rồi lưu.
8. Vào `APIs & Services` -> `Credentials`.
9. Bấm `Create credentials` -> `OAuth client ID`.
10. Chọn `Desktop app`.
11. Tải file JSON về.
12. Copy file JSON vào thư mục bot và đổi tên thành:

```text
google-oauth-client.json
```

Thêm vào `telegram_bot.env`:

```env
GOOGLE_AUTH_MODE=oauth
GOOGLE_OAUTH_CLIENT_SECRET_JSON=google-oauth-client.json
GOOGLE_OAUTH_TOKEN_JSON=google-oauth-token.json
GA4_PROPERTY_ID=123456789
GSC_SITE_URL=https://khaihoanderma.com/
```

Chạy một lần để đăng nhập Gmail đang có quyền GA4/Search Console:

```powershell
python setup_google_oauth.py
```

Trình duyệt sẽ mở ra. Đăng nhập đúng Gmail đang xem được GA4 và Search Console, bấm cho phép. Sau khi xong, bot sẽ tạo file:

```text
google-oauth-token.json
```

Từ lần sau bot dùng token này tự đọc số liệu, không cần đăng nhập lại.

### Cách 2: Service Account

Trong Google Cloud:

1. Tạo hoặc chọn một Project.
2. Bật `Google Analytics Data API`.
3. Bật `Google Search Console API`.
4. Vào `IAM & Admin` -> `Service Accounts`.
5. Tạo service account mới.
6. Tạo JSON key và tải file `.json` về.
7. Copy file JSON vào cùng thư mục bot, ví dụ:

```text
E:\khaihoan-telegram-bot\google-service-account.json
```

### 3. Cấp quyền cho email Service Account

Mở file JSON, lấy dòng `client_email`, ví dụ:

```text
bot-sitekit@project-id.iam.gserviceaccount.com
```

Cấp email này vào:

- GA4: `Admin` -> `Property access management` -> `Add user` -> quyền `Viewer` hoặc `Analyst`.
- Search Console: `Settings` -> `Users and permissions` -> `Add user` -> quyền `Restricted` hoặc `Full`.

### 4. Thêm cấu hình vào `telegram_bot.env`

```env
GOOGLE_SERVICE_ACCOUNT_JSON=google-service-account.json
GA4_PROPERTY_ID=123456789
GSC_SITE_URL=https://khaihoanderma.com/
```

`GA4_PROPERTY_ID` là ID dạng số của property GA4, không phải Measurement ID dạng `G-XXXX`.

Sau khi lưu file, tắt cửa sổ bot rồi chạy lại `start_telegram_bot.bat`.

### 5. Câu chat mẫu

```text
traffic 28 ngày qua
site kit 7 ngày qua
từ khóa search console 28 ngày qua
phân tích từ khóa đang top Google 7 ngày qua
liệt kê từ khóa đang nằm top tìm kiếm Google 30 ngày qua
phân tích các từ khóa top Google của web 90 ngày qua
lượt truy cập hôm nay
```

## Bảo mật

Không gửi `telegram_bot.env` cho người khác nếu trong đó có token/key thật.
Nếu token hoặc WooCommerce key đã lộ, hãy revoke và tạo lại.
