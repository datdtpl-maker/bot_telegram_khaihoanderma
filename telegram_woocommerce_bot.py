import base64
import csv
import html
import json
import os
import re
import socket
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from urllib.parse import quote_plus
from bot_modules.google_reports import build_google_report_html, wants_google_report


CODEX_CONFIG = Path(os.environ.get("CODEX_CONFIG", r"C:\Users\datdt\.codex\config.toml"))
OUT_DIR = Path(__file__).resolve().parent
LOG_FILE = OUT_DIR / "bot.log"
NEW_PRODUCT_URLS_FILE = OUT_DIR / "new_product_urls.log"
STARTED_AT = datetime.now()
GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
DEFAULT_API_TIMEOUT_SECONDS = 15
UPLOAD_TIMEOUT_SECONDS = 120
LONG_POLL_TIMEOUT_SECONDS = 60
PING_CHECK_TIMEOUT_SECONDS = 4


def _read_config_text() -> str:
    return CODEX_CONFIG.read_text(encoding="utf-8")


def _extract_config_value(text: str, key: str) -> str | None:
    match = re.search(rf'^\s*{re.escape(key)}\s*=\s*(["\'])(.*?)\1\s*$', text, re.MULTILINE)
    return match.group(2) if match else None


def load_woocommerce_credentials() -> tuple[str, str, str]:
    site_url = os.environ.get("WORDPRESS_SITE_URL")
    consumer_key = os.environ.get("WOOCOMMERCE_CONSUMER_KEY")
    consumer_secret = os.environ.get("WOOCOMMERCE_CONSUMER_SECRET")

    if site_url and consumer_key and consumer_secret:
        return site_url.rstrip("/"), consumer_key, consumer_secret

    text = _read_config_text()
    wp_api_url = _extract_config_value(text, "WP_API_URL")
    custom_headers = _extract_config_value(text, "CUSTOM_HEADERS")

    if not wp_api_url or not custom_headers:
        raise RuntimeError("Khong tim thay cau hinh WooCommerce MCP trong config.toml.")

    parsed = urllib.parse.urlparse(wp_api_url)
    site_url = f"{parsed.scheme}://{parsed.netloc}"
    headers_json = json.loads(custom_headers)
    api_key = headers_json.get("X-MCP-API-Key", "")
    if ":" not in api_key:
        raise RuntimeError("CUSTOM_HEADERS khong co X-MCP-API-Key dung dinh dang ck:cs.")

    consumer_key, consumer_secret = api_key.split(":", 1)
    return site_url.rstrip("/"), consumer_key, consumer_secret


def load_wordpress_credentials() -> tuple[str, str, str]:
    site_url = os.environ.get("WORDPRESS_SITE_URL")
    username = os.environ.get("WORDPRESS_USERNAME")
    password = os.environ.get("WORDPRESS_PASSWORD")

    if site_url and username and password:
        return site_url.rstrip("/"), username, password

    text = _read_config_text()
    site_url = _extract_config_value(text, "WORDPRESS_SITE_URL")
    username = _extract_config_value(text, "WORDPRESS_USERNAME")
    password = _extract_config_value(text, "WORDPRESS_PASSWORD")

    if not site_url or not username or not password:
        raise RuntimeError(
            "Thiếu cấu hình WordPress. Hãy điền WORDPRESS_SITE_URL, WORDPRESS_USERNAME, "
            "WORDPRESS_PASSWORD trong telegram_bot.env."
        )
    return site_url.rstrip("/"), username, password


PENDING_ACTIONS: dict[int, dict] = {}
CHAT_LOCKS: defaultdict[int, Lock] = defaultdict(Lock)


LOG_LOCK = Lock()


def log(message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line, flush=True)
    try:
        with LOG_LOCK:
            # Giới hạn file log ở mức 5MB, nếu vượt quá sẽ tự động xoay log
            if LOG_FILE.exists() and LOG_FILE.stat().st_size > 5 * 1024 * 1024:
                backup_log = LOG_FILE.with_suffix(".log.bak")
                if backup_log.exists():
                    backup_log.unlink()
                LOG_FILE.rename(backup_log)
            with LOG_FILE.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception:
        pass


def ssl_context() -> ssl.SSLContext | None:
    value = os.environ.get("SSL_NO_VERIFY", "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return ssl._create_unverified_context()
    return None


def wc_request(
    path: str,
    params: dict[str, str | int] | None = None,
    method: str = "GET",
    body: dict | None = None,
    timeout: int = DEFAULT_API_TIMEOUT_SECONDS,
) -> object:
    site_url, consumer_key, consumer_secret = load_woocommerce_credentials()
    query = urllib.parse.urlencode(params or {})
    url = f"{site_url}/wp-json/wc/v3/{path.lstrip('/')}"
    if query:
        url = f"{url}?{query}"
    token = base64.b64encode(f"{consumer_key}:{consumer_secret}".encode("ascii")).decode("ascii")
    data = None
    headers = {
        "Authorization": f"Basic {token}",
        "User-Agent": "Codex Telegram WooCommerce Bot",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as response:
        return json.loads(response.read().decode("utf-8"))


def wc_get(path: str, params: dict[str, str | int], timeout: int = DEFAULT_API_TIMEOUT_SECONDS) -> object:
    return wc_request(path, params=params, timeout=timeout)


def wc_put(path: str, body: dict) -> object:
    return wc_request(path, method="PUT", body=body)


def wc_post(path: str, body: dict) -> object:
    return wc_request(path, method="POST", body=body)


def wc_delete(path: str, params: dict[str, str | int] | None = None) -> object:
    return wc_request(path, params=params, method="DELETE")


def wp_request(path: str, method: str = "GET", body: dict | None = None, timeout: int = DEFAULT_API_TIMEOUT_SECONDS) -> object:
    site_url, username, password = load_wordpress_credentials()
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    data = None
    headers = {
        "Authorization": f"Basic {token}",
        "User-Agent": "Codex Telegram WordPress Bot",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = urllib.request.Request(
        f"{site_url}/wp-json/wp/v2/{path.lstrip('/')}",
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as response:
        return json.loads(response.read().decode("utf-8"))


def wp_create_post(title: str, content: str, excerpt: str = "", status: str = "draft") -> dict:
    return wp_request(
        "posts",
        method="POST",
        body={
            "title": title,
            "content": content,
            "excerpt": excerpt,
            "status": status,
        },
    )
def site_base_url() -> str:
    site_url = os.environ.get("WORDPRESS_SITE_URL")
    if site_url:
        return site_url.rstrip("/")
    site_url, _, _ = load_woocommerce_credentials()
    return site_url.rstrip("/")


def sitemap_url() -> str:
    configured = os.environ.get("SITEMAP_URL", "").strip()
    if configured:
        return configured
    return f"{site_base_url()}/sitemap_index.xml"


def google_inspect_url(page_url: str) -> str:
    return f"https://search.google.com/search-console?resource_id={site_base_url()}/"


def ping_google_sitemap() -> tuple[bool, str]:
    # Google removed the public sitemap ping endpoint. Keep this helper as a
    # no-network status provider so old call sites stay simple and truthful.
    return False, "Google đã bỏ sitemap ping; dùng GSC Inspect để yêu cầu index."


def log_new_product_url(product: dict) -> None:
    link = product.get("permalink") or ""
    if not link:
        return
    line = (
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\t"
        f"{product.get('id')}\t{product.get('name')}\t{link}\n"
    )
    try:
        with NEW_PRODUCT_URLS_FILE.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except Exception as exc:
        log(f"Khong ghi duoc new_product_urls.log: {exc}")


def indexing_after_publish_html(product: dict) -> str:
    link = product.get("permalink") or ""
    log_new_product_url(product)
    _, ping_status = ping_google_sitemap()
    sitemap = sitemap_url()

    lines = [
        "",
        "<b>Google index</b>",
        f"• Sitemap: <code>{h(sitemap)}</code>",
        f"• Tự động ping sitemap: <b>không dùng</b> - {h(ping_status)}",
    ]
    if link:
        lines.append(f'• URL sản phẩm: <a href="{h(link)}">Mở sản phẩm</a>')
        lines.append(f'• GSC Inspect: <a href="{h(google_inspect_url(link))}">Mở để yêu cầu index tay</a>')
    lines.append("• Google vẫn quyết định thời gian crawl/index sau khi nhận sitemap.")
    return "\n".join(lines)


def month_bounds(month: str) -> tuple[str, str]:
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError("Thang phai co dinh dang YYYY-MM, vi du 2026-05.")
    year, mon = map(int, month.split("-"))
    start = datetime(year, mon, 1)
    if mon == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, mon + 1, 1)
    return start.strftime("%Y-%m-%dT00:00:00"), end.strftime("%Y-%m-%dT00:00:00")


def fetch_orders(month: str) -> list[dict]:
    after, before = month_bounds(month)
    orders: list[dict] = []
    page = 1
    while True:
        batch = wc_get(
            "orders",
            {
                "after": after,
                "before": before,
                "per_page": 100,
                "page": page,
                "orderby": "date",
                "order": "asc",
                "status": "any",
            },
        )
        if not isinstance(batch, list):
            raise RuntimeError("WooCommerce tra ve du lieu don hang khong hop le.")
        orders.extend(batch)
        if len(batch) < 100:
            return orders
        page += 1


def summarize_orders(orders: list[dict]) -> tuple[list[dict], list[dict], dict[str, int], float, float]:
    product_map: dict[str, dict] = {}
    statuses: dict[str, int] = defaultdict(int)
    total_revenue = 0.0
    line_revenue = 0.0

    for order in orders:
        statuses[str(order.get("status", ""))] += 1
        total_revenue += float(order.get("total") or 0)
        for item in order.get("line_items", []):
            key = f"{item.get('product_id')}-{item.get('variation_id') or 0}"
            if key not in product_map:
                product_map[key] = {
                    "product_id": item.get("product_id"),
                    "variation_id": item.get("variation_id"),
                    "name": item.get("name", ""),
                    "quantity": 0,
                    "subtotal": 0.0,
                    "total": 0.0,
                    "tax": 0.0,
                    "order_count": 0,
                }
            row = product_map[key]
            row["quantity"] += int(item.get("quantity") or 0)
            row["subtotal"] += float(item.get("subtotal") or 0)
            row["total"] += float(item.get("total") or 0)
            row["tax"] += float(item.get("total_tax") or 0)
            row["order_count"] += 1
            line_revenue += float(item.get("total") or 0)

    order_rows = []
    for order in orders:
        billing = order.get("billing", {})
        items = "; ".join(f"{item.get('name')} x{item.get('quantity')}" for item in order.get("line_items", []))
        order_rows.append(
            {
                "id": order.get("id"),
                "number": order.get("number"),
                "date_created": order.get("date_created"),
                "status": order.get("status"),
                "customer": f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip(),
                "phone": billing.get("phone", ""),
                "email": billing.get("email", ""),
                "payment_method": order.get("payment_method_title", ""),
                "currency": order.get("currency", ""),
                "shipping_total": order.get("shipping_total", ""),
                "discount_total": order.get("discount_total", ""),
                "total": order.get("total", ""),
                "items": items,
            }
        )

    products = sorted(product_map.values(), key=lambda row: row["total"], reverse=True)
    return order_rows, products, dict(statuses), total_revenue, line_revenue


def money(value: float | str) -> str:
    return f"{round(float(value)):,.0f}".replace(",", ".")


def h(value: object) -> str:
    return html.escape(str(value), quote=False)


def normalize_text(text: str) -> str:
    return text.strip().lower()


def current_month() -> str:
    return datetime.now().strftime("%Y-%m")


def day_bounds(day: datetime) -> tuple[str, str]:
    start = datetime(day.year, day.month, day.day)
    end = start + timedelta(days=1)
    return start.strftime("%Y-%m-%dT00:00:00"), end.strftime("%Y-%m-%dT00:00:00")


def extract_day(text: str) -> datetime | None:
    normalized = normalize_text(text)
    if "hôm nay" in normalized or "hom nay" in normalized or "today" in normalized:
        return datetime.now()

    match = re.search(r"\b(20\d{2})[-/.](0?[1-9]|1[0-2])[-/.](0?[1-9]|[12]\d|3[01])\b", normalized)
    if match:
        year, month, day = map(int, match.groups())
        return datetime(year, month, day)

    match = re.search(r"\b(0?[1-9]|[12]\d|3[01])[-/.](0?[1-9]|1[0-2])[-/.](20\d{2})\b", normalized)
    if match:
        day, month, year = map(int, match.groups())
        return datetime(year, month, day)

    match = re.search(
        r"\bng[aà]y\s+(0?[1-9]|[12]\d|3[01])\s+th[aá]ng\s+(0?[1-9]|1[0-2])(?:\s+n[aă]m\s+(20\d{2}))?",
        normalized,
    )
    if match:
        day, month, year = match.groups()
        return datetime(int(year or datetime.now().year), int(month), int(day))
    return None



def make_date(year: int, month: int, day: int) -> datetime | None:
    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def extract_date_range(text: str) -> tuple[datetime, datetime] | None:
    normalized = normalize_text(text)

    # Khớp định dạng tiếng Việt: ngày X đến Y tháng M năm YYYY hoặc ngày X đến Y/M/YYYY
    viet_range = re.search(
        r"(?:từ|tu|from)?\s*(?:ngày|ngay)?\s*(?P<d1>\d{1,2})\s*(?:đến|den|tới|toi|to|-)\s*(?:ngày|ngay)?\s*(?P<d2>\d{1,2})\s*(?:tháng|thang|/)\s*(?P<m>\d{1,2})(?:\s*(?:năm|nam|/)\s*(?P<y>\d{4}))?",
        normalized,
    )
    if viet_range:
        d1 = int(viet_range.group("d1"))
        d2 = int(viet_range.group("d2"))
        m = int(viet_range.group("m"))
        y = int(viet_range.group("y") or datetime.now().year)
        start = make_date(y, m, d1)
        end = make_date(y, m, d2)
        if start and end:
            return (start, end) if start <= end else (end, start)

    iso = re.search(
        r"(?:từ|tu|from)\s*(?:(?:ngày|ngay)\s*)?(20\d{2})[-/.](0?[1-9]|1[0-2])[-/.](0?[1-9]|[12]\d|3[01])\s*(?:đến|den|tới|toi|to|-)\s*(?:(?:ngày|ngay)\s*)?(20\d{2})[-/.](0?[1-9]|1[0-2])[-/.](0?[1-9]|[12]\d|3[01])",
        normalized,
    )
    if iso:
        y1, m1, d1, y2, m2, d2 = map(int, iso.groups())
        start = make_date(y1, m1, d1)
        end = make_date(y2, m2, d2)
        if start and end:
            return (start, end) if start <= end else (end, start)

    vietnamese = re.search(
        r"(?:từ|tu|from)\s*(?:(?:ngày|ngay)\s*)?(0?[1-9]|[12]\d|3[01])[-/.](0?[1-9]|1[0-2])(?:[-/.](20\d{2}))?\s*(?:đến|den|tới|toi|to|-)\s*(?:(?:ngày|ngay)\s*)?(0?[1-9]|[12]\d|3[01])[-/.](0?[1-9]|1[0-2])(?:[-/.](20\d{2}))?",
        normalized,
    )
    if vietnamese:
        d1, m1, y1, d2, m2, y2 = vietnamese.groups()
        year1 = int(y1 or y2 or datetime.now().year)
        year2 = int(y2 or y1 or datetime.now().year)
        start = make_date(year1, int(m1), int(d1))
        end = make_date(year2, int(m2), int(d2))
        if start and end:
            return (start, end) if start <= end else (end, start)

    compact = re.search(
        r"\b(0?[1-9]|[12]\d|3[01])[-/.](0?[1-9]|1[0-2])(?:[-/.](20\d{2}))?\s*(?:đến|den|tới|toi|to|-)\s*(0?[1-9]|[12]\d|3[01])[-/.](0?[1-9]|1[0-2])(?:[-/.](20\d{2}))?\b",
        normalized,
    )
    if compact:
        d1, m1, y1, d2, m2, y2 = compact.groups()
        year1 = int(y1 or y2 or datetime.now().year)
        year2 = int(y2 or y1 or datetime.now().year)
        start = make_date(year1, int(m1), int(d1))
        end = make_date(year2, int(m2), int(d2))
        if start and end:
            return (start, end) if start <= end else (end, start)

    return None

def extract_month(text: str) -> str:
    normalized = normalize_text(text)
    match = re.search(r"\b(20\d{2})[-/.](0?[1-9]|1[0-2])\b", normalized)
    if match:
        year, month = match.groups()
        return f"{int(year):04d}-{int(month):02d}"

    match = re.search(r"\bth[aá]ng\s+(0?[1-9]|1[0-2])(?:\s*(?:n[aă]m)?\s*(20\d{2}))?", normalized)
    if match:
        month, year = match.groups()
        return f"{int(year or datetime.now().year):04d}-{int(month):02d}"

    if "tháng này" in normalized or "thang nay" in normalized:
        return current_month()

    return current_month()


def wants_woocommerce(text: str) -> bool:
    normalized = normalize_text(text)
    keywords = [
        "\u0111\u01a1n h\u00e0ng",
        "don hang",
        "doanh thu",
        "b\u00e1o c\u00e1o",
        "bao cao",
        "s\u1ea3n ph\u1ea9m",
        "san pham",
        "woocommerce",
        "shop",
    ]
    if any(keyword in normalized for keyword in keywords):
        return True
    asks_orders = any(keyword in normalized for keyword in ["\u0111\u01a1n", "don"])
    has_period = bool(extract_date_range_to_now(text) or extract_date_range(text) or extract_day(text))
    return asks_orders and has_period


def parse_order_detail_request(text: str) -> str | None:
    normalized = normalize_text(text)
    if not any(keyword in normalized for keyword in ["chi tiết đơn hàng", "chi tiet don hang", "xem đơn hàng", "xem don hang", "đơn hàng", "don hang"]):
        return None
    if extract_day(text) and not re.search(r"(?:#|id|mã|ma)\s*\d{3,}", normalized):
        return None
    if any(keyword in normalized for keyword in ["tháng", "thang", "các đơn", "cac don"]) and not re.search(r"(?:#|id|mã|ma)\s*\d{3,}", normalized):
        return None
    match = re.search(r"(?<![-/\d])(?:#)?(\d{3,})(?![-/\d])", text)
    return match.group(1) if match else None


def wants_order_details_export(text: str) -> bool:
    normalized = normalize_text(text)
    has_order = any(keyword in normalized for keyword in ["đơn hàng", "don hang", "đơn", "don"])
    has_detail = any(keyword in normalized for keyword in ["chi tiết", "chi tiet", "đầy đủ", "day du", "file", "excel", "xuất", "xuat"])
    has_range = bool(extract_date_range(text))
    has_period = has_range or bool(extract_day(text)) or "tháng" in normalized or "thang" in normalized or re.search(r"\b20\d{2}[-/.](0?[1-9]|1[0-2])\b", normalized)
    return has_order and bool(has_period) and (has_detail or has_range)


def wants_today_orders(text: str) -> bool:
    normalized = normalize_text(text)
    has_today = any(keyword in normalized for keyword in ["hôm nay", "hom nay", "today"])
    has_order = any(keyword in normalized for keyword in ["đơn hàng", "don hang", "đơn", "don"])
    return has_today and has_order


def fetch_orders_between(after: str, before: str) -> list[dict]:
    orders: list[dict] = []
    page = 1
    while True:
        batch = wc_get(
            "orders",
            {
                "after": after,
                "before": before,
                "per_page": 100,
                "page": page,
                "orderby": "date",
                "order": "asc",
                "status": "any",
            },
        )
        if not isinstance(batch, list):
            raise RuntimeError("WooCommerce trả về dữ liệu đơn hàng không hợp lệ.")
        orders.extend(batch)
        if len(batch) < 100:
            return orders
        page += 1


def extract_date_range_to_now(text: str) -> tuple[datetime, datetime] | None:
    normalized = normalize_text(text)
    match = re.search(
        "(?:t\u1eeb|tu|from)\\s*(?:(?:ng\u00e0y|ngay)\\s*)?(0?[1-9]|[12]\\d|3[01])[-/.](0?[1-9]|1[0-2])(?:[-/.](20\\d{2}))?\\s*(?:\u0111\u1ebfn|den|t\u1edbi|toi|to)\\s*(?:h\u00f4m nay|hom nay|nay|hi\u1ec7n t\u1ea1i|hien tai|b\u00e2y gi\u1edd|bay gio|today|now)",
        normalized,
    )
    if not match:
        return None
    day, month, year = match.groups()
    start = make_date(int(year or datetime.now().year), int(month), int(day))
    if not start:
        return None
    return start, datetime.now()


def report_period_from_text(text: str) -> tuple[str, str, str]:
    to_now = extract_date_range_to_now(text)
    if to_now:
        start_day, end_day = to_now
        after, before = day_bounds(start_day)
        _, before = day_bounds(end_day)
        label = f"t\u1eeb ng\u00e0y {start_day.strftime('%d/%m/%Y')} \u0111\u1ebfn ng\u00e0y {end_day.strftime('%d/%m/%Y')}"
        return after, before, label

    date_range = extract_date_range(text)
    if date_range:
        start_day, end_day = date_range
        after, _ = day_bounds(start_day)
        _, before = day_bounds(end_day)
        label = f"t\u1eeb ng\u00e0y {start_day.strftime('%d/%m/%Y')} \u0111\u1ebfn ng\u00e0y {end_day.strftime('%d/%m/%Y')}"
        return after, before, label

    day = extract_day(text)
    if day and any(word in normalize_text(text) for word in ["ng\u00e0y", "ngay", "h\u00f4m nay", "hom nay", "today"]):
        after, before = day_bounds(day)
        return after, before, f"ng\u00e0y {day.strftime('%d/%m/%Y')}"

    month = extract_month(text)
    after, before = month_bounds(month)
    return after, before, f"th\u00e1ng {month}"


def build_woocommerce_html(text: str) -> str:
    after, before, label = report_period_from_text(text)
    orders = fetch_orders_between(after, before)
    order_rows, products, statuses, total_revenue, line_revenue = summarize_orders(orders)
    fee_diff = total_revenue - line_revenue

    lines = [
        f"<b>B\u00e1o c\u00e1o WooCommerce {h(label)}</b>",
        "",
        "<b>T\u1ed5ng quan</b>",
        f"\u2022 T\u1ed5ng \u0111\u01a1n: <b>{len(orders)}</b>",
        f"\u2022 T\u1ed5ng doanh thu: <b>{money(total_revenue)} VND</b>",
        f"\u2022 Doanh thu s\u1ea3n ph\u1ea9m: <b>{money(line_revenue)} VND</b>",
        f"\u2022 Ch\u00eanh l\u1ec7ch v\u1eadn chuy\u1ec3n/ph\u1ee5 ph\u00ed/gi\u1ea3m gi\u00e1: <b>{money(fee_diff)} VND</b>",
    ]

    if statuses:
        lines.extend(["", "<b>Trạng thái đơn</b>"])
        for status, count in sorted(statuses.items(), key=lambda item: item[0]):
            lines.append(f"\u2022 <code>{h(order_status_label(status or 'unknown'))}</code>: <b>{count}</b>")

    if products:
        lines.extend(["", "<b>S\u1ea3n ph\u1ea9m b\u00e1n ra</b>"])
        for row in products[:10]:
            lines.append(
                f"\u2022 <b>{h(row.get('name', 'S\u1ea3n ph\u1ea9m'))}</b> - "
                f"SL: <b>{h(row.get('quantity', 0))}</b> - "
                f"Doanh thu: <b>{money(row.get('total') or 0)} VND</b>"
            )
        if len(products) > 10:
            lines.append(f"... v\u00e0 {len(products) - 10} s\u1ea3n ph\u1ea9m kh\u00e1c.")

    if order_rows:
        lines.extend(["", "<b>\u0110\u01a1n h\u00e0ng g\u1ea7n \u0111\u00e2y</b>"])
        for row in order_rows[-10:]:
            date_text = str(row.get("date_created") or "").replace("T", " ")[:16]
            customer = row.get("customer") or "Ch\u01b0a c\u00f3 t\u00ean"
            lines.append(
                f"\u2022 <b>#{h(row.get('number'))}</b> - {h(date_text)} - "
                f"{h(customer)} - <b>{money(row.get('total') or 0)} VND</b> - "
                f"<code>{h(order_status_label(row.get('status') or ''))}</code>"
            )

    if not orders:
        lines.append("\nKh\u00f4ng c\u00f3 \u0111\u01a1n h\u00e0ng trong kho\u1ea3ng th\u1eddi gian n\u00e0y.")
    return "\n".join(lines)


def build_today_orders_html() -> str:
    today = datetime.now()
    start = today.strftime("%Y-%m-%dT00:00:00")
    tomorrow = datetime(today.year, today.month, today.day) + timedelta(days=1)
    end = tomorrow.strftime("%Y-%m-%dT00:00:00")
    orders = fetch_orders_between(start, end)

    if not orders:
        return (
            "<b>Kiểm tra đơn hàng hôm nay</b>\n\n"
            f"Ngày: <code>{h(today.strftime('%Y-%m-%d'))}</code>\n"
            "Kết quả: <b>Không có đơn hàng mới hôm nay.</b>"
        )

    total = sum(float(order.get("total") or 0) for order in orders)
    lines = [
        "<b>Kiểm tra đơn hàng hôm nay</b>",
        "",
        f"Ngày: <code>{h(today.strftime('%Y-%m-%d'))}</code>",
        f"Kết quả: <b>Có {len(orders)} đơn hàng hôm nay.</b>",
        f"Tổng doanh thu: <b>{money(total)} VND</b>",
        "",
        "<b>Danh sách đơn</b>",
    ]
    for order in orders[:20]:
        billing = order.get("billing") or {}
        customer_name = f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip() or "Chưa có tên"
        lines.append(
            f"• <b>#{h(order.get('number'))}</b> - {h(customer_name)} - "
            f"{money(order.get('total') or 0)} VND - <code>{h(order_status_label(order.get('status') or ''))}</code>"
        )
    if len(orders) > 20:
        lines.append(f"... và {len(orders) - 20} đơn khác.")
    return "\n".join(lines)


def build_order_detail_html(order_id: str) -> str:
    order = wc_get(f"orders/{order_id}", {})
    if not isinstance(order, dict) or not order.get("id"):
        return f"<b>Không tìm thấy đơn hàng</b>\nID: <code>{h(order_id)}</code>"

    billing = order.get("billing") or {}
    customer_name = f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip() or "Chưa có tên"
    phone = billing.get("phone") or ""
    email = billing.get("email") or ""
    shipping_address = format_order_address(order)

    product_lines = []
    for item in order.get("line_items", []):
        product_lines.append(
            "• "
            f"<b>{h(item.get('name', 'Sản phẩm'))}</b>\n"
            f"  ID sản phẩm: <code>{h(item.get('product_id', ''))}</code>\n"
            f"  Số lượng: <b>{h(item.get('quantity', 0))}</b>\n"
            f"  Thành tiền: <b>{money(item.get('total') or 0)} VND</b>"
        )
    products_html = "\n".join(product_lines) if product_lines else "Không có sản phẩm trong đơn."

    return (
        "<b>Chi tiết đơn hàng WooCommerce</b>\n\n"
        f"• ID đơn hàng: <code>{h(order.get('id'))}</code>\n"
        f"• Mã đơn: <b>#{h(order.get('number'))}</b>\n"
        f"• Trạng thái: <b>{h(order_status_label(order.get('status') or ''))}</b>\n"
        f"• Ngày tạo: <code>{h(str(order.get('date_created', '')).replace('T', ' ')[:19])}</code>\n\n"
        f"<b>Người mua</b>\n"
        f"• Tên: <b>{h(customer_name)}</b>\n"
        + (f"• SĐT: <code>{h(phone)}</code>\n" if phone else "")
        + (f"• Email: <code>{h(email)}</code>\n" if email else "")
        + (f"• Địa chỉ nhận hàng: <b>{h(shipping_address)}</b>\n" if shipping_address else "")
        + "\n"
        f"<b>Sản phẩm</b>\n{products_html}\n\n"
        f"<b>Tổng doanh thu đơn hàng:</b> {money(order.get('total') or 0)} VND"
    )


def format_order_address(order: dict) -> str:
    shipping = order.get("shipping") or {}
    billing = order.get("billing") or {}
    source = shipping if any((shipping.get(key) or "").strip() for key in ["address_1", "address_2", "city", "state"]) else billing
    parts = [
        source.get("address_1") or "",
        source.get("address_2") or "",
        source.get("city") or "",
        source.get("state") or "",
        source.get("postcode") or "",
        source.get("country") or "",
    ]
    return ", ".join(part.strip() for part in parts if str(part).strip())


def order_detail_export_rows(orders: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for order in orders:
        billing = order.get("billing") or {}
        customer_name = f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip() or "Chưa có tên"
        address = format_order_address(order)
        line_items = order.get("line_items") or [{}]
        for item in line_items:
            rows.append(
                {
                    "order_id": order.get("id"),
                    "order_number": f"#{order.get('number')}",
                    "date_created": str(order.get("date_created", "")).replace("T", " ")[:19],
                    "status": order_status_label(order.get("status") or ""),
                    "customer": customer_name,
                    "phone": billing.get("phone") or "",
                    "email": billing.get("email") or "",
                    "address": address,
                    "product_id": item.get("product_id", "") if isinstance(item, dict) else "",
                    "product_name": item.get("name", "") if isinstance(item, dict) else "",
                    "quantity": int(item.get("quantity") or 0) if isinstance(item, dict) else 0,
                    "line_total": int(round(float(item.get("total") or 0))) if isinstance(item, dict) else 0,
                    "order_total": int(round(float(order.get("total") or 0))),
                    "shipping_total": int(round(float(order.get("shipping_total") or 0))),
                    "discount_total": int(round(float(order.get("discount_total") or 0))),
                    "payment_method": order.get("payment_method_title") or "",
                    "customer_note": order.get("customer_note") or "",
                }
            )
    return rows


def export_order_details_report(month: str) -> tuple[str, Path]:
    after, before = month_bounds(month)
    return export_order_details_report_between(f"tháng {month}", after, before, f"chi_tiet_don_hang_{month}")


def export_order_details_report_between(label: str, after: str, before: str, filename_prefix: str) -> tuple[str, Path]:
    orders = fetch_orders_between(after, before)
    rows = order_detail_export_rows(orders)
    safe_prefix = re.sub(r"[^A-Za-z0-9_-]+", "_", filename_prefix).strip("_") or "chi_tiet_don_hang"
    xlsx_path = OUT_DIR / f"{safe_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    write_order_details_xlsx(rows, xlsx_path)
    total = sum(float(order.get("total") or 0) for order in orders)
    caption = (
        f"<b>Chi tiết đơn hàng WooCommerce {h(label)}</b>\n"
        f"Tổng đơn: <b>{len(orders)}</b> | Tổng doanh thu: <b>{money(total)} VND</b>\n"
        "File Excel gồm thông tin người mua, địa chỉ nhận hàng, sản phẩm, số lượng và doanh thu."
    )
    return caption, xlsx_path


def export_order_details_report_from_text(text: str) -> tuple[str, Path]:
    to_now = extract_date_range_to_now(text)
    if to_now:
        start_day, end_day = to_now
        after, _ = day_bounds(start_day)
        _, before = day_bounds(end_day)
        label = f"từ ngày {start_day.strftime('%d/%m/%Y')} đến ngày {end_day.strftime('%d/%m/%Y')}"
        prefix = f"chi_tiet_don_hang_{start_day.strftime('%Y%m%d')}_{end_day.strftime('%Y%m%d')}"
        return export_order_details_report_between(label, after, before, prefix)

    date_range = extract_date_range(text)
    if date_range:
        start_day, end_day = date_range
        after, _ = day_bounds(start_day)
        _, before = day_bounds(end_day)
        label = f"từ ngày {start_day.strftime('%d/%m/%Y')} đến ngày {end_day.strftime('%d/%m/%Y')}"
        prefix = f"chi_tiet_don_hang_{start_day.strftime('%Y%m%d')}_{end_day.strftime('%Y%m%d')}"
        return export_order_details_report_between(label, after, before, prefix)

    day = extract_day(text)
    if day:
        after, before = day_bounds(day)
        label = f"ng\u00e0y {day.strftime('%d/%m/%Y')}"
        return export_order_details_report_between(label, after, before, f"chi_tiet_don_hang_{day.strftime('%Y-%m-%d')}")
    month = extract_month(text)
    return export_order_details_report(month)


def wants_product_catalog_report(text: str) -> bool:
    normalized = normalize_text(text)
    has_product = any(keyword in normalized for keyword in ["sản phẩm", "san pham", "product"])
    has_catalog = any(keyword in normalized for keyword in ["bao nhiêu", "bao nhieu", "tất cả", "tat ca", "đang bán", "dang ban", "đang có", "dang co", "hết hàng", "het hang", "có hàng", "co hang", "tồn kho", "ton kho"])
    return has_product and has_catalog


def wants_ping(text: str) -> bool:
    normalized = normalize_text(text)
    return normalized in {
        "ping",
        "/ping",
        "kiem tra bot",
        "kiểm tra bot",
        "bot còn chạy không",
        "bot con chay khong",
        "bot hoạt động không",
        "bot hoat dong khong",
        "test bot",
    }


def format_uptime() -> str:
    seconds = int((datetime.now() - STARTED_AT).total_seconds())
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days} ngày")
    if hours:
        parts.append(f"{hours} giờ")
    if minutes:
        parts.append(f"{minutes} phút")
    parts.append(f"{seconds} giây")
    return " ".join(parts)


def build_ping_html() -> str:
    def check_woocommerce() -> str:
        try:
            products = wc_get("products", {"per_page": 1, "page": 1}, timeout=PING_CHECK_TIMEOUT_SECONDS)
            return f"WooCommerce: <b>OK</b> ({len(products) if isinstance(products, list) else 0} mẫu)"
        except Exception as exc:
            return f"WooCommerce: <b>Lỗi</b> - <code>{h(exc)}</code>"

    def check_wordpress() -> str:
        try:
            wp_request("users/me", timeout=PING_CHECK_TIMEOUT_SECONDS)
            return "WordPress: <b>OK</b>"
        except Exception as exc:
            return f"WordPress: <b>Lỗi</b> - <code>{h(exc)}</code>"

    checks = ["Telegram: <b>OK</b> (đã nhận được tin nhắn)"]
    executor = ThreadPoolExecutor(max_workers=2)
    futures = {
        executor.submit(check_woocommerce): "WooCommerce",
        executor.submit(check_wordpress): "WordPress",
    }
    done, pending = wait(futures, timeout=PING_CHECK_TIMEOUT_SECONDS + 1)
    for future, name in futures.items():
        if future in done:
            checks.append(future.result())
        else:
            checks.append(f"{name}: <b>Chậm</b> - quá {PING_CHECK_TIMEOUT_SECONDS + 1} giây chưa phản hồi")
    executor.shutdown(wait=False, cancel_futures=True)

    return (
        "<b>Trạng thái bot</b>\n\n"
        f"• Bot: <b>đang hoạt động</b>\n"
        f"• Thời gian chạy: <b>{h(format_uptime())}</b>\n"
        f"• Thời điểm kiểm tra: <code>{h(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}</code>\n\n"
        + "\n".join(f"• {line}" for line in checks)
    )


def stock_label(value: str) -> str:
    if value == "instock":
        return "có hàng"
    if value == "outofstock":
        return "hết hàng"
    if value == "onbackorder":
        return "chờ hàng"
    return value or "không rõ"


def order_status_label(value: str) -> str:
    status_map = {
        "pending": "chờ thanh toán",
        "processing": "đang xử lý",
        "on-hold": "tạm giữ",
        "completed": "đã hoàn thành",
        "cancelled": "đã hủy",
        "refunded": "đã hoàn tiền",
        "failed": "thất bại",
        "checkout-draft": "nháp thanh toán",
    }
    return status_map.get(value.lower(), value)


def parse_price(value: str) -> str:
    digits = re.sub(r"[^\d]", "", value)
    if not digits:
        raise ValueError("Không đọc được giá mới.")
    return str(int(digits))


def plain_ascii(value: str) -> str:
    value = unicodedata.normalize("NFD", value.lower())
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return value.replace("\u0111", "d")


def split_variant_from_product_name(name: str) -> tuple[str, str | None]:
    plain_name = plain_ascii(name)
    match = re.search(
        r"(?P<product>.+?)\s+(?:bien\s*the|phan\s*loai|loai|mau|dang)\s+(?P<variant>[^,;|]+)$",
        plain_name,
        flags=re.IGNORECASE,
    )
    if not match:
        return name.strip(" :-"), None
    product_start, product_end = match.span("product")
    variant_start, variant_end = match.span("variant")
    product_name = name[product_start:product_end].strip(" :-")
    variant_name = name[variant_start:variant_end].strip(" :-")
    return product_name or name.strip(" :-"), variant_name or None


def parse_new_product_metadata(caption: str) -> dict:
    metadata: dict = {}
    text = caption or ""
    price_match = re.search(
        r"(?:giá bán|gia ban|giá|gia)\s*(?:là|la|:|=)?\s*(?P<price>[\d.,\s]+)",
        text,
        flags=re.IGNORECASE,
    )
    if price_match:
        metadata["regular_price"] = parse_price(price_match.group("price"))

    category_match = re.search(
        r"(?:danh mục|danh muc|category|cate)\s*(?:là|la|:|=)?\s*(?P<category>.+)",
        text,
        flags=re.IGNORECASE,
    )
    if category_match:
        category = re.split(r"[,;\n|]+", category_match.group("category").strip())[0].strip(" .:-")
        if category:
            metadata["category_name"] = category
    return metadata


def find_or_create_product_category(name: str) -> dict:
    categories = wc_get("products/categories", {"search": name, "per_page": 20, "page": 1})
    if isinstance(categories, list):
        normalized_target = normalize_product_name(name)
        for category in categories:
            if normalize_product_name(category.get("name", "")) == normalized_target:
                return category
        if categories:
            return categories[0]
    result = wc_post("products/categories", {"name": name})
    if not isinstance(result, dict) or not result.get("id"):
        raise RuntimeError("WooCommerce không trả về danh mục hợp lệ.")
    return result


def parse_product_update(text: str) -> dict | None:
    normalized = normalize_text(text)
    plain_text = plain_ascii(text)

    sale_match = re.search(
        r"(?P<name>.+?)\s+(?:(?:sua|doi|chinh|update|cap nhat)\s+)?"
        r"(?:gia\s+giu\s+nguyen\s+)?"
        r"(?:(?:gia)\s+)?(?:khuyen\s*mai|sale|gia\s*sale)\s+"
        r"(?:thanh|la|=)?\s*(?P<price>[\d\.,\s]+)",
        plain_text,
        flags=re.IGNORECASE,
    )
    if sale_match:
        name_start, name_end = sale_match.span("name")
        product_name, variant_name = split_variant_from_product_name(text[name_start:name_end].strip(" :-"))
        return {
            "type": "sale_price",
            "name": product_name,
            "variation": variant_name,
            "sale_price": parse_price(sale_match.group("price")),
        }

    price_match = re.search(
        r"(?P<name>.+?)\s+(?:sua|doi|chinh|update|cap nhat)\s+"
        r"(?:lai\s+)?gia\s+(?:thanh|la|=)?\s*(?P<price>[\d\.,\s]+)",
        plain_text,
        flags=re.IGNORECASE,
    )
    if price_match:
        name_start, name_end = price_match.span("name")
        product_name, variant_name = split_variant_from_product_name(text[name_start:name_end].strip(" :-"))
        return {
            "type": "price",
            "name": product_name,
            "variation": variant_name,
            "regular_price": parse_price(price_match.group("price")),
        }

    stock_match = re.search(
        r"(?P<name>.+?)\s+"
        r"(?:(?:sua|doi|chinh|update|cap nhat|cho|de|set)\s+)?"
        r"(?:ton kho|trang thai kho|kho|ton)?\s*"
        r"(?P<stock>co hang|con hang|het hang|in stock|out of stock)$",
        plain_text,
        flags=re.IGNORECASE,
    )
    if stock_match:
        name_start, name_end = stock_match.span("name")
        raw_stock = stock_match.group("stock")
        stock_status = "outofstock" if raw_stock in {"het hang", "out of stock"} else "instock"
        product_name, variant_name = split_variant_from_product_name(text[name_start:name_end].strip(" :-"))
        return {
            "type": "stock",
            "name": product_name,
            "variation": variant_name,
            "stock_status": stock_status,
        }

    if any(word in plain_ascii(normalized) for word in ["sua gia", "chinh gia", "khuyen mai", "sale", "het hang", "co hang", "con hang"]):
        raise ValueError(
            "T\u00f4i ch\u01b0a \u0111\u1ecdc r\u00f5 t\u00ean s\u1ea3n ph\u1ea9m ho\u1eb7c gi\u00e1/tr\u1ea1ng th\u00e1i m\u1edbi. V\u00ed d\u1ee5: "
            "'Deriva Bpo Gel s\u1eeda gi\u00e1 267000', 'Deriva Bpo Gel khuy\u1ebfn m\u00e3i l\u00e0 240000' ho\u1eb7c 'Deriva Bpo Gel ch\u1ec9nh t\u1ed3n kho h\u1ebft h\u00e0ng'."
        )

    return None


def parse_product_post_request(text: str) -> dict | None:
    normalized = normalize_text(text)
    post_keywords = [
        "đăng bài",
        "dang bai",
        "viết bài",
        "viet bai",
        "tạo bài",
        "tao bai",
        "bài viết sản phẩm",
        "bai viet san pham",
        "content sản phẩm",
        "content san pham",
    ]
    if not any(keyword in normalized for keyword in post_keywords):
        return None

    product_name = text
    patterns = [
        r"^(?:đăng bài|dang bai|viết bài|viet bai|tạo bài|tao bai)\s+(?:viết\s+)?(?:cho\s+)?(?:sản phẩm|san pham)?\s*(?P<name>.+)$",
        r"^(?:sản phẩm|san pham)\s+(?P<name>.+?)\s+(?:đăng bài|dang bai|viết bài|viet bai|tạo bài|tao bai).*$",
        r"^(?P<name>.+?)\s+(?:đăng bài|dang bai|viết bài|viet bai|tạo bài|tao bai).*$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match and match.group("name").strip():
            product_name = match.group("name").strip(" :-")
            break

    for phrase in [
        "đăng bài viết sản phẩm",
        "dang bai viet san pham",
        "viết bài sản phẩm",
        "viet bai san pham",
        "tạo bài sản phẩm",
        "tao bai san pham",
        "đăng bài",
        "dang bai",
        "viết bài",
        "viet bai",
        "tạo bài",
        "tao bai",
    ]:
        product_name = re.sub(re.escape(phrase), "", product_name, flags=re.IGNORECASE).strip(" :-")

    for phrase in ["xuất bản", "xuat ban", "publish", "đăng luôn", "dang luon"]:
        product_name = re.sub(re.escape(phrase), "", product_name, flags=re.IGNORECASE).strip(" :-")

    status = "publish" if any(word in normalized for word in ["xuất bản", "xuat ban", "publish", "đăng luôn", "dang luon"]) else "draft"
    if not product_name:
        raise ValueError("Tôi chưa đọc rõ tên sản phẩm cần đăng bài.")
    return {"type": "post", "name": product_name, "status": status}


def parse_product_delete_request(text: str) -> dict | None:
    normalized = normalize_text(text)
    delete_keywords = ["xóa sản phẩm", "xoa san pham", "xoá sản phẩm", "gỡ sản phẩm", "go san pham", "delete product"]
    if not any(keyword in normalized for keyword in delete_keywords):
        return None
    product_name = text
    for phrase in delete_keywords:
        product_name = re.sub(re.escape(phrase), "", product_name, flags=re.IGNORECASE).strip(" :-")
    if not product_name:
        raise ValueError("Tôi chưa đọc rõ tên sản phẩm cần xóa.")
    return {"type": "delete_product", "name": product_name}


def search_products(name: str, limit: int = 5) -> list[dict]:
    products = wc_get(
        "products",
        {
            "search": name,
            "per_page": limit,
            "page": 1,
            "status": "any",
        },
    )
    if not isinstance(products, list):
        raise RuntimeError("WooCommerce trả về dữ liệu sản phẩm không hợp lệ.")
    return products


def fetch_product_variations(product_id: int | str) -> list[dict]:
    variations: list[dict] = []
    page = 1
    while True:
        batch = wc_get(
            f"products/{product_id}/variations",
            {
                "per_page": 100,
                "page": page,
                "status": "any",
            },
        )
        if not isinstance(batch, list):
            raise RuntimeError("WooCommerce trả về dữ liệu biến thể không hợp lệ.")
        variations.extend(batch)
        if len(batch) < 100:
            return variations
        page += 1


def variation_label(variation: dict) -> str:
    attributes = variation.get("attributes") or []
    parts = []
    for attribute in attributes:
        name = str(attribute.get("name") or "").strip()
        option = str(attribute.get("option") or "").strip()
        if name and option:
            parts.append(f"{name}: {option}")
        elif option:
            parts.append(option)
    if parts:
        return " / ".join(parts)
    sku = str(variation.get("sku") or "").strip()
    return sku or f"Biến thể #{variation.get('id')}"


def variation_search_text(variation: dict) -> str:
    attributes = variation.get("attributes") or []
    values = [variation_label(variation), str(variation.get("sku") or "")]
    for attribute in attributes:
        values.append(str(attribute.get("name") or ""))
        values.append(str(attribute.get("option") or ""))
    return normalize_product_name(" ".join(values))


def find_matching_variations(variations: list[dict], query: str) -> list[dict]:
    target = normalize_product_name(query)
    if not target:
        return []
    exact = [variation for variation in variations if variation_search_text(variation) == target]
    if exact:
        return exact
    return [variation for variation in variations if target in variation_search_text(variation)]


def normalize_product_name(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<.*?>", " ", value)
    value = value.lower()
    value = re.sub(r"[^\w\s]+", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def product_tokens(value: str) -> list[str]:
    stopwords = {
        "gel",
        "kem",
        "serum",
        "sua",
        "sữa",
        "vien",
        "viên",
        "tri",
        "trị",
        "mun",
        "mụn",
        "da",
        "cho",
        "va",
        "và",
        "cua",
        "của",
        "san",
        "sản",
        "pham",
        "phẩm",
    }
    return [token for token in normalize_product_name(value).split() if len(token) >= 2 and token not in stopwords]


_ALL_PRODUCTS_CACHE: list[dict] = []
_ALL_PRODUCTS_CACHE_TIME: float = 0.0
_ALL_PRODUCTS_CACHE_LOCK = Lock()


def get_cached_all_products() -> list[dict]:
    global _ALL_PRODUCTS_CACHE, _ALL_PRODUCTS_CACHE_TIME
    now = time.time()
    with _ALL_PRODUCTS_CACHE_LOCK:
        if not _ALL_PRODUCTS_CACHE or (now - _ALL_PRODUCTS_CACHE_TIME) > 600:
            log("Tai danh sach tat ca san pham tu WooCommerce de cap nhat cache...")
            try:
                _ALL_PRODUCTS_CACHE = fetch_all_products()
                _ALL_PRODUCTS_CACHE_TIME = now
                log(f"Da tai va cache {len(_ALL_PRODUCTS_CACHE)} san pham.")
            except Exception as e:
                log(f"Loi khi tai tat ca san pham: {e}")
                if not _ALL_PRODUCTS_CACHE:
                    _ALL_PRODUCTS_CACHE = []
        return _ALL_PRODUCTS_CACHE


def find_products_by_fuzzy_name(name: str) -> list[dict]:
    # 1. Tim kiem bang API WooCommerce truoc
    products = search_products(name, limit=20)
    target_normalized = normalize_product_name(name)
    if not target_normalized:
        return []

    # Loc/Kiem tra de dam bao ket qua search API co chua cum tu nguoi dung go
    matched_products = []
    for product in products:
        p_name = normalize_product_name(product.get("name", ""))
        if target_normalized in p_name or p_name in target_normalized:
            matched_products.append(product)

    # 2. Neu API search khong ra, tien hanh do tim tu cache (fuzzy search)
    if not matched_products:
        all_products = get_cached_all_products()
        matches = []
        name_tokens = product_tokens(name)

        for product in all_products:
            p_name = product.get("name", "")
            p_normalized = normalize_product_name(p_name)

            # Khop chuoi con
            if target_normalized in p_normalized or p_normalized in target_normalized:
                matches.append((product, 1.0))
                continue

            # Khop theo tu (token)
            p_tokens = product_tokens(p_name)
            if not p_tokens:
                continue
            matched = [t for t in p_tokens if t in name_tokens]
            if matched:
                score = len(matched) / max(len(p_tokens), len(name_tokens))
                if score >= 0.3:
                    matches.append((product, score))

        matches.sort(key=lambda item: item[1], reverse=True)
        matched_products = [item[0] for item in matches[:5]]

    return matched_products


def parse_product_search_request(text: str) -> str | None:
    normalized = normalize_text(text)
    plain = plain_ascii(text)

    # Co san pham nao [ten] khong? / co [ten] khong?
    match_co_khong = re.search(
        r"\b(?:co)\s+"
        r"(?:san pham|thuoc|kem|serum|vien uong|gel|kem duong)?\s*"
        r"(?P<query>.+?)\s*(?:khong|\?)$",
        plain,
        flags=re.IGNORECASE,
    )
    if match_co_khong:
        start, end = match_co_khong.span("query")
        return text[start:end].strip(" :-?")

    # Tim / Tra cuu / Kiem tra / Check san pham [ten]
    match_tim = re.search(
        r"\b(?:tim|tim kiem|tra cuu|kiem tra|check)\s+"
        r"(?:san pham|thuoc|kem|serum|vien uong|gel|kem duong)?\s*"
        r"(?P<query>.+)",
        plain,
        flags=re.IGNORECASE,
    )
    if match_tim:
        start, end = match_tim.span("query")
        return text[start:end].strip(" :-?")

    # Co san pham nao / co thuoc nao [ten]
    match_co_nao = re.search(
        r"\b(?:co san pham nao|co thuoc nao|co kem nao|co serum nao)\s+(?P<query>.+)",
        plain,
        flags=re.IGNORECASE,
    )
    if match_co_nao:
        start, end = match_co_nao.span("query")
        return text[start:end].strip(" :-?")

    return None


def build_product_search_response_html(query: str) -> str:
    products = find_products_by_fuzzy_name(query)
    if not products:
        return (
            f"<b>Không tìm thấy sản phẩm phù hợp trên website.</b>\n"
            f"Từ khóa tìm kiếm: <code>{h(query)}</code>\n\n"
            "Nhắn lại tên sản phẩm gần đúng hơn hoặc từ khóa ngắn hơn (ví dụ: <i>Silver-GSV</i>)."
        )

    lines = [
        f"<b>Kết quả tìm kiếm sản phẩm cho:</b> <code>{h(query)}</code>",
        f"Tìm thấy <b>{len(products)}</b> sản phẩm phù hợp:",
        "",
    ]
    for idx, product in enumerate(products, start=1):
        name = product.get("name") or "Sản phẩm không tên"
        price_val = product.get("regular_price") or product.get("price") or 0
        price_str = f"<b>{money(price_val)} VND</b>" if price_val else "<i>Chưa để giá</i>"
        if product.get("type") == "variable":
            price_str = "<i>Giá theo biến thể</i>"
        stock = stock_label(product.get("stock_status") or "")
        permalink = product.get("permalink") or ""
        lines.append(f"<b>{idx}. {h(name)}</b>")
        lines.append(f"• Giá bán: {price_str}")
        lines.append(f"• Trạng thái: <b>{h(stock)}</b>")
        if permalink:
            lines.append(f"• Đường dẫn: <a href=\"{h(permalink)}\">Xem trên website</a>")
        lines.append("")
    return "\n".join(lines).strip()


def find_exact_product_by_name(name: str) -> dict | None:
    target = normalize_product_name(name)
    if not target:
        return None
    candidates = search_products(name, limit=20)
    for product in candidates:
        if normalize_product_name(product.get("name", "")) == target:
            return product
    return None


def find_product_by_h1_prefix(title: str) -> dict | None:
    normalized_title = normalize_product_name(title)
    if not normalized_title:
        return None
    candidates = []
    seen_ids = set()
    queries = [title]
    if ":" in title:
        queries.append(title.split(":", 1)[0].strip())
    if " - " in title:
        queries.append(title.split(" - ", 1)[0].strip())
    title_tokens = product_tokens(title)
    queries.extend(title_tokens[:6])

    for query in queries:
        if not query:
            continue
        try:
            for product in search_products(query, limit=20):
                product_id = product.get("id")
                if product_id not in seen_ids:
                    candidates.append(product)
                    seen_ids.add(product_id)
        except Exception:
            continue

    if not candidates:
        candidates = get_cached_all_products()

    best = None
    best_score = 0.0
    for product in candidates:
        product_name = normalize_product_name(product.get("name", ""))
        if not product_name:
            continue
        if product_name == normalized_title:
            return product
        if normalized_title.startswith(product_name) or product_name in normalized_title:
            score = 100 + len(product_name) / 100
        else:
            tokens = product_tokens(product_name)
            if not tokens:
                continue
            matched = [token for token in tokens if token in title_tokens]
            score = len(matched) / len(tokens)
            important = [token for token in tokens if token not in {"bpo", "spf", "ai"}]
            if important and all(token in title_tokens for token in important[: min(3, len(important))]):
                score += 0.25
        if score > best_score:
            best = product
            best_score = score

    return best if best_score >= 0.55 else None


def fetch_all_products() -> list[dict]:
    products: list[dict] = []
    page = 1
    while True:
        batch = wc_get(
            "products",
            {
                "per_page": 100,
                "page": page,
                "status": "any",
                "orderby": "title",
                "order": "asc",
            },
        )
        if not isinstance(batch, list):
            raise RuntimeError("WooCommerce trả về dữ liệu sản phẩm không hợp lệ.")
        products.extend(batch)
        if len(batch) < 100:
            return products
        page += 1


def export_product_catalog_report() -> tuple[str, Path]:
    products = fetch_all_products()
    rows = []
    counts = defaultdict(int)
    for product in products:
        status = product.get("stock_status") or "unknown"
        counts[status] += 1
        price = float(product.get("price") or product.get("regular_price") or 0)
        rows.append(
            {
                "id": product.get("id"),
                "name": product.get("name"),
                "price": product.get("price"),
                "stock_label": stock_label(status),
            }
        )

    xlsx_path = OUT_DIR / f"bao_cao_san_pham_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    write_products_xlsx(rows, xlsx_path)
    visible_products = [p for p in products if p.get("status") == "publish"]
    caption = (
        "<b>Báo cáo sản phẩm WooCommerce</b>\n"
        f"Tổng: <b>{len(products)}</b> | Hiển thị: <b>{len(visible_products)}</b> | "
        f"Có hàng: <b>{counts.get('instock', 0)}</b> | Hết hàng: <b>{counts.get('outofstock', 0)}</b>"
    )
    return caption, xlsx_path


def xlsx_col_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def xlsx_cell(ref: str, value: object, style: int | None = None) -> str:
    style_attr = f' s="{style}"' if style is not None else ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"{style_attr}><v>{value}</v></c>'
    return f'<c r="{ref}" t="inlineStr"{style_attr}><is><t>{h(value)}</t></is></c>'


def write_products_xlsx(rows: list[dict], path: Path) -> None:
    headers = ["ID", "Tên sản phẩm", "Giá", "Tình trạng"]
    data_rows = [[row["id"], row["name"], int(float(row["price"] or 0)), row["stock_label"]] for row in rows]
    sheet_rows = []
    all_rows = [headers] + data_rows
    for row_idx, row in enumerate(all_rows, start=1):
        cells = []
        for col_idx, value in enumerate(row, start=1):
            ref = f"{xlsx_col_name(col_idx)}{row_idx}"
            style = 1 if row_idx == 1 else (2 if col_idx == 3 and row_idx > 1 else None)
            cells.append(xlsx_cell(ref, value, style))
        sheet_rows.append(f'<row r="{row_idx}">{"".join(cells)}</row>')

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<cols><col min="1" max="1" width="12" customWidth="1"/>'
        '<col min="2" max="2" width="78" customWidth="1"/>'
        '<col min="3" max="3" width="16" customWidth="1"/>'
        '<col min="4" max="4" width="18" customWidth="1"/></cols>'
        '<sheetData>'
        + "".join(sheet_rows)
        + '</sheetData><autoFilter ref="A1:D1"/></worksheet>'
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sản phẩm" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>'
    )
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFE2F0D9"/><bgColor indexed="64"/></patternFill></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
        '<xf numFmtId="3" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>'
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '</Types>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types_xml)
        archive.writestr("_rels/.rels", rels_xml)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        archive.writestr("xl/styles.xml", styles_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def write_order_details_xlsx(rows: list[dict], path: Path) -> None:
    headers = [
        "ID đơn hàng",
        "Mã đơn",
        "Ngày tạo",
        "Trạng thái",
        "Tên người mua",
        "SĐT",
        "Email",
        "Địa chỉ nhận hàng",
        "ID sản phẩm",
        "Tên sản phẩm",
        "Số lượng",
        "Doanh thu sản phẩm",
        "Tổng đơn",
        "Phí vận chuyển",
        "Giảm giá",
        "Thanh toán",
        "Ghi chú",
    ]
    keys = [
        "order_id",
        "order_number",
        "date_created",
        "status",
        "customer",
        "phone",
        "email",
        "address",
        "product_id",
        "product_name",
        "quantity",
        "line_total",
        "order_total",
        "shipping_total",
        "discount_total",
        "payment_method",
        "customer_note",
    ]
    widths = [12, 12, 20, 14, 24, 18, 28, 58, 12, 58, 10, 18, 16, 16, 14, 24, 42]
    money_cols = {12, 13, 14, 15}
    numeric_cols = {1, 9, 11} | money_cols

    all_rows = [headers] + [[row.get(key, "") for key in keys] for row in rows]
    sheet_rows = []
    for row_idx, row in enumerate(all_rows, start=1):
        cells = []
        for col_idx, value in enumerate(row, start=1):
            ref = f"{xlsx_col_name(col_idx)}{row_idx}"
            style = 1 if row_idx == 1 else (2 if col_idx in numeric_cols and row_idx > 1 else None)
            cells.append(xlsx_cell(ref, value, style))
        sheet_rows.append(f'<row r="{row_idx}">{"".join(cells)}</row>')

    col_xml = "".join(
        f'<col min="{idx}" max="{idx}" width="{width}" customWidth="1"/>'
        for idx, width in enumerate(widths, start=1)
    )
    last_col = xlsx_col_name(len(headers))
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f'<cols>{col_xml}</cols>'
        '<sheetData>'
        + "".join(sheet_rows)
        + f'</sheetData><autoFilter ref="A1:{last_col}1"/></worksheet>'
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Chi tiết đơn hàng" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>'
    )
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFD9EAF7"/><bgColor indexed="64"/></patternFill></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
        '<xf numFmtId="3" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>'
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '</Types>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types_xml)
        archive.writestr("_rels/.rels", rels_xml)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        archive.writestr("xl/styles.xml", styles_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def product_label(product: dict) -> str:
    price = product.get("regular_price") or product.get("price") or "0"
    stock = product.get("stock_status") or ""
    return f"{product.get('name')} | ID {product.get('id')} | Giá {money(price)} | Kho {stock}"


def clean_wp_html(value: str) -> str:
    value = re.sub(r"<script.*?</script>", "", value or "", flags=re.DOTALL | re.IGNORECASE)
    value = re.sub(r"<style.*?</style>", "", value, flags=re.DOTALL | re.IGNORECASE)
    return value.strip()


def plain_text_from_html(value: str, max_len: int = 155) -> str:
    text = re.sub(r"<.*?>", " ", value or "")
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def docx_to_html(path: Path) -> tuple[str, str, str]:
    namespaces = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        document_xml = archive.read("word/document.xml")
    root = ET.fromstring(document_xml)
    body = root.find("w:body", namespaces)
    if body is None:
        raise RuntimeError("File DOCX không có nội dung hợp lệ.")

    html_parts = []
    title = path.stem
    title_found = False

    for paragraph in body.findall("w:p", namespaces):
        texts = []
        for node in paragraph.findall(".//w:t", namespaces):
            texts.append(node.text or "")
        text = "".join(texts).strip()
        if not text:
            continue

        style_node = paragraph.find("w:pPr/w:pStyle", namespaces)
        style = ""
        if style_node is not None:
            style = style_node.attrib.get(f"{{{namespaces['w']}}}val", "")
        style_lower = style.lower()

        if style_lower in {"heading1", "heading 1", "tieude1"} or style_lower.startswith("heading1"):
            tag = "h1"
        elif style_lower in {"heading2", "heading 2", "tieude2"} or style_lower.startswith("heading2"):
            tag = "h2"
        elif style_lower in {"heading3", "heading 3", "tieude3"} or style_lower.startswith("heading3"):
            tag = "h3"
        elif style_lower in {"title", "tieude"}:
            tag = "h1"
        else:
            tag = "p"

        if tag == "h1" and not title_found:
            title = text
            title_found = True

        html_parts.append(f"<{tag}>{h(text)}</{tag}>")

    if not html_parts:
        raise RuntimeError("File DOCX không có đoạn văn bản nào để import.")

    content = "\n".join(html_parts)
    excerpt = plain_text_from_html(content)
    return title, content, excerpt


def prepare_docx_post(chat_id: int, path: Path, caption: str = "") -> str:
    title, content, excerpt = docx_to_html(path)
    normalized_caption = normalize_text(caption)
    new_product_metadata = parse_new_product_metadata(caption)
    force_update = any(
        phrase in normalized_caption
        for phrase in [
            "cập nhật mô tả",
            "cap nhat mo ta",
            "cập nhật lại mô tả",
            "cap nhat lai mo ta",
            "sửa mô tả",
            "sua mo ta",
            "update mô tả",
            "update mo ta",
        ]
    )
    product_query = caption.strip() if force_update and caption.strip() else title
    for phrase in [
        "nội dung sản phẩm",
        "noi dung san pham",
        "mô tả sản phẩm",
        "mo ta san pham",
        "cập nhật sản phẩm",
        "cap nhat san pham",
        "cập nhật mô tả",
        "cap nhat mo ta",
        "cập nhật lại mô tả",
        "cap nhat lai mo ta",
        "sửa mô tả",
        "sua mo ta",
    ]:
        product_query = re.sub(re.escape(phrase), "", product_query, flags=re.IGNORECASE).strip(" :-")

    if force_update:
        products = search_products(product_query, limit=10)
        if not products:
            return (
                "<b>Không tìm thấy sản phẩm để cập nhật mô tả</b>\n\n"
                f"Từ khóa đã dò: <code>{h(product_query)}</code>\n"
                "Hãy gửi lại file kèm caption rõ hơn, ví dụ: <i>cập nhật mô tả Gel Trị Mụn Deriva Bpo Gel</i>."
            )
        if len(products) > 1:
            exact = find_exact_product_by_name(product_query)
            if exact:
                products = [exact]
            else:
                lines = [f"<b>Tìm thấy nhiều sản phẩm cho:</b> <code>{h(product_query)}</code>", ""]
                for product in products:
                    lines.append(f"• {h(product_label(product))}")
                lines.append("")
                lines.append("Hãy gửi lại file kèm caption là tên sản phẩm cụ thể hơn.")
                return "\n".join(lines)
        product = products[0]
        action_type = "product_description"
    else:
        product = find_product_by_h1_prefix(title)
        action_type = "product_description" if product else "product_create"

    action = {
        "type": action_type,
        "title": title,
        "content": content,
        "excerpt": excerpt,
        "source_file": str(path),
    }
    if action_type == "product_create":
        action.update(new_product_metadata)
    if product:
        action["product_id"] = product["id"]
        action["product_name"] = product.get("name", "")
    PENDING_ACTIONS[chat_id] = action
    heading_counts = {
        "H1": len(re.findall(r"<h1>", content)),
        "H2": len(re.findall(r"<h2>", content)),
        "H3": len(re.findall(r"<h3>", content)),
    }
    if action_type == "product_create":
        meta_lines = []
        if action.get("regular_price"):
            meta_lines.append(f"Giá bán: <b>{money(action['regular_price'])} VND</b>")
        if action.get("category_name"):
            meta_lines.append(f"Danh mục: <b>{h(action['category_name'])}</b>")
        if not meta_lines:
            meta_lines.append("Giá bán/danh mục: <i>chưa có trong caption</i>")
        return (
            "<b>Cần xác nhận trước khi tạo sản phẩm WooCommerce mới</b>\n\n"
            f"Tên sản phẩm mới lấy từ H1: <b>{h(title)}</b>\n"
            "Không tìm thấy sản phẩm đã đăng có tên trùng H1, nên bot sẽ tạo sản phẩm mới.\n"
            + "\n".join(meta_lines)
            + "\n"
            f"Cấu trúc nội dung: H1={heading_counts['H1']}, H2={heading_counts['H2']}, H3={heading_counts['H3']}\n\n"
            f"<b>Tóm tắt:</b>\n{i_tag(excerpt)}\n\n"
            "Nhắn <b>xác nhận</b> để tạo sản phẩm mới, hoặc <b>hủy</b> để bỏ qua."
        )

    return (
        "<b>Cần xác nhận trước khi cập nhật mô tả sản phẩm WooCommerce</b>\n\n"
        f"File: <code>{h(path.name)}</code>\n"
        f"Sản phẩm: <b>{h(product.get('name'))}</b>\n"
        f"ID sản phẩm: <code>{h(product.get('id'))}</code>\n"
        f"H1 trong file: <b>{h(title)}</b>\n"
        f"Cấu trúc: H1={heading_counts['H1']}, H2={heading_counts['H2']}, H3={heading_counts['H3']}\n\n"
        f"<b>Tóm tắt:</b>\n{i_tag(excerpt)}\n\n"
        "Nhắn <b>xác nhận</b> để cập nhật vào mục <b>Sản phẩm</b>, hoặc <b>hủy</b> để bỏ qua."
    )


def build_product_article(product: dict) -> tuple[str, str, str]:
    name = product.get("name", "").strip()
    price = product.get("price") or product.get("regular_price") or "0"
    stock_status = product.get("stock_status") or ""
    stock_label = "Còn hàng" if stock_status == "instock" else "Tạm hết hàng"
    permalink = product.get("permalink") or ""
    short_description = clean_wp_html(product.get("short_description") or "")
    description = clean_wp_html(product.get("description") or "")
    image = ""
    images = product.get("images") or []
    if images and isinstance(images, list):
        image = images[0].get("src") or ""

    title = f"{name} - Thông tin sản phẩm và hướng dẫn sử dụng"
    excerpt_source = short_description or description or f"Tìm hiểu thông tin sản phẩm {name} tại Khải Hoàn Derma."
    excerpt = plain_text_from_html(excerpt_source)

    content = [
        f"<h2>{h(name)}</h2>",
    ]
    if image:
        content.append(f'<figure><img src="{h(image)}" alt="{h(name)}" /></figure>')
    content.extend(
        [
            "<h3>Thông tin nhanh</h3>",
            "<ul>",
            f"<li><strong>Giá bán:</strong> {money(price)} VND</li>",
            f"<li><strong>Tình trạng:</strong> {h(stock_label)}</li>",
            "</ul>",
        ]
    )
    if short_description:
        content.extend(["<h3>Mô tả ngắn</h3>", short_description])
    if description:
        content.extend(["<h3>Chi tiết sản phẩm</h3>", description])
    content.extend(
        [
            "<h3>Gợi ý tư vấn</h3>",
            "<p>Khách hàng nên đọc kỹ thông tin sản phẩm, tình trạng da và hướng dẫn sử dụng trước khi mua. "
            "Với sản phẩm đặc trị hoặc sản phẩm có hoạt chất mạnh, nên tham khảo ý kiến chuyên môn nếu da đang kích ứng, "
            "mang thai, cho con bú hoặc đang dùng thuốc điều trị.</p>",
        ]
    )
    if permalink:
        content.append(f'<p><a href="{h(permalink)}">Xem sản phẩm tại Khải Hoàn Derma</a></p>')

    return title, "\n".join(content), excerpt


def prepare_product_post(chat_id: int, request: dict) -> str:
    products = search_products(request["name"])
    if not products:
        return (
            f"<b>Không tìm thấy sản phẩm để đăng bài</b>\n"
            f"Từ khóa: <code>{h(request['name'])}</code>\n\n"
            "Bạn hãy gửi tên gần đúng hơn hoặc dùng một đoạn tên sản phẩm dài hơn."
        )

    if len(products) > 1:
        lines = [f"<b>Tìm thấy nhiều sản phẩm cho:</b> <code>{h(request['name'])}</code>", ""]
        for product in products:
            lines.append(f"• {h(product_label(product))}")
        lines.append("")
        lines.append("Hãy nhắn lại với tên cụ thể hơn để tránh đăng nhầm sản phẩm.")
        return "\n".join(lines)

    product = products[0]
    title, content, excerpt = build_product_article(product)
    action = {
        "type": "product_description",
        "product_id": product["id"],
        "product_name": product.get("name", ""),
        "title": title,
        "content": content,
        "excerpt": excerpt,
    }
    PENDING_ACTIONS[chat_id] = action

    return (
        "<b>Cần xác nhận trước khi cập nhật mô tả sản phẩm WooCommerce</b>\n\n"
        f"Sản phẩm: <b>{h(product.get('name'))}</b>\n"
        f"ID sản phẩm: <code>{h(product.get('id'))}</code>\n"
        f"H1 nội dung: <b>{h(title)}</b>\n\n"
        f"<b>Tóm tắt:</b>\n{i_tag(excerpt)}\n\n"
        "Nhắn <b>xác nhận</b> để cập nhật vào mục <b>Sản phẩm</b>, hoặc <b>hủy</b> để bỏ qua."
    )


def i_tag(value: str) -> str:
    return f"<i>{h(value)}</i>"


def prepare_product_update(chat_id: int, update: dict) -> str:
    products = find_products_by_fuzzy_name(update["name"])
    if not products:
        return (
            f"<b>Không tìm thấy sản phẩm</b>\n"
            f"Từ khóa: <code>{h(update['name'])}</code>\n\n"
            "Bạn hãy gửi tên gần đúng hơn hoặc dùng một đoạn tên sản phẩm dài hơn."
        )

    if len(products) > 1:
        exact = find_exact_product_by_name(update["name"])
        if exact:
            products = [exact]
        else:
            target = normalize_product_name(update["name"])
            contained = [product for product in products if normalize_product_name(product.get("name", "")) in target]
            variable_contained = [product for product in contained if product.get("type") == "variable"]
            if len(variable_contained) == 1:
                products = variable_contained

    if len(products) > 1:
        lines = [f"<b>Tìm thấy nhiều sản phẩm cho:</b> <code>{h(update['name'])}</code>", ""]
        for product in products:
            lines.append(f"• {h(product_label(product))}")
        lines.append("")
        lines.append("Hãy nhắn lại với tên cụ thể hơn để tránh sửa nhầm.")
        return "\n".join(lines)

    product = products[0]
    variations = fetch_product_variations(product["id"]) if product.get("type") == "variable" else []

    if variations:
        variant_query = update.get("variation")
        if not variant_query:
            lines = [
                "<b>Sản phẩm này có nhiều biến thể.</b>",
                "",
                f"Sản phẩm: <b>{h(product.get('name'))}</b>",
                f"ID: <code>{h(product.get('id'))}</code>",
                "",
                "<b>Biến thể hiện có</b>",
            ]
            for variation in variations:
                lines.append(
                    f"• <b>{h(variation_label(variation))}</b> - "
                    f"ID <code>{h(variation.get('id'))}</code> - "
                    f"Giá: <b>{money(variation.get('regular_price') or variation.get('price') or 0)} VND</b>"
                    + (
                        f" - Sale: <b>{money(variation.get('sale_price') or 0)} VND</b>"
                        if variation.get("sale_price")
                        else ""
                    )
                    + f" - Kho: <b>{h(stock_label(variation.get('stock_status') or ''))}</b>"
                )
            lines.extend(
                [
                    "",
                    "Hãy nhắn rõ biến thể cần sửa, ví dụ:",
                    f"<code>{h(product.get('name'))} loại Cream sửa giá 1400000</code>",
                    f"<code>{h(product.get('name'))} phân loại Gel khuyến mãi 1200000</code>",
                ]
            )
            return "\n".join(lines)

        matches = find_matching_variations(variations, variant_query)
        if not matches:
            lines = [
                "<b>Không tìm thấy biến thể phù hợp.</b>",
                "",
                f"Sản phẩm: <b>{h(product.get('name'))}</b>",
                f"Biến thể bạn nhập: <code>{h(variant_query)}</code>",
                "",
                "<b>Biến thể hiện có</b>",
            ]
            for variation in variations:
                lines.append(f"• <b>{h(variation_label(variation))}</b> - ID <code>{h(variation.get('id'))}</code>")
            return "\n".join(lines)

        if len(matches) > 1:
            lines = [
                "<b>Tìm thấy nhiều biến thể phù hợp.</b>",
                "",
                f"Biến thể bạn nhập: <code>{h(variant_query)}</code>",
                "",
            ]
            for variation in matches:
                lines.append(f"• <b>{h(variation_label(variation))}</b> - ID <code>{h(variation.get('id'))}</code>")
            lines.append("")
            lines.append("Hãy nhắn rõ hơn tên biến thể để tránh sửa nhầm.")
            return "\n".join(lines)

        variation = matches[0]
        action = {
            "product_id": product["id"],
            "product_name": product.get("name", ""),
            "variation_id": variation["id"],
            "variation_label": variation_label(variation),
            "update": update,
        }
        PENDING_ACTIONS[chat_id] = action

        if update["type"] == "price":
            change = f"đổi giá thường biến thể thành <b>{money(update['regular_price'])} VND</b>"
        elif update["type"] == "sale_price":
            change = f"đổi giá khuyến mãi biến thể thành <b>{money(update['sale_price'])} VND</b>; giá thường giữ nguyên"
        else:
            label = "có hàng" if update["stock_status"] == "instock" else "hết hàng"
            change = f"đổi trạng thái kho biến thể thành <b>{label}</b>"

        return (
            "<b>Cần xác nhận trước khi cập nhật biến thể WooCommerce</b>\n\n"
            f"Sản phẩm: <b>{h(product.get('name'))}</b>\n"
            f"ID sản phẩm: <code>{h(product.get('id'))}</code>\n"
            f"Biến thể: <b>{h(variation_label(variation))}</b>\n"
            f"ID biến thể: <code>{h(variation.get('id'))}</code>\n"
            f"Thao tác: {change}\n\n"
            "Nhắn <b>xác nhận</b> để thực hiện, hoặc <b>hủy</b> để bỏ qua."
        )

    action = {
        "product_id": product["id"],
        "product_name": product.get("name", ""),
        "update": update,
    }
    PENDING_ACTIONS[chat_id] = action

    if update["type"] == "price":
        change = f"\u0111\u1ed5i gi\u00e1 th\u01b0\u1eddng th\u00e0nh <b>{money(update['regular_price'])} VND</b>"
    elif update["type"] == "sale_price":
        change = f"\u0111\u1ed5i gi\u00e1 khuy\u1ebfn m\u00e3i th\u00e0nh <b>{money(update['sale_price'])} VND</b>; gi\u00e1 th\u01b0\u1eddng gi\u1eef nguy\u00ean"
    else:
        label = "c\u00f3 h\u00e0ng" if update["stock_status"] == "instock" else "h\u1ebft h\u00e0ng"
        change = f"\u0111\u1ed5i tr\u1ea1ng th\u00e1i kho th\u00e0nh <b>{label}</b>"

    return (
        "<b>Cần xác nhận trước khi cập nhật WooCommerce</b>\n\n"
        f"Sản phẩm: <b>{h(product.get('name'))}</b>\n"
        f"ID: <code>{h(product.get('id'))}</code>\n"
        f"Thao tác: {change}\n\n"
        "Nhắn <b>xác nhận</b> để thực hiện, hoặc <b>hủy</b> để bỏ qua."
    )


def prepare_product_delete(chat_id: int, request: dict) -> str:
    products = find_products_by_fuzzy_name(request["name"])
    if not products:
        return (
            "<b>Không tìm thấy sản phẩm để xóa</b>\n\n"
            f"Từ khóa: <code>{h(request['name'])}</code>"
        )
    if len(products) > 1:
        exact = find_exact_product_by_name(request["name"])
        if exact:
            products = [exact]
        else:
            lines = [f"<b>Tìm thấy nhiều sản phẩm cho:</b> <code>{h(request['name'])}</code>", ""]
            for product in products:
                lines.append(f"• {h(product_label(product))}")
            lines.append("")
            lines.append("Hãy nhắn lại tên cụ thể hơn để tránh xóa nhầm.")
            return "\n".join(lines)

    product = products[0]
    PENDING_ACTIONS[chat_id] = {
        "type": "product_delete",
        "product_id": product["id"],
        "product_name": product.get("name", ""),
    }
    return (
        "<b>Cần xác nhận trước khi xóa sản phẩm WooCommerce</b>\n\n"
        f"Sản phẩm: <b>{h(product.get('name'))}</b>\n"
        f"ID sản phẩm: <code>{h(product.get('id'))}</code>\n\n"
        "Nhắn <b>xác nhận</b> để xóa sản phẩm, hoặc <b>hủy</b> để bỏ qua."
    )


def apply_pending_action(chat_id: int) -> str:
    global _ALL_PRODUCTS_CACHE_TIME
    _ALL_PRODUCTS_CACHE_TIME = 0.0
    action = PENDING_ACTIONS.pop(chat_id, None)
    if not action:
        return "Không có thao tác nào đang chờ xác nhận."

    if action.get("type") == "product_description":
        try:
            result = wc_put(
                f"products/{action['product_id']}",
                {
                    "description": action["content"],
                    "short_description": action["excerpt"],
                },
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code in {401, 403}:
                return (
                    "<b>Không có quyền cập nhật sản phẩm WooCommerce.</b>\n\n"
                    "WooCommerce API key cần quyền <b>Read/Write</b> để cập nhật mô tả sản phẩm.\n\n"
                    f"<code>{h(detail[:800])}</code>"
                )
            return f"<b>Lỗi WooCommerce:</b>\n<code>{h(detail[:1000])}</code>"

        link = result.get("permalink") or ""
        return (
            "<b>Đã cập nhật mô tả sản phẩm WooCommerce</b>\n\n"
            f"Sản phẩm: <b>{h(result.get('name', action['product_name']))}</b>\n"
            f"ID sản phẩm: <code>{h(result.get('id', action['product_id']))}</code>\n"
            "Vị trí: <b>Sản phẩm -> Tất cả sản phẩm</b>\n"
            + (f'\n<a href="{h(link)}">Mở sản phẩm</a>' if link else "")
        )

    if action.get("type") == "product_create":
        try:
            body = {
                "name": action["title"],
                "type": "simple",
                "status": "publish",
                "description": action["content"],
                "short_description": action["excerpt"],
                "stock_status": "instock",
            }
            if action.get("regular_price"):
                body["regular_price"] = action["regular_price"]
            if action.get("category_name"):
                category = find_or_create_product_category(action["category_name"])
                body["categories"] = [{"id": category["id"]}]
            result = wc_post(
                "products",
                body,
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code in {401, 403}:
                return (
                    "<b>Không có quyền tạo sản phẩm WooCommerce.</b>\n\n"
                    "WooCommerce API key cần quyền <b>Read/Write</b> để tạo sản phẩm mới.\n\n"
                    f"<code>{h(detail[:800])}</code>"
                )
            return f"<b>Lỗi WooCommerce:</b>\n<code>{h(detail[:1000])}</code>"

        link = result.get("permalink") or ""
        return (
            "<b>Đã tạo sản phẩm WooCommerce mới</b>\n\n"
            f"Sản phẩm: <b>{h(result.get('name', action['title']))}</b>\n"
            f"ID sản phẩm: <code>{h(result.get('id'))}</code>\n"
            + (f"Giá bán: <b>{money(result.get('regular_price') or result.get('price') or 0)} VND</b>\n" if result.get("regular_price") or result.get("price") else "")
            + (f"Danh mục: <b>{h(action.get('category_name'))}</b>\n" if action.get("category_name") else "")
            + "Vị trí: <b>Sản phẩm -> Tất cả sản phẩm</b>\n"
            "Trạng thái: <b>đã xuất bản</b>\n"
            + (f'\n<a href="{h(link)}">Mở sản phẩm</a>' if link else "")
            + indexing_after_publish_html(result)
        )

    if action.get("type") == "product_delete":
        try:
            result = wc_delete(f"products/{action['product_id']}", {"force": "true"})
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code in {401, 403}:
                return (
                    "<b>Không có quyền xóa sản phẩm WooCommerce.</b>\n\n"
                    "WooCommerce API key cần quyền <b>Read/Write</b> để xóa sản phẩm.\n\n"
                    f"<code>{h(detail[:800])}</code>"
                )
            return f"<b>Lỗi WooCommerce:</b>\n<code>{h(detail[:1000])}</code>"

        return (
            "<b>Đã xóa sản phẩm WooCommerce</b>\n\n"
            f"Sản phẩm: <b>{h(result.get('name', action['product_name']))}</b>\n"
            f"ID sản phẩm: <code>{h(result.get('id', action['product_id']))}</code>"
        )

    update = action["update"]
    if update["type"] == "price":
        body = {"regular_price": update["regular_price"]}
    elif update["type"] == "sale_price":
        body = {"sale_price": update["sale_price"]}
    else:
        body = {"stock_status": update["stock_status"]}

    try:
        if action.get("variation_id"):
            result = wc_put(f"products/{action['product_id']}/variations/{action['variation_id']}", body)
        else:
            result = wc_put(f"products/{action['product_id']}", body)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if exc.code in {401, 403}:
            return (
                "<b>Không có quyền cập nhật sản phẩm.</b>\n\n"
                "WooCommerce API key hiện tại có thể chỉ là quyền <b>Read</b>. "
                "Bạn cần tạo key quyền <b>Read/Write</b>, thay vào config rồi chạy lại bot.\n\n"
                f"<code>{h(detail[:800])}</code>"
            )
        return f"<b>Lỗi WooCommerce:</b>\n<code>{h(detail[:1000])}</code>"

    target_name = action.get("product_name") or result.get("name") or ""
    if action.get("variation_id"):
        target_name = f"{target_name} - {action.get('variation_label')}"
    result_id_line = ""
    if action.get("variation_id"):
        result_id_line = f"ID biến thể: <code>{h(action.get('variation_id'))}</code>\n"

    if update["type"] == "price":
        return (
            "<b>\u0110\u00e3 c\u1eadp nh\u1eadt gi\u00e1 th\u01b0\u1eddng s\u1ea3n ph\u1ea9m</b>\n\n"
            f"S\u1ea3n ph\u1ea9m: <b>{h(target_name)}</b>\n"
            f"{result_id_line}"
            f"Gi\u00e1 th\u01b0\u1eddng m\u1edbi: <b>{money(result.get('regular_price') or result.get('price') or 0)} VND</b>\n"
            f"Khuy\u1ebfn m\u00e3i hi\u1ec7n t\u1ea1i: <b>{money(result.get('sale_price') or 0)} VND</b>"
        )

    if update["type"] == "sale_price":
        return (
            "<b>\u0110\u00e3 c\u1eadp nh\u1eadt gi\u00e1 khuy\u1ebfn m\u00e3i s\u1ea3n ph\u1ea9m</b>\n\n"
            f"S\u1ea3n ph\u1ea9m: <b>{h(target_name)}</b>\n"
            f"{result_id_line}"
            f"Gi\u00e1 th\u01b0\u1eddng gi\u1eef nguy\u00ean: <b>{money(result.get('regular_price') or 0)} VND</b>\n"
            f"Khuy\u1ebfn m\u00e3i m\u1edbi: <b>{money(result.get('sale_price') or 0)} VND</b>"
        )

    stock_label = "có hàng" if result.get("stock_status") == "instock" else "hết hàng"
    return (
        "<b>Đã cập nhật tồn kho sản phẩm</b>\n\n"
        f"Sản phẩm: <b>{h(target_name)}</b>\n"
        f"{result_id_line}"
        f"Trạng thái mới: <b>{stock_label}</b>"
    )


def wants_web_search(text: str) -> bool:
    normalized = normalize_text(text)
    keywords = [
        "tìm",
        "tim",
        "tra cứu",
        "tra cuu",
        "google",
        "search",
        "web",
        "tin tức",
        "tin tuc",
        "là gì",
        "la gi",
        "ở đâu",
        "o dau",
    ]
    return any(keyword in normalized for keyword in keywords)




WEB_SEARCH_TIMEOUT_SECONDS = 3
WEB_SEARCH_MAX_SECONDS = 8

VIETNAMESE_SOURCE_DOMAINS = [
    "nhathuoclongchau.com.vn",
    "vinmec.com",
    "hellobacsi.com",
    "youmed.vn",
    "medlatec.vn",
    "nhathuocankhang.com",
    "pharmacity.vn",
]


def is_health_or_skincare_query(query: str) -> bool:
    normalized = normalize_text(query)
    keywords = [
        "serum",
        "nam",
        "n\u00e1m",
        "mun",
        "m\u1ee5n",
        "da",
        "retinol",
        "isotretinoin",
        "deriva",
        "bpo",
        "benzoyl",
        "adapalene",
        "kem duong",
        "kem d\u01b0\u1ee1ng",
        "duoc",
        "d\u01b0\u1ee3c",
        "thuoc",
        "thu\u1ed1c",
        "da lieu",
        "da li\u1ec5u",
    ]
    return any(keyword in normalized for keyword in keywords)


def enrich_search_query(query: str) -> str:
    query = query.strip()
    if is_health_or_skincare_query(query):
        return f"{query} ti\u1ebfng Vi\u1ec7t da li\u1ec5u m\u1ef9 ph\u1ea9m ch\u0103m s\u00f3c da"
    return f"{query} ti\u1ebfng Vi\u1ec7t Vi\u1ec7t Nam"


def web_search_queries(query: str) -> list[str]:
    base = enrich_search_query(query)
    queries = [base]
    if is_health_or_skincare_query(query):
        for domain in VIETNAMESE_SOURCE_DOMAINS[:1]:
            queries.append(f"{query} site:{domain}")
    queries.append(f"{query} ti\u1ebfng Vi\u1ec7t")
    return list(dict.fromkeys(q for q in queries if q.strip()))[:3]


def strip_html_text(value: str) -> str:
    value = re.sub(r"<script.*?</script>", " ", value, flags=re.DOTALL | re.IGNORECASE)
    value = re.sub(r"<style.*?</style>", " ", value, flags=re.DOTALL | re.IGNORECASE)
    value = re.sub(r"<.*?>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def has_cjk_text(value: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]", value or ""))


def looks_vietnamese(value: str) -> bool:
    normalized = normalize_text(value or "")
    vietnamese_marks = "\u00e0\u00e1\u1ea3\u00e3\u1ea1\u0103\u1eb1\u1eaf\u1eb3\u1eb5\u1eb7\u00e2\u1ea7\u1ea5\u1ea9\u1eab\u1ead\u00e8\u00e9\u1ebb\u1ebd\u1eb9\u00ea\u1ec1\u1ebf\u1ec3\u1ec5\u1ec7\u00ec\u00ed\u1ec9\u0129\u1ecb\u00f2\u00f3\u1ecf\u00f5\u1ecd\u00f4\u1ed3\u1ed1\u1ed5\u1ed7\u1ed9\u01a1\u1edd\u1edb\u1edf\u1ee1\u1ee3\u00f9\u00fa\u1ee7\u0169\u1ee5\u01b0\u1eeb\u1ee9\u1eed\u1eef\u1ef1\u1ef3\u00fd\u1ef7\u1ef9\u1ef5\u0111"
    if any(ch in normalized for ch in vietnamese_marks):
        return True

    common_words = [
        " la ", " cua ", " va ", " trong ", " dieu tri ", " tri ",
        " thuoc ", " san pham ", " lam dep ", " cham soc ", " mun ", " da ", " kem ", " serum ",
    ]
    padded = f" {normalized} "
    return sum(1 for word in common_words if word in padded) >= 2


def source_domain(link: str) -> str:
    try:
        host = urllib.parse.urlparse(link).netloc.lower()
    except Exception:
        return ""
    return host.removeprefix("www.")


def result_score(result: dict[str, str], query: str) -> int:
    text = f"{result.get('title', '')} {result.get('snippet', '')}"
    domain = source_domain(result.get("link", ""))
    score = 0
    if looks_vietnamese(text):
        score += 40
    if any(domain.endswith(item) for item in VIETNAMESE_SOURCE_DOMAINS):
        score += 35
    if ".vn" in domain:
        score += 15
    if has_cjk_text(text):
        score -= 100
    if is_health_or_skincare_query(query) and not looks_vietnamese(text) and not any(domain.endswith(item) for item in VIETNAMESE_SOURCE_DOMAINS):
        score -= 30
    return score


def significant_query_terms(query: str) -> list[str]:
    normalized = normalize_text(remove_search_prefix(query or ""))
    stop_words = {
        "la", "gi", "ve", "cho", "toi", "tim", "thong", "tin", "tra", "cuu",
        "cua", "va", "co", "khong", "mot", "nhung", "cac", "san", "pham",
        "serum", "kem", "gel", "thuoc", "tri", "dieu", "da", "mun", "nam",
    }
    terms: list[str] = []
    for token in re.findall(r"[a-z0-9]+", normalized):
        if len(token) < 3 or token in stop_words:
            continue
        if token not in terms:
            terms.append(token)
    return terms[:8]


def query_matches_result(query: str, title: str, snippet: str, link: str) -> bool:
    terms = significant_query_terms(query)
    if not terms:
        return True
    haystack = normalize_text(f"{title} {snippet} {link}")
    return any(term in haystack for term in terms)


def clean_search_results(results: list[dict[str, str]], query: str, limit: int) -> list[dict[str, str]]:
    seen: set[str] = set()
    cleaned: list[dict[str, str]] = []
    for result in results:
        title = strip_html_text(result.get("title", ""))
        snippet = strip_html_text(result.get("snippet", ""))
        link = html.unescape(result.get("link", "")).strip()
        if not title or not link:
            continue
        if has_cjk_text(f"{title} {snippet}"):
            continue
        if not query_matches_result(query, title, snippet, link):
            continue
        domain = source_domain(link)
        is_trusted_vietnamese_source = any(domain.endswith(item) for item in VIETNAMESE_SOURCE_DOMAINS) or ".vn" in domain
        if is_health_or_skincare_query(query) and not is_trusted_vietnamese_source and not looks_vietnamese(f"{title} {snippet}"):
            continue
        parsed = urllib.parse.urlparse(link)
        key = parsed.scheme + "://" + parsed.netloc + parsed.path
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({"title": title[:160], "snippet": snippet[:320], "link": link})
    cleaned.sort(key=lambda row: result_score(row, query), reverse=True)
    preferred = [row for row in cleaned if result_score(row, query) > 0]
    return (preferred or cleaned)[:limit]


def search_bing_rss(query: str, limit: int) -> list[dict[str, str]]:
    url = f"https://www.bing.com/search?q={quote_plus(query)}&format=rss&setlang=vi-VN&mkt=vi-VN&cc=VN"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CodexTelegramBot/1.0",
            "Accept-Language": "vi-VN,vi;q=0.9",
        },
    )
    with urllib.request.urlopen(request, timeout=WEB_SEARCH_TIMEOUT_SECONDS, context=ssl_context()) as response:
        feed = response.read()
    root = ET.fromstring(feed)
    results: list[dict[str, str]] = []
    for item in root.findall(".//item"):
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        snippet = item.findtext("description") or ""
        if title and link:
            results.append({"title": title, "link": link, "snippet": snippet})
        if len(results) >= limit:
            break
    return results


def search_duckduckgo_html(query: str, limit: int) -> list[dict[str, str]]:
    url = f"https://duckduckgo.com/html/?q={quote_plus(query)}&kl=vn-vi"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CodexTelegramBot/1.0",
            "Accept-Language": "vi-VN,vi;q=0.9",
        },
    )
    with urllib.request.urlopen(request, timeout=WEB_SEARCH_TIMEOUT_SECONDS, context=ssl_context()) as response:
        page = response.read().decode("utf-8", errors="replace")
    results: list[dict[str, str]] = []
    pattern = re.compile(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="(?P<link>[^"]+)"[^>]*>(?P<title>.*?)</a>'
        r'.{0,2500}?(?:<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>|<div[^>]+class="[^"]*result__snippet[^"]*"[^>]*>)(?P<snippet>.*?)(?:</a>|</div>)',
        re.DOTALL,
    )
    for match in pattern.finditer(page):
        link = html.unescape(match.group("link"))
        if "uddg=" in link:
            parsed = urllib.parse.parse_qs(urllib.parse.urlparse(link).query).get("uddg", [link])[0]
            link = urllib.parse.unquote(parsed)
        results.append({"title": match.group("title"), "link": link, "snippet": match.group("snippet")})
        if len(results) >= limit:
            break
    return results


def search_duckduckgo_instant(query: str, limit: int) -> list[dict[str, str]]:
    url = f"https://api.duckduckgo.com/?q={quote_plus(query)}&format=json&no_html=1&skip_disambig=1&kl=vn-vi"
    request = urllib.request.Request(url, headers={"User-Agent": "CodexTelegramBot/1.0", "Accept-Language": "vi-VN,vi;q=0.9"})
    with urllib.request.urlopen(request, timeout=WEB_SEARCH_TIMEOUT_SECONDS, context=ssl_context()) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    results: list[dict[str, str]] = []
    if payload.get("AbstractText"):
        results.append({"title": payload.get("Heading") or query, "link": payload.get("AbstractURL") or "", "snippet": payload.get("AbstractText") or ""})
    for topic in payload.get("RelatedTopics", []):
        candidates = topic.get("Topics", []) if "Topics" in topic else [topic]
        for item in candidates:
            if item.get("Text") and item.get("FirstURL"):
                results.append({"title": item.get("Text", "").split(" - ", 1)[0], "link": item.get("FirstURL", ""), "snippet": item.get("Text", "")})
            if len(results) >= limit:
                return results
    return results


def duckduckgo_search(query: str, limit: int = 5) -> list[dict[str, str]]:
    all_results: list[dict[str, str]] = []
    started_at = time.time()
    searchers = (search_bing_rss,)
    for search_query in web_search_queries(query):
        for searcher in searchers:
            if time.time() - started_at > WEB_SEARCH_MAX_SECONDS:
                return clean_search_results(all_results, query, limit)
            try:
                all_results.extend(searcher(search_query, limit * 2))
            except Exception as exc:
                log(f"Bo qua nguon tra cuu web {searcher.__name__}: {exc}")
                continue
        cleaned = clean_search_results(all_results, query, limit)
        if len(cleaned) >= min(2, limit):
            return cleaned
    return clean_search_results(all_results, query, limit)


def remove_search_prefix(text: str) -> str:
    return re.sub(r"^(t\u00ecm|tim|tra c\u1ee9u|tra cuu|google|search)\s+", "", text.strip(), flags=re.IGNORECASE).strip()


def vietnamese_context_note(query: str, result_count: int) -> str:
    if is_health_or_skincare_query(query):
        return (
            "Bot \u01b0u ti\u00ean ngu\u1ed3n ti\u1ebfng Vi\u1ec7t v\u1ec1 d\u01b0\u1ee3c, da li\u1ec5u v\u00e0 ch\u0103m s\u00f3c da. "
            "Th\u00f4ng tin ch\u1ec9 d\u00f9ng \u0111\u1ec3 tham kh\u1ea3o, kh\u00f4ng thay th\u1ebf t\u01b0 v\u1ea5n c\u1ee7a b\u00e1c s\u0129 ho\u1eb7c d\u01b0\u1ee3c s\u0129."
        )
    return "Bot \u01b0u ti\u00ean k\u1ebft qu\u1ea3 ti\u1ebfng Vi\u1ec7t v\u00e0 ngu\u1ed3n ph\u00f9 h\u1ee3p v\u1edbi n\u1ed9i dung b\u1ea1n h\u1ecfi."


def build_web_search_html(text: str) -> str:
    query = remove_search_prefix(text)
    results = duckduckgo_search(query)
    if not results:
        return (
            "<b>Tra c\u1ee9u web</b>\n\n"
            f"<b>T\u1eeb kh\u00f3a:</b> <code>{h(query)}</code>\n"
            "Kh\u00f4ng t\u00ecm th\u1ea5y k\u1ebft qu\u1ea3 ti\u1ebfng Vi\u1ec7t ph\u00f9 h\u1ee3p. B\u1ea1n c\u00f3 th\u1ec3 h\u1ecfi l\u1ea1i b\u1eb1ng t\u1eeb kh\u00f3a c\u1ee5 th\u1ec3 h\u01a1n."
        )

    lines = [
        "<b>Tra c\u1ee9u web</b>",
        f"<b>T\u1eeb kh\u00f3a:</b> <code>{h(query)}</code>",
        f"<i>{h(vietnamese_context_note(query, len(results)))}</i>",
        "",
        "<b>K\u1ebft qu\u1ea3 ph\u00f9 h\u1ee3p</b>",
    ]
    for idx, result in enumerate(results, start=1):
        title = result.get("title", "").strip()
        snippet = result.get("snippet", "").strip()
        link = result.get("link", "").strip()
        domain = source_domain(link)
        if snippet and not looks_vietnamese(snippet) and is_health_or_skincare_query(query):
            snippet = "Ngu\u1ed3n n\u00e0y c\u00f3 th\u00f4ng tin li\u00ean quan b\u1eb1ng ti\u1ebfng Vi\u1ec7t. M\u1edf ngu\u1ed3n \u0111\u1ec3 xem n\u1ed9i dung \u0111\u1ea7y \u0111\u1ee7."
        lines.append(
            f"<b>{idx}. {h(title)}</b>\n"
            + (f"{h(snippet)}\n" if snippet else "")
            + (f"<code>{h(domain)}</code>\n" if domain else "")
            + f"<a href=\"{h(link)}\">M\u1edf ngu\u1ed3n</a>"
        )
    return "\n\n".join(lines)


def telegram_api(method: str, payload: dict, timeout: int = DEFAULT_API_TIMEOUT_SECONDS) -> dict:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    data = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data)
    with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as response:
        return json.loads(response.read().decode("utf-8"))


def telegram_get_file_url(file_id: str) -> str:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    result = telegram_api("getFile", {"file_id": file_id})
    file_path = result.get("result", {}).get("file_path")
    if not file_path:
        raise RuntimeError("Telegram không trả về đường dẫn file.")
    return f"https://api.telegram.org/file/bot{token}/{file_path}"


def download_telegram_file(file_id: str, file_name: str) -> Path:
    safe_name = re.sub(r"[^A-Za-z0-9._ -]+", "_", file_name).strip() or "document.docx"
    target = OUT_DIR / "telegram_uploads" / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_name}"
    target.parent.mkdir(exist_ok=True)
    url = telegram_get_file_url(file_id)
    request = urllib.request.Request(url, headers={"User-Agent": "Codex Telegram Bot"})
    with urllib.request.urlopen(request, timeout=60, context=ssl_context()) as response:
        target.write_bytes(response.read())
    return target


def send_document(chat_id: int, path: Path, caption: str = "") -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    boundary = f"----CodexTelegramBoundary{int(time.time() * 1000)}"
    fields = {
        "chat_id": str(chat_id),
        "caption": caption,
        "parse_mode": "HTML",
    }
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        f'Content-Disposition: form-data; name="document"; filename="{path.name}"\r\n'.encode("utf-8")
    )
    if path.suffix.lower() == ".xlsx":
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif path.suffix.lower() == ".csv":
        content_type = "text/csv"
    else:
        content_type = "application/octet-stream"
    body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
    body.extend(path.read_bytes())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendDocument",
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request, timeout=UPLOAD_TIMEOUT_SECONDS, context=ssl_context()) as response:
        json.loads(response.read().decode("utf-8"))


def send_message(chat_id: int, text: str) -> None:
    telegram_api(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text[:3900],
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        },
        timeout=DEFAULT_API_TIMEOUT_SECONDS,
    )


def send_typing(chat_id: int) -> None:
    try:
        telegram_api("sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=1)
    except Exception:
        pass


def allowed(chat_id: int) -> bool:
    raw = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "")
    allowed_ids = {item.strip() for item in raw.split(",") if item.strip()}
    return str(chat_id) in allowed_ids


def handle_message(chat_id: int, text: str) -> None:
    started_at = time.time()
    normalized = normalize_text(text)

    if text.startswith("/whoami"):
        send_message(chat_id, f"<b>Chat ID của bạn:</b> <code>{chat_id}</code>")
        return

    if not allowed(chat_id):
        send_message(
            chat_id,
            f"Chưa cho phép chat này.\nThêm <code>TELEGRAM_ALLOWED_CHAT_IDS={chat_id}</code> rồi chạy lại bot.",
        )
        return

    if text.startswith("/start") or text.startswith("/help"):
        send_message(
            chat_id,
            "<b>Bot Khải Hoàn Derma</b>\n"
            "<i>Bạn cứ nhắn tự nhiên. Bot sẽ tự động đọc hiểu ý định và thao tác với WooCommerce.</i>\n\n"
            "<b>1. Đơn hàng & doanh thu</b>\n"
            "• <i>Hôm nay có đơn hàng không?</i>\n"
            "• <i>Doanh thu tháng này là bao nhiêu?</i>\n"
            "• <i>Chi tiết đơn hàng 2365</i> (Xem thông tin khách hàng & sản phẩm)\n"
            "• <i>Chi tiết đơn hàng hôm nay</i> - xuất file Excel\n"
            "• <i>Chi tiết đơn hàng tháng này</i> - xuất file Excel\n"
            "• <i>Xuất chi tiết đơn hàng từ ngày 03 đến 04/06/2026</i> - xuất file Excel\n\n"
            "<b>2. Sản phẩm & tồn kho</b>\n"
            "• <i>Có bao nhiêu sản phẩm đang bán?</i>\n"
            "• <i>Có bao nhiêu sản phẩm còn hàng?</i>\n"
            "• <i>Xuất tất cả sản phẩm trên web</i> - gửi file Excel\n"
            "• <i>[Tên sản phẩm] sửa giá 350000</i>\n"
            "• Sản phẩm có biến thể: <i>[Tên sản phẩm] loại Cream sửa giá 1400000</i>\n"
            "• Sản phẩm có biến thể: <i>[Tên sản phẩm] phân loại Gel khuyến mãi 1200000</i>\n"
            "• <i>[Tên sản phẩm] có hàng</i> hoặc <i>còn hàng</i>\n"
            "• <i>[Tên sản phẩm] hết hàng</i>\n\n"
            "<b>3. Đăng hoặc cập nhật sản phẩm</b>\n"
            "• Gửi file <code>.docx</code>; H1 là tên sản phẩm, H2/H3 giữ đúng định dạng.\n"
            "• Caption sản phẩm mới: <i>giá là 350000, danh mục là Trị Mụn</i>.\n"
            "• Nếu tên đã có trên web: bot cập nhật mô tả.\n"
            "• Nếu tên chưa có: bot tạo sản phẩm mới, gán giá, danh mục, trạng thái còn hàng.\n"
            "• Sau khi tạo mới: bot gửi URL sản phẩm và link Google Search Console.\n\n"
            "<b>4. Xóa sản phẩm</b>\n"
            "• <i>Xóa sản phẩm [Tên sản phẩm]</i>\n\n"
            "<b>5. Site Kit / Google</b>\n"
            "• <i>Traffic 28 ngày qua</i>\n"
            "• <i>Site Kit 7 ngày qua</i>\n"
            "• <i>Từ khóa Search Console 28 ngày qua</i>\n"
            "• <i>Phân tích từ khóa đang top Google 30 ngày qua</i>\n\n"
            "<b>6. Tìm kiếm sản phẩm</b>\n"
            "• <i>có sản phẩm Thuốc Silver-GSV Isotretinoin 20mg không?</i>\n"
            "• <i>tìm sản phẩm Thuốc Silver-GSV</i>\n"
            "• <i>check sản phẩm Isotretinoin</i>\n\n"
            "<b>7. Lệnh kiểm tra</b>\n"
            "• <code>ping</code> - kiểm tra bot còn chạy.\n"
            "• <code>/whoami</code> - xem Chat ID.\n\n"
            "<b>Xác nhận</b>\n"
            "Các thao tác sửa giá, tồn kho, mô tả, tạo hoặc xóa sản phẩm đều cần nhắn <b>xác nhận</b>. "
            "Nếu sản phẩm có biến thể mà chưa ghi rõ loại, bot sẽ liệt kê biến thể để chọn. "
            "Muốn bỏ qua thì nhắn <b>hủy</b>.",
        )
        return

    try:
        if wants_ping(text):
            html_text = build_ping_html()
        elif normalized in {"xác nhận", "xac nhan", "ok", "đồng ý", "dong y"}:
            html_text = apply_pending_action(chat_id)
        elif normalized in {"hủy", "huy", "cancel", "không", "khong"} and chat_id in PENDING_ACTIONS:
            PENDING_ACTIONS.pop(chat_id, None)
            html_text = "Đã hủy thao tác đang chờ."
        elif (product_delete := parse_product_delete_request(text)):
            send_typing(chat_id)
            html_text = prepare_product_delete(chat_id, product_delete)
        elif (product_update := parse_product_update(text)):
            send_typing(chat_id)
            html_text = prepare_product_update(chat_id, product_update)
        elif (product_post := parse_product_post_request(text)):
            send_typing(chat_id)
            html_text = prepare_product_post(chat_id, product_post)
        elif wants_order_details_export(text):
            send_typing(chat_id)
            caption, report_path = export_order_details_report_from_text(text)
            send_document(chat_id, report_path, caption)
            return
        elif wants_today_orders(text):
            send_typing(chat_id)
            html_text = build_today_orders_html()
        elif (order_id := parse_order_detail_request(text)):
            send_typing(chat_id)
            html_text = build_order_detail_html(order_id)
        elif re.match(r"^/(report|orders|products)\s+(\d{4}-\d{2})$", text.strip()):
            send_typing(chat_id)
            _, month = re.match(r"^/(report|orders|products)\s+(\d{4}-\d{2})$", text.strip()).groups()
            html_text = build_woocommerce_html(f"báo cáo {month}")
        elif (search_query := parse_product_search_request(text)):
            send_typing(chat_id)
            html_text = build_product_search_response_html(search_query)
        elif wants_product_catalog_report(text):
            send_typing(chat_id)
            caption, report_path = export_product_catalog_report()
            send_document(chat_id, report_path, caption)
            return
        elif wants_woocommerce(text):
            send_typing(chat_id)
            html_text = build_woocommerce_html(text)
        elif wants_google_report(text):
            send_typing(chat_id)
            html_text = build_google_report_html(text)
        else:
            html_text = (
                "<b>Tôi chưa hiểu yêu cầu của bạn. Bạn có thể nhắn các yêu cầu như:</b>\n\n"
                "• Báo cáo doanh thu: <i>doanh thu tháng này</i>, <i>đơn hàng hôm nay</i>\n"
                "• Tra cứu sản phẩm: <i>có sản phẩm Thuốc Silver-GSV không?</i>\n"
                "• Xuất tệp báo cáo: <i>xuất tất cả sản phẩm trên web</i>\n\n"
                "Bạn hãy nhắn yêu cầu trực tiếp, không cần dùng dấu <code>/</code>. Gõ <code>/help</code> để xem hướng dẫn đầy đủ."
            )
    except Exception as exc:
        send_message(chat_id, f"<b>Lỗi khi xử lý yêu cầu:</b>\n<code>{h(exc)}</code>")
        return

    send_message(chat_id, html_text)
    elapsed = time.time() - started_at
    if elapsed >= 3:
        log(f"Xu ly tin nhan mat {elapsed:.1f}s: {text[:80]}")


def handle_document(chat_id: int, document: dict, caption: str = "") -> None:
    if not allowed(chat_id):
        send_message(
            chat_id,
            f"Chưa cho phép chat này.\nThêm <code>TELEGRAM_ALLOWED_CHAT_IDS={chat_id}</code> rồi chạy lại bot.",
        )
        return

    file_name = document.get("file_name") or "document"
    if not file_name.lower().endswith(".docx"):
        send_message(
            chat_id,
            "<b>Bot chỉ hỗ trợ import file DOCX.</b>\n\n"
            "Hãy gửi file Word định dạng <code>.docx</code>. Bot sẽ giữ Heading 1/2/3 thành H1/H2/H3 khi cập nhật mô tả sản phẩm.",
        )
        return

    send_typing(chat_id)
    try:
        path = download_telegram_file(document["file_id"], file_name)
        html_text = prepare_docx_post(chat_id, path, caption)
    except Exception as exc:
        send_message(chat_id, f"<b>Lỗi khi đọc file DOCX:</b>\n<code>{h(exc)}</code>")
        return

    send_message(chat_id, html_text)


def is_lock_free_text(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith("/start") or stripped.startswith("/help") or stripped.startswith("/whoami") or wants_ping(text)


def process_update(update: dict) -> None:
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    text = message.get("text") or ""
    document = message.get("document")
    chat_id = chat.get("id")
    if not chat_id:
        return
    if text and is_lock_free_text(text):
        process_chat_message(int(chat_id), text, document, message.get("caption") or "")
        return
    with CHAT_LOCKS[int(chat_id)]:
        process_chat_message(int(chat_id), text, document, message.get("caption") or "")


def process_chat_message(chat_id: int, text: str, document: dict | None, caption: str = "") -> None:
    if chat_id and text:
        log(f"Nhan tin nhan tu {chat_id}: {text[:120]}")
        handle_message(int(chat_id), text)
        log(f"Da xu ly tin nhan tu {chat_id}")
    elif chat_id and document:
        log(f"Nhan file tu {chat_id}: {document.get('file_name')}")
        handle_document(int(chat_id), document, caption)
        log(f"Da xu ly file tu {chat_id}")


def cleanup_old_uploads() -> None:
    uploads_dir = OUT_DIR / "telegram_uploads"
    if not uploads_dir.exists():
        return
    try:
        now = time.time()
        count = 0
        for item in uploads_dir.iterdir():
            if item.is_file() and (now - item.stat().st_mtime) > 7 * 24 * 3600:
                item.unlink()
                count += 1
        if count > 0:
            log(f"Da don dep {count} file docx cu trong thu muc upload de tiet kiem bo nho.")
    except Exception as exc:
        log(f"Loi khi don dep file upload cu: {exc}")


_singleton_socket = None


def ensure_single_instance() -> None:
    global _singleton_socket
    try:
        _singleton_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _singleton_socket.bind(("127.0.0.1", 52365))
    except OSError:
        log("Loi: Phat hien mot phien ban bot khac dang chay tren may nay (Port 52365 da bi chiem). Tien trinh nay se tu thoat.")
        sys.exit(99)


def main() -> None:
    # Thiet lap timeout mac dinh cho tat ca cac ket noi socket
    socket.setdefaulttimeout(75)

    # Kiem tra tranh chay nhieu phien ban bot cung mot luc
    ensure_single_instance()

    if "TELEGRAM_BOT_TOKEN" not in os.environ:
        raise RuntimeError("Thieu TELEGRAM_BOT_TOKEN.")

    # Ngan Windows tu dong Sleep/Standby de giu bot hoat dong lien tuc 24/7
    try:
        import ctypes
        # ES_CONTINUOUS (0x80000000) | ES_SYSTEM_REQUIRED (0x00000001)
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        log("Che do ngan Windows ngu dong da duoc kich hoat thanh cong.")
    except Exception as exc:
        log(f"Khong the kich hoat che do ngan Windows ngu dong (co the khong chay tren Windows): {exc}")

    # Don dep file docx cu nguoi dung tung upload tu truoc
    cleanup_old_uploads()

    log("Bot dang khoi dong...")
    log(f"Thu muc chay bot: {OUT_DIR}")
    log(f"Chat ID duoc phep: {os.environ.get('TELEGRAM_ALLOWED_CHAT_IDS', '')}")

    while True:
        try:
            me = telegram_api("getMe", {})
            bot = me.get("result", {})
            log(f"Ket noi Telegram OK: @{bot.get('username')} - {bot.get('first_name')}")
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            log(f"Khong ket noi duoc Telegram HTTP {exc.code}: {detail[:500]}")
            time.sleep(30)
        except urllib.error.URLError as exc:
            log(f"Khong ket noi duoc Telegram do loi mang/DNS: {exc}. Thu lai sau 30 giay.")
            time.sleep(30)
        except Exception as exc:
            log(f"Khong ket noi duoc Telegram: {exc}. Thu lai sau 30 giay.")
            time.sleep(30)

    offset = 0
    executor = ThreadPoolExecutor(max_workers=8)
    while True:
        try:
            result = telegram_api("getUpdates", {"timeout": 45, "offset": offset}, timeout=LONG_POLL_TIMEOUT_SECONDS)
            for update in result.get("result", []):
                offset = max(offset, update["update_id"] + 1)
                executor.submit(process_update, update)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            log(f"Loi Telegram HTTP {exc.code}: {detail[:500]}")
            if exc.code == 409:
                log("Co the dang co mot bot khac cung token dang chay. Hay tat bot o may cu/tiến trinh cu.")
            time.sleep(10)
        except urllib.error.URLError:
            log("Loi mang Telegram, thu lai sau 5 giay.")
            time.sleep(5)
        except Exception as exc:
            log(f"Loi khong mong doi: {exc}")
            time.sleep(10)


if __name__ == "__main__":
    main()




