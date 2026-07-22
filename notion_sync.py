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

# Setup encoding for Windows Console compatibility
try:
    if sys.stdout.encoding != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8')
    if sys.stderr.encoding != 'utf-8':
        sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

OUT_DIR = Path(__file__).resolve().parent
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
        raise WorkflowValidationError(
            f"Đuôi file không khớp nội dung ảnh: {path.name} ({mime_type})"
        )

    return ImageUploadInfo(
        path=path,
        mime_type=mime_type,
        filename=f"{_ascii_slug(path.stem)}{expected_suffix}",
    )


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
    print(f"[{timestamp}] [NotionSync] {msg}", flush=True)

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
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
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
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        results.extend(data.get("results", []))
        if not data.get("has_more") or not data.get("next_cursor"):
            return results
        cursor = data["next_cursor"]

def download_drive_folder(folder_url, temp_dir):
    import gdown
    os.makedirs(temp_dir, exist_ok=True)
    log_message(f"Downloading Google Drive: {folder_url}")
    try:
        downloaded_files = gdown.download_folder(
            url=folder_url,
            output=str(temp_dir),
            quiet=True,
            use_cookies=False,
        )
        if not downloaded_files:
            raise WorkflowValidationError(
                "Google Drive không trả về file nào; kiểm tra link và quyền 'Bất kỳ ai có đường liên kết'."
            )
        image_extensions = {".png", ".jpg", ".jpeg", ".webp"}
        downloaded_images = []
        for root, _, files in os.walk(temp_dir):
            for file in files:
                p = Path(root) / file
                if p.suffix.lower() in image_extensions:
                    downloaded_images.append(p)
        # Sắp xếp danh sách ảnh theo tên tăng dần để ảnh số 1 làm ảnh sản phẩm đại diện, các ảnh tiếp theo làm album
        return sort_product_images(downloaded_images, temp_dir)
    except Exception as e:
        log_message(f"Error downloading from Drive: {e}")
        return []

def wp_upload_media(config, file_path):
    url = f"{config.get('WORDPRESS_SITE_URL', '').rstrip('/')}/wp-json/wp/v2/media"
    token = base64.b64encode(f"{config.get('WORDPRESS_USERNAME')}:{config.get('WORDPRESS_PASSWORD')}".encode("utf-8")).decode("ascii")
    try:
        upload = inspect_image_for_upload(file_path)
        headers = {
            "Authorization": f"Basic {token}",
            "Content-Disposition": f'attachment; filename="{upload.filename}"',
            "Content-Type": upload.mime_type,
            "User-Agent": "Notion WooCommerce AutoSync"
        }
        data = upload.path.read_bytes()
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        ctx = None
        if config.get("SSL_NO_VERIFY", "").lower() in {"1", "true", "yes", "on"}:
            ctx = urllib.request.ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            returned_mime = res.get("mime_type")
            if returned_mime and returned_mime != upload.mime_type:
                raise WorkflowValidationError(
                    f"WordPress trả MIME sai cho {file_path.name}: {returned_mime}"
                )
            log_message(f"Uploaded image: {file_path.name} | WP Media ID: {res.get('id')}")
            return res.get("id")
    except Exception as e:
        log_message(f"Error uploading image {file_path.name}: {e}")
        return None


def wp_update_media_metadata(config, media_id, alt_text, title):
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
    try:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        ctx = None
        if config.get("SSL_NO_VERIFY", "").lower() in {"1", "true", "yes", "on"}:
            ctx = urllib.request.ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            log_message(f"Updated media metadata for ID {media_id} -> Alt: '{alt_text}'")
            return True
    except Exception as e:
        log_message(f"Error updating media metadata for ID {media_id}: {e}")
        return False


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
                log_message(f"Created new WooCommerce category: {name} | ID: {new_cat.get('id')}")
                return new_cat.get("id")
    except Exception as e:
        log_message(f"Error handling category {name}: {e}")
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
            log_message(f"Created WooCommerce product: {product.get('name')} | ID: {product.get('id')}")
            return product
    except Exception as e:
        if hasattr(e, "read"):
            log_message(f"Error creating product: {e.read().decode('utf-8')}")
        else:
            log_message(f"Error creating product: {e}")
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


def update_notion_status(token, page_id, product_url):
    url = f"https://api.notion.com/v1/pages/{page_id}"
    update_data = {
        "properties": {
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
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    req = urllib.request.Request(url, headers=headers, method="PATCH", data=json.dumps(update_data).encode("utf-8"))
    with urllib.request.urlopen(req, timeout=15) as resp:
        log_message(f"Updated Notion page status to 'Đã đăng web' for ID: {page_id}")

def _run_notion_sync_workflow(progress_callback=None, page_ids=None):
    config = load_config()
    token = config.get("NOTION_TOKEN")
    db_id = config.get("NOTION_DATABASE_ID")
    
    if not token or not db_id:
        return {"status": "error", "message": "Thiếu cấu hình NOTION_TOKEN hoặc NOTION_DATABASE_ID trong telegram_bot.env."}
        
    try:
        pages = query_notion_pages_to_post(token, db_id)
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
        
        # 1. Product title
        title_list = properties.get("Tên sản phẩm", {}).get("title", [])
        product_title = "".join(item.get("plain_text", "") for item in title_list).strip()
        log_message(f"Start processing: {product_title}")
        
        if progress_callback:
            progress_callback(f"📦 <b>Sản phẩm: {product_title}</b>\n1️⃣ Đang phân tích thông tin chi tiết trên Notion...")
            
        # 2. Get focus keywords (lấy từ 2 đến 3 từ khóa đầu tiên cách nhau bởi dấu phẩy)
        keyword_list = properties.get("Từ khóa SEO Rank Math", {}).get("rich_text", [])
        seo_keywords_raw = "".join(item.get("plain_text", "") for item in keyword_list).strip()
        if seo_keywords_raw:
            kw_parts = [k.strip() for k in seo_keywords_raw.split(",") if k.strip()]
            seo_keywords = ", ".join(kw_parts[:3])
        else:
            seo_keywords = ""
        
        try:
            existing_product = find_woocommerce_product_by_page_id(config, page_id)
            if existing_product:
                validate_existing_product_for_recovery(existing_product)
                existing_meta = _product_meta_map(existing_product)
                if existing_product.get("status") != "publish":
                    existing_product = update_woocommerce_product(
                        config,
                        existing_product.get("id"),
                        {
                            "status": "publish",
                            "meta_data": [{"key": "_khd_sync_verified", "value": "1"}],
                        },
                    )
                    if existing_product.get("status") != "publish":
                        raise RuntimeError("Không publish được bản nháp đã phục hồi.")
                    validate_existing_product_for_recovery(existing_product)
                elif str(existing_meta.get("_khd_sync_verified")) != "1":
                    raise WorkflowValidationError(
                        f"Sản phẩm Notion page {page_id} đã publish nhưng chưa có dấu xác minh an toàn."
                    )
                product_url = existing_product.get("permalink", "")
                update_notion_status(token, page_id, product_url)
                processed_products.append({
                    "title": product_title,
                    "url": product_url,
                    "recovered": True,
                })
                log_message(f"Recovered existing product for Notion page {page_id}: {product_url}")
                continue
        except Exception as exc:
            log_message(f"Không kiểm tra/phục hồi được sản phẩm '{product_title}': {exc}")
            failed_products.append({"title": product_title, "error": str(exc)})
            continue

        # 3. Google Drive folder URL
        drive_url = properties.get("Media sản phẩm", {}).get("url")
        
        # 4. Related Page Content (Bài content Tây)
        relation = properties.get("Bài content Tây", {}).get("relation", [])
        if not relation:
            log_message(f"Bỏ qua '{product_title}': Cột 'Bài content Tây' trống.")
            failed_products.append({
                "title": product_title,
                "error": "Cột 'Bài content Tây' trống.",
            })
            if progress_callback:
                progress_callback(f"⚠️ Bỏ qua '{product_title}': Cột 'Bài content Tây' trống.")
            continue
            
        related_page_id = relation[0].get("id")
        try:
            blocks = get_page_blocks(token, related_page_id)
        except Exception as e:
            log_message(f"Lỗi đọc nội dung liên kết của '{product_title}': {e}")
            failed_products.append({
                "title": product_title,
                "error": f"Không đọc được 'Bài content Tây': {e}",
            })
            if progress_callback:
                progress_callback(f"❌ Lỗi đọc nội dung liên kết của '{product_title}': {e}")
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
                progress_callback("📥 2️⃣ Đang tải hình ảnh từ Google Drive...")
            downloaded_images = download_drive_folder(drive_url, temp_dir)
            validate_image_sequence(downloaded_images, temp_dir)
            for image_path in downloaded_images:
                inspect_image_for_upload(image_path)

            validate_product_for_publish(
                title=product_title,
                description=product_description,
                category=category_name,
                regular_price=price_val,
                sale_price=sale_price_val,
                image_count=len(downloaded_images),
            )

            category_id = find_or_create_category(config, category_name)
            if not category_id:
                raise WorkflowValidationError(f"Không xác định được danh mục: {category_name}")

            if progress_callback:
                progress_callback(f"📤 3️⃣ Đang upload {len(downloaded_images)} ảnh lên website (WordPress Media)...")
            for idx, image_path in enumerate(downloaded_images):
                media_id = wp_upload_media(config, image_path)
                if not media_id:
                    raise WorkflowValidationError(
                        f"Upload thất bại ảnh số {idx + 1}: {image_path.name}. Sản phẩm chưa được đăng."
                    )
                uploaded_media_ids.append(media_id)
                alt_text = product_title if idx == 0 else f"{product_title} {idx}"
                if not wp_update_media_metadata(config, media_id, alt_text, alt_text):
                    raise WorkflowValidationError(
                        f"Không cập nhật được Alt/Title cho ảnh số {idx + 1}. Sản phẩm chưa được đăng."
                    )

            images_payload = [{"id": media_id} for media_id in uploaded_media_ids]
            product_payload = {
                "name": product_title,
                "type": "simple",
                "description": product_description,
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
                ],
            }
            if sale_price_val > 0:
                product_payload["sale_price"] = str(sale_price_val)

            if progress_callback:
                progress_callback(f"⚙️ 4️⃣ Đang tạo bản nháp và kiểm tra sản phẩm (Giá: {price_val:,}đ)...")
            create_response = create_woocommerce_product(config, product_payload)
            if create_response and create_response.get("id"):
                # Phản hồi trực tiếp từ POST chứng minh bản nháp thuộc lượt hiện tại.
                created_product = create_response
                owns_created_product = True
            else:
                # POST có thể đã thành công trên server nhưng client bị timeout trước
                # khi nhận response. Chỉ nhận lại bản nháp có đúng mã lượt đồng bộ;
                # tuyệt đối không chiếm hoặc xóa sản phẩm do máy khác vừa tạo.
                recovered_draft = find_woocommerce_product_by_page_id_with_retry(config, page_id)
                if recovered_draft:
                    validate_sync_attempt_ownership(recovered_draft, page_id, sync_attempt_id)
                    created_product = recovered_draft
                    owns_created_product = True
            if not created_product or not created_product.get("id"):
                raise RuntimeError("WooCommerce không tạo được bản nháp sản phẩm.")
            validate_sync_attempt_ownership(created_product, page_id, sync_attempt_id)
            verify_product_image_ids(created_product, uploaded_media_ids)

            try:
                publish_response = update_woocommerce_product(
                    config,
                    created_product.get("id"),
                    {
                        "status": "publish",
                        "meta_data": [{"key": "_khd_sync_verified", "value": "1"}],
                    },
                )
            except Exception:
                # Tương tự, PUT publish có thể hoàn tất trên server dù client timeout.
                recovered_product = find_woocommerce_product_by_page_id_with_retry(config, page_id)
                if (
                    recovered_product
                    and recovered_product.get("id") == created_product.get("id")
                    and recovered_product.get("status") == "publish"
                ):
                    validate_sync_attempt_ownership(recovered_product, page_id, sync_attempt_id)
                    publish_response = recovered_product
                else:
                    raise
            # Chỉ đánh dấu là đã publish sau khi WooCommerce xác nhận thật sự.
            # Nếu API trả 200 nhưng vẫn là draft, nhánh rollback bên dưới vẫn hoạt động.
            published_product = require_published_product(publish_response)
            verify_product_image_ids(published_product, uploaded_media_ids)
            if str(_product_meta_map(published_product).get("_khd_sync_verified")) != "1":
                raise RuntimeError("WooCommerce chưa lưu dấu xác minh đồng bộ an toàn.")

            product_url = published_product.get("permalink", "")
            if not product_url:
                raise RuntimeError("WooCommerce không trả permalink sản phẩm.")
            if progress_callback:
                progress_callback("📝 5️⃣ Cập nhật trạng thái Đã đăng lên Notion...")
            update_notion_status(token, page_id, product_url)
            processed_products.append({"title": product_title, "url": product_url})
        except Exception as exc:
            log_message(f"Lỗi xử lý '{product_title}': {exc}")
            if published_product:
                failed_products.append({
                    "title": product_title,
                    "url": published_product.get("permalink", ""),
                    "error": f"Sản phẩm đã publish nhưng Notion chưa hoàn tất: {exc}",
                })
            else:
                if owns_created_product and created_product and created_product.get("id"):
                    delete_woocommerce_product(config, created_product.get("id"))
                rollback_uploaded_media(config, uploaded_media_ids)
                failed_products.append({"title": product_title, "error": str(exc)})
            if progress_callback:
                progress_callback(f"❌ Không đăng '{product_title}': {exc}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
                        
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
            f"• <b>{h(item.get('title') or 'Sản phẩm')}</b>:\n  ↳ {h(item.get('error') or 'Lỗi chưa xác định')}"
            for item in failed_products
        )
        return {
            "status": "error",
            "message": f"Không thể đăng sản phẩm do các lỗi sau:\n{err_details}",
            "count": 0,
            "errors": failed_products,
        }
    return {"status": "success", "message": "Không có sản phẩm hợp lệ cần đăng.", "count": 0}


def run_notion_sync_workflow(progress_callback=None, page_ids=None):
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
        return _run_notion_sync_workflow(progress_callback=progress_callback, page_ids=page_ids)
    finally:
        NOTION_SYNC_PROCESS_LOCK.release()
        NOTION_SYNC_LOCK.release()


if __name__ == "__main__":
    res = run_notion_sync_workflow()
    print(json.dumps(res, indent=2, ensure_ascii=False))
