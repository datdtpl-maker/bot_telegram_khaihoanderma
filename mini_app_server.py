import os
import sys
import json
import time
import hmac
import hashlib
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional, Any
import threading

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
            formatted.append({
                "id": o.get("id"),
                "status": o.get("status"),
                "total": float(o.get("total") or 0),
                "date_created": o.get("date_created"),
                "billing": o.get("billing") or {},
                "line_items": o.get("line_items") or []
            })
        return {"orders": formatted}
    except Exception as exc:
        bot.log(f"[MiniApp] Lỗi tải đơn hàng: {exc}")
        raise HTTPException(status_code=500, detail="Không thể tải danh sách đơn hàng.")


# ================= PRODUCTS API =================

@app.get("/api/products")
def get_products(
    search: str = Query("", alias="search"),
    per_page: int = Query(30),
    page: int = Query(1),
    current_user: dict = Depends(get_current_admin)
):
    try:
        if search.strip():
            # Sử dụng tìm kiếm thông minh từ bot
            matched = bot.find_products_by_fuzzy_name(search.strip())
            products = matched[:per_page]
        else:
            # Lấy danh sách sản phẩm từ cache bot hoặc WooCommerce
            cached = bot.get_cached_all_products()
            products = cached[:per_page]

        formatted = []
        for p in products:
            p_id = p.get("id")
            p_type = p.get("type", "simple")
            variations_list = []

            # Nếu là sản phẩm biến thể, nạp các phân loại
            if p_type == "variable":
                try:
                    vars_res = bot.wc_get(f"products/{p_id}/variations", {"per_page": 50})
                    if isinstance(vars_res, list):
                        for v in vars_res:
                            # Tạo nhãn hiển thị cho biến thể
                            attrs = v.get("attributes") or []
                            label = " / ".join(a.get("option", "") for a in attrs) or f"#{v.get('id')}"
                            variations_list.append({
                                "id": v.get("id"),
                                "name_label": label,
                                "price": float(v.get("price") or 0),
                                "regular_price": v.get("regular_price") or "",
                                "sale_price": v.get("sale_price") or "",
                                "stock_status": v.get("stock_status", "instock")
                            })
                except Exception:
                    pass

            formatted.append({
                "id": p.get("id"),
                "name": p.get("name"),
                "sku": p.get("sku"),
                "price": float(p.get("price") or 0),
                "regular_price": p.get("regular_price") or "",
                "sale_price": p.get("sale_price") or "",
                "stock_status": p.get("stock_status", "instock"),
                "images": p.get("images") or [],
                "type": p_type,
                "variations": variations_list
            })
        return {"products": formatted}
    except Exception as exc:
        bot.log(f"[MiniApp] Lỗi tải sản phẩm: {exc}")
        raise HTTPException(status_code=500, detail="Không thể tải danh sách sản phẩm.")


class UpdatePriceRequest(BaseModel):
    regular_price: Optional[str] = None
    sale_price: Optional[str] = None
    variation_id: Optional[int] = None


@app.post("/api/products/{product_id}/price")
def update_product_price(
    product_id: int,
    body: UpdatePriceRequest,
    current_user: dict = Depends(get_current_admin)
):
    try:
        if body.variation_id:
            # Cập nhật giá biến thể
            var_payload = {}
            if body.regular_price is not None:
                var_payload["regular_price"] = str(body.regular_price).strip()
            if body.sale_price is not None:
                var_payload["sale_price"] = str(body.sale_price).strip()
            res = bot.wc_put(f"products/{product_id}/variations/{body.variation_id}", var_payload)
            if not isinstance(res, dict) or not res.get("id"):
                raise HTTPException(status_code=400, detail="Lỗi cập nhật giá biến thể.")
            return {"ok": True, "message": f"Đã cập nhật giá biến thể #{body.variation_id} thành công!"}
        else:
            # Cập nhật giá sản phẩm chính
            prod_payload = {}
            if body.regular_price is not None:
                prod_payload["regular_price"] = str(body.regular_price).strip()
            if body.sale_price is not None:
                prod_payload["sale_price"] = str(body.sale_price).strip()
            res = bot.wc_put(f"products/{product_id}", prod_payload)
            if not isinstance(res, dict) or not res.get("id"):
                raise HTTPException(status_code=400, detail="Lỗi cập nhật giá sản phẩm.")
            update_product_cache_locally(product_id, prod_payload)
            return {"ok": True, "message": f"Đã cập nhật giá sản phẩm #{product_id} thành công!"}
    except HTTPException:
        raise
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
    NOTION_SYNC_STATE["current_status"] = "Đang kết nối Notion và tải bài viết..."
    NOTION_SYNC_STATE["last_error"] = None
    try:
        def callback(step, total, detail):
            NOTION_SYNC_STATE["current_status"] = f"Bước {step}/{total}: {detail}"

        notion_sync.run_notion_sync_workflow(progress_callback=callback)
        NOTION_SYNC_STATE["current_status"] = "Hoàn tất đồng bộ Notion thành công!"
        NOTION_SYNC_STATE["last_run"] = datetime.now().isoformat()
    except Exception as exc:
        NOTION_SYNC_STATE["current_status"] = f"Lỗi: {exc}"
        NOTION_SYNC_STATE["last_error"] = str(exc)
        bot.log(f"[MiniApp] Lỗi đồng bộ Notion: {exc}")
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
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=400, detail="Chưa cấu hình GEMINI_API_KEY trong file telegram_bot.env.")

    model = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash").strip()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    system_instruction = (
        "Bạn là Trợ lý AI Khải Hoàn Derma (Gemini 3.8 Flash). "
        "Hãy trả lời súc tích, chuyên nghiệp, chính xác về mỹ phẩm, da liễu hoặc quản lý kinh doanh. "
        "Không chào hỏi rườm rà. Định dạng câu trả lời bằng Markdown rõ ràng."
    )

    req_body = {
        "contents": [
            {
                "parts": [
                    {"text": f"{system_instruction}\n\nCâu hỏi: {body.prompt}"}
                ]
            }
        ]
    }

    try:
        verify_ssl = not (os.environ.get("SSL_NO_VERIFY", "").strip().lower() in {"1", "true", "yes", "on"})
        res = bot.HTTP_SESSION.post(
            url,
            json=req_body,
            headers={"Content-Type": "application/json"},
            timeout=25,
            verify=verify_ssl
        )
        if res.status_code == 404 and model != "gemini-3.7-flash":
            fb_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.7-flash:generateContent?key={api_key}"
            res = bot.HTTP_SESSION.post(fb_url, json=req_body, headers={"Content-Type": "application/json"}, timeout=25, verify=verify_ssl)
        if res.status_code == 404 and model != "gemini-2.5-flash":
            fb_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
            res = bot.HTTP_SESSION.post(fb_url, json=req_body, headers={"Content-Type": "application/json"}, timeout=25, verify=verify_ssl)

        if res.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Lỗi phản hồi từ Gemini API HTTP {res.status_code}")

        res_data = res.json()
        candidates = res_data.get("candidates") or []
        answer = "Không có câu trả lời từ AI."
        if candidates:
            parts = (candidates[0].get("content") or {}).get("parts") or []
            if parts:
                answer = parts[0].get("text") or answer
        return {"ok": True, "answer": answer}
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


def start_mini_app_server(host: str = "0.0.0.0", port: int = 8088):
    """Khởi chạy uvicorn server"""
    import uvicorn
    bot.log(f"Khởi chạy Telegram Mini App Server tại http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    port = int(os.environ.get("MINI_APP_PORT", 8088))
    start_mini_app_server(port=port)
