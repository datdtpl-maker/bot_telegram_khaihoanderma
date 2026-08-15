# Prompt bàn giao cho Antigravity

Hãy mở và làm việc trực tiếp trong dự án:

`C:\Users\datdt\Documents\Codex\2026-05-07\bot khaihoanderma`

## Mục tiêu

Đọc toàn bộ thay đổi hiện có, kiểm tra lại, sau đó commit và push lên GitHub hiện tại của dự án. Không tự sửa hoặc hoàn nguyên những thay đổi không liên quan.

## Phạm vi thay đổi đã thực hiện

### 1. Luồng đồng bộ Notion sang WooCommerce

File chính: `notion_sync.py`

- Sắp xếp ảnh theo số tự nhiên: `1, 2, 3, ... 10, 11`.
- Bắt buộc bộ ảnh liên tiếp bắt đầu từ số `1`; ảnh số `1` là ảnh đại diện.
- Từ chối trường hợp trùng số ảnh khác phần mở rộng, ví dụ `1.jpg` và `1.png`.
- Không cho lấy ảnh nằm trong thư mục con của thư mục Drive sản phẩm.
- Xác thực magic bytes và MIME thật của PNG, JPG và WebP.
- Chuẩn hóa tên file upload thành ASCII an toàn.
- Dùng thư mục tạm riêng cho từng sản phẩm để tránh trộn ảnh.
- Kiểm tra bắt buộc tên, mô tả, danh mục, giá lớn hơn 0 và đầy đủ ảnh.
- Giá khuyến mãi phải nhỏ hơn giá thường.
- Nếu upload ảnh hoặc cập nhật Alt/Title lỗi thì không publish.
- Tạo sản phẩm ở trạng thái `draft`, kiểm tra đúng ID và thứ tự ảnh rồi mới `publish`.
- Rollback bản nháp và media vừa upload nếu lỗi xảy ra trước khi publish.
- Dùng SKU ổn định `notion-{page_id}` để chống đăng trùng và phục hồi khi API timeout.
- Lưu metadata kiểm chứng `_khd_notion_page_id`, `_khd_expected_media_ids`, `_khd_sync_verified` và `_khd_sync_attempt_id`.
- Mỗi lượt đồng bộ có `sync_attempt_id` riêng, tránh một máy xóa nhầm sản phẩm do máy khác vừa tạo.
- Chỉ coi là thành công khi WooCommerce thực sự trả trạng thái `publish`.
- Phân trang đầy đủ Notion database và content blocks.
- Đọc được danh mục và giá khi nằm ở các paragraph riêng.
- Có khóa thread và khóa file để chặn hai lượt chạy đồng thời trên cùng máy.

### 2. Luồng xác nhận Telegram

File: `telegram_woocommerce_bot.py`

- Chu kỳ kiểm tra Notion là 15 phút; chu kỳ kiểm tra đánh giá là 30 phút.
- Đồng bộ thủ công cố định danh sách Notion Page trước khi người dùng xác nhận.
- Background poll gom các sản phẩm chờ đăng vào một yêu cầu xác nhận theo lô.
- Không làm thay đổi danh sách page trong lúc chờ xác nhận.

### 3. Script khởi động

Các file:

- `start_telegram_bot.bat`
- `run_telegram_bot.ps1`

Script mới:

- Luôn chạy từ đúng thư mục chứa bot.
- Thiết lập UTF-8 cho Windows Console và Python.
- Kiểm tra Python 3.10 trở lên.
- Kiểm tra các file và biến môi trường bắt buộc.
- Kiểm tra dependencies trước khi chạy.
- Có chế độ chỉ kiểm tra cấu hình:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_telegram_bot.ps1 -ValidateOnly
```

- Không khởi động trùng một bot trên cùng máy.
- Dừng sau 5 lần crash nhanh liên tiếp thay vì restart vô hạn.

### 4. Tài liệu, cấu hình và kiểm thử

Các file đã thêm hoặc cập nhật:

- `README.md`
- `HUONG_DAN_DANG_BAI_WEB.md`
- `requirements.txt`
- `telegram_bot.env.example`
- `.gitignore`
- `tests/test_notion_sync.py`
- `tests/test_telegram_notion_flow.py`

## Việc Antigravity cần thực hiện

1. Đọc `README.md` và `HUONG_DAN_DANG_BAI_WEB.md` để hiểu quy trình mới.
2. Chạy `git status --short` và xem diff hiện tại; không revert thay đổi đã có.
3. Kiểm tra chắc chắn các file bí mật và file sinh ra khi chạy không được stage:
   - `telegram_bot.env`
   - Google OAuth client/token chứa thông tin thật
   - `bot.log`
   - `__pycache__/`
   - `temp_notion_images/`
   - `telegram_uploads/`
   - báo cáo Excel hoặc file xuất tạm
4. Chạy kiểm thử:

```powershell
python -m unittest discover -s tests -v
python -m py_compile notion_sync.py telegram_woocommerce_bot.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_telegram_bot.ps1 -ValidateOnly
git diff --check
```

5. Kết quả gần nhất trước bàn giao là `19/19 tests PASS`, Python syntax PASS, PowerShell parser PASS và `STARTUP_VALIDATION=PASS`.
6. Không chạy bot thật và không gọi API đăng sản phẩm thật chỉ để kiểm tra, trừ khi chủ dự án yêu cầu rõ ràng.
7. Không đưa thư mục `.codegraph/` lên GitHub nếu repository hiện tại không chủ ý quản lý dữ liệu CodeGraph. Kiểm tra `.gitignore` hoặc bỏ nó khỏi staging.
8. File `PROMPT_ANTIGRAVITY_PUBLISH_GITHUB.md` chỉ là tài liệu bàn giao cho Antigravity, mặc định không stage hoặc publish file này lên GitHub.
9. Chỉ stage đúng source code, tài liệu, cấu hình mẫu và tests thuộc thay đổi này.
10. Trước khi commit, hiển thị danh sách file sẽ commit để chủ dự án kiểm tra.
11. Nếu mọi kiểm tra đều đạt, commit với nội dung gợi ý:

```text
Harden Notion WooCommerce product sync workflow
```

12. Push lên đúng remote và đúng branch đang được dự án sử dụng; không force-push và không đổi lịch sử Git.
13. Sau khi push, báo lại branch, commit hash, remote URL, danh sách file đã publish và kết quả test.

## Yêu cầu bảo mật quan trọng

- Tuyệt đối không in hoặc đưa Telegram token, Notion token, mật khẩu WordPress, WooCommerce key/secret hay Google OAuth token vào chat, log commit hoặc GitHub.
- `telegram_bot.env.example` chỉ được chứa giá trị mẫu, không chứa khóa thật.
- Nếu phát hiện secret đã được stage, phải dừng publish, bỏ file đó khỏi staging và báo lại trước khi tiếp tục.
- Không dùng `git reset --hard`, `git clean`, force-push hoặc lệnh phá hủy dữ liệu.
