import logging
import os
import sys

from telegram.ext import Application

from bot import setup_handlers
from bot.deploy_notify import maybe_broadcast_deploy_notice
from bot.workout_auto_end import close_stale_workout_sessions, stale_workout_background_loop
from config import BOT_TOKEN, WORKOUT_AUTO_END_HOURS, WORKOUT_AUTO_END_INTERVAL_SEC
from db.database import init_database
_log_level = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_LEVEL = getattr(logging, _log_level, logging.INFO)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=LOG_LEVEL,
    stream=sys.stdout,
    force=True,
)
logger = logging.getLogger(__name__)


async def error_handler(update: object, context) -> None:
    """Глобальный обработчик ошибок — логирует все необработанные исключения."""
    logger.exception("ERROR_HANDLER | update=%s error=%s", update, context.error)


def main() -> None:
    logger.info("Запуск бота")
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не задан. Создайте файл .env с BOT_TOKEN=...")
        return

    async def post_init(application: Application):
        try:
            await init_database()
            logger.info("База данных инициализирована")
        except Exception as e:
            logger.exception("Ошибка инициализации БД: %s", e)
            raise
        try:
            await maybe_broadcast_deploy_notice(application)
        except Exception as e:
            logger.exception("Рассылка после деплоя: %s", e)
        if WORKOUT_AUTO_END_HOURS > 0:
            try:
                n = await close_stale_workout_sessions()
                if n:
                    logger.info("При старте закрыто висящих тренировок: %s", n)
            except Exception as e:
                logger.exception("Автозавершение при старте: %s", e)
            application.create_task(stale_workout_background_loop(application))
            logger.info(
                "Автозавершение тренировок: через %s ч, проверка каждые %s с",
                WORKOUT_AUTO_END_HOURS,
                max(60, WORKOUT_AUTO_END_INTERVAL_SEC),
            )

    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    application.add_error_handler(error_handler)
    setup_handlers(application)
    logger.info("Обработчики зарегистрированы, запуск polling")
    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
