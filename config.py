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

# Рассылка при деплое на Railway (см. bot/deploy_notify.py). Текст можно переопределить в .env.
DEPLOY_NOTIFY_TEXT = os.getenv(
    "DEPLOY_NOTIFY_TEXT",
    "🔄 Бот обновлён (новая версия на сервере).\n\n"
    "Если меню или кнопки ведут себя странно — нажмите /start.",
).replace("\\n", "\n")


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


# Автозавершение «забытых» тренировок (ended_at = NULL дольше N часов после старта). 0 — выключено.
WORKOUT_AUTO_END_HOURS = _float_env("WORKOUT_AUTO_END_HOURS", 4.0)
# Как часто проверять БД (секунды). По умолчанию 15 минут.
WORKOUT_AUTO_END_INTERVAL_SEC = int(_float_env("WORKOUT_AUTO_END_INTERVAL_SEC", 900.0))

if not DATABASE_URL:
    db_path = Path(__file__).parent / "training.db"
    DATABASE_URL = f"sqlite+aiosqlite:///{db_path}"
else:
    # Railway даёт postgres:// или postgresql://, нужен postgresql+asyncpg://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = "postgresql+asyncpg://" + DATABASE_URL[11:]
    elif DATABASE_URL.startswith("postgresql://") and "+asyncpg" not in DATABASE_URL:
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
