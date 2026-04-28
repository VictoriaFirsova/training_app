import logging
import re

from sqlalchemy import select
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, ContextTypes

from bot.keyboards import CB_STATS, main_menu, stats_exercises_keyboard
from db.database import get_session
from db.models import Exercise, User
from services.stats_report import build_exercise_stats_report

logger = logging.getLogger(__name__)


async def _safe_edit(query, text: str, reply_markup=None) -> None:
    try:
        await query.edit_message_text(text, reply_markup=reply_markup)
        await query.answer()
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            await query.answer("Уже здесь")
            return
        await query.answer()
        raise


async def _load_user_exercises(session, telegram_id: int) -> tuple[User | None, list[Exercise]]:
    user_result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        return None, []
    ex_result = await session.execute(
        select(Exercise).where(Exercise.user_id == user.id).order_by(Exercise.name.asc(), Exercise.id.asc())
    )
    exercises = list(ex_result.scalars().all())
    return user, exercises


async def show_stats_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return
    async with get_session() as session:
        user, exercises = await _load_user_exercises(session, query.from_user.id)
    if user is None:
        await _safe_edit(query, "Пользователь не найден. Нажмите /start", reply_markup=main_menu())
        return
    if not exercises:
        await _safe_edit(
            query,
            "Пока нет упражнений для статистики. Добавьте упражнение и записи тренировок.",
            reply_markup=main_menu(),
        )
        return
    await _safe_edit(
        query,
        "📊 Выберите упражнение для отчёта (CSV + PDF):",
        reply_markup=stats_exercises_keyboard(exercises, page=page),
    )


async def callback_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return
    data = query.data or ""
    uid = query.from_user.id
    logger.info("callback_stats | user=%s data=%s", uid, data)
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "list":
        page = 0
        if len(parts) > 2 and parts[2].isdigit():
            page = max(0, int(parts[2]))
        await show_stats_menu(update, context, page=page)
        return

    if action != "pick" or len(parts) < 3 or not parts[2].isdigit():
        await query.answer()
        return

    exercise_id = int(parts[2])
    await query.answer("Формирую отчёт…")
    await query.edit_message_text("Готовлю отчёт по упражнению…")
    try:
        async with get_session() as session:
            user, _ = await _load_user_exercises(session, uid)
            if user is None:
                await query.message.reply_text("Пользователь не найден. Нажмите /start", reply_markup=main_menu())
                return
            csv_path, pdf_path = await build_exercise_stats_report(session, user.id, exercise_id)
            exercise = await session.get(Exercise, exercise_id)
    except Exception:
        logger.exception("callback_stats | report_build_failed user=%s exercise_id=%s", uid, exercise_id)
        await query.message.reply_text("Не удалось сформировать отчёт. Попробуйте позже.", reply_markup=main_menu())
        return

    title = exercise.name if exercise else f"#{exercise_id}"
    safe_title = re.sub(r"[^\w\-.]+", "_", title, flags=re.UNICODE).strip("_") or f"exercise_{exercise_id}"
    with pdf_path.open("rb") as pdf_f:
        await query.message.reply_document(
            document=pdf_f,
            filename=f"stats_{safe_title}.pdf",
            caption=f"Отчёт по упражнению: {title}",
        )
    with csv_path.open("rb") as csv_f:
        await query.message.reply_document(
            document=csv_f,
            filename=f"stats_{safe_title}.csv",
            caption=f"Данные отчёта (CSV): {title}",
            reply_markup=main_menu(),
        )
    pdf_path.unlink(missing_ok=True)
    csv_path.unlink(missing_ok=True)


def setup_stats_handlers(application) -> None:
    application.add_handler(CallbackQueryHandler(callback_stats, pattern=f"^{CB_STATS}:"))
