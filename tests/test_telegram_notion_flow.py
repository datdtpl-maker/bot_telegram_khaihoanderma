import unittest
from unittest.mock import patch

import telegram_woocommerce_bot as bot


class NotionConfirmationTests(unittest.TestCase):
    def tearDown(self):
        bot.PENDING_ACTIONS.clear()

    def test_confirmation_keeps_all_detected_page_ids(self):
        message = bot.prepare_notion_sync_confirmation(
            123,
            page_ids=["page-1", "page-2"],
            titles=["Sản phẩm 1", "Sản phẩm 2"],
        )

        self.assertIn("Cần xác nhận", message)
        self.assertIn("Sản phẩm 1", message)
        self.assertEqual(
            bot.PENDING_ACTIONS[123],
            {"type": "notion_sync", "page_ids": ["page-1", "page-2"]},
        )

    @patch("notion_sync.query_notion_pages_to_post")
    @patch("notion_sync.load_config")
    def test_manual_confirmation_freezes_current_page_list(self, load_config, query_pages):
        load_config.return_value = {"NOTION_TOKEN": "token", "NOTION_DATABASE_ID": "database"}
        query_pages.return_value = [
            {
                "id": "page-1",
                "properties": {
                    "Tên sản phẩm": {"title": [{"plain_text": "Sản phẩm 1"}]}
                },
            }
        ]

        message = bot.prepare_manual_notion_sync_confirmation(123)

        self.assertIn("Sản phẩm 1", message)
        self.assertEqual(bot.PENDING_ACTIONS[123]["page_ids"], ["page-1"])


if __name__ == "__main__":
    unittest.main()
