import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL")
# Часовой пояс для отображения (Тбилиси UTC+4)
TIMEZONE = os.getenv("TIMEZONE", "Asia/Tbilisi")

# Сообщение при потере состояния (перезапуск/обновление бота)
RESTART_MSG = (
    "⚠️ Бот был перезапущен (обновление).\n\n"
    "Нажмите /start и начните заново."
)
if not DATABASE_URL:
    db_path = Path(__file__).parent / "training.db"
    DATABASE_URL = f"sqlite+aiosqlite:///{db_path}"
else:
    # Railway даёт postgres:// или postgresql://, нужен postgresql+asyncpg://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = "postgresql+asyncpg://" + DATABASE_URL[11:]
    elif DATABASE_URL.startswith("postgresql://") and "+asyncpg" not in DATABASE_URL:
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
