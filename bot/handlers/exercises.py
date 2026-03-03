import logging

from sqlalchemy import or_, select
from sqlalchemy.sql import func
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from bot.handlers.common import get_or_create_user, safe_edit_message_text
from bot.keyboards import (
    CB_EXERCISES,
    BODY_PARTS,
    back_to_main,
    exercise_after_add_keyboard,
    exercise_body_part_keyboard,
    exercise_detail_keyboard,
    exercise_pick_or_create_keyboard,
    exercises_list_keyboard,
)
from bot.states import States
from db.database import get_session
from db.models import Exercise, User

logger = logging.getLogger(__name__)


async def _get_user(session: AsyncSession, telegram_id: int, username: str | None) -> User:
    return await get_or_create_user(session, telegram_id, username)


async def _find_similar_exercises(session, user_id: int, name: str) -> list:
    """Ищет упражнения, в названии которых встречается любое из слов."""
    words = [w for w in name.split() if len(w) >= 2]
    if not words:
        return []
    conditions = [func.lower(Exercise.name).like(f"%{w.lower()}%") for w in words]
    result = await session.execute(
        select(Exercise)
        .where(Exercise.user_id == user_id, or_(*conditions))
        .order_by(Exercise.name)
    )
    return list(result.scalars().unique().all())


async def exercises_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    parts = query.data.split(":")
    page = int(parts[2]) if len(parts) > 2 else 0
    if not update.effective_user:
        await query.answer()
        return
    async with get_session() as session:
        user = await _get_user(session, update.effective_user.id, update.effective_user.username)
        result = await session.execute(
            select(Exercise).where(Exercise.user_id == user.id).order_by(Exercise.name)
        )
        exercises = list(result.scalars().all())
    if not exercises:
        await safe_edit_message_text(
            query,
            "Список упражнений пуст.\nДобавьте первое упражнение:",
            reply_markup=exercises_list_keyboard([], page),
        )
        return
    await safe_edit_message_text(
        query,
        "Ваши упражнения:",
        reply_markup=exercises_list_keyboard(exercises, page),
    )


async def exercise_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()
    ex_id = int(query.data.split(":")[2])
    async with get_session() as session:
        result = await session.execute(select(Exercise).where(Exercise.id == ex_id))
        ex = result.scalar_one_or_none()
    if not ex:
        await query.edit_message_text("Упражнение не найдено.", reply_markup=back_to_main())
        return
    await query.edit_message_text(
        f"🏋 {ex.name}\nЧасть тела: {getattr(ex, 'body_part', 'Другое')}\n\nID: {ex.id}",
        reply_markup=exercise_detail_keyboard(ex.id),
    )


async def exercise_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()
    parts = query.data.split(":")
    ex_id = int(parts[2])
    async with get_session() as session:
        result = await session.execute(select(Exercise).where(Exercise.id == ex_id))
        ex = result.scalar_one_or_none()
        if ex:
            await session.delete(ex)
    await query.edit_message_text("Упражнение удалено.", reply_markup=back_to_main())
    context.user_data.pop("exercise_name", None)
    return ConversationHandler.END


async def exercise_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("Введите название упражнения (или /cancel для отмены):")
    return States.EXERCISE_NAME.value


async def exercise_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        return States.EXERCISE_NAME.value
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("Введите непустое название.")
        return States.EXERCISE_NAME.value
    context.user_data["exercise_name"] = text
    if not update.effective_user:
        return ConversationHandler.END
    async with get_session() as session:
        user = await _get_user(session, update.effective_user.id, update.effective_user.username)
        similar = await _find_similar_exercises(session, user.id, text)
    if similar:
        await update.message.reply_text(
            "Найдены похожие упражнения. Выберите или создайте новое:",
            reply_markup=exercise_pick_or_create_keyboard(similar),
        )
        return States.EXERCISE_PICK_OR_CREATE.value
    await update.message.reply_text("Выберите часть тела:", reply_markup=exercise_body_part_keyboard())
    return States.EXERCISE_BODY_PART.value


async def exercise_add_pick_or_create(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.data:
        return States.EXERCISE_PICK_OR_CREATE.value
    await query.answer()
    parts = query.data.split(":")
    if parts[1] == "create_anyway":
        await query.edit_message_text("Выберите часть тела:", reply_markup=exercise_body_part_keyboard())
        return States.EXERCISE_BODY_PART.value
    if parts[1] == "use" and len(parts) >= 3:
        ex_id = int(parts[2])
        async with get_session() as session:
            result = await session.execute(select(Exercise).where(Exercise.id == ex_id))
            ex = result.scalar_one_or_none()
        if ex:
            await query.edit_message_text(
                f"Упражнение уже есть: {ex.name} ({getattr(ex, 'body_part', 'Другое')})",
                reply_markup=exercise_detail_keyboard(ex.id),
            )
        else:
            await query.edit_message_text("Упражнение не найдено.", reply_markup=back_to_main())
        context.user_data.pop("exercise_name", None)
        return ConversationHandler.END
    return ConversationHandler.END


async def _cancel_pick_to_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("exercise_name", None)
    await exercises_list(update, context)
    return ConversationHandler.END


async def exercise_add_body_part(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.data:
        return States.EXERCISE_BODY_PART.value
    await query.answer()
    parts = query.data.split(":")
    if len(parts) < 3 or parts[1] != "body":
        return ConversationHandler.END
    try:
        idx = int(parts[2])
        body_part = BODY_PARTS[idx] if 0 <= idx < len(BODY_PARTS) else "Другое"
    except (ValueError, IndexError):
        body_part = "Другое"
    name = context.user_data.pop("exercise_name", "")
    if not name or not update.effective_user:
        await query.edit_message_text("Сессия истекла. Начните заново.", reply_markup=back_to_main())
        return ConversationHandler.END
    async with get_session() as session:
        user = await _get_user(session, update.effective_user.id, update.effective_user.username)
        ex = Exercise(user_id=user.id, name=name, body_part=body_part)
        session.add(ex)
    await query.edit_message_text(f"✅ Упражнение «{name}» ({body_part}) добавлено.", reply_markup=exercise_after_add_keyboard())
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        await update.message.reply_text("Отменено.", reply_markup=back_to_main())
    context.user_data.pop("exercise_name", None)
    context.user_data.pop("exercise_pick_or_create", None)
    return ConversationHandler.END


def setup_exercise_handlers(application):
    application.add_handler(
        CallbackQueryHandler(exercises_list, pattern=f"^{CB_EXERCISES}:list"),
    )
    application.add_handler(
        CallbackQueryHandler(exercise_view, pattern=f"^{CB_EXERCISES}:view:\\d+"),
    )
    from bot.keyboards import CB_DELETE
    application.add_handler(
        CallbackQueryHandler(exercise_delete, pattern=f"^{CB_EXERCISES}:{CB_DELETE}:\\d+$"),
    )
    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(exercise_add_start, pattern=f"^{CB_EXERCISES}:add$"),
        ],
        states={
            States.EXERCISE_NAME.value: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, exercise_add_name),
            ],
            States.EXERCISE_PICK_OR_CREATE.value: [
                CallbackQueryHandler(exercise_add_pick_or_create, pattern=f"^{CB_EXERCISES}:(use|create_anyway)"),
                CallbackQueryHandler(_cancel_pick_to_list, pattern=f"^{CB_EXERCISES}:list"),
            ],
            States.EXERCISE_BODY_PART.value: [
                CallbackQueryHandler(exercise_add_body_part, pattern=f"^{CB_EXERCISES}:body:\\d+$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(conv)
