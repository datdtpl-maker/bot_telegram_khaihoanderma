import os
import re
import sys
import json
import base64
import html
import urllib.request
import urllib.error
import urllib.parse
import time
import shutil
import tempfile
import threading
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Setup encoding for Windows Console compatibility
try:
    if sys.stdout.encoding != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8')
    if sys.stderr.encoding != 'utf-8':
        sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

import socket
def _force_ipv4_dns():
    try:
        _orig_getaddrinfo = socket.getaddrinfo
        def _getaddrinfo_ipv4(host, port, family=0, type=0, proto=0, flags=0):
            return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
        socket.getaddrinfo = _getaddrinfo_ipv4
    except Exception:
        pass
_force_ipv4_dns()


OUT_DIR = Path(__file__).resolve().parent
LOG_FILE = OUT_DIR / "bot.log"
NOTION_SYNC_LOCK = threading.Lock()


class ProcessFileLock:
    def __init__(self, path):
        self.path = Path(path)
        self.handle = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self):
        if not self.handle:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


NOTION_SYNC_PROCESS_LOCK = ProcessFileLock(OUT_DIR / ".notion_sync.lock")


class WorkflowValidationError(ValueError):
    """Dữ liệu không đủ an toàn để công khai sản phẩm."""


@dataclass(frozen=True)
class ImageUploadInfo:
    path: Path
    mime_type: str
    filename: str


def _natural_text_key(value):
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)]


def sort_product_images(images, root_dir):
    root = Path(root_dir)

    def sort_key(path):
        image_path = Path(path)
        try:
            relative = image_path.relative_to(root)
        except ValueError:
            relative = image_path
        return (len(relative.parts), _natural_text_key(relative.as_posix()))

    return sorted((Path(path) for path in images), key=sort_key)


def validate_image_sequence(images, root_dir):
    root = Path(root_dir)
    if not images:
        raise WorkflowValidationError("Không tìm thấy file ảnh PNG/JPG/WebP nào trong thư mục Google Drive.")
    
    numeric_stems = []
    seen_stems = set()
    
    for image_path in images:
        path = Path(image_path)
        try:
            relative = path.relative_to(root)
        except ValueError as exc:
            raise WorkflowValidationError(f"Ảnh nằm ngoài thư mục sản phẩm: {path}") from exc
        if len(relative.parts) != 1:
            raise WorkflowValidationError(
                f"Ảnh phải nằm trực tiếp trong thư mục Drive của sản phẩm (không để trong thư mục con): {relative.as_posix()}"
            )
        
        stem = path.stem.lower()
        if stem in seen_stems:
            raise WorkflowValidationError("Mỗi số ảnh chỉ được dùng một lần; không để đồng thời 1.jpg và 1.png.")
        seen_stems.add(stem)

        if stem.isdigit():
            numeric_stems.append(int(stem))

    if len(numeric_stems) == len(images):
        expected = list(range(1, len(numeric_stems) + 1))
        if sorted(numeric_stems) != expected:
            raise WorkflowValidationError(
                f"Ảnh dạng số phải là dãy liên tiếp từ 1 đến {len(numeric_stems)}; hiện có {sorted(numeric_stems)}."
            )


def _ascii_slug(value):
    normalized = unicodedata.normalize("NFKD", value.replace("đ", "d").replace("Đ", "D"))
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-") or "image"


def inspect_image_for_upload(file_path):
    path = Path(file_path)
    header = path.read_bytes()[:16]
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        mime_type, expected_suffix = "image/png", ".png"
    elif header.startswith(b"\xff\xd8\xff"):
        mime_type, expected_suffix = "image/jpeg", ".jpg"
    elif len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        mime_type, expected_suffix = "image/webp", ".webp"
    else:
        raise WorkflowValidationError(f"File không phải PNG/JPG/WebP hợp lệ: {path.name}")

    actual_suffix = path.suffix.lower()
    valid_suffixes = {".jpg", ".jpeg"} if expected_suffix == ".jpg" else {expected_suffix}
    if actual_suffix not in valid_suffixes:
        log_message(
            f"Tự động chuẩn hóa đuôi ảnh {path.name} thành {expected_suffix} theo định dạng thực tế ({mime_type})"
        )

    return ImageUploadInfo(
        path=path,
        mime_type=mime_type,
        filename=f"{_ascii_slug(path.stem)}{expected_suffix}",
    )

def optimize_single_image(img_path, max_dim=1600, quality=88):
    """
    Tự động tối ưu hóa ảnh trước khi upload:
    - Nếu có Pillow, chuyển đổi sang chuẩn WebP chất lượng cao (quality=88),
      giảm dung lượng file từ ~2.7MB xuống còn ~170KB (nhẹ hơn 15x).
    - Giới hạn kích thước tối đa 1600px chuẩn cho e-commerce / mobile web.
    - Giữ nguyên độ trong suốt (alpha) nếu là ảnh không nền PNG.
    - Giữ nguyên số thứ tự 1, 2, 3... trong tên file để đảm bảo gallery thứ tự.
    - Nếu gặp lỗi hoặc không có Pillow, giữ nguyên file gốc không làm gián đoạn bot.
    """
    try:
        from PIL import Image
    except ImportError:
        return img_path

    try:
        orig_size = img_path.stat().st_size
        with Image.open(img_path) as im:
            w, h = im.size
            if max(w, h) > max_dim:
                scale = max_dim / max(w, h)
                new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
                im = im.resize((new_w, new_h), Image.Resampling.LANCZOS)

            # Xử lý màu sắc và kênh alpha trong suốt
            if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                im = im.convert("RGBA")
            else:
                im = im.convert("RGB")

            target_path = img_path.with_suffix(".webp")
            im.save(target_path, format="WEBP", quality=quality, method=4)
            new_size = target_path.stat().st_size

            # Xóa file cũ nếu đã đổi sang .webp để temp_dir không bị trùng số
            if target_path.resolve() != img_path.resolve() and img_path.exists():
                try:
                    img_path.unlink()
                except Exception:
                    pass

            ratio = orig_size / max(new_size, 1)
            log_message(
                f"Tối ưu ảnh {img_path.name} -> {target_path.name}: "
                f"{orig_size/1024:.1f} KB -> {new_size/1024:.1f} KB (nhẹ hơn {ratio:.1f}x) chuẩn WebP."
            )
            return target_path
    except Exception as exc:
        log_message(f"Không thể tối ưu ảnh {img_path.name} sang WebP ({exc}). Giữ nguyên file gốc.")
        return img_path


def optimize_product_images(downloaded_images, temp_dir, max_dim=1600, quality=88):
    """
    Tối ưu hóa toàn bộ danh sách ảnh song song bằng đa luồng, hoàn tất trong ~1-2 giây.
    """
    if not downloaded_images:
        return []

    workers = min(4, len(downloaded_images))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        optimized = list(executor.map(lambda p: optimize_single_image(p, max_dim, quality), downloaded_images))

    return sort_product_images(optimized, temp_dir)



def validate_product_for_publish(title, description, category, regular_price, sale_price, image_count):
    if not str(title or "").strip() or title == "Sản phẩm không tên":
        raise WorkflowValidationError("Thiếu tên sản phẩm.")
    if not str(description or "").strip():
        raise WorkflowValidationError("Thiếu nội dung mô tả sản phẩm.")
    if not str(category or "").strip():
        raise WorkflowValidationError("Thiếu danh mục sản phẩm.")
    try:
        regular_price_value = int(float(regular_price or 0))
        sale_price_value = int(float(sale_price or 0))
    except (TypeError, ValueError) as exc:
        raise WorkflowValidationError("Giá sản phẩm không hợp lệ.") from exc
    if regular_price_value <= 0:
        raise WorkflowValidationError("Giá thường phải lớn hơn 0.")
    if sale_price_value > 0 and sale_price_value >= regular_price_value:
        raise WorkflowValidationError("Giá khuyến mãi phải nhỏ hơn giá thường.")
    if int(image_count or 0) <= 0:
        raise WorkflowValidationError("Phải có ít nhất một ảnh; ảnh số 1 là ảnh đại diện.")


def build_notion_sku(page_id):
    normalized = re.sub(r"[^a-zA-Z0-9]", "", str(page_id or "")).lower()
    if not normalized:
        raise WorkflowValidationError("Notion page ID không hợp lệ.")
    return f"notion-{normalized}"

def load_config():
    env_path = OUT_DIR / "telegram_bot.env"
    config = {}
    if env_path.exists():
        with env_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    parts = line.split("=", 1)
                    if len(parts) == 2:
                        config[parts[0].strip()] = parts[1].strip()
    return config

def log_message(msg):
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{timestamp}] [NotionSync] {msg}"
    print(line, flush=True)
    if "unittest" in sys.modules or os.environ.get("TESTING") == "1":
        return
    try:
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        pass

def rich_text_to_html(rich_text_list):
    html_out = ""
    for r in rich_text_list:
        text_content = r.get("plain_text", "")
        annotations = r.get("annotations", {})
        href = r.get("href")
        
        wrapped = html.escape(text_content)
        if annotations.get("bold"):
            wrapped = f"<strong>{wrapped}</strong>"
        if annotations.get("italic"):
            wrapped = f"<em>{wrapped}</em>"
        if annotations.get("code"):
            wrapped = f"<code>{wrapped}</code>"
        if annotations.get("strikethrough"):
            wrapped = f"<s>{wrapped}</s>"
        if annotations.get("underline"):
            wrapped = f"<u>{wrapped}</u>"
        if href:
            wrapped = f'<a href="{html.escape(href)}">{wrapped}</a>'
        html_out += wrapped
    return html_out


def _parse_money_value(raw_value):
    value = str(raw_value or "").strip().lower()
    multiplier = 1000 if value.endswith("k") else 1
    if multiplier == 1000:
        value = value[:-1]
    digits = re.sub(r"[^\d]", "", value)
    return int(digits) * multiplier if digits else 0


def _extract_catalog_metadata(plain_text):
    category = ""
    regular_price = 0
    sale_price = 0
    matched = False

    parts = [p.strip() for p in re.split(r"\s+-\s+|\n", plain_text) if p.strip()]
    for part in parts:
        part_lower = part.lower()
        if "danh mục" in part_lower:
            if ":" in part:
                cat_val = part.split(":", 1)[1].strip(" -")
                if cat_val:
                    category = cat_val
                    matched = True
        elif "giá khuyến mãi" in part_lower or "giá km" in part_lower:
            if ":" in part:
                val = _parse_money_value(part.split(":", 1)[1])
                if val > 0:
                    sale_price = val
                    matched = True
        elif "giá" in part_lower:
            if ":" in part:
                val = _parse_money_value(part.split(":", 1)[1])
                if val > 0:
                    regular_price = val
                    matched = True

    if not category:
        cat_m = re.search(r"danh mục(?:\s+sản phẩm)?\s*:\s*([^-\n\r]+)", plain_text, flags=re.IGNORECASE)
        if cat_m:
            category = cat_m.group(1).strip(" -")
            if category:
                matched = True

    return category, regular_price, sale_price, matched


def parse_notion_blocks(blocks):
    html_parts = []
    in_bullet_list = False
    in_numbered_list = False
    
    category_name = ""
    price_val = 0
    sale_price_val = 0
    
    for block in blocks:
        b_type = block.get("type")
        
        if b_type != "bulleted_list_item" and in_bullet_list:
            html_parts.append("</ul>")
            in_bullet_list = False
        if b_type != "numbered_list_item" and in_numbered_list:
            html_parts.append("</ol>")
            in_numbered_list = False
            
        if b_type == "paragraph":
            # Lấy plain_text sạch không chứa thẻ HTML để parse danh mục và giá chính xác
            plain_text = "".join([r.get("plain_text", "") for r in block["paragraph"]["rich_text"]])
            parsed_category, parsed_price, parsed_sale, is_metadata = _extract_catalog_metadata(plain_text)
            if is_metadata:
                if parsed_category:
                    category_name = parsed_category
                if parsed_price:
                    price_val = parsed_price
                if parsed_sale:
                    sale_price_val = parsed_sale
                continue
            text = rich_text_to_html(block["paragraph"]["rich_text"])
            html_parts.append(f"<p>{text}</p>")
            
        elif b_type == "heading_1":
            text = rich_text_to_html(block["heading_1"]["rich_text"])
            html_parts.append(f"<h1>{text}</h1>")
        elif b_type == "heading_2":
            text = rich_text_to_html(block["heading_2"]["rich_text"])
            html_parts.append(f"<h2>{text}</h2>")
        elif b_type == "heading_3":
            text = rich_text_to_html(block["heading_3"]["rich_text"])
            html_parts.append(f"<h3>{text}</h3>")
            
        elif b_type == "bulleted_list_item":
            if not in_bullet_list:
                html_parts.append("<ul>")
                in_bullet_list = True
            text = rich_text_to_html(block["bulleted_list_item"]["rich_text"])
            html_parts.append(f"<li>{text}</li>")
            
        elif b_type == "numbered_list_item":
            if not in_numbered_list:
                html_parts.append("<ol>")
                in_numbered_list = True
            text = rich_text_to_html(block["numbered_list_item"]["rich_text"])
            html_parts.append(f"<li>{text}</li>")
            
        elif b_type == "divider":
            html_parts.append("<hr />")
            
    if in_bullet_list:
        html_parts.append("</ul>")
    if in_numbered_list:
        html_parts.append("</ol>")
        
    return "\n".join(html_parts), category_name, price_val, sale_price_val


def _urlopen_with_retry(req, timeout=20, retries=3):
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(1.5)
    raise last_err

def query_notion_pages_to_post(token, db_id):
    url = f"https://api.notion.com/v1/databases/{db_id}/query"
    base_body = {
        "filter": {
            "property": "Trạng thái",
            "select": {
                "equals": "Báo IT đăng"
            }
        },
        "page_size": 100,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    results = []
    cursor = None
    while True:
        request_body = dict(base_body)
        if cursor:
            request_body["start_cursor"] = cursor
        req = urllib.request.Request(
            url,
            headers=headers,
            method="POST",
            data=json.dumps(request_body).encode("utf-8"),
        )
        raw_resp = _urlopen_with_retry(req, timeout=20, retries=3)
        data = json.loads(raw_resp.decode("utf-8"))
        results.extend(data.get("results", []))
        if not data.get("has_more") or not data.get("next_cursor"):
            return results
        cursor = data["next_cursor"]

def get_page_blocks(token, page_id):
    base_url = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100"
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    results = []
    cursor = None
    while True:
        url = base_url
        if cursor:
            url += "&start_cursor=" + urllib.parse.quote(cursor)
        req = urllib.request.Request(url, headers=headers, method="GET")
        raw_resp = _urlopen_with_retry(req, timeout=20, retries=3)
        data = json.loads(raw_resp.decode("utf-8"))
        results.extend(data.get("results", []))
        if not data.get("has_more") or not data.get("next_cursor"):
            return results
        cursor = data["next_cursor"]

def _clean_drive_folder_url(url):
    match = re.search(r"folders/([a-zA-Z0-9_-]+)", str(url or ""))
    if match:
        return f"https://drive.google.com/drive/folders/{match.group(1)}"
    return str(url or "").strip()

_DRIVE_FOLDER_CACHE = {}


def get_drive_folder_files(folder_url):
    clean_url = _clean_drive_folder_url(folder_url)
    if clean_url in _DRIVE_FOLDER_CACHE:
        return _DRIVE_FOLDER_CACHE[clean_url]
    import gdown
    folder_files = gdown.download_folder(url=clean_url, skip_download=True, quiet=True)
    if folder_files:
        _DRIVE_FOLDER_CACHE[clean_url] = folder_files
    return folder_files



def scan_drive_folder_videos(folder_url):
    """
    Quét và lấy danh sách file video trong thư mục Google Drive (nếu có).
    Hỗ trợ .mov, .mp4, .webm, .m4v, .avi, .mkv.
    """
    import gdown
    clean_url = _clean_drive_folder_url(folder_url)
    video_extensions = {".mov", ".mp4", ".webm", ".m4v", ".avi", ".mkv"}
    found_videos = []
    try:
        folder_files = get_drive_folder_files(clean_url)
        if folder_files:
            for f in folder_files:
                f_name = Path(f.path).name
                if Path(f_name).suffix.lower() in video_extensions:
                    found_videos.append({"id": f.id, "name": f_name})
    except Exception as e:
        log_message(f"Không thể quét danh sách video từ Google Drive: {e}")

    # Sắp xếp theo tên tự nhiên (ví dụ 7.MOV, 8.MOV)
    return sorted(found_videos, key=lambda x: _natural_text_key(x.get("name", "")))


def build_drive_video_gallery_script(video_files):
    """
    Tích hợp video Google Drive thành 1 slide riêng (cell) nằm trong chuỗi trượt ảnh (slider gallery)
    của WooCommerce / Flatsome.
    - Video trở thành 1 khối slide độc lập trong gallery (.woocommerce-product-gallery__image.slide.khd-video-slide).
    - Tự động đồng bộ với thanh thumbnail (.product-thumbnails), hỗ trợ đầy đủ Desktop, iPad và Mobile.
    - Không làm vỡ layout, không can thiệp ẩn/hiện slider gốc, không gây lỗi Flickity.
    - Bảo toàn 100% qua bộ lọc WordPress wpautop/wptexturize nhờ Base64 encoding.
    """
    if not video_files:
        return ""

    first_vid = None
    for v in video_files:
        if v.get("id"):
            first_vid = v
            break
    if not first_vid:
        return ""

    vid = first_vid["id"]
    view_url = f"https://drive.google.com/file/d/{vid}/view"
    preview_url = f"https://drive.google.com/file/d/{vid}/preview"

    # Slide container 1:1 aspect ratio with Google Drive preview player
    slide_html = (
        '<div class="woocommerce-product-gallery__image slide khd-video-slide" style="position:relative;width:100%;box-sizing:border-box;">'
        f'<div data-drive-view="{view_url}" style="position:relative;width:100%;padding-top:100%;background:#000;overflow:hidden;border-radius:4px;">'
        f'<iframe id="khd-gallery-iframe" src="{preview_url}" style="position:absolute;top:-54px;left:0;width:100%;height:calc(100% + 54px);border:0;" allow="autoplay; fullscreen; encrypted-media; picture-in-picture" allowfullscreen="true" loading="lazy"></iframe>'
        '</div>'
        '</div>'
    )

    # Video thumbnail overlay (Centering play button on product photo)
    overlay_html = (
        '<div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;background:rgba(15,23,42,0.38);pointer-events:none;">'
        '<div style="width:28px;height:28px;border-radius:50%;background:#ef4444;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 6px rgba(0,0,0,0.4);">'
        '<svg viewBox="0 0 24 24" width="13" height="13" fill="#ffffff" style="margin-left:2px;"><path d="M8 5v14l11-7z"/></svg>'
        '</div>'
        '<span style="font-size:10px;font-weight:800;color:#ffffff;letter-spacing:0.5px;text-shadow:0 1px 3px rgba(0,0,0,0.8);line-height:1;">VIDEO</span>'
        '</div>'
    )

    b64_slide = base64.b64encode(slide_html.encode('utf-8')).decode('ascii')
    b64_overlay = base64.b64encode(overlay_html.encode('utf-8')).decode('ascii')

    css = (
        '<style>'
        '.product-video-wrapper{display:none!important}'
        '#khd-main-video-badge{display:none!important}'
        '#khd-gallery-video-player{display:none!important}'
        '.khd-video-thumb-col{cursor:pointer;touch-action:manipulation;transition:transform .2s ease}'
        '.khd-video-thumb-col:hover{transform:scale(1.03)}'
        '.khd-video-thumb-card{position:relative;width:100%;aspect-ratio:1/1;border-radius:6px;overflow:hidden;background:#0f172a;border:2px solid transparent;box-sizing:border-box;transition:all .2s ease}'
        '.is-nav-selected .khd-video-thumb-card{border-color:#ef4444!important;box-shadow:0 0 10px rgba(239,68,68,0.7)!important}'
        '</style>'
    )

    js = (
        '<script type="text/javascript">'
        'function initKhdGalleryVideo(){'
        'var gal=document.querySelector(".woocommerce-product-gallery")||document.querySelector(".product-images");'
        'var slider=document.querySelector(".product-gallery-slider");'
        'var thumbs=document.querySelector(".product-thumbnails");'
        'if(!gal||!slider)return;'
        'if(document.querySelector(".khd-video-slide"))return;'
        'var tempSlide=document.createElement("div");'
        f'tempSlide.innerHTML=decodeURIComponent(escape(window.atob("{b64_slide}")));'
        'var slideEl=tempSlide.firstElementChild;'
        'var $=window.jQuery;'
        'var sliderFlkty=($?$(slider).data("flickity"):null);'
        'if(sliderFlkty){'
        '$(slider).flickity("prepend",slideEl);'
        '$(slider).flickity("resize");'
        '}else{'
        'slider.insertBefore(slideEl,slider.firstChild);'
        '}'
        'var thumbCol=null;'
        'if(thumbs){'
        'var firstImg=thumbs.querySelector(".col:not(.khd-video-thumb-col) img");'
        'var thumbSrc=firstImg?(firstImg.getAttribute("data-khd-thumb-src")||firstImg.getAttribute("data-src")||firstImg.src):"";'
        'thumbCol=document.createElement("div");'
        'thumbCol.className="col khd-video-thumb-col is-nav-selected";'
        'thumbCol.id="khd-video-thumb-col";'
        'var card=document.createElement("div");'
        'card.className="khd-video-thumb-card";'
        'card.id="khd-video-thumb-card";'
        'if(thumbSrc){'
        'var bgImg=document.createElement("img");'
        'bgImg.src=thumbSrc;'
        'bgImg.style.cssText="width:100%;height:100%;object-fit:cover;display:block;filter:brightness(0.75);";'
        'card.appendChild(bgImg);'
        '}'
        'var over=document.createElement("div");'
        f'over.innerHTML=decodeURIComponent(escape(window.atob("{b64_overlay}")));'
        'card.appendChild(over);'
        'var link=document.createElement("a");'
        'link.href="javascript:void(0);";'
        'link.style.cssText="display:block;width:100%;height:100%;";'
        'link.appendChild(card);'
        'thumbCol.appendChild(link);'
        'var thumbsFlkty=($?$(thumbs).data("flickity"):null);'
        'if(thumbsFlkty){'
        '$(thumbs).flickity("prepend",thumbCol);'
        '$(thumbs).flickity("resize");'
        '}else{'
        'thumbs.insertBefore(thumbCol,thumbs.firstChild);'
        '}'
        '}'
        'function syncThumbSelected(idx){'
        'if(!thumbs)return;'
        'var cols=thumbs.querySelectorAll(".col");'
        'for(var i=0;i<cols.length;i++){cols[i].classList.remove("is-nav-selected");}'
        'if(cols[idx])cols[idx].classList.add("is-nav-selected");'
        'if($){if($(thumbs).data("flickity")){$(thumbs).flickity("select",idx);}}'
        '}'
        'if($){'
        '$(slider).off("change.flickity.khd").on("change.flickity.khd",function(e,idx){syncThumbSelected(idx);});'
        '$(thumbs).off("click.khd touchend.khd").on("click.khd touchend.khd",".col",function(e){'
        'e.preventDefault();'
        'var idx=$(this).index();'
        'if($(slider).data("flickity")){$(slider).flickity("select",idx);}'
        'syncThumbSelected(idx);'
        '});'
        '}'
        'if(sliderFlkty){$(slider).flickity("select",0);}'
        'syncThumbSelected(0);'
        '}'
        'initKhdGalleryVideo();'
        'document.addEventListener("DOMContentLoaded",initKhdGalleryVideo);'
        'window.addEventListener("load",initKhdGalleryVideo);'
        'setTimeout(initKhdGalleryVideo,150);'
        'setTimeout(initKhdGalleryVideo,600);'
        'setTimeout(initKhdGalleryVideo,1500);'
        '</script>'
    )

    return css + js


def build_drive_video_embed_html(video_files):
    """
    Tạo khối mã HTML nhúng trình phát video Google Drive responsive, tối ưu giao diện đặt ở đầu mô tả sản phẩm.
    Hỗ trợ hiển thị hoàn hảo trọn vẹn khung hình cho video quay điện thoại (dọc/ngang) trên cả Web, iPad và Mobile.
    """
    if not video_files:
        return ""

    html_parts = [
        '<div class="product-video-section" style="margin: 0 0 28px 0; padding: 16px; background: #f8fafc; border-radius: 14px; border: 1px solid #e2e8f0; text-align: center;">',
        '  <h3 style="margin-top: 0; margin-bottom: 14px; font-size: 17px; font-weight: 700; color: #1e293b; display: flex; align-items: center; justify-content: center; gap: 8px;">',
        '    <span>🎬</span> Video Thực Tế &amp; Hướng Dẫn Sử Dụng',
        '  </h3>',
        '  <div style="display: flex; flex-direction: column; gap: 18px; align-items: center; width: 100%; max-width: 440px; margin: 0 auto;">'
    ]
    for v in video_files:
        vid = v.get("id")
        if not vid:
            continue
        vname = v.get("name", "Video sản phẩm")
        html_parts.extend([
            '    <div style="width: 100%; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 14px rgba(0,0,0,0.08); background: #0f172a;">',
            '      <div style="position: relative; width: 100%; height: 520px; max-height: 75vh; min-height: 400px; overflow: hidden; background: #0f172a;">',
            f'        <iframe src="https://drive.google.com/file/d/{vid}/preview" style="position: absolute; top: -56px; left: 0; width: 100%; height: calc(100% + 56px); border: 0;" allow="autoplay; fullscreen" allowfullscreen loading="lazy"></iframe>',
            '      </div>',
            '      <div style="padding: 10px 14px; background: #1e293b; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px;">',
            f'        <span style="font-size: 13px; color: #cbd5e1; font-weight: 500;">📺 {vname}</span>',
            f'        <a href="https://drive.google.com/file/d/{vid}/view" target="_blank" rel="noopener noreferrer" style="display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: #38bdf8; text-decoration: none; font-weight: 600; padding: 4px 10px; border-radius: 6px; background: rgba(56, 189, 248, 0.12);">▶ Mở xem trực tiếp HD ↗</a>',
            '      </div>',
            '    </div>'
        ])
    html_parts.append('  </div>')
    html_parts.append('</div>')
    return "\n".join(html_parts)


def build_native_video_embed_html(video_urls):
    """
    Tạo khối mã HTML nhúng trình phát video HTML5 nguyên bản, sạch 100% không dính thanh công cụ hay nút bấm của bên thứ ba.
    """
    if not video_urls:
        return ""
    html_parts = [
        '<div class="product-video-section" style="margin: 0 0 28px 0; padding: 16px; background: #f8fafc; border-radius: 14px; border: 1px solid #e2e8f0; text-align: center;">',
        '  <h3 style="margin-top: 0; margin-bottom: 14px; font-size: 17px; font-weight: 700; color: #1e293b; display: flex; align-items: center; justify-content: center; gap: 8px;">',
        '    <span>🎬</span> Video Thực Tế &amp; Hướng Dẫn Sử Dụng',
        '  </h3>',
        '  <div style="display: flex; flex-direction: column; gap: 18px; align-items: center; width: 100%; max-width: 440px; margin: 0 auto;">'
    ]
    for url in video_urls:
        html_parts.extend([
            '    <div style="width: 100%; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 14px rgba(0,0,0,0.12); background: #000;">',
            '      <video controls playsinline preload="metadata" style="width: 100%; height: auto; max-height: 540px; display: block; border-radius: 12px; background: #000;">',
            f'        <source src="{url}" type="video/mp4">',
            '        Trình duyệt của bạn không hỗ trợ thẻ video HTML5.',
            '      </video>',
            '    </div>'
        ])
    html_parts.append('  </div>')
    html_parts.append('</div>')
    return "\n".join(html_parts)


def download_drive_folder(folder_url, temp_dir):
    import gdown
    import requests
    os.makedirs(temp_dir, exist_ok=True)
    clean_url = _clean_drive_folder_url(folder_url)
    log_message(f"Đang tải thư mục Google Drive: {clean_url}")

    image_extensions = {".png", ".jpg", ".jpeg", ".webp", ".webb"}
    downloaded_images = []

    # Chiến lược 1: Lấy danh sách file và tải song song qua CDN Google Drive (nhanh & ổn định nhất)
    try:
        folder_files = get_drive_folder_files(clean_url)
        if folder_files:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            image_candidates = [
                f for f in folder_files
                if Path(Path(f.path).name).suffix.lower() in image_extensions
            ]

            def _dl_file(f):
                f_name = Path(f.path).name
                dest_file = Path(temp_dir) / f_name
                dl_url = f"https://drive.usercontent.google.com/download?id={f.id}"
                try:
                    r = requests.get(dl_url, headers=headers, timeout=30)
                    if r.status_code == 200 and len(r.content) > 0 and "text/html" not in r.headers.get("Content-Type", ""):
                        dest_file.write_bytes(r.content)
                        return dest_file
                except Exception as err:
                    log_message(f"Không thể tải file {f_name} trực tiếp: {err}")
                return None

            with ThreadPoolExecutor(max_workers=min(4, len(image_candidates) or 1)) as pool:
                results = list(pool.map(_dl_file, image_candidates))
            downloaded_images = [p for p in results if p is not None]
    except Exception as e:
        log_message(f"Không lấy được danh sách file trực tiếp ({e}), tự động chuyển sang phương thức tải thư mục...")

    # Chiến lược 2: Nếu chưa tải được, thử tải toàn bộ thư mục qua gdown chuẩn
    if not downloaded_images:
        try:
            gdown.download_folder(
                url=clean_url,
                output=str(temp_dir),
                quiet=True,
                use_cookies=False,
            )
            for root, _, files in os.walk(temp_dir):
                for file in files:
                    p = Path(root) / file
                    if p.suffix.lower() in image_extensions and p.stat().st_size > 0:
                        downloaded_images.append(p)
        except Exception as e:
            log_message(f"Lần tải Google Drive qua gdown thất bại: {e}")

    # Chiến lược 3: Trích xuất folder_id trực tiếp từ URL
    if not downloaded_images:
        match = re.search(r"([a-zA-Z0-9_-]{25,})", str(folder_url or ""))
        if match:
            folder_id = match.group(1)
            direct_url = f"https://drive.google.com/drive/folders/{folder_id}"
            try:
                gdown.download_folder(
                    url=direct_url,
                    output=str(temp_dir),
                    quiet=True,
                )
                for root, _, files in os.walk(temp_dir):
                    for file in files:
                        p = Path(root) / file
                        if p.suffix.lower() in image_extensions and p.stat().st_size > 0:
                            downloaded_images.append(p)
            except Exception as e:
                log_message(f"Lần tải Google Drive bằng folder_id thất bại: {e}")

    # Loại bỏ trùng lặp file (nếu có)
    unique_map = {}
    for p in downloaded_images:
        unique_map[p.resolve()] = p
    downloaded_images = list(unique_map.values())

    if not downloaded_images:
        raise WorkflowValidationError(
            "Tải ảnh từ Google Drive thất bại (thư mục trống hoặc bị chặn quyền truy cập). "
            "Vui lòng kiểm tra lại link Drive và bật quyền 'Bất kỳ ai có đường liên kết'."
        )

    return sort_product_images(downloaded_images, temp_dir)

def wp_upload_media(config, file_path, max_retries=3):
    url = f"{config.get('WORDPRESS_SITE_URL', '').rstrip('/')}/wp-json/wp/v2/media"
    token = base64.b64encode(f"{config.get('WORDPRESS_USERNAME')}:{config.get('WORDPRESS_PASSWORD')}".encode("utf-8")).decode("ascii")
    ctx = None
    if config.get("SSL_NO_VERIFY", "").lower() in {"1", "true", "yes", "on"}:
        ctx = urllib.request.ssl._create_unverified_context()

    upload = inspect_image_for_upload(file_path)
    data = upload.path.read_bytes()
    direct_headers = {
        "Authorization": f"Basic {token}",
        "Content-Disposition": f'attachment; filename="{upload.filename}"',
        "Content-Type": upload.mime_type,
        "User-Agent": "Notion WooCommerce AutoSync"
    }

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=direct_headers, method="POST")
            try:
                with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as http_err:
                log_message(
                    f"Upload trực tiếp {file_path.name} gặp HTTP {http_err.code} ({http_err.reason}). "
                    f"Tự động chuyển sang phương thức multipart/form-data..."
                )
                time.sleep(1.5)
                boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
                multipart_data = (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="file"; filename="{upload.filename}"\r\n'
                    f"Content-Type: {upload.mime_type}\r\n\r\n"
                ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")

                mp_headers = {
                    "Authorization": f"Basic {token}",
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                }
                mp_req = urllib.request.Request(url, data=multipart_data, headers=mp_headers, method="POST")
                with urllib.request.urlopen(mp_req, context=ctx, timeout=60) as resp:
                    res = json.loads(resp.read().decode("utf-8"))

            returned_mime = res.get("mime_type")
            if returned_mime and returned_mime != upload.mime_type:
                raise WorkflowValidationError(
                    f"WordPress trả MIME sai cho {file_path.name}: {returned_mime}"
                )
            log_message(f"Đã tải ảnh lên: {file_path.name} | WP Media ID: {res.get('id')}")
            return res.get("id")
        except Exception as e:
            last_error = e
            log_message(f"Lỗi tải ảnh {file_path.name} lên WordPress (lần {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                backoff_delay = attempt * 2.5
                log_message(f"Chờ {backoff_delay:.1f}s trước khi thử lại tải ảnh {file_path.name}...")
                time.sleep(backoff_delay)

    log_message(f"Thất bại hoàn toàn khi tải ảnh {file_path.name} sau {max_retries} lần thử: {last_error}")
    return None


def wp_update_media_metadata(config, media_id, alt_text, title, max_retries=3):
    url = f"{config.get('WORDPRESS_SITE_URL', '').rstrip('/')}/wp-json/wp/v2/media/{media_id}"
    token = base64.b64encode(f"{config.get('WORDPRESS_USERNAME')}:{config.get('WORDPRESS_PASSWORD')}".encode("utf-8")).decode("ascii")
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "Notion WooCommerce AutoSync"
    }
    body = {
        "alt_text": alt_text,
        "title": title
    }
    ctx = None
    if config.get("SSL_NO_VERIFY", "").lower() in {"1", "true", "yes", "on"}:
        ctx = urllib.request.ssl._create_unverified_context()
    data = json.dumps(body).encode("utf-8")

    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                log_message(f"Đã cập nhật metadata ảnh ID {media_id} -> Alt: '{alt_text}'")
                return True
        except Exception as e:
            log_message(f"Lỗi cập nhật metadata ảnh ID {media_id} (lần {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                time.sleep(attempt * 1.5)
    return False


def upload_product_images(config, downloaded_images, product_title, progress_callback=None, max_workers=2):
    """
    Upload danh sách ảnh lên WordPress Media song song đa luồng (multi-threading),
    giúp rút ngắn thời gian upload mà vẫn bảo toàn tuyệt đối 100% đúng thứ tự ảnh 1, 2, 3...
    cho gallery sản phẩm WooCommerce. Tự động thu hồi (rollback) các ảnh đã tải nếu có sự cố.
    """
    if not downloaded_images:
        return []

    if progress_callback:
        progress_callback(f"📤 3️⃣ Đang upload {len(downloaded_images)} ảnh lên website (WordPress Media)...")

    workers = min(max_workers, len(downloaded_images)) if downloaded_images else 1
    uploaded_media_map = {}
    completed_count = 0
    upload_lock = threading.Lock()
    all_successful_media_ids = []

    def _upload_task(idx, image_path):
        media_id = wp_upload_media(config, image_path)
        if not media_id:
            raise WorkflowValidationError(
                f"Upload thất bại ảnh số {idx + 1}: {image_path.name}. Sản phẩm chưa được đăng."
            )
        with upload_lock:
            all_successful_media_ids.append(media_id)

        alt_text = product_title if idx == 0 else f"{product_title} {idx}"
        if not wp_update_media_metadata(config, media_id, alt_text, alt_text):
            raise WorkflowValidationError(
                f"Không cập nhật được Alt/Title cho ảnh số {idx + 1}. Sản phẩm chưa được đăng."
            )
        return idx, media_id

    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(_upload_task, idx, img_path)
                for idx, img_path in enumerate(downloaded_images)
            ]
            for future in as_completed(futures):
                idx, media_id = future.result()
                uploaded_media_map[idx] = media_id
                completed_count += 1
                if progress_callback:
                    progress_callback(
                        f"📤 3️⃣ Đang upload ảnh lên website: {completed_count}/{len(downloaded_images)}..."
                    )
    except Exception as exc:
        with upload_lock:
            to_rollback = list(all_successful_media_ids)
        if to_rollback:
            log_message(
                f"Upload bị gián đoạn ({exc}). Đang tự động dọn dẹp {len(to_rollback)} ảnh "
                f"đã tải lên dở dang trên WordPress: {to_rollback}"
            )
            rollback_uploaded_media(config, to_rollback)
        raise

    return [uploaded_media_map[i] for i in range(len(downloaded_images))]


def wp_update_product_content(config, product_id, content):
    """
    Cập nhật nội dung mô tả sản phẩm qua WordPress REST API (/wp-json/wp/v2/product/{id}).
    WordPress REST API cho phép tài khoản Administrator lưu trữ mã nhúng <iframe> nguyên vẹn
    (không bị bộ lọc KSES của WooCommerce REST API tự động tước bỏ).
    """
    url = f"{config.get('WORDPRESS_SITE_URL', '').rstrip('/')}/wp-json/wp/v2/product/{product_id}"
    token = base64.b64encode(f"{config.get('WORDPRESS_USERNAME')}:{config.get('WORDPRESS_PASSWORD')}".encode("utf-8")).decode("ascii")
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "Notion WooCommerce AutoSync"
    }
    body = {
        "content": content
    }
    try:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        ctx = None
        if config.get("SSL_NO_VERIFY", "").lower() in {"1", "true", "yes", "on"}:
            ctx = urllib.request.ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
            log_message(f"Đã cập nhật mô tả HTML (giữ nguyên iframe video) cho sản phẩm ID {product_id}")
            return True
    except Exception as e:
        log_message(f"Lỗi cập nhật mô tả sản phẩm ID {product_id} qua wp/v2: {e}")
        return False


def wp_upload_video(config, file_path, filename):
    """
    Tải file video MP4 lên WordPress Media Library (/wp-json/wp/v2/media).
    """
    url = f"{config.get('WORDPRESS_SITE_URL', '').rstrip('/')}/wp-json/wp/v2/media"
    token = base64.b64encode(f"{config.get('WORDPRESS_USERNAME')}:{config.get('WORDPRESS_PASSWORD')}".encode("utf-8")).decode("ascii")
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": "video/mp4",
        "User-Agent": "Notion WooCommerce AutoSync"
    }
    data = Path(file_path).read_bytes()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    ctx = None
    if config.get("SSL_NO_VERIFY", "").lower() in {"1", "true", "yes", "on"}:
        ctx = urllib.request.ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            log_message(f"Đã tải video lên WordPress Media: ID {res.get('id')} -> {res.get('source_url')}")
            return res.get("source_url")
    except Exception as e:
        log_message(f"Lỗi tải video {filename} lên WordPress: {e}")
        return None


def process_and_upload_drive_videos(config, video_files, temp_dir):
    """
    Tải video từ Google Drive, chuyển đổi/nén sang MP4 H.264 web chuẩn, tải lên WordPress Media
    và trả về danh sách URL trực tiếp để nhúng vào trình phát HTML5 không dính thanh công cụ.
    Nếu chuyển đổi hoặc tải lên thất bại, hàm trả về danh sách rỗng để fallback sang Drive preview.
    """
    uploaded_urls = []
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as fe:
        log_message(f"Không có ffmpeg để xử lý video: {fe}")
        return uploaded_urls

    for v in video_files:
        vid = v.get("id")
        if not vid:
            continue
        vname = v.get("name", "video")
        raw_path = Path(temp_dir) / f"raw_{vid}_{vname}"
        out_mp4 = Path(temp_dir) / f"web_{vid}.mp4"
        try:
            import gdown
            gdown.download(id=vid, output=str(raw_path), quiet=True)
            if not raw_path.exists() or raw_path.stat().st_size == 0:
                continue
            cmd = [
                ffmpeg_exe, "-y",
                "-i", str(raw_path),
                "-vf", "scale='min(1080,iw)':-2",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "24",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "128k",
                "-movflags", "+faststart",
                str(out_mp4)
            ]
            import subprocess
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if out_mp4.exists() and out_mp4.stat().st_size > 0:
                safe_name = f"video_{uuid.uuid4().hex[:8]}.mp4"
                url = wp_upload_video(config, out_mp4, safe_name)
                if url:
                    uploaded_urls.append(url)
        except Exception as err:
            log_message(f"Không thể xử lý/nén video {vname}: {err}")
    return uploaded_urls


def find_or_create_category(config, name):
    url = f"{config.get('WORDPRESS_SITE_URL', '').rstrip('/')}/wp-json/wc/v3/products/categories?search={urllib.parse.quote(name)}"
    token = base64.b64encode(f"{config.get('WOOCOMMERCE_CONSUMER_KEY')}:{config.get('WOOCOMMERCE_CONSUMER_SECRET')}".encode("ascii")).decode("ascii")
    headers = {
        "Authorization": f"Basic {token}",
        "User-Agent": "Notion WooCommerce AutoSync"
    }
    ctx = None
    if config.get("SSL_NO_VERIFY", "").lower() in {"1", "true", "yes", "on"}:
        ctx = urllib.request.ssl._create_unverified_context()
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            categories = json.loads(resp.read().decode("utf-8"))
            for category in categories:
                if category.get("name", "").lower() == name.lower():
                    return category.get("id")
            
            # Create category if not found
            create_url = f"{config.get('WORDPRESS_SITE_URL', '').rstrip('/')}/wp-json/wc/v3/products/categories"
            create_data = json.dumps({"name": name}).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
            req = urllib.request.Request(create_url, data=create_data, headers=headers, method="POST")
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp2:
                new_cat = json.loads(resp2.read().decode("utf-8"))
                log_message(f"Đã tạo danh mục WooCommerce: {name} | ID: {new_cat.get('id')}")
                return new_cat.get("id")
    except Exception as e:
        log_message(f"Lỗi xử lý danh mục {name}: {e}")
        return None

def create_woocommerce_product(config, product_data):
    url = f"{config.get('WORDPRESS_SITE_URL', '').rstrip('/')}/wp-json/wc/v3/products"
    token = base64.b64encode(f"{config.get('WOOCOMMERCE_CONSUMER_KEY')}:{config.get('WOOCOMMERCE_CONSUMER_SECRET')}".encode("ascii")).decode("ascii")
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "Notion WooCommerce AutoSync"
    }
    ctx = None
    if config.get("SSL_NO_VERIFY", "").lower() in {"1", "true", "yes", "on"}:
        ctx = urllib.request.ssl._create_unverified_context()
    try:
        req_data = json.dumps(product_data).encode("utf-8")
        req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            product = json.loads(resp.read().decode("utf-8"))
            log_message(f"Đã tạo sản phẩm WooCommerce: {product.get('name')} | ID: {product.get('id')}")
            return product
    except Exception as e:
        if hasattr(e, "read"):
            log_message(f"Lỗi tạo sản phẩm: {e.read().decode('utf-8')}")
        else:
            log_message(f"Lỗi tạo sản phẩm: {e}")
        return None


def _woocommerce_api_request(config, method, path, body=None, timeout=30):
    base_url = config.get("WORDPRESS_SITE_URL", "").rstrip("/")
    url = f"{base_url}/wp-json/wc/v3/{path.lstrip('/')}"
    token = base64.b64encode(
        f"{config.get('WOOCOMMERCE_CONSUMER_KEY')}:{config.get('WOOCOMMERCE_CONSUMER_SECRET')}".encode("ascii")
    ).decode("ascii")
    headers = {
        "Authorization": f"Basic {token}",
        "User-Agent": "Notion WooCommerce AutoSync",
    }
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
        data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    context = None
    if config.get("SSL_NO_VERIFY", "").lower() in {"1", "true", "yes", "on"}:
        context = urllib.request.ssl._create_unverified_context()
    with urllib.request.urlopen(request, context=context, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def find_woocommerce_product_by_page_id(config, page_id):
    clean_page_id = str(page_id or "").replace("-", "").strip()
    if not clean_page_id:
        return None
    page = 1
    while True:
        products = _woocommerce_api_request(
            config,
            "GET",
            f"products?status=any&per_page=100&page={page}&orderby=date&order=desc",
            timeout=20,
        )
        if not isinstance(products, list) or not products:
            break
        for product in products:
            meta = _product_meta_map(product)
            pid = str(meta.get("_khd_notion_page_id") or "").replace("-", "").strip()
            if pid and pid == clean_page_id:
                return product
            sku = str(product.get("sku") or "").strip()
            if sku and clean_page_id in sku.replace("-", ""):
                return product
        if len(products) < 100:
            break
        page += 1
    return None


def find_woocommerce_product_by_page_id_with_retry(config, page_id, attempts=3, delay_seconds=1):
    last_error = None
    for attempt in range(max(1, int(attempts))):
        try:
            product = find_woocommerce_product_by_page_id(config, page_id)
            if product:
                return product
        except Exception as exc:
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(delay_seconds)
    if last_error:
        raise last_error
    return None


def find_woocommerce_product_by_sku(config, sku):
    encoded_sku = urllib.parse.quote(str(sku), safe="")
    products = _woocommerce_api_request(
        config,
        "GET",
        f"products?sku={encoded_sku}&status=any&per_page=10",
        timeout=20,
    )
    if not isinstance(products, list):
        return None
    for product in products:
        if str(product.get("sku") or "").casefold() == str(sku).casefold():
            return product
    return None


def find_woocommerce_product_by_sku_with_retry(config, sku, attempts=3, delay_seconds=1):
    last_error = None
    for attempt in range(max(1, int(attempts))):
        try:
            product = find_woocommerce_product_by_sku(config, sku)
            if product:
                return product
        except Exception as exc:
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(delay_seconds)
    if last_error:
        raise last_error
    return None


def update_woocommerce_product(config, product_id, body):
    return _woocommerce_api_request(config, "PUT", f"products/{int(product_id)}", body=body, timeout=30)


def delete_woocommerce_product(config, product_id):
    try:
        return _woocommerce_api_request(
            config,
            "DELETE",
            f"products/{int(product_id)}?force=true",
            timeout=30,
        )
    except Exception as exc:
        log_message(f"Không rollback được sản phẩm WooCommerce ID {product_id}: {exc}")
        return None


def wp_delete_media(config, media_id):
    base_url = config.get("WORDPRESS_SITE_URL", "").rstrip("/")
    url = f"{base_url}/wp-json/wp/v2/media/{int(media_id)}?force=true"
    token = base64.b64encode(
        f"{config.get('WORDPRESS_USERNAME')}:{config.get('WORDPRESS_PASSWORD')}".encode("utf-8")
    ).decode("ascii")
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Basic {token}",
            "User-Agent": "Notion WooCommerce AutoSync",
        },
        method="DELETE",
    )
    context = None
    if config.get("SSL_NO_VERIFY", "").lower() in {"1", "true", "yes", "on"}:
        context = urllib.request.ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(request, context=context, timeout=20):
            return True
    except Exception as exc:
        log_message(f"Không xóa được media rác ID {media_id}: {exc}")
        return False


def rollback_uploaded_media(config, media_ids):
    for media_id in reversed(list(media_ids)):
        wp_delete_media(config, media_id)


def verify_product_image_ids(product, expected_media_ids):
    actual_ids = [int(image.get("id")) for image in product.get("images", []) if image.get("id")]
    expected_ids = [int(media_id) for media_id in expected_media_ids]
    if actual_ids != expected_ids:
        raise WorkflowValidationError(
            f"WooCommerce trả sai thứ tự ảnh. Mong đợi {expected_ids}, nhận {actual_ids}."
        )


def _product_meta_map(product):
    return {
        str(item.get("key")): item.get("value")
        for item in product.get("meta_data", [])
        if item.get("key")
    }


def validate_sync_attempt_ownership(product, page_id, attempt_id):
    meta = _product_meta_map(product)
    if str(meta.get("_khd_notion_page_id") or "") != str(page_id):
        raise WorkflowValidationError(
            "SKU đã được một lượt đồng bộ khác tạo; không được phép sửa hoặc xóa sản phẩm đó."
        )
    if str(meta.get("_khd_sync_attempt_id") or "") != str(attempt_id):
        raise WorkflowValidationError(
            "SKU đang được một máy hoặc tiến trình khác xử lý; lượt hiện tại đã dừng an toàn."
        )
    return product


def require_published_product(product):
    if not product or product.get("status") != "publish":
        raise RuntimeError("WooCommerce chưa xác nhận trạng thái publish.")
    return product


def validate_existing_product_for_recovery(product):
    meta = _product_meta_map(product)
    expected_raw = str(meta.get("_khd_expected_media_ids") or "")
    try:
        expected_media_ids = [int(value) for value in expected_raw.split(",") if value.strip()]
    except ValueError as exc:
        raise WorkflowValidationError("Bản nháp có metadata thứ tự ảnh không hợp lệ.") from exc
    if not expected_media_ids:
        raise WorkflowValidationError("Bản nháp chưa có metadata xác minh thứ tự ảnh.")

    validate_product_for_publish(
        title=product.get("name"),
        description=product.get("description"),
        category=(product.get("categories") or [{}])[0].get("name"),
        regular_price=product.get("regular_price"),
        sale_price=product.get("sale_price"),
        image_count=len(product.get("images") or []),
    )
    verify_product_image_ids(product, expected_media_ids)
    return expected_media_ids


def get_site_sync_config(config: dict, site_key: str = "derma") -> dict:
    site_key_clean = str(site_key or "derma").strip().lower()
    ssl_no_verify = config.get("SSL_NO_VERIFY", "").lower() in {"1", "true", "yes", "on"}
    if site_key_clean == "pharma":
        pharma_url = config.get("PHARMA_SITE_URL", "https://khaihoanpharma.com").rstrip("/")
        pharma_user = config.get("PHARMA_USERNAME", "admin").strip()
        pharma_pass = config.get("PHARMA_PASSWORD", "").strip()
        pharma_wc_key = config.get("PHARMA_WC_CONSUMER_KEY", "").strip() or pharma_user
        pharma_wc_secret = config.get("PHARMA_WC_CONSUMER_SECRET", "").strip() or pharma_pass
        pharma_db_id = config.get("PHARMA_NOTION_DATABASE_ID", "40870655a9aa8150a9c0e01105450e7e").strip()
        return {
            "site_key": "pharma",
            "site_name": "Khải Hoàn Pharma",
            "site_short": "Pharma",
            "site_badge": "💊 Pharma",
            "WORDPRESS_SITE_URL": pharma_url,
            "WORDPRESS_USERNAME": pharma_user,
            "WORDPRESS_PASSWORD": pharma_pass,
            "WOOCOMMERCE_CONSUMER_KEY": pharma_wc_key,
            "WOOCOMMERCE_CONSUMER_SECRET": pharma_wc_secret,
            "NOTION_DATABASE_ID": pharma_db_id,
            "NOTION_TOKEN": config.get("NOTION_TOKEN", ""),
            "SSL_NO_VERIFY": "true" if ssl_no_verify else "false",
        }
    else:
        derma_url = config.get("WORDPRESS_SITE_URL", "https://khaihoanderma.com").rstrip("/")
        derma_db_id = config.get("NOTION_DATABASE_ID", "9788b8a031cc42d391be4e26d6b8c8e8").strip()
        return {
            "site_key": "derma",
            "site_name": "Khải Hoàn Derma",
            "site_short": "Derma",
            "site_badge": "🌿 Derma",
            "WORDPRESS_SITE_URL": derma_url,
            "WORDPRESS_USERNAME": config.get("WORDPRESS_USERNAME", "").strip(),
            "WORDPRESS_PASSWORD": config.get("WORDPRESS_PASSWORD", "").strip(),
            "WOOCOMMERCE_CONSUMER_KEY": config.get("WOOCOMMERCE_CONSUMER_KEY", "").strip(),
            "WOOCOMMERCE_CONSUMER_SECRET": config.get("WOOCOMMERCE_CONSUMER_SECRET", "").strip(),
            "NOTION_DATABASE_ID": derma_db_id,
            "NOTION_TOKEN": config.get("NOTION_TOKEN", ""),
            "SSL_NO_VERIFY": "true" if ssl_no_verify else "false",
        }


def determine_page_site_key(page: dict, config: dict) -> str:
    parent = page.get("parent") or {}
    parent_db = str(parent.get("database_id", "")).replace("-", "").lower()
    pharma_db = str(config.get("PHARMA_NOTION_DATABASE_ID", "40870655a9aa8150a9c0e01105450e7e")).replace("-", "").lower()
    if parent_db and parent_db == pharma_db:
        return "pharma"
    if page.get("_site_key"):
        return page.get("_site_key")
    return "derma"


def query_all_notion_pages_to_post(token: str, config: dict, site_key: str = "all") -> list[dict]:
    pages = []
    target = str(site_key or "all").lower().strip()

    if target in {"all", "derma"}:
        derma_db = config.get("NOTION_DATABASE_ID", "").strip()
        if token and derma_db:
            try:
                d_pages = query_notion_pages_to_post(token, derma_db)
                for p in d_pages:
                    p["_site_key"] = "derma"
                    p["_site_name"] = "Khải Hoàn Derma"
                    p["_site_short"] = "Derma"
                    p["_site_badge"] = "🌿 Derma"
                pages.extend(d_pages)
            except Exception as e:
                log_message(f"Lỗi truy vấn Notion Derma DB ({derma_db}): {e}")

    if target in {"all", "pharma"}:
        pharma_db = config.get("PHARMA_NOTION_DATABASE_ID", "").strip()
        if token and pharma_db:
            try:
                p_pages = query_notion_pages_to_post(token, pharma_db)
                for p in p_pages:
                    p["_site_key"] = "pharma"
                    p["_site_name"] = "Khải Hoàn Pharma"
                    p["_site_short"] = "Pharma"
                    p["_site_badge"] = "💊 Pharma"
                pages.extend(p_pages)
            except Exception as e:
                log_message(f"Lỗi truy vấn Notion Pharma DB ({pharma_db}): {e}")

    seen_ids = set()
    deduped = []
    for p in pages:
        p_id = str(p.get("id"))
        if p_id not in seen_ids:
            seen_ids.add(p_id)
            deduped.append(p)
    return deduped


def update_notion_status(token, page_id, product_url, page_properties=None):
    url = f"https://api.notion.com/v1/pages/{page_id}"
    props = {
        "Trạng thái": {
            "select": {
                "name": "Đã đăng web"
            }
        },
        "IT đã đăng": {
            "checkbox": True
        },
        "Link web": {
            "url": product_url
        }
    }
    if page_properties is None or "Facebook" in page_properties:
        props["Facebook"] = {
            "select": {
                "name": "Chưa đăng"
            }
        }
    update_data = {
        "properties": props
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    req = urllib.request.Request(url, headers=headers, method="PATCH", data=json.dumps(update_data).encode("utf-8"))
    _urlopen_with_retry(req, timeout=20, retries=3)
    log_message(f"Đã cập nhật trạng thái Notion thành 'Đã đăng web' cho ID: {page_id}")


def _run_notion_sync_workflow(progress_callback=None, page_ids=None, site_key: str = "all"):
    config = load_config()
    token = config.get("NOTION_TOKEN")
    derma_db = config.get("NOTION_DATABASE_ID")
    pharma_db = config.get("PHARMA_NOTION_DATABASE_ID", "40870655a9aa8150a9c0e01105450e7e")

    if not token or (not derma_db and not pharma_db):
        return {"status": "error", "message": "Thiếu cấu hình NOTION_TOKEN hoặc NOTION_DATABASE_ID trong telegram_bot.env."}

    try:
        pages = query_all_notion_pages_to_post(token, config, site_key=site_key)
    except Exception as e:
        return {"status": "error", "message": f"Không kết nối được Notion API: {e}"}

    if page_ids:
        allowed_page_ids = {str(page_id) for page_id in page_ids if page_id}
        pages = [page for page in pages if str(page.get("id")) in allowed_page_ids]

    if not pages:
        return {"status": "success", "message": "Không tìm thấy sản phẩm nào có trạng thái 'Báo IT đăng'.", "count": 0}

    if progress_callback:
        progress_callback(f"🔍 Tìm thấy {len(pages)} sản phẩm chờ đăng trên Notion. Bắt đầu xử lý...")

    processed_products = []
    failed_products = []
    temp_root = OUT_DIR / "temp_notion_images"
    temp_root.mkdir(parents=True, exist_ok=True)

    for page in pages:
        page_id = page.get("id")
        properties = page.get("properties", {})

        page_site_key = determine_page_site_key(page, config)
        site_config = get_site_sync_config(config, page_site_key)
        site_short = site_config.get("site_short", "Derma")
        site_badge = site_config.get("site_badge", "🌿 Derma")

        # 1. Product title
        title_list = properties.get("Tên sản phẩm", {}).get("title", [])
        product_title = "".join(item.get("plain_text", "") for item in title_list).strip()
        log_message(f"Bắt đầu xử lý [{site_short}]: {product_title}")

        if progress_callback:
            progress_callback(f"📦 <b>[{site_badge}] {product_title}</b>\n1️⃣ Đang phân tích thông tin chi tiết trên Notion...")

        # 2. Get focus keywords (kiểm tra cả 'Từ khóa SEO Rank Math' và 'Từ khóa chính')
        keyword_list = (
            properties.get("Từ khóa SEO Rank Math", {}).get("rich_text", []) or
            properties.get("Từ khóa chính", {}).get("rich_text", [])
        )
        seo_keywords_raw = "".join(item.get("plain_text", "") for item in keyword_list).strip()
        if seo_keywords_raw:
            kw_parts = [k.strip() for k in re.split(r"[,;]", seo_keywords_raw) if k.strip()]
            seo_keywords = ", ".join(kw_parts[:3])
        else:
            seo_keywords = ""

        try:
            existing_product = find_woocommerce_product_by_page_id(site_config, page_id)
            if existing_product:
                validate_existing_product_for_recovery(existing_product)
                existing_meta = _product_meta_map(existing_product)
                if existing_product.get("status") != "publish":
                    existing_product = update_woocommerce_product(
                        site_config,
                        existing_product.get("id"),
                        {
                            "status": "publish",
                            "meta_data": [{"key": "_khd_sync_verified", "value": "1"}],
                        },
                    )
                    if existing_product.get("status") != "publish":
                        raise RuntimeError(f"Không publish được bản nháp đã phục hồi trên {site_short}.")
                    validate_existing_product_for_recovery(existing_product)
                elif str(existing_meta.get("_khd_sync_verified")) != "1":
                    raise WorkflowValidationError(
                        f"Sản phẩm Notion page {page_id} đã publish nhưng chưa có dấu xác minh an toàn."
                    )
                product_url = existing_product.get("permalink", "")
                update_notion_status(token, page_id, product_url, page_properties=properties)
                processed_products.append({
                    "title": product_title,
                    "url": product_url,
                    "recovered": True,
                    "site_key": page_site_key,
                    "site_short": site_short,
                })
                log_message(f"Đã phục hồi sản phẩm [{site_short}] của Notion page {page_id}: {product_url}")
                continue
        except Exception as exc:
            log_message(f"Không kiểm tra/phục hồi được sản phẩm '{product_title}': {exc}")
            failed_products.append({"title": product_title, "error": str(exc)})
            continue

        # 3. Google Drive folder URL
        drive_url = properties.get("Media sản phẩm", {}).get("url")
        # 4. Content blocks:
        # Ưu tiên lấy trực tiếp từ trang sản phẩm (page_id)
        blocks = []
        try:
            blocks = get_page_blocks(token, page_id)
        except Exception as e:
            log_message(f"Không lấy được blocks trực tiếp từ trang '{product_title}' ({page_id}): {e}")

        # Dự phòng: Nếu trang sản phẩm không có blocks, kiểm tra cột 'Bài content Tây'
        if not blocks:
            relation = properties.get("Bài content Tây", {}).get("relation", [])
            if relation:
                related_page_id = relation[0].get("id")
                try:
                    blocks = get_page_blocks(token, related_page_id)
                except Exception as e:
                    log_message(f"Lỗi đọc nội dung liên kết của '{product_title}': {e}")

        if not blocks:
            log_message(f"Bỏ qua '{product_title}': Không tìm thấy nội dung bài viết trong trang sản phẩm hoặc 'Bài content Tây'.")
            failed_products.append({
                "title": product_title,
                "error": "Không tìm thấy nội dung bài viết trong trang sản phẩm.",
            })
            if progress_callback:
                progress_callback(f"⚠️ Bỏ qua '{product_title}': Không tìm thấy nội dung bài viết.")
            continue
        product_description, category_name, price_val, sale_price_val = parse_notion_blocks(blocks)
        temp_dir = Path(tempfile.mkdtemp(prefix=f"notion-{page_id}-", dir=temp_root))
        uploaded_media_ids = []
        created_product = None
        owns_created_product = False
        published_product = None
        sync_attempt_id = uuid.uuid4().hex

        try:
            if not drive_url:
                raise WorkflowValidationError("Cột 'Media sản phẩm' chưa có link Google Drive.")
            if progress_callback:
                progress_callback(f"📥 2️⃣ Đang tải hình ảnh từ Google Drive cho {site_short}...")
            downloaded_images = download_drive_folder(drive_url, temp_dir)
            validate_image_sequence(downloaded_images, temp_dir)

            if progress_callback:
                progress_callback("⚡ Đang tối ưu hóa hình ảnh chuẩn WebP siêu nhẹ cho SEO...")
            processed_images = optimize_product_images(downloaded_images, temp_dir)
            validate_image_sequence(processed_images, temp_dir)
            for image_path in processed_images:
                inspect_image_for_upload(image_path)

            validate_product_for_publish(
                title=product_title,
                description=product_description,
                category=category_name,
                regular_price=price_val,
                sale_price=sale_price_val,
                image_count=len(processed_images),
            )

            category_id = find_or_create_category(site_config, category_name)
            if not category_id:
                raise WorkflowValidationError(f"Không xác định được danh mục: {category_name} trên {site_short}")

            uploaded_media_ids = upload_product_images(
                site_config,
                processed_images,
                product_title,
                progress_callback=progress_callback,
                max_workers=3
            )

            final_description = product_description
            has_embedded_video = False
            try:
                found_videos = scan_drive_folder_videos(drive_url)
                if found_videos:
                    log_message(f"Tìm thấy {len(found_videos)} video trong Drive của '{product_title}'. Tự động tạo nút xem video tại khu vực gallery ảnh sản phẩm.")
                    video_script = build_drive_video_gallery_script(found_videos)
                    final_description = video_script.strip() + "\n\n" + product_description.strip()
                    has_embedded_video = True
            except Exception as v_err:
                log_message(f"Lỗi quét video Drive cho '{product_title}': {v_err}")

            images_payload = [{"id": media_id} for media_id in uploaded_media_ids]
            product_payload = {
                "name": product_title,
                "type": "simple",
                "description": final_description,
                "regular_price": str(price_val),
                "status": "draft",
                "images": images_payload,
                "categories": [{"id": category_id}],
                "meta_data": [
                    {"key": "rank_math_focus_keyword", "value": seo_keywords},
                    {"key": "_khd_notion_page_id", "value": page_id},
                    {
                        "key": "_khd_expected_media_ids",
                        "value": ",".join(str(media_id) for media_id in uploaded_media_ids),
                    },
                    {"key": "_khd_sync_verified", "value": "0"},
                    {"key": "_khd_sync_attempt_id", "value": sync_attempt_id},
                    {"key": "_khd_site_key", "value": page_site_key},
                ],
            }
            if sale_price_val > 0:
                product_payload["sale_price"] = str(sale_price_val)

            if progress_callback:
                progress_callback(f"⚙️ 4️⃣ Đang tạo bản nháp trên {site_short} (Giá: {price_val:,}đ)...")
            create_response = create_woocommerce_product(site_config, product_payload)
            if create_response and create_response.get("id"):
                created_product = create_response
                owns_created_product = True
            else:
                recovered_draft = find_woocommerce_product_by_page_id_with_retry(site_config, page_id)
                if recovered_draft:
                    validate_sync_attempt_ownership(recovered_draft, page_id, sync_attempt_id)
                    created_product = recovered_draft
                    owns_created_product = True
            if not created_product or not created_product.get("id"):
                raise RuntimeError(f"WooCommerce {site_short} không tạo được bản nháp sản phẩm.")
            validate_sync_attempt_ownership(created_product, page_id, sync_attempt_id)
            verify_product_image_ids(created_product, uploaded_media_ids)

            if has_embedded_video or "<script" in final_description or "<iframe" in final_description:
                wp_update_product_content(site_config, created_product.get("id"), final_description)

            try:
                publish_response = update_woocommerce_product(
                    site_config,
                    created_product.get("id"),
                    {
                        "status": "publish",
                        "meta_data": [{"key": "_khd_sync_verified", "value": "1"}],
                    },
                )
            except Exception:
                recovered_product = find_woocommerce_product_by_page_id_with_retry(site_config, page_id)
                if (
                    recovered_product
                    and recovered_product.get("id") == created_product.get("id")
                    and recovered_product.get("status") == "publish"
                ):
                    validate_sync_attempt_ownership(recovered_product, page_id, sync_attempt_id)
                    publish_response = recovered_product
                else:
                    raise
            published_product = require_published_product(publish_response)
            verify_product_image_ids(published_product, uploaded_media_ids)
            if str(_product_meta_map(published_product).get("_khd_sync_verified")) != "1":
                raise RuntimeError("WooCommerce chưa lưu dấu xác minh đồng bộ an toàn.")

            product_url = published_product.get("permalink", "")
            if not product_url:
                raise RuntimeError(f"WooCommerce {site_short} không trả permalink sản phẩm.")
            if progress_callback:
                progress_callback(f"📝 5️⃣ Cập nhật trạng thái Đã đăng lên Notion ({site_short})...")
            update_notion_status(token, page_id, product_url, page_properties=properties)
            processed_products.append({
                "title": product_title,
                "url": product_url,
                "site_key": page_site_key,
                "site_short": site_short,
            })
            log_message(f"✅ Đã đăng thành công lên {site_short}: {product_title} -> {product_url}")
        except Exception as exc:
            log_message(f"Lỗi xử lý '{product_title}' trên {site_short}: {exc}")
            if published_product:
                failed_products.append({
                    "title": product_title,
                    "url": published_product.get("permalink", ""),
                    "error": f"Sản phẩm đã publish trên {site_short} nhưng Notion chưa hoàn tất: {exc}",
                })
            else:
                if owns_created_product and created_product and created_product.get("id"):
                    delete_woocommerce_product(site_config, created_product.get("id"))
                rollback_uploaded_media(site_config, uploaded_media_ids)
                failed_products.append({"title": product_title, "error": str(exc)})
            if progress_callback:
                progress_callback(f"❌ Không đăng '{product_title}': {exc}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    try:
        if temp_root.exists():
            shutil.rmtree(temp_root, ignore_errors=True)
    except Exception:
        pass
                        
    if processed_products and failed_products:
        msg = (
            f"Đồng bộ một phần: đăng thành công <b>{len(processed_products)}</b>, "
            f"có <b>{len(failed_products)}</b> sản phẩm cần kiểm tra."
        )
        return {
            "status": "warning",
            "message": msg,
            "count": len(processed_products),
            "products": processed_products,
            "errors": failed_products,
        }
    if processed_products:
        msg = f"Đồng bộ thành công! Đã đăng <b>{len(processed_products)}</b> sản phẩm mới từ Notion lên WooCommerce."
        return {"status": "success", "message": msg, "count": len(processed_products), "products": processed_products}
    if failed_products:
        err_details = "\n".join(
            f"• <b>{html.escape(item.get('title') or 'Sản phẩm')}</b>:\n  ↳ {html.escape(item.get('error') or 'Lỗi chưa xác định')}"
            for item in failed_products
        )
        return {
            "status": "error",
            "message": f"Không thể đăng sản phẩm do các lỗi sau:\n{err_details}",
            "count": 0,
            "errors": failed_products,
        }
    return {"status": "success", "message": "Không có sản phẩm hợp lệ cần đăng.", "count": 0}


def run_notion_sync_workflow(progress_callback=None, page_ids=None, site_key: str = "all"):
    if not NOTION_SYNC_LOCK.acquire(blocking=False):
        return {
            "status": "busy",
            "message": "Một lượt đồng bộ Notion đang chạy. Vui lòng chờ hoàn tất rồi thử lại.",
            "count": 0,
        }
    try:
        process_lock_acquired = NOTION_SYNC_PROCESS_LOCK.acquire()
    except Exception as exc:
        NOTION_SYNC_LOCK.release()
        return {
            "status": "error",
            "message": f"Không tạo được khóa đồng bộ trên máy: {exc}",
            "count": 0,
        }
    if not process_lock_acquired:
        NOTION_SYNC_LOCK.release()
        return {
            "status": "busy",
            "message": "Một process khác trên máy đang đồng bộ Notion. Vui lòng chờ hoàn tất.",
            "count": 0,
        }
    try:
        return _run_notion_sync_workflow(progress_callback=progress_callback, page_ids=page_ids, site_key=site_key)
    finally:
        _DRIVE_FOLDER_CACHE.clear()
        NOTION_SYNC_PROCESS_LOCK.release()
        NOTION_SYNC_LOCK.release()


if __name__ == "__main__":
    res = run_notion_sync_workflow()
    print(json.dumps(res, indent=2, ensure_ascii=False))
