import os
import sys
import json
import time
import hmac
import hashlib
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional, Any, Union
import threading
import requests

from fastapi import FastAPI, Depends, HTTPException, Header, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Ensure base dir is in python path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

def load_env_file():
    env_file = BASE_DIR / "telegram_bot.env"
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k and k not in os.environ:
                    os.environ[k] = v
        except Exception:
            pass

load_env_file()

# Import existing core bot & notion functions
import telegram_woocommerce_bot as bot
import notion_sync

app = FastAPI(
    title="Khải Hoàn Derma - Telegram Mini App API",
    version="1.0.0",
    docs_url=None, # Tắt swagger công khai để bảo mật
    redoc_url=None
)

# Cho phép CORS cho Telegram WebView
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Phục vụ thư mục static frontend
STATIC_DIR = BASE_DIR / "mini_app"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Trạng thái tiến trình đồng bộ Notion toàn cục
NOTION_SYNC_STATE = {
    "is_running": False,
    "current_status": "Sẵn sàng",
    "last_run": None,
    "last_error": None
}


# ================= XÁC THỰC BẢO MẬT TELEGRAM WEBAPP =================

def verify_telegram_webapp_data(init_data_str: str, bot_token: str) -> Optional[dict]:
    """
    Xác thực chữ ký HMAC-SHA256 của Telegram WebApp theo chuẩn Telegram.
    Trả về dictionary thông tin người dùng nếu hợp lệ, ngược lại trả về None.
    """
    if not init_data_str or not bot_token:
        return None
    try:
        parsed = urllib.parse.parse_qsl(init_data_str, keep_blank_values=True)
        data_dict = dict(parsed)
        received_hash = data_dict.pop("hash", None)
        if not received_hash:
            return None
        
        # Sắp xếp các tham số theo bảng chữ cái
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data_dict.items()))
        
        # Secret key = HMAC_SHA256("WebAppData", bot_token)
        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        
        if not hmac.compare_digest(calculated_hash, received_hash):
            return None
            
        user_raw = data_dict.get("user")
        if user_raw:
            return json.loads(user_raw)
        return data_dict
    except Exception as e:
        bot.log(f"[MiniApp] Lỗi giải mã Telegram WebApp data: {e}")
        return None


def get_current_admin(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
    tg_init_data: Optional[str] = Query(None, alias="tgWebAppInitData"),
    admin_token: Optional[str] = Query(None)
) -> dict:
    """
    Dependency kiểm tra quyền Admin:
    1. Qua header Authorization: tma <initData> hoặc query param tgWebAppInitData
    2. Hoặc qua X-Admin-Token / admin_token dự phòng
    """
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    allowed_chat_ids_str = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
    allowed_ids = {int(x.strip()) for x in allowed_chat_ids_str.split(",") if x.strip().replace("-", "").isdigit()}

    # 1. Kiểm tra Admin Token dự phòng (Dành cho trình duyệt web thường)
    provided_token = x_admin_token or admin_token
    configured_admin_token = os.environ.get("MINI_APP_ADMIN_TOKEN", "").strip()
    if provided_token:
        if configured_admin_token and provided_token == configured_admin_token:
            return {"id": 0, "first_name": "Admin (Token)", "username": "admin", "is_admin": True}
        # Hoặc 16 ký tự đầu của BOT_TOKEN làm token mặc định nếu chưa đặt
        if bot_token and provided_token == bot_token[:16]:
            return {"id": 0, "first_name": "Admin (BotKey)", "username": "admin", "is_admin": True}

    # 2. Kiểm tra Telegram WebApp initData
    init_data = None
    if authorization and authorization.startswith("tma "):
        init_data = authorization[4:].strip()
    elif tg_init_data:
        init_data = tg_init_data

    if init_data and bot_token:
        user_info = verify_telegram_webapp_data(init_data, bot_token)
        if user_info:
            user_id = user_info.get("id")
            if user_id in allowed_ids or not allowed_ids:
                return user_info
            bot.log(f"[MiniApp] Từ chối truy cập user ID {user_id} (không có trong TELEGRAM_ALLOWED_CHAT_IDS)")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tài khoản Telegram không nằm trong danh sách Admin.")

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Yêu cầu xác thực tài khoản Admin.")


def update_product_cache_locally(product_id: int, updates: dict) -> None:
    """Cập nhật dữ liệu sản phẩm trong bộ nhớ đệm cache của bot"""
    try:
        with bot._ALL_PRODUCTS_CACHE_LOCK:
            for p in bot._ALL_PRODUCTS_CACHE:
                if p.get("id") == product_id:
                    p.update(updates)
                    break
    except Exception:
        pass


# ================= FRONTEND ROUTE =================

@app.get("/")
def serve_index():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Frontend chưa được cài đặt")
    return FileResponse(index_file)


# ================= AUTH API =================

@app.post("/api/auth/verify")
def auth_verify(current_user: dict = Depends(get_current_admin)):
    site_url = ""
    try:
        site_url = bot.site_base_url()
    except Exception:
        site_url = os.environ.get("WORDPRESS_SITE_URL", "")

    return {
        "ok": True,
        "user": current_user,
        "role": "admin",
        "site_url": site_url
    }


# ================= DASHBOARD STATS API =================

@app.get("/api/dashboard/stats")
def get_dashboard_stats(current_user: dict = Depends(get_current_admin)):
    today = datetime.now()
    t_after, t_before = bot.day_bounds(today)
    m_after, m_before = bot.month_bounds(bot.current_month())

    # 1. Doanh thu hôm nay
    today_orders = bot.fetch_orders_between(t_after, t_before)
    _, _, _, today_rev, _, _ = bot.summarize_orders(today_orders)

    # 2. Doanh thu tháng này
    month_orders = bot.fetch_orders(bot.current_month())
    _, _, _, month_rev, _, _ = bot.summarize_orders(month_orders)

    # 3. Đánh giá chờ duyệt (hold)
    pending_reviews_count = 0
    try:
        revs = bot.wc_get("products/reviews", {"status": "hold", "per_page": 100})
        if isinstance(revs, list):
            pending_reviews_count = len(revs)
    except Exception:
        pass

    # 4. Notion chờ đăng
    notion_queue_count = 0
    token = os.environ.get("NOTION_TOKEN")
    db_id = os.environ.get("NOTION_DATABASE_ID")
    if token and db_id:
        try:
            pages = notion_sync.query_notion_pages_to_post(token, db_id)
            notion_queue_count = len(pages)
        except Exception:
            pass

    return {
        "today_revenue": today_rev,
        "today_orders": len(today_orders),
        "month_revenue": month_rev,
        "month_orders": len(month_orders),
        "pending_reviews": pending_reviews_count,
        "notion_queue": notion_queue_count,
        "system_health": {
            "woocommerce": "ok",
            "wordpress": "ok",
            "gemini": "ok",
            "notion": "ok"
        }
    }


# ================= CONCURRENT VARIATIONS CACHE =================
_VARIATIONS_CACHE: dict[int, list[dict]] = {}
_VARIATIONS_CACHE_TIME: dict[int, float] = {}
_VARIATIONS_LOCK = threading.Lock()

def get_product_variations_cached(product_id: int) -> list[dict]:
    """Lấy danh sách biến thể của sản phẩm, có cache trong 10 phút"""
    now = time.time()
    with _VARIATIONS_LOCK:
        if product_id in _VARIATIONS_CACHE and (now - _VARIATIONS_CACHE_TIME.get(product_id, 0)) < 600:
            return _VARIATIONS_CACHE[product_id]

    try:
        vars_res = bot.wc_get(f"products/{product_id}/variations", {"per_page": 50})
        variations_list = []
        if isinstance(vars_res, list):
            for v in vars_res:
                attrs = v.get("attributes") or []
                label_parts = []
                for a in attrs:
                    opt = str(a.get("option", "")).strip()
                    name = str(a.get("name", "")).strip()
                    if opt:
                        label_parts.append(f"{name}: {opt}" if name else opt)
                label = " / ".join(label_parts) or f"#{v.get('id')}"
                reg_p = str(v.get("regular_price") or "").strip()
                sale_p = str(v.get("sale_price") or "").strip()
                discount = None
                if reg_p and sale_p:
                    try:
                        r_val = float(reg_p)
                        s_val = float(sale_p)
                        if r_val > 0 and s_val < r_val:
                            discount = round((1 - s_val / r_val) * 100)
                    except Exception:
                        pass

                variations_list.append({
                    "id": v.get("id"),
                    "name_label": label,
                    "price": float(v.get("price") or 0),
                    "regular_price": reg_p,
                    "sale_price": sale_p,
                    "discount_percent": discount,
                    "stock_status": v.get("stock_status", "instock"),
                    "sku": v.get("sku") or ""
                })

        with _VARIATIONS_LOCK:
            _VARIATIONS_CACHE[product_id] = variations_list
            _VARIATIONS_CACHE_TIME[product_id] = now
        return variations_list
    except Exception as e:
        bot.log(f"[MiniApp] Lỗi nạp biến thể #{product_id}: {e}")
        return []


# ================= ORDERS API =================

@app.get("/api/orders")
def get_orders(
    status_filter: str = Query("all", alias="status"),
    per_page: int = Query(25),
    page: int = Query(1),
    current_user: dict = Depends(get_current_admin)
):
    params = {"per_page": min(per_page, 50), "page": page}
    if status_filter and status_filter != "all":
        params["status"] = status_filter

    try:
        res = bot.wc_get("orders", params)
        if not isinstance(res, list):
            return {"orders": []}

        formatted = []
        for o in res:
            billing = o.get("billing") or {}
            shipping = o.get("shipping") or {}
            first_name = (billing.get("first_name") or "").strip()
            last_name = (billing.get("last_name") or "").strip()
            full_name = f"{first_name} {last_name}".strip()
            if not full_name:
                full_name = f"{shipping.get('first_name') or ''} {shipping.get('last_name') or ''}".strip() or "Khách hàng"

            phone = billing.get("phone") or shipping.get("phone") or "Chưa có SĐT"
            addr_parts = [
                billing.get("address_1"),
                billing.get("address_2"),
                billing.get("city"),
                billing.get("state")
            ]
            address = ", ".join([p.strip() for p in addr_parts if p and p.strip()]) or "Chưa có địa chỉ"

            line_items = []
            for it in o.get("line_items") or []:
                line_items.append({
                    "id": it.get("id"),
                    "name": it.get("name") or "Sản phẩm",
                    "quantity": it.get("quantity", 1),
                    "price": float(it.get("price") or 0),
                    "total": float(it.get("total") or 0),
                    "sku": it.get("sku") or ""
                })

            formatted.append({
                "id": o.get("id"),
                "status": o.get("status"),
                "total": float(o.get("total") or 0),
                "date_created": o.get("date_created"),
                "customer_name": full_name,
                "phone": phone,
                "address": address,
                "payment_method_title": o.get("payment_method_title") or "COD",
                "customer_note": o.get("customer_note") or "",
                "billing": billing,
                "line_items": line_items
            })
        return {"orders": formatted}
    except Exception as exc:
        bot.log(f"[MiniApp] Lỗi tải đơn hàng: {exc}")
        raise HTTPException(status_code=500, detail="Không thể tải danh sách đơn hàng.")


class UpdateOrderStatusRequest(BaseModel):
    status: str


@app.post("/api/orders/{order_id}/status")
def update_order_status(
    order_id: int,
    body: UpdateOrderStatusRequest,
    current_user: dict = Depends(get_current_admin)
):
    valid_statuses = {"processing", "completed", "on-hold", "cancelled", "refunded", "failed", "pending"}
    if body.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Trạng thái {body.status} không hợp lệ.")
    try:
        res = bot.wc_put(f"orders/{order_id}", {"status": body.status})
        if not isinstance(res, dict) or not res.get("id"):
            raise HTTPException(status_code=400, detail="Lỗi khi cập nhật trạng thái đơn hàng trên WooCommerce.")
        return {"ok": True, "message": f"Đã chuyển đơn hàng #{order_id} sang trạng thái: {body.status}!"}
    except HTTPException:
        raise
    except Exception as exc:
        bot.log(f"[MiniApp] Lỗi đổi trạng thái đơn #{order_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# ================= PRODUCTS API =================

@app.get("/api/products")
def get_products(
    search: str = Query("", alias="search"),
    filter_type: str = Query("all", alias="filter"),
    category: str = Query("", alias="category"),
    per_page: int = Query(20),
    page: int = Query(1),
    current_user: dict = Depends(get_current_admin)
):
    import math
    import concurrent.futures

    try:
        all_cached = bot.get_cached_all_products()

        # Thu thập danh sách tất cả danh mục của shop
        all_categories = sorted(list({
            c.get("name") 
            for prod in all_cached 
            for c in (prod.get("categories") or []) 
            if c.get("name")
        }))

        # Thống kê số lượng tổng theo từng loại
        counts = {
            "all": len(all_cached),
            "instock": sum(1 for p in all_cached if p.get("stock_status") == "instock"),
            "outofstock": sum(1 for p in all_cached if p.get("stock_status") == "outofstock"),
            "sale": sum(1 for p in all_cached if p.get("on_sale") or (p.get("sale_price") and str(p.get("sale_price")).strip())),
            "variable": sum(1 for p in all_cached if p.get("type") == "variable")
        }

        # 1. Tìm kiếm hoặc lấy toàn bộ
        search_term = str(search or "").strip()
        if search_term:
            matched = bot.find_products_by_fuzzy_name(search_term)
        else:
            matched = all_cached

        # 2. Lọc theo danh mục (nếu có)
        category_term = str(category or "").strip()
        if category_term:
            matched = [
                p for p in matched
                if any(c.get("name") == category_term for c in (p.get("categories") or []))
            ]

        # 3. Bộ lọc tab
        if filter_type == "instock":
            matched = [p for p in matched if p.get("stock_status") == "instock"]
        elif filter_type == "outofstock":
            matched = [p for p in matched if p.get("stock_status") == "outofstock"]
        elif filter_type == "sale":
            matched = [p for p in matched if p.get("on_sale") or (p.get("sale_price") and str(p.get("sale_price")).strip())]
        elif filter_type == "variable":
            matched = [p for p in matched if p.get("type") == "variable"]

        total = len(matched)
        per_page = max(1, min(per_page, 100))
        total_pages = max(1, math.ceil(total / per_page))
        page = max(1, min(page, total_pages)) if total > 0 else 1

        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        page_items = matched[start_idx:end_idx]

        # 4. Tải song song thông tin biến thể cho các sản phẩm biến thể trong trang này
        variable_prods_to_fetch = [
            p.get("id") for p in page_items 
            if p.get("type") == "variable" and p.get("id") not in _VARIATIONS_CACHE
        ]

        if variable_prods_to_fetch:
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                futures = {executor.submit(get_product_variations_cached, pid): pid for pid in variable_prods_to_fetch}
                for future in concurrent.futures.as_completed(futures):
                    try:
                        future.result()
                    except Exception:
                        pass

        # 5. Định dạng dữ liệu trả về giao diện đầy đủ thông tin
        formatted = []
        for p in page_items:
            p_id = p.get("id")
            p_type = p.get("type", "simple")
            variations_list = []
            min_price = None
            max_price = None

            if p_type == "variable":
                variations_list = get_product_variations_cached(p_id)
                prices = [v.get("price") for v in variations_list if v.get("price") is not None and v.get("price") > 0]
                if prices:
                    min_price = min(prices)
                    max_price = max(prices)

            reg_p = str(p.get("regular_price") or "").strip()
            sale_p = str(p.get("sale_price") or "").strip()
            discount = None
            if reg_p and sale_p:
                try:
                    r_val = float(reg_p)
                    s_val = float(sale_p)
                    if r_val > 0 and s_val < r_val:
                        discount = round((1 - s_val / r_val) * 100)
                except Exception:
                    pass

            images = p.get("images") or []
            categories = [c.get("name") for c in (p.get("categories") or []) if c.get("name")]

            formatted.append({
                "id": p_id,
                "name": p.get("name"),
                "sku": p.get("sku") or "",
                "price": float(p.get("price") or 0),
                "regular_price": reg_p,
                "sale_price": sale_p,
                "discount_percent": discount,
                "min_price": min_price,
                "max_price": max_price,
                "stock_status": p.get("stock_status", "instock"),
                "stock_quantity": p.get("stock_quantity"),
                "manage_stock": bool(p.get("manage_stock")),
                "images": images,
                "type": p_type,
                "permalink": p.get("permalink") or "",
                "categories": categories,
                "total_sales": p.get("total_sales", 0),
                "average_rating": p.get("average_rating", "0"),
                "rating_count": p.get("rating_count", 0),
                "on_sale": bool(p.get("on_sale") or discount),
                "variations": variations_list
            })

        return {
            "products": formatted,
            "total": total,
            "total_pages": total_pages,
            "page": page,
            "per_page": per_page,
            "counts": counts,
            "categories": all_categories
        }
    except Exception as exc:
        bot.log(f"[MiniApp] Lỗi tải sản phẩm: {exc}")
        raise HTTPException(status_code=500, detail="Không thể tải danh sách sản phẩm.")


class UpdatePriceRequest(BaseModel):
    regular_price: Optional[Union[str, int, float]] = None
    sale_price: Optional[Union[str, int, float]] = None
    variation_id: Optional[int] = None


@app.post("/api/products/{product_id}/price")
def update_product_price(
    product_id: int,
    body: UpdatePriceRequest,
    current_user: dict = Depends(get_current_admin)
):
    try:
        reg_str = None
        if body.regular_price is not None:
            reg_str = str(body.regular_price).strip()
            if reg_str:
                try:
                    r_val = float(reg_str)
                    if r_val < 0:
                        raise HTTPException(status_code=400, detail="Giá thường không thể âm.")
                except ValueError:
                    raise HTTPException(status_code=400, detail="Giá thường phải là số hợp lệ.")

        sale_str = None
        if body.sale_price is not None:
            sale_str = str(body.sale_price).strip()
            if sale_str:
                try:
                    s_val = float(sale_str)
                    if s_val < 0:
                        raise HTTPException(status_code=400, detail="Giá khuyến mãi không thể âm.")
                except ValueError:
                    raise HTTPException(status_code=400, detail="Giá khuyến mãi phải là số hợp lệ.")

        # Business logic validation: sale_price must be strictly lower than regular_price
        if reg_str and sale_str:
            try:
                r_num = float(reg_str)
                s_num = float(sale_str)
                if s_num > 0 and s_num >= r_num:
                    raise HTTPException(
                        status_code=400,
                        detail="Giá khuyến mãi phải thấp hơn giá bán thông thường."
                    )
            except ValueError:
                pass

        if body.variation_id:
            # Cập nhật giá biến thể
            var_payload = {}
            if reg_str is not None:
                var_payload["regular_price"] = reg_str
            if sale_str is not None:
                var_payload["sale_price"] = sale_str
            res = bot.wc_put(f"products/{product_id}/variations/{body.variation_id}", var_payload)
            if not isinstance(res, dict) or not res.get("id"):
                raise HTTPException(status_code=400, detail="Lỗi cập nhật giá biến thể trên WooCommerce.")
            return {"ok": True, "message": f"Đã cập nhật giá biến thể #{body.variation_id} thành công!"}
        else:
            # Cập nhật giá sản phẩm chính
            prod_payload = {}
            if reg_str is not None:
                prod_payload["regular_price"] = reg_str
            if sale_str is not None:
                prod_payload["sale_price"] = sale_str
            res = bot.wc_put(f"products/{product_id}", prod_payload)
            if not isinstance(res, dict) or not res.get("id"):
                raise HTTPException(status_code=400, detail="Lỗi cập nhật giá sản phẩm trên WooCommerce.")
            update_product_cache_locally(product_id, prod_payload)
            return {"ok": True, "message": f"Đã cập nhật giá sản phẩm #{product_id} thành công!"}
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as exc:
        err_msg = "Lỗi cập nhật giá từ WooCommerce."
        try:
            err_json = exc.response.json()
            if isinstance(err_json, dict) and err_json.get("message"):
                err_msg = f"WooCommerce: {err_json['message']}"
        except Exception:
            err_msg = str(exc)
        bot.log(f"[MiniApp] Lỗi WooCommerce cập nhật giá #{product_id}: {err_msg}")
        raise HTTPException(status_code=400, detail=err_msg)
    except Exception as exc:
        bot.log(f"[MiniApp] Lỗi cập nhật giá #{product_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


class UpdateStockRequest(BaseModel):
    stock_status: str # "instock" | "outofstock"
    variation_id: Optional[int] = None


@app.post("/api/products/{product_id}/stock")
def update_product_stock(
    product_id: int,
    body: UpdateStockRequest,
    current_user: dict = Depends(get_current_admin)
):
    if body.stock_status not in {"instock", "outofstock"}:
        raise HTTPException(status_code=400, detail="stock_status không hợp lệ.")

    try:
        if body.variation_id:
            res = bot.wc_put(f"products/{product_id}/variations/{body.variation_id}", {"stock_status": body.stock_status})
            if not isinstance(res, dict) or not res.get("id"):
                raise HTTPException(status_code=400, detail="Lỗi cập nhật tồn kho biến thể.")
            return {"ok": True, "message": f"Đã đổi tồn kho biến thể #{body.variation_id} thành {body.stock_status}!"}
        else:
            res = bot.wc_put(f"products/{product_id}", {"stock_status": body.stock_status})
            if not isinstance(res, dict) or not res.get("id"):
                raise HTTPException(status_code=400, detail="Lỗi cập nhật tồn kho sản phẩm.")
            update_product_cache_locally(product_id, {"stock_status": body.stock_status})
            return {"ok": True, "message": f"Đã đổi tồn kho sản phẩm #{product_id} thành {body.stock_status}!"}
    except HTTPException:
        raise
    except Exception as exc:
        bot.log(f"[MiniApp] Lỗi đổi tồn kho #{product_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# ================= REVIEWS API =================

@app.get("/api/reviews")
def get_reviews(
    status_filter: str = Query("hold", alias="status"),
    current_user: dict = Depends(get_current_admin)
):
    try:
        params = {"per_page": 50}
        if status_filter != "all":
            params["status"] = status_filter
        revs = bot.wc_get("products/reviews", params)
        if not isinstance(revs, list):
            return {"reviews": []}

        formatted = []
        for r in revs:
            p_id = r.get("product_id")
            p_name = bot.find_product_name_by_id(p_id) if p_id else ""
            formatted.append({
                "id": r.get("id"),
                "status": r.get("status") or "approved",
                "reviewer": r.get("reviewer") or "Ẩn danh",
                "reviewer_email": r.get("reviewer_email") or "",
                "rating": r.get("rating") or 5,
                "review_text": bot.plain_text_from_html(r.get("review") or ""),
                "product_id": p_id,
                "product_name": p_name,
                "date_created": r.get("date_created")
            })
        return {"reviews": formatted}
    except Exception as exc:
        bot.log(f"[MiniApp] Lỗi lấy đánh giá: {exc}")
        raise HTTPException(status_code=500, detail="Không thể tải danh sách đánh giá.")


@app.post("/api/reviews/{review_id}/approve")
def approve_review(review_id: int, current_user: dict = Depends(get_current_admin)):
    try:
        res = bot.wc_put(f"products/reviews/{review_id}", {"status": "approved"})
        if not isinstance(res, dict) or not res.get("id"):
            raise HTTPException(status_code=400, detail="Lỗi phê duyệt đánh giá.")
        bot.NOTIFIED_REVIEWS.add(review_id)
        return {"ok": True, "message": f"Đã duyệt đánh giá #{review_id} thành công!"}
    except Exception as exc:
        bot.log(f"[MiniApp] Lỗi duyệt đánh giá #{review_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/reviews/{review_id}/delete")
def delete_review(review_id: int, current_user: dict = Depends(get_current_admin)):
    try:
        res = bot.wc_put(f"products/reviews/{review_id}", {"status": "trash"})
        if not isinstance(res, dict) or not res.get("id"):
            raise HTTPException(status_code=400, detail="Lỗi xóa đánh giá.")
        bot.NOTIFIED_REVIEWS.add(review_id)
        return {"ok": True, "message": f"Đã chuyển đánh giá #{review_id} vào thùng rác!"}
    except Exception as exc:
        bot.log(f"[MiniApp] Lỗi xóa đánh giá #{review_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# ================= NOTION SYNC API =================

@app.get("/api/notion/pages")
def get_notion_pages(current_user: dict = Depends(get_current_admin)):
    token = os.environ.get("NOTION_TOKEN")
    db_id = os.environ.get("NOTION_DATABASE_ID")
    if not token or not db_id:
        return {"pages": []}
    try:
        pages = notion_sync.query_notion_pages_to_post(token, db_id)
        formatted = []
        for p in pages:
            props = p.get("properties") or {}
            title = "Bài viết không tên"
            for k, val in props.items():
                if val.get("type") == "title":
                    title_list = val.get("title") or []
                    if title_list:
                        title = title_list[0].get("plain_text") or title
                    break
            formatted.append({
                "id": p.get("id"),
                "title": title
            })
        return {"pages": formatted}
    except Exception as exc:
        bot.log(f"[MiniApp] Lỗi lấy Notion pages: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


def _notion_sync_worker():
    global NOTION_SYNC_STATE
    NOTION_SYNC_STATE["is_running"] = True
    NOTION_SYNC_STATE["current_status"] = "Đang kết nối Notion và kiểm tra bài viết..."
    NOTION_SYNC_STATE["last_error"] = None
    bot.log("[NotionSync] Bắt đầu kích hoạt quy trình đồng bộ bài viết Notion...")
    try:
        def callback(step, total, detail):
            status_text = f"Bước {step}/{total}: {detail}"
            NOTION_SYNC_STATE["current_status"] = status_text
            bot.log(f"[NotionSync] {status_text}")

        notion_sync.run_notion_sync_workflow(progress_callback=callback)
        NOTION_SYNC_STATE["current_status"] = "Hoàn tất đồng bộ Notion thành công!"
        NOTION_SYNC_STATE["last_run"] = datetime.now().isoformat()
        bot.log("[NotionSync] ✅ Hoàn tất đồng bộ bài viết Notion thành công!")
    except Exception as exc:
        NOTION_SYNC_STATE["current_status"] = f"Lỗi: {exc}"
        NOTION_SYNC_STATE["last_error"] = str(exc)
        bot.log(f"[NotionSync] ❌ Lỗi đồng bộ Notion: {exc}")
    finally:
        NOTION_SYNC_STATE["is_running"] = False


@app.post("/api/notion/sync")
def trigger_notion_sync(current_user: dict = Depends(get_current_admin)):
    global NOTION_SYNC_STATE
    if NOTION_SYNC_STATE["is_running"]:
        raise HTTPException(status_code=400, detail="Quá trình đồng bộ đang chạy dở.")

    thread = threading.Thread(target=_notion_sync_worker, daemon=True)
    thread.start()
    return {"ok": True, "message": "Đã bắt đầu tiến trình đồng bộ Notion."}


@app.get("/api/notion/status")
def get_notion_status(current_user: dict = Depends(get_current_admin)):
    return NOTION_SYNC_STATE


# ================= GEMINI AI CHAT API =================

class AskGeminiRequest(BaseModel):
    prompt: str


@app.post("/api/ai/ask")
def ask_gemini(body: AskGeminiRequest, current_user: dict = Depends(get_current_admin)):
    try:
        answer = bot.ask_gemini_store(body.prompt)
        return {"ok": True, "answer": answer}
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except HTTPException:
        raise
    except Exception as exc:
        bot.log(f"[MiniApp] Lỗi gọi Gemini: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# ================= EXCEL EXPORTS API =================

@app.get("/api/export/orders-today")
def export_orders_today(current_user: dict = Depends(get_current_admin)):
    try:
        today = datetime.now()
        after, before = bot.day_bounds(today)
        label = today.strftime("%d/%m/%Y")
        _, xlsx_path = bot.export_order_details_report_between(label, after, before, "don_hang_hom_nay")
        return FileResponse(
            xlsx_path,
            filename=f"Bao_cao_don_hang_{today.strftime('%Y%m%d')}.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as exc:
        bot.log(f"[MiniApp] Lỗi xuất Excel đơn hôm nay: {exc}")
        raise HTTPException(status_code=500, detail="Không thể tạo file Excel.")


@app.get("/api/export/orders-month")
def export_orders_month(current_user: dict = Depends(get_current_admin)):
    try:
        m_str = bot.current_month()
        after, before = bot.month_bounds(m_str)
        _, xlsx_path = bot.export_order_details_report_between(f"thang_{m_str}", after, before, f"don_hang_{m_str}")
        return FileResponse(
            xlsx_path,
            filename=f"Bao_cao_don_hang_thang_{m_str}.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as exc:
        bot.log(f"[MiniApp] Lỗi xuất Excel đơn tháng này: {exc}")
        raise HTTPException(status_code=500, detail="Không thể tạo file Excel.")


@app.get("/api/export/products-catalog")
def export_products_catalog(current_user: dict = Depends(get_current_admin)):
    try:
        _, xlsx_path = bot.export_product_catalog_report()
        return FileResponse(
            xlsx_path,
            filename=f"Danh_muc_san_pham_{datetime.now().strftime('%Y%m%d')}.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as exc:
        bot.log(f"[MiniApp] Lỗi xuất danh mục sản phẩm Excel: {exc}")
        raise HTTPException(status_code=500, detail="Không thể tạo file Excel danh mục sản phẩm.")


@app.delete("/api/products/{product_id}")
def delete_product_endpoint(product_id: int, current_user: dict = Depends(get_current_admin)):
    try:
        res = bot.wc_delete(f"products/{product_id}", {"force": "false"})
        if not isinstance(res, dict) or not res.get("id"):
            raise HTTPException(status_code=400, detail="Lỗi khi xóa sản phẩm trên WooCommerce.")
        try:
            with bot._ALL_PRODUCTS_CACHE_LOCK:
                bot._ALL_PRODUCTS_CACHE = [p for p in bot._ALL_PRODUCTS_CACHE if p.get("id") != product_id]
        except Exception:
            pass
        return {"ok": True, "message": f"Đã chuyển sản phẩm #{product_id} vào thùng rác thành công!"}
    except HTTPException:
        raise
    except Exception as exc:
        bot.log(f"[MiniApp] Lỗi xóa sản phẩm #{product_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# ================= SYSTEM & GOOGLE SEO API =================

@app.get("/api/reports/google-seo")
def get_google_seo_report(
    report_type: str = Query("traffic", alias="type"),
    current_user: dict = Depends(get_current_admin)
):
    try:
        from bot_modules.google_reports import build_google_report_web_html, build_google_report_html
        try:
            html_report = build_google_report_web_html(report_type)
        except Exception as e_web:
            bot.log(f"[MiniApp] Web report formatter fallback: {e_web}")
            query = "traffic 28 ngày qua" if report_type == "traffic" else "từ khóa search console 28 ngày qua"
            raw = build_google_report_html(query)
            html_report = f'<div class="whitespace-pre-line text-xs leading-relaxed space-y-2 p-2">{raw.replace(chr(10), "<br>")}</div>'
        return {"ok": True, "type": report_type, "html_report": html_report}
    except Exception as exc:
        bot.log(f"[MiniApp] Lỗi tạo báo cáo Google SEO: {exc}")
        return {"ok": False, "html_report": f'<div class="text-rose-500 font-bold p-3">Lỗi lấy dữ liệu Google: {exc}</div>'}


@app.post("/api/system/cache-refresh")
def refresh_cache_endpoint(current_user: dict = Depends(get_current_admin)):
    try:
        msg = bot.trigger_cache_refresh()
        return {"ok": True, "message": bot.plain_text_from_html(msg)}
    except Exception as exc:
        bot.log(f"[MiniApp] Lỗi làm mới cache: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/system/reviews-cache-clear")
def clear_reviews_cache_endpoint(current_user: dict = Depends(get_current_admin)):
    try:
        bot.NOTIFIED_REVIEWS.clear()
        notified_file = bot.OUT_DIR / "notified_reviews.json"
        if notified_file.exists():
            try:
                notified_file.unlink()
            except Exception:
                pass
        count = bot.scan_and_notify_reviews()
        return {"ok": True, "count": count, "message": f"Đã xóa cache đánh giá. Tìm thấy {count} đánh giá chờ duyệt."}
    except Exception as exc:
        bot.log(f"[MiniApp] Lỗi xóa cache đánh giá: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/system/ping")
def ping_system_endpoint(current_user: dict = Depends(get_current_admin)):
    try:
        html_msg = bot.build_ping_html()
        formatted_html = (
            f'<div class="p-3.5 card-sub rounded-2xl border border-slate-200 dark:border-slate-800 text-xs leading-relaxed font-medium space-y-2">'
            f'{html_msg.replace(chr(10), "<br>")}'
            f'</div>'
        )
        return {"ok": True, "html": formatted_html}
    except Exception as exc:
        bot.log(f"[MiniApp] Lỗi ping hệ thống: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/system/logs")
def get_system_logs(
    lines: int = Query(60, ge=10, le=300),
    filter_tag: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_admin)
):
    log_file = bot.OUT_DIR / "bot.log"
    if not log_file.exists():
        return {"ok": True, "logs": [], "total": 0}

    try:
        content = log_file.read_text(encoding="utf-8", errors="replace")
        all_lines = [l.strip() for l in content.splitlines() if l.strip()]

        if filter_tag and filter_tag.strip():
            tag = filter_tag.strip().lower()
            filtered_lines = [l for l in all_lines if tag in l.lower()]
        else:
            filtered_lines = all_lines

        recent = filtered_lines[-lines:]

        parsed = []
        for line in recent:
            timestamp = ""
            level = "info"
            msg = line
            if line.startswith("[") and "]" in line:
                parts = line.split("]", 1)
                timestamp = parts[0].lstrip("[")
                msg = parts[1].strip()

            lowered = msg.lower()
            if any(w in lowered for w in ["lỗi", "error", "fail", "thất bại"]):
                level = "error"
            elif any(w in lowered for w in ["thành công", "success", "hoàn tất", "đã đăng"]):
                level = "success"
            elif any(w in lowered for w in ["cảnh báo", "warning", "warn"]):
                level = "warn"
            elif any(w in lowered for w in ["notionsync", "drive", "notion"]):
                level = "sync"

            parsed.append({
                "raw": line,
                "timestamp": timestamp,
                "time": timestamp,
                "level": level,
                "message": msg,
                "text": msg
            })

        return {"ok": True, "logs": parsed, "total": len(all_lines)}
    except Exception as exc:
        bot.log(f"[MiniApp] Lỗi đọc log hệ thống: {exc}")
        return {"ok": False, "error": str(exc), "logs": []}



def start_mini_app_server(host: str = "0.0.0.0", port: int = 8088):
    """Khởi chạy uvicorn server"""
    import uvicorn
    bot.log(f"Khởi chạy Telegram Mini App Server tại http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    port = int(os.environ.get("MINI_APP_PORT", 8088))
    start_mini_app_server(port=port)
