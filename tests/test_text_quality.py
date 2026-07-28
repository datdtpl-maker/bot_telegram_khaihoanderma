import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class VietnameseTextQualityTests(unittest.TestCase):
    def test_runtime_files_do_not_contain_common_mojibake_markers(self):
        markers = ("Ã¡", "Ã©", "Ã­", "Ã³", "Ãº", "áº", "á»", "â€", "ï¸", "ðŸ", "�")
        for name in (
            "telegram_woocommerce_bot.py",
            "notion_sync.py",
            "run_telegram_bot.ps1",
            "start_telegram_bot.bat",
        ):
            text = (ROOT / name).read_text(encoding="utf-8-sig")
            with self.subTest(file=name):
                self.assertFalse([marker for marker in markers if marker in text])

    def test_startup_configures_utf8_and_unicode_console_font(self):
        text = (ROOT / "run_telegram_bot.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("PYTHONIOENCODING", text)
        self.assertIn("SetCurrentConsoleFontEx", text)
        self.assertIn('info.FaceName = "Consolas"', text)
        self.assertIn("KHỞI ĐỘNG BOT KHẢI HOÀN DERMA", text)

    def test_batch_launcher_is_ascii_with_windows_line_endings(self):
        raw = (ROOT / "start_telegram_bot.bat").read_bytes()
        self.assertTrue(raw.isascii())
        self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"))
        self.assertNotIn(b"if not", raw.lower())
        self.assertIn(b"%*", raw)


if __name__ == "__main__":
    unittest.main()
