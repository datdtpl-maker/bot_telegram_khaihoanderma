import tempfile
from pathlib import Path
import telegram_woocommerce_bot as bot
import notion_sync

test_log = Path(tempfile.gettempdir()) / "test_bot.log"
bot.LOG_FILE = test_log
notion_sync.LOG_FILE = test_log
