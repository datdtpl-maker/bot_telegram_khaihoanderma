import unittest
import hmac
import hashlib
import json
import urllib.parse
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

import mini_app_server
from mini_app_server import app, verify_telegram_webapp_data


class MiniAppTestSuite(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.bot_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
        self.admin_token = self.bot_token[:16]

    def _generate_valid_init_data(self, user_dict, bot_token):
        user_json = json.dumps(user_dict, separators=(",", ":"))
        data = {
            "auth_date": "1728450000",
            "query_id": "AAHdF6IQAAAAAN0XohC0-B7z",
            "user": user_json
        }
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
        calc_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        data["hash"] = calc_hash
        return urllib.parse.urlencode(data)

    def test_verify_telegram_webapp_data_valid(self):
        user_dict = {"id": 987654321, "first_name": "Test", "username": "testuser"}
        valid_init_data = self._generate_valid_init_data(user_dict, self.bot_token)
        
        result = verify_telegram_webapp_data(valid_init_data, self.bot_token)
        self.assertIsNotNone(result)
        self.assertEqual(result.get("id"), 987654321)
        self.assertEqual(result.get("username"), "testuser")

    def test_verify_telegram_webapp_data_tampered(self):
        user_dict = {"id": 987654321, "first_name": "Test"}
        valid_init_data = self._generate_valid_init_data(user_dict, self.bot_token)
        # Tamper the init data string
        tampered_data = valid_init_data.replace("987654321", "111111111")
        result = verify_telegram_webapp_data(tampered_data, self.bot_token)
        self.assertIsNone(result)

    def test_auth_verify_endpoint_with_admin_token(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": self.bot_token}):
            headers = {"X-Admin-Token": self.admin_token}
            response = self.client.post("/api/auth/verify", headers=headers)
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(data.get("ok"))
            self.assertEqual(data.get("role"), "admin")

    def test_auth_verify_endpoint_unauthorized(self):
        response = self.client.post("/api/auth/verify")
        self.assertEqual(response.status_code, 401)

    def test_dashboard_stats_endpoint(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": self.bot_token}), \
             patch.object(mini_app_server.bot, "fetch_orders_between", return_value=[]), \
             patch.object(mini_app_server.bot, "fetch_orders", return_value=[]), \
             patch.object(mini_app_server.bot, "wc_get", return_value=[]), \
             patch.object(mini_app_server.notion_sync, "query_notion_pages_to_post", return_value=[]):
            headers = {"X-Admin-Token": self.admin_token}
            response = self.client.get("/api/dashboard/stats", headers=headers)
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertIn("today_revenue", data)
            self.assertIn("month_revenue", data)
            self.assertIn("pending_reviews", data)

    def test_update_product_price_endpoint(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": self.bot_token}), \
             patch.object(mini_app_server.bot, "wc_put", return_value={"id": 100}), \
             patch.object(mini_app_server, "update_product_cache_locally"):
            headers = {"X-Admin-Token": self.admin_token}
            payload = {"regular_price": "350000", "sale_price": "299000"}
            response = self.client.post("/api/products/100/price", json=payload, headers=headers)
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(data.get("ok"))
            self.assertIn("thành công", data.get("message"))

    def test_update_product_price_with_numeric_inputs(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": self.bot_token}), \
             patch.object(mini_app_server.bot, "wc_put", return_value={"id": 1968}), \
             patch.object(mini_app_server, "update_product_cache_locally"):
            headers = {"X-Admin-Token": self.admin_token}
            # Numbers from Vue v-model numeric input should be accepted without 422 error
            payload = {"regular_price": 270000, "sale_price": 250000}
            response = self.client.post("/api/products/1968/price", json=payload, headers=headers)
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(data.get("ok"))
            self.assertIn("thành công", data.get("message"))

    def test_update_product_price_validation_sale_greater_than_regular(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": self.bot_token}):
            headers = {"X-Admin-Token": self.admin_token}
            payload = {"regular_price": 200000, "sale_price": 250000}
            response = self.client.post("/api/products/100/price", json=payload, headers=headers)
            self.assertEqual(response.status_code, 400)
            self.assertIn("thấp hơn giá bán", response.json().get("detail", ""))

    def test_approve_review_endpoint(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": self.bot_token}), \
             patch.object(mini_app_server.bot, "wc_put", return_value={"id": 555}):
            headers = {"X-Admin-Token": self.admin_token}
            response = self.client.post("/api/reviews/555/approve", headers=headers)
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(data.get("ok"))
            self.assertIn(555, mini_app_server.bot.NOTIFIED_REVIEWS)

    def test_serve_frontend(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers.get("content-type", ""))
        self.assertIn("Khải Hoàn Derma", response.text)

    def test_cloudflare_tunnel_regex_parsing(self):
        import re
        sample_output = (
            "2026-10-09T08:45:28Z INF +--------------------------------------------------------------------------------------------+\n"
            "2026-10-09T08:45:28Z INF | Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):    |\n"
            "2026-10-09T08:45:28Z INF | https://scaled-norfolk-export-treo.trycloudflare.com                                       |\n"
            "2026-10-09T08:45:28Z INF +--------------------------------------------------------------------------------------------+\n"
        )
        match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", sample_output)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(0), "https://scaled-norfolk-export-treo.trycloudflare.com")

    def test_start_cloudflare_tunnel_custom_domain(self):
        with patch.dict("os.environ", {"MINI_APP_URL": "https://app.khaihoanderma.com"}):
            url = mini_app_server.bot.start_cloudflare_tunnel(8088)
            self.assertEqual(url, "https://app.khaihoanderma.com")

    def test_delete_product_endpoint(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": self.bot_token}), \
             patch.object(mini_app_server.bot, "wc_delete", return_value={"id": 105}):
            headers = {"X-Admin-Token": self.admin_token}
            response = self.client.delete("/api/products/105", headers=headers)
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(data.get("ok"))
            self.assertIn("thùng rác", data.get("message"))

    def test_export_products_catalog_endpoint(self):
        from pathlib import Path
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tf:
            tf.write(b"PK\x03\x04test")
            fake_path = Path(tf.name)
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": self.bot_token}), \
             patch.object(mini_app_server.bot, "export_product_catalog_report", return_value=("caption", fake_path)):
            headers = {"X-Admin-Token": self.admin_token}
            response = self.client.get("/api/export/products-catalog", headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertIn("sheet", response.headers.get("content-type", ""))
        try:
            fake_path.unlink()
        except Exception:
            pass

    def test_system_cache_refresh_endpoint(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": self.bot_token}), \
             patch.object(mini_app_server.bot, "trigger_cache_refresh", return_value="<b>Làm mới cache xong</b>"):
            headers = {"X-Admin-Token": self.admin_token}
            response = self.client.post("/api/system/cache-refresh", headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json().get("ok"))

    def test_system_reviews_cache_clear_endpoint(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": self.bot_token}), \
             patch.object(mini_app_server.bot, "scan_and_notify_reviews", return_value=2):
            headers = {"X-Admin-Token": self.admin_token}
            response = self.client.post("/api/system/reviews-cache-clear", headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json().get("ok"))
            self.assertEqual(response.json().get("count"), 2)

    def test_system_ping_endpoint(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": self.bot_token}), \
             patch.object(mini_app_server.bot, "build_ping_html", return_value="<b>Ping OK</b>"):
            headers = {"X-Admin-Token": self.admin_token}
            response = self.client.get("/api/system/ping", headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json().get("ok"))

    def test_google_seo_report_endpoint(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": self.bot_token}), \
             patch("bot_modules.google_reports.build_google_report_web_html", return_value="<div class='test-report'>Report</div>"), \
             patch("bot_modules.google_reports.build_google_report_html", return_value="<b>Báo cáo SEO OK</b>"):
            headers = {"X-Admin-Token": self.admin_token}
            response = self.client.get("/api/reports/google-seo?type=traffic", headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json().get("ok"))
    def test_get_products_pagination_and_filters(self):
        fake_prods = [
            {"id": 1, "name": "Kem trị mụn", "type": "simple", "regular_price": "100000", "sale_price": "80000", "stock_status": "instock", "price": "80000"},
            {"id": 2, "name": "Sữa rửa mặt", "type": "simple", "regular_price": "200000", "sale_price": "", "stock_status": "outofstock", "price": "200000"},
            {"id": 3, "name": "Serum B5", "type": "variable", "regular_price": "", "sale_price": "", "stock_status": "instock", "price": "300000"}
        ]
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": self.bot_token}), \
             patch.object(mini_app_server.bot, "get_cached_all_products", return_value=fake_prods):
            headers = {"X-Admin-Token": self.admin_token}
            # Test all with per_page=2
            res = self.client.get("/api/products?page=1&per_page=2&filter=all", headers=headers)
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data.get("total"), 3)
            self.assertEqual(len(data.get("products")), 2)
            self.assertEqual(data.get("total_pages"), 2)
            self.assertEqual(data.get("counts", {}).get("all"), 3)
            self.assertEqual(data.get("counts", {}).get("sale"), 1)

            # Test filter=sale
            res_sale = self.client.get("/api/products?filter=sale", headers=headers)
            self.assertEqual(res_sale.status_code, 200)
            self.assertEqual(res_sale.json().get("total"), 1)
            self.assertEqual(res_sale.json().get("products")[0]["id"], 1)

    def test_get_products_with_category_filter_and_full_metadata(self):
        fake_prods = [
            {"id": 1, "name": "Kem trị mụn", "type": "simple", "regular_price": "100000", "sale_price": "80000", "stock_status": "instock", "price": "80000", "categories": [{"name": "Trị Mụn"}], "permalink": "https://example.com/p1", "sku": "TM01"},
            {"id": 2, "name": "Sữa rửa mặt", "type": "simple", "regular_price": "200000", "sale_price": "", "stock_status": "outofstock", "price": "200000", "categories": [{"name": "Làm Sạch"}], "permalink": "https://example.com/p2", "sku": "SRM02"}
        ]
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": self.bot_token}),              patch.object(mini_app_server.bot, "get_cached_all_products", return_value=fake_prods):
            headers = {"X-Admin-Token": self.admin_token}
            res = self.client.get("/api/products?category=Trị Mụn", headers=headers)
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data.get("total"), 1)
            self.assertEqual(data.get("products")[0]["id"], 1)
            self.assertEqual(data.get("products")[0]["categories"], ["Trị Mụn"])
            self.assertEqual(data.get("products")[0]["permalink"], "https://example.com/p1")
            self.assertEqual(data.get("products")[0]["sku"], "TM01")
            self.assertIn("Trị Mụn", data.get("categories"))
            self.assertIn("Làm Sạch", data.get("categories"))

    def test_update_order_status_endpoint(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": self.bot_token}), \
             patch.object(mini_app_server.bot, "wc_put", return_value={"id": 888, "status": "completed"}):
            headers = {"X-Admin-Token": self.admin_token}
            payload = {"status": "completed"}
            res = self.client.post("/api/orders/888/status", json=payload, headers=headers)
            self.assertEqual(res.status_code, 200)
            self.assertTrue(res.json().get("ok"))
            self.assertIn("completed", res.json().get("message"))

    def test_system_logs_endpoint(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": self.bot_token}):
            headers = {"X-Admin-Token": self.admin_token}
            res = self.client.get("/api/system/logs?lines=10", headers=headers)
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertTrue(data.get("ok"))
            self.assertIsInstance(data.get("logs"), list)
            if data["logs"]:
                first = data["logs"][0]
                self.assertIn("timestamp", first)
                self.assertIn("time", first)
                self.assertIn("message", first)
                self.assertIn("text", first)

    def test_get_reviews_with_status_filter(self):
        fake_reviews = [
            {"id": 1, "status": "approved", "reviewer": "Lan", "review": "Rất tốt", "product_id": 100},
            {"id": 2, "status": "hold", "reviewer": "Hương", "review": "Chờ duyệt", "product_id": 100}
        ]
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": self.bot_token}), \
             patch.object(mini_app_server.bot, "wc_get", return_value=fake_reviews):
            headers = {"X-Admin-Token": self.admin_token}
            res = self.client.get("/api/reviews?status=approved", headers=headers)
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertIn("reviews", data)
            self.assertEqual(len(data["reviews"]), 2)
            self.assertEqual(data["reviews"][0]["status"], "approved")

    def test_google_seo_report_web_html(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": self.bot_token}), \
             patch("bot_modules.google_reports.build_google_report_web_html", return_value="<div class='test-report'>Report</div>"):
            headers = {"X-Admin-Token": self.admin_token}
            res = self.client.get("/api/reports/google-seo?type=keywords", headers=headers)
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertTrue(data.get("ok"))
            self.assertIn("test-report", data.get("html_report"))


    def test_ai_ask_orders_history_month(self):
        fake_orders = [
            {
                "id": 3001,
                "status": "processing",
                "total": "500000",
                "date_created": "2026-09-15T10:00:00",
                "billing": {"first_name": "Minh", "last_name": "Anh", "phone": "0987654321"},
                "line_items": [{"id": 1, "name": "Kem trị mụn", "quantity": 2, "price": "250000", "total": "500000"}]
            }
        ]
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": self.bot_token, "GEMINI_API_KEY": "fake_key"}), \
             patch.object(mini_app_server.bot, "fetch_orders_between", return_value=fake_orders), \
             patch.object(mini_app_server.bot.HTTP_SESSION, "post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {
                "candidates": [
                    {"content": {"parts": [{"text": "Báo cáo tháng 09/2026: 1 đơn hàng, tổng 500.000 đ"}]}}
                ]
            }
            headers = {"X-Admin-Token": self.admin_token}
            res = self.client.post("/api/ai/ask", json={"prompt": "đơn hàng tháng 9/2026"}, headers=headers)
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertTrue(data.get("ok"))
            self.assertIn("tháng 09/2026", data.get("answer"))

    def test_ai_ask_fallback_when_gemini_unavailable(self):
        fake_orders = [
            {
                "id": 3002,
                "status": "processing",
                "total": "350000",
                "date_created": "2026-09-20T10:00:00",
                "billing": {"first_name": "Linh", "last_name": "Hoa", "phone": "0912345678"},
                "line_items": [{"id": 2, "name": "Serum dưỡng ẩm", "quantity": 1, "price": "350000", "total": "350000"}]
            }
        ]
        # Không có GEMINI_API_KEY -> tự động fallback sang báo cáo thực tế
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": self.bot_token, "GEMINI_API_KEY": ""}), \
             patch.object(mini_app_server.bot, "fetch_orders_between", return_value=fake_orders):
            headers = {"X-Admin-Token": self.admin_token}
            res = self.client.post("/api/ai/ask", json={"prompt": "đơn hàng tháng 9/2026"}, headers=headers)
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertTrue(data.get("ok"))
            self.assertIn("Báo cáo đơn hàng", data.get("answer"))
            self.assertIn("350,000", data.get("answer"))


if __name__ == "__main__":
    unittest.main()


