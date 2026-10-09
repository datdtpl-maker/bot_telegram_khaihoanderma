# 🚀 KHẢI HOÀN DERMA - TELEGRAM BOT & MINI APP HANDOFF GUIDE

Tài liệu này dành riêng cho AI Agent (hoặc kỹ thuật viên) trên máy tool (`E:\khaihoan-telegram-bot`) để đồng bộ mã nguồn mới nhất từ GitHub, cài đặt môi trường và vận hành hệ thống tự động 100%.

---

## 📌 1. TỔNG QUAN HỆ THỐNG & CƠ CHẾ HOẠT ĐỘNG

1. **Telegram WooCommerce Bot (`telegram_woocommerce_bot.py`)**:
   - Tự động duyệt đánh giá khách hàng (kiểm duyệt thông minh bằng Gemini AI).
   - Tự động quét Notion và đồng bộ sản phẩm vào WooCommerce (`notion_sync.py`).
   - Báo cáo doanh thu hôm nay / tháng này, danh sách đơn hàng, xuất Excel.
   - Nhận diện lệnh thông minh tiếng Việt không dấu, fuzzy match sản phẩm & biến thể.
   - Singleton socket (cổng `52365`) tự động dọn dẹp phiên bản cũ chạy ngầm.

2. **Telegram Mini App Server (`mini_app_server.py`)**:
   - Backend FastAPI + Uvicorn chạy ngầm trên luồng daemon (cổng `8088`).
   - Xác thực bảo mật: HMAC-SHA256 kiểm tra Telegram `initData` khớp với `TELEGRAM_BOT_TOKEN`, đối chiếu danh sách `TELEGRAM_ALLOWED_CHAT_IDS`. Có fallback token (`MINI_APP_ADMIN_TOKEN`) cho trình duyệt.
   - Giao diện SPA Vue 3 + Tailwind CSS (`mini_app/index.html`, `mini_app/app.js`, `mini_app/style.css`) gồm 5 tab: Dashboard, Orders, Products (sửa giá / bật tắt kho nhanh), Reviews (duyệt/xóa 1-click), Tools & AI (đồng bộ Notion, trợ lý Gemini 3.8 Flash).

3. **Cơ chế Cloudflare Tunnel Tự Động 100% (Zero-Configuration)**:
   - Bot tự động phát hiện `cloudflared` (trong PATH hệ thống hoặc file `cloudflared.exe` cục bộ).
   - Tự động mở tunnel ngầm không hiển thị cửa sổ đen: `cloudflared tunnel --url http://127.0.0.1:8088`.
   - Tự động trích xuất link `https://xxxx.trycloudflare.com` trực tiếp từ tiến trình.
   - Tự động cập nhật link vào `telegram_bot.env` (`MINI_APP_URL`).
   - Tự động cấu hình Telegram Bot Menu Button (`setChatMenuButton`) cho toàn bot và cho từng admin.
   - Tự động gửi tin nhắn chào mừng kèm nút bấm WebApp trực tiếp đến admin Telegram.
   - Watchdog thread giám sát và tự phục hồi tunnel nếu kết nối mạng gián đoạn.

---

## 🛠️ 2. QUY TRÌNH THỰC THI CHO AGENT TRÊN MÁY TOOL (`E:\khaihoan-telegram-bot`)

Agent trên máy tool chỉ cần mở PowerShell/Terminal tại thư mục `E:\khaihoan-telegram-bot` và chạy các bước sau:

### Bước 1: Dừng các tiến trình bot & tunnel cũ
```powershell
cmd.exe /c Dung_Bot.bat
```
Hoặc cưỡng chế dừng bằng PowerShell:
```powershell
Stop-Process -Name python, cloudflared -Force -ErrorAction SilentlyContinue
```

### Bước 2: Đồng bộ mã nguồn mới nhất từ GitHub
```powershell
git fetch origin main
git reset --hard origin/main
```
*(⚠️ Lưu ý: File `telegram_bot.env` đã được cấu hình trong `.gitignore` nên an toàn và không bị ghi đè).*

### Bước 3: Cài đặt / cập nhật đầy đủ thư viện Python
```powershell
python -m pip install -r requirements.txt
```

### Bước 4: Kiểm tra Cloudflare CLI
Kiểm tra xem máy đã nhận lệnh `cloudflared` chưa:
```powershell
where.exe cloudflared
```
- Nếu đã có: Sang Bước 5.
- Nếu chưa có: Chạy file `Cai_Dat_Cloudflared.bat` để tự động tải `cloudflared.exe` về thư mục bot.

### Bước 5: Chạy bộ kiểm thử tự động (Unit Tests)
Đảm bảo 100% 40 test cases đều PASS:
```powershell
python -m unittest discover -s tests -v
```

### Bước 6: Khởi động hệ thống
- **Chạy kiểm tra có log trực quan (khuyến nghị cho lần chạy đầu tiên):**
  ```powershell
  cmd.exe /c start_telegram_bot.bat
  ```
  Quan sát màn hình console:
  1. `Đã khởi động Telegram Mini App Server tại http://0.0.0.0:8088`
  2. `Cloudflare Tunnel tự động thành công: https://xxxx.trycloudflare.com`
  3. `Kết nối Telegram OK`
  4. `Đã đặt Menu Button mặc định cho Mini App`
  5. `Đã gửi tin nhắn mở Mini App tới admin`

- **Chạy ngầm 24/7 (vận hành chính thức):**
  ```powershell
  wscript.exe Chay_Ngam_Bot.vbs
  ```

---

## 📱 3. KIỂM TRA TRÊN TELEGRAM

1. Mở ứng dụng Telegram trên điện thoại/máy tính, vào khung chat với bot Khải Hoàn Derma.
2. Bot sẽ tự gửi tin nhắn mới nhất: `🟢 Bot Khải Hoàn Derma & Mini App đã sẵn sàng!` kèm nút `📱 Mở Bảng Điều Khiển (Mini App)`.
3. Bấm vào nút đó (hoặc bấm nút **Menu** ở góc dưới bên trái khung chat, hoặc gõ lệnh `/app`).
4. Giao diện Mini App mở lên mượt mà, sẵn sàng quản trị.

---

## 📁 4. DANH SÁCH FILE TRỌNG YẾU

| Tên File | Vai trò |
| :--- | :--- |
| `telegram_woocommerce_bot.py` | Core bot Telegram + Quản lý Cloudflare Tunnel ngầm + Notion sync worker |
| `mini_app_server.py` | FastAPI backend server phục vụ API & giao diện Mini App (port 8088) |
| `mini_app/` | Toàn bộ mã nguồn giao diện SPA Telegram Mini App (HTML, CSS, JS) |
| `notion_sync.py` | Core engine đồng bộ bài viết / sản phẩm từ Notion sang WooCommerce |
| `run_telegram_bot.ps1` | Engine PowerShell khởi chạy bot có auto-recovery và auto-pip-install |
| `start_telegram_bot.bat` | File chạy bot kèm console log kiểm tra lỗi |
| `Chay_Ngam_Bot.vbs` | 1-click chạy ngầm bot hoàn toàn không có cửa sổ |
| `Dung_Bot.bat` | 1-click tắt toàn bộ tiến trình bot, giải phóng cổng 52365, 8088 và cloudflared |
| `Cai_Dat_Cloudflared.bat` | 1-click tải cloudflared.exe nếu máy chưa cài |
| `Bat_Tu_Dong_Chay_Khi_Mo_May.bat` | Đăng ký tự động chạy ngầm bot khi mở máy tính Windows |
| `Tat_Tu_Dong_Chay_Khi_Mo_May.bat` | Gỡ bỏ tự động chạy khi mở máy |
| `telegram_bot.env` | File cấu hình bí mật (Token, Password, API Key) - **KHÔNG PUSH GIT** |
| `tests/` | Bộ kiểm thử tự động gồm 40 unit test cases |
