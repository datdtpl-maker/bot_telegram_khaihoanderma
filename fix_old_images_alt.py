import os
import re
import sys
import json
import base64
import urllib.request
import urllib.error
import urllib.parse
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

def ssl_context():
    config = load_config()
    if config.get("SSL_NO_VERIFY", "").lower() in {"1", "true", "yes", "on"}:
        import ssl
        return ssl._create_unverified_context()
    return None

def log_msg(msg):
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {msg}", flush=True)

def wc_get(config, path, params):
    site_url = config.get("WORDPRESS_SITE_URL", "").rstrip("/")
    consumer_key = config.get("WOOCOMMERCE_CONSUMER_KEY")
    consumer_secret = config.get("WOOCOMMERCE_CONSUMER_SECRET")
    
    query = urllib.parse.urlencode(params)
    url = f"{site_url}/wp-json/wc/v3/{path.lstrip('/')}?{query}"
    token = base64.b64encode(f"{consumer_key}:{consumer_secret}".encode("ascii")).decode("ascii")
    
    headers = {
        "Authorization": f"Basic {token}",
        "User-Agent": "Alt Fixer Script"
    }
    
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=15, context=ssl_context()) as resp:
        return json.loads(resp.read().decode("utf-8"))

def wp_update_media_metadata(config, media_id, alt_text, title):
    url = f"{config.get('WORDPRESS_SITE_URL', '').rstrip('/')}/wp-json/wp/v2/media/{media_id}"
    token = base64.b64encode(f"{config.get('WORDPRESS_USERNAME')}:{config.get('WORDPRESS_PASSWORD')}".encode("utf-8")).decode("ascii")
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "Alt Fixer Script"
    }
    body = {
        "alt_text": alt_text,
        "title": title
    }
    try:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15, context=ssl_context()) as resp:
            return True
    except Exception as e:
        log_msg(f"Loi cap nhat anh ID {media_id}: {e}")
        return False

def fix_all_products_alt():
    config = load_config()
    if not config.get("WORDPRESS_SITE_URL"):
        log_msg("Loi: Khong tim thay file telegram_bot.env hoac cau hinh trong.")
        return
        
    log_msg("Bat dau quet tat ca san pham de sua thuoc tinh Alt cua hinh anh...")
    
    page = 1
    total_fixed_images = 0
    total_processed_products = 0
    
    while True:
        try:
            log_msg(f"Dang tai san pham trang {page}...")
            products = wc_get(config, "products", {"per_page": 100, "page": page})
            if not isinstance(products, list) or not products:
                break
                
            for product in products:
                product_id = product.get("id")
                product_name = product.get("name")
                images = product.get("images") or []
                
                if not images:
                    continue
                    
                log_msg(f"Xu ly san pham #{product_id}: '{product_name}' ({len(images)} anh)")
                
                for idx, img in enumerate(images):
                    img_id = img.get("id")
                    img_alt = img.get("alt") or ""
                    
                    if not img_alt.strip():
                        alt_text = product_name if idx == 0 else f"{product_name} {idx}"
                        success = wp_update_media_metadata(config, img_id, alt_text, alt_text)
                        if success:
                            total_fixed_images += 1
                            log_msg(f"  -> Da sua anh ID {img_id} thanh Alt: '{alt_text}'")
                            
                total_processed_products += 1
                
            if len(products) < 100:
                break
            page += 1
        except Exception as e:
            log_msg(f"Loi o trang {page}: {e}")
            break
            
    log_msg(f"Hoan thanh! Da xu ly {total_processed_products} san pham. Da bo sung Alt cho {total_fixed_images} hinh anh thieu Alt.")

if __name__ == "__main__":
    fix_all_products_alt()
