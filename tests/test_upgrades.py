import unittest
from unittest.mock import patch, MagicMock
import telegram_woocommerce_bot as bot


class UpgradesTestSuite(unittest.TestCase):
    def test_resilient_session_configured(self):
        session = bot.create_resilient_session()
        self.assertIsNotNone(session)
        # Check HTTPAdapter and retries on https://
        adapter = session.adapters.get("https://")
        self.assertIsNotNone(adapter)
        self.assertEqual(adapter.max_retries.total, 3)
        self.assertEqual(adapter.max_retries.backoff_factor, 1.0)
        self.assertIn(500, adapter.max_retries.status_forcelist)
        self.assertIn(502, adapter.max_retries.status_forcelist)
        self.assertIn(503, adapter.max_retries.status_forcelist)
        self.assertIn(504, adapter.max_retries.status_forcelist)

    def test_fuzzy_matching_variations(self):
        variations = [
            {"id": 101, "attributes": [{"name": "Dung tích", "option": "Tuýp 15g"}]},
            {"id": 102, "attributes": [{"name": "Dung tích", "option": "Tuýp 30g"}]},
            {"id": 103, "attributes": [{"name": "Dung tích", "option": "Chai 50ml"}]},
        ]
        # 1. Exact match
        res_exact = bot.find_matching_variations(variations, "tuyp 15g")
        self.assertEqual(len(res_exact), 1)
        self.assertEqual(res_exact[0]["id"], 101)

        # 2. Substring match
        res_sub = bot.find_matching_variations(variations, "30g")
        self.assertEqual(len(res_sub), 1)
        self.assertEqual(res_sub[0]["id"], 102)

        # 3. Fuzzy match (typo: '15gr' vs 'tuyp 15g' or 'chai 50m')
        res_fuzzy = bot.find_matching_variations(variations, "chai 50m")
        self.assertTrue(len(res_fuzzy) >= 1)
        self.assertEqual(res_fuzzy[0]["id"], 103)

    def test_fuzzy_matching_products(self):
        mock_cache = [
            {"id": 201, "name": "Gel Trị Mụn Epiduo 0.1% Adapalene 15g"},
            {"id": 202, "name": "Serum Obagi Daily Hydro-Drops 30ml"},
            {"id": 203, "name": "Kem Dưỡng Ẩm La Roche-Posay B5+ 40ml"},
        ]
        with patch.object(bot, "search_products", return_value=[]), \
             patch.object(bot, "get_cached_all_products", return_value=mock_cache):
            
            # Exact substring
            res1 = bot.find_products_by_fuzzy_name("Epiduo")
            self.assertEqual(len(res1), 1)
            self.assertEqual(res1[0]["id"], 201)

            # Typo search: "obagi hydra" -> matches Obagi Daily Hydro-Drops
            res2 = bot.find_products_by_fuzzy_name("obagi hydro drop")
            self.assertTrue(len(res2) >= 1)
            self.assertEqual(res2[0]["id"], 202)

    def test_revenue_inline_keyboard_structure(self):
        markup = bot.revenue_inline_keyboard("tháng 7")
        self.assertIn("inline_keyboard", markup)
        buttons = markup["inline_keyboard"]
        self.assertEqual(len(buttons), 2)
        # Row 1: Excel export
        self.assertIn("Xuất file Excel", buttons[0][0]["text"])
        self.assertEqual(buttons[0][0]["callback_data"], "chi tiết tháng 7")
        # Row 2: Today, This Month, This Year
        self.assertEqual(len(buttons[1]), 3)
        self.assertIn("Hôm nay", buttons[1][0]["text"])
        self.assertIn("Tháng này", buttons[1][1]["text"])
        self.assertIn("Năm nay", buttons[1][2]["text"])

    def test_wants_ping_recognizes_health_commands(self):
        self.assertTrue(bot.wants_ping("ping"))
        self.assertTrue(bot.wants_ping("/health"))
        self.assertTrue(bot.wants_ping("health"))
        self.assertTrue(bot.wants_ping("kiểm tra kết nối"))
        self.assertTrue(bot.wants_ping("kiem tra he thong"))
        self.assertTrue(bot.wants_ping("/status"))

    def test_build_ping_html_reports_latency(self):
        with patch.object(bot, "wc_get", return_value=[{"id": 1}]), \
             patch.object(bot, "wp_request", return_value={"id": 1}), \
             patch.object(bot.HTTP_SESSION, "post", return_value=MagicMock(status_code=200)):
            html = bot.build_ping_html()
            self.assertIn("Trạng thái hệ thống & kết nối", html)
            self.assertIn("WooCommerce: <b>OK</b>", html)
            self.assertIn("WordPress: <b>OK</b>", html)
            self.assertIn("Telegram: <b>OK</b>", html)
            self.assertIn("ms", html)


if __name__ == "__main__":
    unittest.main()
