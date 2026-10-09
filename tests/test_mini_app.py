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
             patch.object(mini_app_server.bot, "wc_get", return_value=[]):
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


if __name__ == "__main__":
    unittest.main()

