import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from bot.handlers.stats import show_stats_menu
from bot.keyboards import CB_MAIN, main_menu
from db.database import get_session
from db.models import User

logger = logging.getLogger(__name__)


async def safe_edit_message_text(query, text: str, reply_markup=None, *, answer_if_same: str | None = "Уже здесь", **kwargs):
    """Редактирует сообщение и отвечает на callback. При «message is not modified» показывает всплывающую подсказку."""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, **kwargs)
        await query.answer()
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            await query.answer()
            raise
        await query.answer(answer_if_same or "")


async def get_or_create_user(session: AsyncSession, telegram_id: int, username: str | None) -> User:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if not user:
        user = User(telegram_id=telegram_id, username=username)
        session.add(user)
        await session.flush()
    return user


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return
    uid = update.effective_user.id
    logger.info("cmd_start | user=%s", uid)
    async with get_session() as session:
        await get_or_create_user(
            session,
            update.effective_user.id,
            update.effective_user.username,
        )
    await update.message.reply_text(
        "Привет! Я бот для учёта тренировок.\n\n"
        "Что умею:\n"
        "• Создавать и редактировать тренировки\n"
        "• Вести справочник упражнений\n"
        "• Записывать тренировки (текстом или голосом)\n"
        "• Показывать статистику\n\n"
        "Выберите действие:",
        reply_markup=main_menu(),
    )


async def callback_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    data = query.data or ""
    uid = query.from_user.id if query.from_user else 0
    logger.info("callback_main | user=%s data=%s", uid, data)
    if data == f"{CB_MAIN}:menu":
        await safe_edit_message_text(
            query,
            "Главное меню:",
            reply_markup=main_menu(),
        )
    elif data == f"{CB_MAIN}:stats":
        await show_stats_menu(update, context, page=0)


def setup_common_handlers(application):
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CallbackQueryHandler(callback_main, pattern=f"^{CB_MAIN}:"))
