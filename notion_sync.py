import os
import re
import sys
import json
import base64
import html
import urllib.request
import urllib.error
import time
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
            text = rich_text_to_html(block["paragraph"]["rich_text"])
            text_lower = text.lower()
            if "danh mục" in text_lower and ("giá" in text_lower or "sp" in text_lower):
                # Tách dòng này theo dấu "-" để parse
                parts = text.split("-")
                for part in parts:
                    part_lower = part.lower()
                    if "danh mục" in part_lower:
                        if ":" in part:
                            category_name = part.split(":", 1)[1].strip()
                    elif "giá khuyến mãi" in part_lower or "giá km" in part_lower:
                        if ":" in part:
                            price_raw = part.split(":", 1)[1].strip().lower().replace("k", "000").replace(".", "").replace(",", "").replace(" ", "")
                            digits = re.sub(r"[^\d]", "", price_raw)
                            if digits:
                                sale_price_val = int(digits)
                    elif "giá" in part_lower:
                        if ":" in part:
                            price_raw = part.split(":", 1)[1].strip().lower().replace("k", "000").replace(".", "").replace(",", "").replace(" ", "")
                            digits = re.sub(r"[^\d]", "", price_raw)
                            if digits:
                                price_val = int(digits)
                continue
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
    filter_body = {
        "filter": {
            "property": "Trạng thái",
            "select": {
                "equals": "Báo IT đăng"
            }
        }
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    req = urllib.request.Request(url, headers=headers, method="POST", data=json.dumps(filter_body).encode("utf-8"))
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data.get("results", [])

def get_page_blocks(token, page_id):
    url = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100"
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8")).get("results", [])

def download_drive_folder(folder_url, temp_dir):
    import gdown
    os.makedirs(temp_dir, exist_ok=True)
    log_message(f"Downloading Google Drive: {folder_url}")
    try:
        gdown.download_folder(url=folder_url, output=str(temp_dir), quiet=True, use_cookies=False)
        image_extensions = {".png", ".jpg", ".jpeg", ".webp"}
        downloaded_images = []
        for root, _, files in os.walk(temp_dir):
            for file in files:
                p = Path(root) / file
                if p.suffix.lower() in image_extensions:
                    downloaded_images.append(p)
        return downloaded_images
    except Exception as e:
        log_message(f"Error downloading from Drive: {e}")
        return []

def wp_upload_media(config, file_path):
    url = f"{config.get('WORDPRESS_SITE_URL', '').rstrip('/')}/wp-json/wp/v2/media"
    token = base64.b64encode(f"{config.get('WORDPRESS_USERNAME')}:{config.get('WORDPRESS_PASSWORD')}".encode("utf-8")).decode("ascii")
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Disposition": f'attachment; filename="{file_path.name}"',
        "Content-Type": "image/png" if file_path.suffix.lower() == ".png" else "image/jpeg",
        "User-Agent": "Notion WooCommerce AutoSync"
    }
    try:
        data = file_path.read_bytes()
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        ctx = None
        if config.get("SSL_NO_VERIFY", "").lower() in {"1", "true", "yes", "on"}:
            ctx = urllib.request.ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            log_message(f"Uploaded image: {file_path.name} | WP Media ID: {res.get('id')}")
            return res.get("id")
    except Exception as e:
        log_message(f"Error uploading image {file_path.name}: {e}")
        return None

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
        with urllib.request.urlopen(req, context=ctx) as resp:
            categories = json.loads(resp.read().decode("utf-8"))
            for category in categories:
                if category.get("name", "").lower() == name.lower():
                    return category.get("id")
            
            # Create category if not found
            create_url = f"{config.get('WORDPRESS_SITE_URL', '').rstrip('/')}/wp-json/wc/v3/products/categories"
            create_data = json.dumps({"name": name}).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
            req = urllib.request.Request(create_url, data=create_data, headers=headers, method="POST")
            with urllib.request.urlopen(req, context=ctx) as resp2:
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
        with urllib.request.urlopen(req, context=ctx) as resp:
            product = json.loads(resp.read().decode("utf-8"))
            log_message(f"Created WooCommerce product: {product.get('name')} | ID: {product.get('id')}")
            return product
    except Exception as e:
        if hasattr(e, "read"):
            log_message(f"Error creating product: {e.read().decode('utf-8')}")
        else:
            log_message(f"Error creating product: {e}")
        return None

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
    with urllib.request.urlopen(req) as resp:
        log_message(f"Updated Notion page status to 'Đã đăng web' for ID: {page_id}")

def run_notion_sync_workflow():
    config = load_config()
    token = config.get("NOTION_TOKEN")
    db_id = config.get("NOTION_DATABASE_ID")
    
    if not token or not db_id:
        return {"status": "error", "message": "Thiếu cấu hình NOTION_TOKEN hoặc NOTION_DATABASE_ID trong telegram_bot.env."}
        
    try:
        pages = query_notion_pages_to_post(token, db_id)
    except Exception as e:
        return {"status": "error", "message": f"Không kết nối được Notion API: {e}"}
        
    if not pages:
        return {"status": "success", "message": "Không tìm thấy sản phẩm nào có trạng thái 'Báo IT đăng'.", "count": 0}
        
    processed_products = []
    
    for page in pages:
        page_id = page.get("id")
        properties = page.get("properties", {})
        
        # 1. Product title
        title_list = properties.get("Tên sản phẩm", {}).get("title", [])
        product_title = title_list[0].get("plain_text", "") if title_list else "Sản phẩm không tên"
        log_message(f"Start processing: {product_title}")
        
        # 2. Get focus keywords (lấy từ 2 đến 3 từ khóa đầu tiên cách nhau bởi dấu phẩy)
        keyword_list = properties.get("Từ khóa SEO Rank Math", {}).get("rich_text", [])
        seo_keywords_raw = keyword_list[0].get("plain_text", "") if keyword_list else ""
        if seo_keywords_raw:
            kw_parts = [k.strip() for k in seo_keywords_raw.split(",") if k.strip()]
            seo_keywords = ", ".join(kw_parts[:3])
        else:
            seo_keywords = ""
        
        # 3. Google Drive folder URL
        drive_url = properties.get("Media sản phẩm", {}).get("url")
        
        # 4. Related Page Content (Bài content Tây)
        relation = properties.get("Bài content Tây", {}).get("relation", [])
        if not relation:
            log_message(f"Bỏ qua '{product_title}': Cột 'Bài content Tây' trống.")
            continue
            
        related_page_id = relation[0].get("id")
        try:
            blocks = get_page_blocks(token, related_page_id)
        except Exception as e:
            log_message(f"Lỗi đọc nội dung liên kết của '{product_title}': {e}")
            continue
            
        product_description, category_name, price_val, sale_price_val = parse_notion_blocks(blocks)
        
        # 5. Download images from drive folder
        temp_dir = OUT_DIR / "temp_notion_images"
        if temp_dir.exists():
            for root, _, files in os.walk(temp_dir):
                for f in files:
                    try:
                        os.unlink(os.path.join(root, f))
                    except Exception:
                        pass
        
        downloaded_images = []
        if drive_url:
            downloaded_images = download_drive_folder(drive_url, temp_dir)
            
        # 6. Upload images to WordPress Media Library
        uploaded_media_ids = []
        for img_path in downloaded_images:
            media_id = wp_upload_media(config, img_path)
            if media_id:
                uploaded_media_ids.append(media_id)
                
        # 7. Map/Create category
        category_id = None
        if category_name:
            category_id = find_or_create_category(config, category_name)
            
        # 8. Build WooCommerce Product payload
        images_payload = [{"id": mid} for mid in uploaded_media_ids]
        categories_payload = [{"id": category_id}] if category_id else []
        
        product_payload = {
            "name": product_title,
            "type": "simple",
            "description": product_description,
            "regular_price": str(price_val),
            "status": "publish",
            "images": images_payload,
            "categories": categories_payload,
            "meta_data": [
                {
                    "key": "rank_math_focus_keyword",
                    "value": seo_keywords
                }
            ]
        }
        if sale_price_val > 0:
            product_payload["sale_price"] = str(sale_price_val)
        
        # 9. Create product on WooCommerce
        product_res = create_woocommerce_product(config, product_payload)
        if product_res:
            product_url = product_res.get("permalink", "")
            # 10. Update status and URL back to Notion
            try:
                update_notion_status(token, page_id, product_url)
                processed_products.append({"title": product_title, "url": product_url})
            except Exception as e:
                log_message(f"Lỗi cập nhật lại Notion cho '{product_title}': {e}")
                processed_products.append({"title": product_title, "url": product_url, "warning": "Chưa cập nhật được Notion"})
                
        # Clear temp images
        if temp_dir.exists():
            for root, _, files in os.walk(temp_dir):
                for f in files:
                    try:
                        os.unlink(os.path.join(root, f))
                    except Exception:
                        pass
                        
    if processed_products:
        msg = f"Đồng bộ thành công! Đã đăng <b>{len(processed_products)}</b> sản phẩm mới từ Notion lên WooCommerce."
        return {"status": "success", "message": msg, "count": len(processed_products), "products": processed_products}
    else:
        return {"status": "success", "message": "Quá trình đồng bộ hoàn tất nhưng không có sản phẩm mới nào được đăng (vui lòng kiểm tra lỗi chi tiết trong bot.log).", "count": 0}

if __name__ == "__main__":
    res = run_notion_sync_workflow()
    print(json.dumps(res, indent=2, ensure_ascii=False))
