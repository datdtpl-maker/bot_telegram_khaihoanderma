import html
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock


OUT_DIR = Path(__file__).resolve().parent.parent
GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
GOOGLE_API_TIMEOUT_SECONDS = 15
TOKEN_REFRESH_SAFETY_SECONDS = 300
_TOKEN_CACHE: dict[tuple[str, ...], tuple[str, datetime | None]] = {}
_TOKEN_LOCK = Lock()


def token_has_enough_life(expiry: datetime | None, now: datetime) -> bool:
    if expiry is None:
        return True
    if getattr(expiry, "tzinfo", None) is not None:
        now = datetime.now(expiry.tzinfo)
    return (expiry - now).total_seconds() > TOKEN_REFRESH_SAFETY_SECONDS


def h(value: object) -> str:
    return html.escape(str(value), quote=False)


def normalize_text(text: str) -> str:
    return text.strip().lower()


def plain_text(text: str) -> str:
    value = text.replace("đ", "d").replace("Đ", "D")
    try:
        import unicodedata

        value = unicodedata.normalize("NFD", value)
        value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    except Exception:
        pass
    return value.lower()


def ssl_context() -> ssl.SSLContext | None:
    value = os.environ.get("SSL_NO_VERIFY", "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return ssl._create_unverified_context()
    return None


def site_base_url() -> str:
    site_url = os.environ.get("WORDPRESS_SITE_URL")
    if site_url:
        return site_url.rstrip("/")
    return "https://khaihoanderma.com"


def load_google_credentials(scopes: list[str]):
    auth_mode = os.environ.get("GOOGLE_AUTH_MODE", "").strip().lower()
    oauth_token = os.environ.get("GOOGLE_OAUTH_TOKEN_JSON", "").strip()
    if auth_mode == "oauth" or oauth_token:
        token_path = Path(oauth_token or "google-oauth-token.json")
        if not token_path.is_absolute():
            token_path = OUT_DIR / token_path
        if not token_path.exists():
            raise RuntimeError(
                f"Không tìm thấy file OAuth token: {token_path}. "
                "Hãy chạy: python setup_google_oauth.py"
            )
        try:
            from google.oauth2.credentials import Credentials
        except ImportError as exc:
            raise RuntimeError(
                "Máy chưa cài thư viện OAuth. Chạy một lần: "
                "python -m pip install google-auth google-auth-oauthlib requests"
            ) from exc
        credentials = Credentials.from_authorized_user_file(str(token_path), scopes=scopes)
        if not credentials.valid and not (credentials.expired and credentials.refresh_token):
            raise RuntimeError("Google OAuth token không hợp lệ. Hãy chạy lại: python setup_google_oauth.py")
        return credentials

    key_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not key_path:
        raise RuntimeError("Thiếu GOOGLE_SERVICE_ACCOUNT_JSON trong telegram_bot.env.")
    path = Path(key_path)
    if not path.is_absolute():
        path = OUT_DIR / path
    if not path.exists():
        raise RuntimeError(f"Không tìm thấy file Google service account JSON: {path}")
    try:
        from google.oauth2 import service_account
    except ImportError as exc:
        raise RuntimeError(
            "Máy chưa cài google-auth. Chạy một lần: python -m pip install google-auth requests"
        ) from exc
    return service_account.Credentials.from_service_account_file(str(path), scopes=scopes)


def google_access_token(scopes: list[str]) -> str:
    cache_key = tuple(sorted(scopes))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with _TOKEN_LOCK:
        cached = _TOKEN_CACHE.get(cache_key)
        if cached:
            token, expiry = cached
            if token and token_has_enough_life(expiry, now):
                return token

    credentials = load_google_credentials(scopes)
    try:
        from google.auth.transport.requests import Request
    except ImportError as exc:
        raise RuntimeError(
            "Máy chưa cài requests/google-auth transport. Chạy một lần: python -m pip install google-auth requests"
        ) from exc
    with _TOKEN_LOCK:
        cached = _TOKEN_CACHE.get(cache_key)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if cached:
            token, expiry = cached
            if token and token_has_enough_life(expiry, now):
                return token
        credentials.refresh(Request())
        token_path = Path(os.environ.get("GOOGLE_OAUTH_TOKEN_JSON", "").strip() or "google-oauth-token.json")
        if not token_path.is_absolute():
            token_path = OUT_DIR / token_path
        if token_path.exists() and hasattr(credentials, "to_json"):
            token_path.write_text(credentials.to_json(), encoding="utf-8")
        _TOKEN_CACHE[cache_key] = (credentials.token, getattr(credentials, "expiry", None))
        return credentials.token


def google_api_json(url: str, body: dict, scopes: list[str]) -> dict:
    token = google_access_token(scopes)
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "KhaiHoanTelegramBot/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=GOOGLE_API_TIMEOUT_SECONDS, context=ssl_context()) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Google API lỗi {exc.code}: {detail[:700]}") from exc


def env_path_exists(value: str) -> bool:
    if not value:
        return False
    path = Path(value)
    if not path.is_absolute():
        path = OUT_DIR / path
    return path.exists()


def google_configured() -> bool:
    auth_mode = os.environ.get("GOOGLE_AUTH_MODE", "").strip().lower()
    oauth_token = os.environ.get("GOOGLE_OAUTH_TOKEN_JSON", "").strip()
    oauth_client = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET_JSON", "").strip()
    service_account = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    has_ga4 = bool(os.environ.get("GA4_PROPERTY_ID", "").strip())
    if auth_mode == "oauth" or oauth_token or oauth_client:
        return bool(has_ga4 and env_path_exists(oauth_client) and env_path_exists(oauth_token))
    return bool(has_ga4 and env_path_exists(service_account))


def ga4_property_id() -> str:
    value = os.environ.get("GA4_PROPERTY_ID", "").strip()
    if not value:
        raise RuntimeError("Thiếu GA4_PROPERTY_ID trong telegram_bot.env.")
    return value.removeprefix("properties/")


def gsc_site_url() -> str:
    return os.environ.get("GSC_SITE_URL", "").strip() or f"{site_base_url()}/"


def google_days_from_text(text: str, default: int = 28) -> int:
    normalized = normalize_text(text)
    plain = plain_text(text)
    match = re.search(r"\b(\d{1,3})\s*(?:ngày|ngay|day|days)\b", normalized)
    if not match:
        match = re.search(r"\b(\d{1,3})\s*(?:ngay|day|days)\b", plain)
    if match:
        return max(1, min(int(match.group(1)), 90))
    if "hôm nay" in normalized or "hom nay" in plain or "today" in plain:
        return 1
    if "7 ngày" in normalized or "7 ngay" in plain or "tuần" in normalized or "tuan" in plain:
        return 7
    return default


def google_date_range(days: int, end_yesterday: bool = False) -> tuple[str, str]:
    end = datetime.now()
    if end_yesterday:
        end -= timedelta(days=1)
    start = end - timedelta(days=max(days - 1, 0))
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def google_metric(row: dict, index: int, default: float = 0) -> float:
    try:
        return float(row.get("metricValues", [])[index].get("value", default))
    except (IndexError, TypeError, ValueError, AttributeError):
        return default


def google_dimension(row: dict, index: int, default: str = "") -> str:
    try:
        return str(row.get("dimensionValues", [])[index].get("value", default))
    except (IndexError, TypeError, AttributeError):
        return default


def fmt_number(value: float | str) -> str:
    return f"{round(float(value)):,.0f}".replace(",", ".")


def fmt_percent(value: float | str) -> str:
    return f"{float(value) * 100:.1f}%".replace(".", ",")


def ga4_run_report(
    metrics: list[str],
    dimensions: list[str] | None = None,
    start_date: str = "28daysAgo",
    end_date: str = "today",
    limit: int = 10,
    order_metric: str | None = None,
) -> dict:
    body: dict = {
        "dateRanges": [{"startDate": start_date, "endDate": end_date}],
        "metrics": [{"name": metric} for metric in metrics],
        "limit": limit,
    }
    if dimensions:
        body["dimensions"] = [{"name": dimension} for dimension in dimensions]
    if order_metric:
        body["orderBys"] = [{"metric": {"metricName": order_metric}, "desc": True}]
    url = f"https://analyticsdata.googleapis.com/v1beta/properties/{ga4_property_id()}:runReport"
    return google_api_json(url, body, [GA4_SCOPE])


def build_ga4_report_html(text: str) -> str:
    days = google_days_from_text(text)
    start, end = google_date_range(days)
    with ThreadPoolExecutor(max_workers=3) as executor:
        total_future = executor.submit(
            ga4_run_report,
            ["activeUsers", "sessions", "screenPageViews", "engagedSessions"],
            start_date=start,
            end_date=end,
            limit=1,
        )
        channels_future = executor.submit(
            ga4_run_report,
            ["sessions", "activeUsers", "engagedSessions"],
            ["sessionDefaultChannelGroup"],
            start_date=start,
            end_date=end,
            limit=6,
            order_metric="sessions",
        )
        pages_future = executor.submit(
            ga4_run_report,
            ["screenPageViews", "activeUsers"],
            ["pageTitle", "pagePath"],
            start_date=start,
            end_date=end,
            limit=6,
            order_metric="screenPageViews",
        )
        total = total_future.result()
        channels = channels_future.result().get("rows") or []
        pages = pages_future.result().get("rows") or []
    total_row = (total.get("rows") or [{}])[0]
    active_users = google_metric(total_row, 0)
    sessions = google_metric(total_row, 1)
    views = google_metric(total_row, 2)
    engaged = google_metric(total_row, 3)

    lines = [
        "<b>Google Analytics / Site Kit</b>",
        f"Thời gian: <code>{h(start)}</code> đến <code>{h(end)}</code>",
        "",
        "<b>Tổng quan</b>",
        f"• Người dùng: <b>{fmt_number(active_users)}</b>",
        f"• Phiên truy cập: <b>{fmt_number(sessions)}</b>",
        f"• Lượt xem trang: <b>{fmt_number(views)}</b>",
        f"• Phiên tương tác: <b>{fmt_number(engaged)}</b>",
        "",
        "<b>Nguồn traffic chính</b>",
    ]
    if channels:
        for row in channels:
            lines.append(
                f"• {h(google_dimension(row, 0) or '(not set)')}: "
                f"<b>{fmt_number(google_metric(row, 0))}</b> phiên"
            )
    else:
        lines.append("• Chưa có dữ liệu nguồn traffic.")

    lines.extend(["", "<b>Trang được xem nhiều</b>"])
    if pages:
        for row in pages:
            title = google_dimension(row, 0) or google_dimension(row, 1) or "(not set)"
            path = google_dimension(row, 1)
            lines.append(
                f"• <b>{h(title[:70])}</b> - {fmt_number(google_metric(row, 0))} lượt xem"
                + (f"\n  <code>{h(path)}</code>" if path else "")
            )
    else:
        lines.append("• Chưa có dữ liệu trang.")
    return "\n".join(lines)


def gsc_query(dimensions: list[str] | None, start_date: str, end_date: str, row_limit: int = 10) -> dict:
    body: dict = {
        "startDate": start_date,
        "endDate": end_date,
        "rowLimit": row_limit,
    }
    if dimensions:
        body["dimensions"] = dimensions
    encoded_site = urllib.parse.quote(gsc_site_url(), safe="")
    url = f"https://www.googleapis.com/webmasters/v3/sites/{encoded_site}/searchAnalytics/query"
    return google_api_json(url, body, [GSC_SCOPE])


def build_gsc_report_html(text: str) -> str:
    days = google_days_from_text(text)
    start, end = google_date_range(days, end_yesterday=True)
    with ThreadPoolExecutor(max_workers=3) as executor:
        summary_future = executor.submit(gsc_query, None, start, end, 1)
        queries_future = executor.submit(gsc_query, ["query"], start, end, 8)
        pages_future = executor.submit(gsc_query, ["page"], start, end, 6)
        summary_rows = summary_future.result().get("rows") or []
        queries = queries_future.result().get("rows") or []
        pages = pages_future.result().get("rows") or []
    summary = summary_rows[0] if summary_rows else {}
    clicks = float(summary.get("clicks") or 0)
    impressions = float(summary.get("impressions") or 0)
    ctr = float(summary.get("ctr") or 0)
    position = float(summary.get("position") or 0)

    lines = [
        "<b>Google Search Console</b>",
        f"Thời gian: <code>{h(start)}</code> đến <code>{h(end)}</code>",
        "",
        "<b>Tổng quan tìm kiếm</b>",
        f"• Click: <b>{fmt_number(clicks)}</b>",
        f"• Hiển thị: <b>{fmt_number(impressions)}</b>",
        f"• CTR: <b>{fmt_percent(ctr)}</b>",
        f"• Vị trí TB: <b>{position:.1f}</b>",
        "",
        "<b>Từ khóa nổi bật</b>",
    ]
    if queries:
        for row in queries:
            keyword = (row.get("keys") or [""])[0]
            lines.append(
                f"• <b>{h(keyword)}</b>: {fmt_number(row.get('clicks') or 0)} click, "
                f"{fmt_number(row.get('impressions') or 0)} hiển thị, CTR {fmt_percent(row.get('ctr') or 0)}"
            )
    else:
        lines.append("• Chưa có dữ liệu từ khóa.")

    lines.extend(["", "<b>Trang có click nhiều</b>"])
    if pages:
        for row in pages:
            page = (row.get("keys") or [""])[0]
            lines.append(
                f"• {fmt_number(row.get('clicks') or 0)} click - "
                f"<a href=\"{h(page)}\">{h(page.replace(site_base_url(), '')[:80] or page[:80])}</a>"
            )
    else:
        lines.append("• Chưa có dữ liệu trang.")
    return "\n".join(lines)


def wants_top_keyword_report(text: str) -> bool:
    plain = plain_text(text)
    has_keyword = any(term in plain for term in ["tu khoa", "keyword", "keywords", "query", "truy van"])
    has_ranking = any(term in plain for term in ["top", "thu hang", "xep hang", "vi tri", "nam top", "dang top", "len top"])
    has_analysis = any(term in plain for term in ["phan tich", "liet ke", "bao cao", "xem", "cho toi"])
    has_google_source = any(term in plain for term in ["google", "sitekit", "site kit", "search console", "gsc", "tim kiem"])
    return has_keyword and (has_ranking or has_analysis) and has_google_source


def top_keyword_rows(text: str, limit: int = 15) -> tuple[int, str, str, list[dict]]:
    days = google_days_from_text(text, default=30)
    start, end = google_date_range(days, end_yesterday=True)
    rows = gsc_query(["query"], start, end, 250).get("rows") or []
    parsed = []
    for row in rows:
        keyword = (row.get("keys") or [""])[0]
        impressions = float(row.get("impressions") or 0)
        if not keyword or impressions <= 0:
            continue
        parsed.append(
            {
                "keyword": keyword,
                "clicks": float(row.get("clicks") or 0),
                "impressions": impressions,
                "ctr": float(row.get("ctr") or 0),
                "position": float(row.get("position") or 0),
            }
        )
    parsed.sort(key=lambda row: (row["position"] if row["position"] > 0 else 999, -row["impressions"], -row["clicks"]))
    return days, start, end, parsed[:limit]


def build_top_keyword_report_html(text: str) -> str:
    days, start, end, rows = top_keyword_rows(text)
    lines = [
        "<b>Từ khóa đang top Google</b>",
        f"Nguồn: <b>Google Search Console / Site Kit</b>",
        f"Thời gian: <code>{h(start)}</code> đến <code>{h(end)}</code> ({days} ngày)",
        "",
    ]
    if not rows:
        lines.append("Chưa có dữ liệu từ khóa phù hợp trong khoảng thời gian này.")
        return "\n".join(lines)

    lines.append("<b>Top theo vị trí trung bình tốt nhất</b>")
    for index, row in enumerate(rows, start=1):
        lines.append(
            f"{index}. <b>{h(row['keyword'])}</b>\n"
            f"   • Vị trí TB: <b>{row['position']:.1f}</b> | "
            f"Click: <b>{fmt_number(row['clicks'])}</b> | "
            f"Hiển thị: <b>{fmt_number(row['impressions'])}</b> | "
            f"CTR: <b>{fmt_percent(row['ctr'])}</b>"
        )

    top_3 = sum(1 for row in rows if 0 < row["position"] <= 3)
    top_10 = sum(1 for row in rows if 0 < row["position"] <= 10)
    lines.extend(
        [
            "",
            "<b>Tóm tắt nhanh</b>",
            f"• Top 3: <b>{top_3}</b> từ khóa trong danh sách trên",
            f"• Top 10: <b>{top_10}</b> từ khóa trong danh sách trên",
            "• Vị trí TB là dữ liệu trung bình của Search Console, không phải thứ hạng cố định theo từng lần tìm kiếm.",
        ]
    )
    return "\n".join(lines)


def wants_google_report(text: str) -> bool:
    normalized = normalize_text(text)
    if wants_top_keyword_report(text):
        return True
    keywords = [
        "site kit",
        "sitekit",
        "analytics",
        "ga4",
        "search console",
        "gsc",
        "traffic",
        "truy cập",
        "truy cap",
        "lượt truy cập",
        "luot truy cap",
        "nguồn truy cập",
        "nguon truy cap",
        "từ khóa",
        "tu khoa",
        "click",
        "hiển thị",
        "hien thi",
        "impression",
        "ctr",
    ]
    return any(keyword in normalized for keyword in keywords)


def build_google_report_html(text: str) -> str:
    if not google_configured():
        auth_mode = os.environ.get("GOOGLE_AUTH_MODE", "").strip().lower()
        oauth_client = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET_JSON", "").strip()
        oauth_token = os.environ.get("GOOGLE_OAUTH_TOKEN_JSON", "").strip()
        if auth_mode == "oauth" or oauth_client or oauth_token:
            missing = []
            if not oauth_client:
                missing.append("GOOGLE_OAUTH_CLIENT_SECRET_JSON")
            if not oauth_token:
                missing.append("GOOGLE_OAUTH_TOKEN_JSON")
            if not os.environ.get("GA4_PROPERTY_ID", "").strip():
                missing.append("GA4_PROPERTY_ID")
            missing_text = ", ".join(missing) if missing else "google-oauth-token.json"
            return (
                "<b>Google Site Kit chưa sẵn sàng</b>\n\n"
                f"Thiếu hoặc chưa tạo: <code>{h(missing_text)}</code>\n\n"
                "Cách xử lý trên máy đang chạy bot:\n"
                "1. Mở CMD trong thư mục bot.\n"
                "2. Chạy: <code>python setup_google_oauth.py</code>\n"
                "3. Đăng nhập Google và cho phép quyền.\n"
                "4. Khởi động lại bot.\n\n"
                "Sau khi xong, trong thư mục bot phải có file <code>google-oauth-token.json</code>."
            )
        return (
            "<b>Google Site Kit chưa được cấu hình cho bot</b>\n\n"
            "Cần thêm vào <code>telegram_bot.env</code>:\n"
            "<code>GOOGLE_SERVICE_ACCOUNT_JSON=google-service-account.json</code>\n"
            "<code>GA4_PROPERTY_ID=123456789</code>\n"
            "<code>GSC_SITE_URL=https://khaihoanderma.com/</code>\n\n"
            "Sau đó cấp email service account vào GA4 và Search Console rồi khởi động lại bot."
        )

    normalized = normalize_text(text)
    if wants_top_keyword_report(text):
        return build_top_keyword_report_html(text)

    wants_gsc = any(
        keyword in normalized
        for keyword in ["search console", "gsc", "từ khóa", "tu khoa", "click", "hiển thị", "hien thi", "impression", "ctr"]
    )
    wants_ga4 = any(
        keyword in normalized
        for keyword in ["site kit", "sitekit", "analytics", "ga4", "traffic", "truy cập", "truy cap", "lượt truy cập", "luot truy cap", "nguồn", "nguon"]
    )
    if wants_gsc and not wants_ga4:
        return build_gsc_report_html(text)
    if wants_ga4 and not wants_gsc:
        return build_ga4_report_html(text)

    ga4 = build_ga4_report_html(text)
    gsc = build_gsc_report_html(text)
    return f"{ga4}\n\n{gsc}"
