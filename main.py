import asyncio
import logging

from telegram.ext import Application

from bot import setup_handlers
from config import BOT_TOKEN
from db.database import init_database

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main() -> None:
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не задан. Создайте файл .env с BOT_TOKEN=...")
        return

    async def post_init(_):
        await init_database()

    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    setup_handlers(application)
    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
