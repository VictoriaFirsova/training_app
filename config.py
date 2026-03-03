import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    db_path = Path(__file__).parent / "training.db"
    DATABASE_URL = f"sqlite+aiosqlite:///{db_path}"
else:
    # Railway/Heroku дают postgres://, нужен postgresql+asyncpg://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
