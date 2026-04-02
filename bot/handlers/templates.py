import logging

from sqlalchemy import select
from sqlalchemy.orm import selectinload
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
from config import RESTART_MSG
from bot.handlers.exercises import _find_similar_exercises
from bot.keyboards import (
    BODY_PARTS,
    CB_TEMPLATES,
    CB_DELETE,
    back_to_main,
    template_add_exercise_choose_keyboard,
    template_add_exercise_by_body_keyboard,
    template_exercise_pick_or_create_keyboard,
    template_delete_confirm_keyboard,
    template_detail_keyboard,
    template_new_exercise_body_part_keyboard,
    templates_list_keyboard,
)
from bot.states import States
from db.database import get_session
from db.models import Exercise, TemplateExercise, User, WorkoutTemplate

logger = logging.getLogger(__name__)


async def _get_user(session, telegram_id: int, username: str | None) -> User:
    return await get_or_create_user(session, telegram_id, username)


async def templates_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            select(WorkoutTemplate).where(WorkoutTemplate.user_id == user.id).order_by(WorkoutTemplate.name)
        )
        templates = list(result.scalars().all())
    if not templates:
        await safe_edit_message_text(
            query,
            "Нет тренировок.\nСоздайте первую:",
            reply_markup=templates_list_keyboard([], page),
        )
        return
    await safe_edit_message_text(
        query,
        "Ваши тренировки:",
        reply_markup=templates_list_keyboard(templates, page),
    )


async def template_add_exercise_show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать выбор: часть тела или создать новое упражнение."""
    query = update.callback_query
    if not query or not query.data or ":add_ex:" not in query.data:
        return
    await query.answer()
    parts = query.data.split(":")
    t_id = int(parts[2])
    if not update.effective_user:
        return
    async with get_session() as session:
        user = await _get_user(session, update.effective_user.id, update.effective_user.username)
        result = await session.execute(
            select(WorkoutTemplate).where(WorkoutTemplate.id == t_id, WorkoutTemplate.user_id == user.id)
        )
        tpl = result.scalar_one_or_none()
        if not tpl:
            await query.edit_message_text("Тренировка не найдена.", reply_markup=back_to_main())
            return
    await query.edit_message_text(
        "Выберите часть тела или создайте новое упражнение:",
        reply_markup=template_add_exercise_choose_keyboard(t_id),
    )


async def template_add_exercise_by_body(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать упражнения по выбранной части тела."""
    query = update.callback_query
    if not query or not query.data or ":add_ex_body:" not in query.data:
        return
    await query.answer()
    parts = query.data.split(":")
    t_id = int(parts[2])
    body_idx = int(parts[3]) if len(parts) > 3 else 0
    body_part = BODY_PARTS[body_idx] if 0 <= body_idx < len(BODY_PARTS) else "Другое"
    if not update.effective_user:
        return
    async with get_session() as session:
        user = await _get_user(session, update.effective_user.id, update.effective_user.username)
        result = await session.execute(
            select(WorkoutTemplate)
            .options(selectinload(WorkoutTemplate.template_exercises))
            .where(WorkoutTemplate.id == t_id, WorkoutTemplate.user_id == user.id)
        )
        tpl = result.scalar_one_or_none()
        if not tpl:
            await query.edit_message_text("Тренировка не найдена.", reply_markup=back_to_main())
            return
        ex_result = await session.execute(
            select(Exercise)
            .where(Exercise.user_id == user.id, Exercise.body_part == body_part)
            .order_by(Exercise.name)
        )
        exercises = list(ex_result.scalars().all())
        added_ids = {te.exercise_id for te in tpl.template_exercises}
    await query.edit_message_text(
        f"Упражнения ({body_part}):",
        reply_markup=template_add_exercise_by_body_keyboard(t_id, body_idx, exercises, added_ids),
    )


async def template_add_exercise_new_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запуск создания упражнения прямо в тренировке (с выбором части тела)."""
    query = update.callback_query
    if not query or not query.data or ":add_ex_new:" not in query.data:
        return ConversationHandler.END
    await query.answer()
    parts = query.data.split(":")
    t_id = int(parts[2])
    context.user_data["template_new_exercise_id"] = t_id
    context.user_data.pop("template_new_exercise_body_idx", None)
    await query.edit_message_text("Введите название упражнения (или /cancel для отмены):")
    return States.TEMPLATE_NEW_EXERCISE_NAME.value


async def template_add_exercise_new_body_prefilled_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Создание упражнения с уже выбранной частью тела."""
    query = update.callback_query
    if not query or not query.data or ":add_ex_new_body:" not in query.data:
        return ConversationHandler.END
    await query.answer()
    parts = query.data.split(":")
    t_id = int(parts[2])
    body_idx = int(parts[3]) if len(parts) > 3 else len(BODY_PARTS) - 1
    context.user_data["template_new_exercise_id"] = t_id
    context.user_data["template_new_exercise_body_idx"] = body_idx
    await query.edit_message_text("Введите название упражнения (или /cancel для отмены):")
    return States.TEMPLATE_NEW_EXERCISE_NAME.value


async def template_add_exercise_new_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос части тела или создание (если часть уже выбрана)."""
    if not update.message or not update.message.text:
        return States.TEMPLATE_NEW_EXERCISE_NAME.value
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Введите непустое название.")
        return States.TEMPLATE_NEW_EXERCISE_NAME.value
    t_id = context.user_data.get("template_new_exercise_id")
    body_idx = context.user_data.get("template_new_exercise_body_idx")
    if not t_id or not update.effective_user:
        await update.message.reply_text(RESTART_MSG, reply_markup=back_to_main())
        return ConversationHandler.END
    context.user_data["template_new_exercise_name"] = name
    async with get_session() as session:
        user = await _get_user(session, update.effective_user.id, update.effective_user.username)
        similar = await _find_similar_exercises(session, user.id, name)
    if similar:
        await update.message.reply_text(
            "Найдены похожие упражнения. Выберите или создайте новое:",
            reply_markup=template_exercise_pick_or_create_keyboard(t_id, similar),
        )
        return States.TEMPLATE_NEW_EXERCISE_PICK_OR_CREATE.value
    if body_idx is not None:
        body_part = BODY_PARTS[body_idx] if 0 <= body_idx < len(BODY_PARTS) else "Другое"
        context.user_data.pop("template_new_exercise_body_idx", None)
        async with get_session() as session:
            user = await _get_user(session, update.effective_user.id, update.effective_user.username)
            result = await session.execute(
                select(WorkoutTemplate)
                .options(selectinload(WorkoutTemplate.template_exercises))
                .where(WorkoutTemplate.id == t_id, WorkoutTemplate.user_id == user.id)
            )
            tpl = result.scalar_one_or_none()
            if not tpl:
                await update.message.reply_text("Тренировка не найдена.", reply_markup=back_to_main())
                return ConversationHandler.END
            ex = Exercise(user_id=user.id, name=name, body_part=body_part)
            session.add(ex)
            await session.flush()
            logger.info("template_add_exercise | NEW ex user=%s ex_id=%s template_id=%s name=%r", user.id, ex.id, t_id, name)
            order = max([te.order for te in tpl.template_exercises], default=0) + 1
            te = TemplateExercise(template_id=t_id, exercise_id=ex.id, order=order)
            session.add(te)
            result = await session.execute(
                select(WorkoutTemplate)
                .options(selectinload(WorkoutTemplate.template_exercises).selectinload(TemplateExercise.exercise))
                .where(WorkoutTemplate.id == t_id)
            )
            tpl = result.scalar_one()
        lines = [f"📋 {tpl.name}", "\nУпражнения:"]
        for te in tpl.template_exercises:
            lines.append(f"  • {te.exercise.name} ({te.exercise.body_part})")
        lines.append(f"\n✅ Упражнение «{name}» ({body_part}) создано и добавлено.")
        await update.message.reply_text("\n".join(lines), reply_markup=template_detail_keyboard(tpl.id))
        context.user_data.pop("template_new_exercise_name", None)
        context.user_data.pop("template_new_exercise_id", None)
        return ConversationHandler.END
    await update.message.reply_text(
        "Выберите часть тела:",
        reply_markup=template_new_exercise_body_part_keyboard(t_id),
    )
    return States.TEMPLATE_NEW_EXERCISE_BODY_PART.value


async def template_add_exercise_pick_or_create(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Выбор: использовать существующее упражнение или создать новое."""
    query = update.callback_query
    if not query or not query.data:
        return States.TEMPLATE_NEW_EXERCISE_PICK_OR_CREATE.value
    await query.answer()
    parts = query.data.split(":")
    t_id = context.user_data.get("template_new_exercise_id")
    name = context.user_data.get("template_new_exercise_name", "")
    body_idx = context.user_data.get("template_new_exercise_body_idx")
    if not t_id or not update.effective_user:
        await query.edit_message_text(RESTART_MSG, reply_markup=back_to_main())
        return ConversationHandler.END
    if "use_ex" in query.data and len(parts) >= 4:
        ex_id = int(parts[3])
        async with get_session() as session:
            user = await _get_user(session, update.effective_user.id, update.effective_user.username)
            result = await session.execute(
                select(WorkoutTemplate)
                .options(selectinload(WorkoutTemplate.template_exercises))
                .where(WorkoutTemplate.id == t_id, WorkoutTemplate.user_id == user.id)
            )
            tpl = result.scalar_one_or_none()
            if not tpl:
                await query.edit_message_text("Тренировка не найдена.", reply_markup=back_to_main())
                return ConversationHandler.END
            ex_result = await session.execute(select(Exercise).where(Exercise.id == ex_id))
            ex = ex_result.scalar_one_or_none()
            if not ex or ex.user_id != user.id:
                await query.edit_message_text("Упражнение не найдено.", reply_markup=back_to_main())
                return ConversationHandler.END
            if any(te.exercise_id == ex_id for te in tpl.template_exercises):
                await query.edit_message_text("Упражнение уже в тренировке.", reply_markup=template_detail_keyboard(t_id))
                context.user_data.pop("template_new_exercise_name", None)
                context.user_data.pop("template_new_exercise_id", None)
                context.user_data.pop("template_new_exercise_body_idx", None)
                return ConversationHandler.END
            order = max([te.order for te in tpl.template_exercises], default=0) + 1
            te = TemplateExercise(template_id=t_id, exercise_id=ex_id, order=order)
            session.add(te)
            result = await session.execute(
                select(WorkoutTemplate)
                .options(selectinload(WorkoutTemplate.template_exercises).selectinload(TemplateExercise.exercise))
                .where(WorkoutTemplate.id == t_id)
            )
            tpl = result.scalar_one()
        lines = [f"📋 {tpl.name}", "\nУпражнения:"]
        for te in tpl.template_exercises:
            lines.append(f"  • {te.exercise.name} ({te.exercise.body_part})")
        lines.append(f"\n✅ Упражнение «{ex.name}» добавлено.")
        await query.edit_message_text("\n".join(lines), reply_markup=template_detail_keyboard(tpl.id))
        context.user_data.pop("template_new_exercise_name", None)
        context.user_data.pop("template_new_exercise_id", None)
        context.user_data.pop("template_new_exercise_body_idx", None)
        return ConversationHandler.END
    if "create_anyway" in query.data:
        context.user_data.pop("template_new_exercise_pick", None)
        if body_idx is not None:
            body_part = BODY_PARTS[body_idx] if 0 <= body_idx < len(BODY_PARTS) else "Другое"
            context.user_data.pop("template_new_exercise_body_idx", None)
            async with get_session() as session:
                user = await _get_user(session, update.effective_user.id, update.effective_user.username)
                result = await session.execute(
                    select(WorkoutTemplate)
                    .options(selectinload(WorkoutTemplate.template_exercises))
                    .where(WorkoutTemplate.id == t_id, WorkoutTemplate.user_id == user.id)
                )
                tpl = result.scalar_one_or_none()
                if not tpl:
                    await query.edit_message_text("Тренировка не найдена.", reply_markup=back_to_main())
                    return ConversationHandler.END
                ex = Exercise(user_id=user.id, name=name, body_part=body_part)
                session.add(ex)
                await session.flush()
                logger.info("template_add_exercise | NEW ex (create_anyway) user=%s ex_id=%s template_id=%s name=%r", user.id, ex.id, t_id, name)
                order = max([te.order for te in tpl.template_exercises], default=0) + 1
                te = TemplateExercise(template_id=t_id, exercise_id=ex.id, order=order)
                session.add(te)
                result = await session.execute(
                    select(WorkoutTemplate)
                    .options(selectinload(WorkoutTemplate.template_exercises).selectinload(TemplateExercise.exercise))
                    .where(WorkoutTemplate.id == t_id)
                )
                tpl = result.scalar_one()
            lines = [f"📋 {tpl.name}", "\nУпражнения:"]
            for te in tpl.template_exercises:
                lines.append(f"  • {te.exercise.name} ({te.exercise.body_part})")
            lines.append(f"\n✅ Упражнение «{name}» ({body_part}) создано и добавлено.")
            await query.edit_message_text("\n".join(lines), reply_markup=template_detail_keyboard(tpl.id))
            context.user_data.pop("template_new_exercise_name", None)
            context.user_data.pop("template_new_exercise_id", None)
            return ConversationHandler.END
        await query.edit_message_text(
            "Выберите часть тела:",
            reply_markup=template_new_exercise_body_part_keyboard(t_id),
        )
        return States.TEMPLATE_NEW_EXERCISE_BODY_PART.value
    return ConversationHandler.END


async def _template_pick_cancel_to_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("template_new_exercise_name", None)
    context.user_data.pop("template_new_exercise_id", None)
    context.user_data.pop("template_new_exercise_body_idx", None)
    await template_view(update, context)
    return ConversationHandler.END


async def template_add_exercise_new_body(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Создание упражнения и добавление в тренировку."""
    query = update.callback_query
    if not query or not query.data or ":ex_body:" not in query.data:
        return States.TEMPLATE_NEW_EXERCISE_BODY_PART.value
    await query.answer()
    parts = query.data.split(":")
    t_id = int(parts[2])
    try:
        idx = int(parts[3])
        body_part = BODY_PARTS[idx] if 0 <= idx < len(BODY_PARTS) else "Другое"
    except (ValueError, IndexError):
        body_part = "Другое"
    name = context.user_data.pop("template_new_exercise_name", "")
    context.user_data.pop("template_new_exercise_id", None)
    if not name or not update.effective_user:
        await query.edit_message_text(RESTART_MSG, reply_markup=back_to_main())
        return ConversationHandler.END
    async with get_session() as session:
        user = await _get_user(session, update.effective_user.id, update.effective_user.username)
        result = await session.execute(
            select(WorkoutTemplate)
            .options(selectinload(WorkoutTemplate.template_exercises))
            .where(WorkoutTemplate.id == t_id, WorkoutTemplate.user_id == user.id)
        )
        tpl = result.scalar_one_or_none()
        if not tpl:
            await query.edit_message_text("Тренировка не найдена.", reply_markup=back_to_main())
            return ConversationHandler.END
        ex = Exercise(user_id=user.id, name=name, body_part=body_part)
        session.add(ex)
        await session.flush()
        logger.info("template_add_exercise | NEW ex (ex_body) user=%s ex_id=%s template_id=%s name=%r", user.id, ex.id, t_id, name)
        order = max([te.order for te in tpl.template_exercises], default=0) + 1
        te = TemplateExercise(template_id=t_id, exercise_id=ex.id, order=order)
        session.add(te)
        result = await session.execute(
            select(WorkoutTemplate)
            .options(selectinload(WorkoutTemplate.template_exercises).selectinload(TemplateExercise.exercise))
            .where(WorkoutTemplate.id == t_id)
        )
        tpl = result.scalar_one()
    lines = [f"📋 {tpl.name}"]
    if tpl.description:
        lines.append(f"Описание: {tpl.description}")
    lines.append("\nУпражнения:")
    for te in tpl.template_exercises:
        lines.append(f"  • {te.exercise.name} ({te.exercise.body_part})")
    lines.append(f"\n✅ Упражнение «{name}» ({body_part}) создано и добавлено.")
    await query.edit_message_text("\n".join(lines), reply_markup=template_detail_keyboard(tpl.id))
    return ConversationHandler.END


async def template_add_exercise_do(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or ":add_ex_sel:" not in query.data:
        return
    await query.answer()
    parts = query.data.split(":")
    t_id = int(parts[2])
    ex_id = int(parts[3])
    if not update.effective_user:
        return
    async with get_session() as session:
        user = await _get_user(session, update.effective_user.id, update.effective_user.username)
        result = await session.execute(
            select(WorkoutTemplate)
            .options(selectinload(WorkoutTemplate.template_exercises))
            .where(WorkoutTemplate.id == t_id, WorkoutTemplate.user_id == user.id)
        )
        tpl = result.scalar_one_or_none()
        if not tpl:
            await query.edit_message_text("Тренировка не найдена.", reply_markup=back_to_main())
            return
        order = max([te.order for te in tpl.template_exercises], default=0) + 1
        te = TemplateExercise(template_id=t_id, exercise_id=ex_id, order=order)
        session.add(te)
        await session.flush()
        logger.info("template_add_exercise | user=%s template_id=%s exercise_id=%s", user.id, t_id, ex_id)
        result = await session.execute(
            select(WorkoutTemplate)
            .options(selectinload(WorkoutTemplate.template_exercises).selectinload(TemplateExercise.exercise))
            .where(WorkoutTemplate.id == t_id)
        )
        tpl = result.scalar_one()
    lines = [f"📋 {tpl.name}"]
    if tpl.description:
        lines.append(f"Описание: {tpl.description}")
    lines.append("\nУпражнения:")
    for te in tpl.template_exercises:
        lines.append(f"  • {te.exercise.name}")
    lines.append("\n✅ Упражнение добавлено.")
    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=template_detail_keyboard(tpl.id),
    )


async def template_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()
    parts = query.data.split(":")
    t_id = int(parts[2])
    async with get_session() as session:
        result = await session.execute(
            select(WorkoutTemplate)
            .options(selectinload(WorkoutTemplate.template_exercises).selectinload(TemplateExercise.exercise))
            .where(WorkoutTemplate.id == t_id)
        )
        tpl = result.scalar_one_or_none()
    if not tpl:
        await query.edit_message_text("Тренировка не найдена.", reply_markup=back_to_main())
        return
    lines = [f"📋 {tpl.name}"]
    if tpl.description:
        lines.append(f"Описание: {tpl.description}")
    lines.append("\nУпражнения:")
    for te in tpl.template_exercises:
        lines.append(f"  • {te.exercise.name}")
    await query.edit_message_text(
        "\n".join(lines) if lines else "Пустая тренировка",
        reply_markup=template_detail_keyboard(tpl.id),
    )


async def template_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()
    parts = query.data.split(":")
    if len(parts) < 4 or parts[2] != "del" or parts[3] != "confirm":
        return
    t_id = int(parts[4])
    async with get_session() as session:
        result = await session.execute(select(WorkoutTemplate).where(WorkoutTemplate.id == t_id))
        tpl = result.scalar_one_or_none()
        if tpl:
            await query.edit_message_text(
                f"Удалить тренировку «{tpl.name}»? (да/нет)",
                reply_markup=template_delete_confirm_keyboard(t_id),
            )
        return


async def template_delete_do(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()
    parts = query.data.split(":")
    # tmpl:del:confirm:<id>
    if (
        len(parts) == 4
        and parts[0] == CB_TEMPLATES
        and parts[1] == CB_DELETE
        and parts[2] == "confirm"
    ):
        t_id = int(parts[3])
    else:
        return
    async with get_session() as session:
        result = await session.execute(select(WorkoutTemplate).where(WorkoutTemplate.id == t_id))
        tpl = result.scalar_one_or_none()
        if tpl:
            name = tpl.name
            await session.delete(tpl)
            await query.edit_message_text(f"Тренировка «{name}» удалена.", reply_markup=back_to_main())
        else:
            await query.edit_message_text("Тренировка не найдена.", reply_markup=back_to_main())


def _template_delete_cb(query_data: str) -> bool:
    parts = query_data.split(":")
    return len(parts) >= 4 and parts[2] == CB_DELETE and parts[3] == "confirm"


async def template_delete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    parts = query.data.split(":")
    if f"{CB_TEMPLATES}:{CB_DELETE}:" not in query.data:
        return
    # tmpl:del:confirm:<id> -> удалить (answer внутри template_delete_do)
    if len(parts) == 4 and parts[2] == "confirm":
        await template_delete_do(update, context)
        return
    await query.answer()
    # tmpl:del:<id> -> подтверждение
    if len(parts) == 3 and parts[0] == CB_TEMPLATES and parts[1] == CB_DELETE:
        t_id = int(parts[2])
        async with get_session() as session:
            result = await session.execute(select(WorkoutTemplate).where(WorkoutTemplate.id == t_id))
            tpl = result.scalar_one_or_none()
        if tpl:
            await query.edit_message_text(
                f"Точно удалить тренировку «{tpl.name}»?",
                reply_markup=template_delete_confirm_keyboard(t_id),
            )
        else:
            await query.edit_message_text("Тренировка не найдена.", reply_markup=back_to_main())
        return


async def template_create_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("Введите название тренировки (или /cancel для отмены):")
    return States.TEMPLATE_NAME.value


async def template_create_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        return States.TEMPLATE_NAME.value
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Введите непустое название.")
        return States.TEMPLATE_NAME.value
    context.user_data["template_name"] = name
    await update.message.reply_text("Введите описание (или «пропустить»):")
    return States.TEMPLATE_DESC.value


async def template_create_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return States.TEMPLATE_DESC.value
    desc = update.message.text.strip() if update.message.text else ""
    if desc.lower() in ("пропустить", "skip", "-"):
        desc = None
    name = context.user_data.get("template_name", "Без названия")
    if not update.effective_user:
        return ConversationHandler.END
    async with get_session() as session:
        user = await _get_user(session, update.effective_user.id, update.effective_user.username)
        tpl = WorkoutTemplate(user_id=user.id, name=name, description=desc)
        session.add(tpl)
        await session.flush()
        logger.info("template_create | user=%s template_id=%s name=%r", user.id, tpl.id, name)
    context.user_data.pop("template_name", None)
    context.user_data.pop("template_desc", None)
    await update.message.reply_text(
        f"✅ Тренировка «{name}» создана.\n"
        "Добавьте упражнения через «Редактировать» в списке тренировок.",
        reply_markup=back_to_main(),
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        await update.message.reply_text("Отменено.", reply_markup=back_to_main())
    context.user_data.pop("template_name", None)
    context.user_data.pop("template_desc", None)
    context.user_data.pop("template_new_exercise_id", None)
    context.user_data.pop("template_new_exercise_name", None)
    context.user_data.pop("template_new_exercise_body_idx", None)
    return ConversationHandler.END


def setup_template_handlers(application):
    application.add_handler(
        CallbackQueryHandler(templates_list, pattern=f"^{CB_TEMPLATES}:list"),
    )
    application.add_handler(
        CallbackQueryHandler(template_view, pattern=f"^{CB_TEMPLATES}:view:\\d+"),
    )
    application.add_handler(
        CallbackQueryHandler(template_add_exercise_show, pattern=f"^{CB_TEMPLATES}:add_ex:\\d+$"),
    )
    application.add_handler(
        CallbackQueryHandler(template_add_exercise_by_body, pattern=f"^{CB_TEMPLATES}:add_ex_body:\\d+:\\d+"),
    )
    application.add_handler(
        CallbackQueryHandler(template_add_exercise_do, pattern=f"^{CB_TEMPLATES}:add_ex_sel:\\d+:\\d+"),
    )
    application.add_handler(
        CallbackQueryHandler(template_delete_handler, pattern=f"^{CB_TEMPLATES}:{CB_DELETE}:"),
    )
    conv_new_ex = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(template_add_exercise_new_start, pattern=f"^{CB_TEMPLATES}:add_ex_new:\\d+$"),
            CallbackQueryHandler(template_add_exercise_new_body_prefilled_start, pattern=f"^{CB_TEMPLATES}:add_ex_new_body:\\d+:\\d+"),
        ],
        states={
            States.TEMPLATE_NEW_EXERCISE_NAME.value: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, template_add_exercise_new_name),
            ],
            States.TEMPLATE_NEW_EXERCISE_PICK_OR_CREATE.value: [
                CallbackQueryHandler(template_add_exercise_pick_or_create, pattern=f"^{CB_TEMPLATES}:(use_ex|create_anyway):"),
                CallbackQueryHandler(_template_pick_cancel_to_view, pattern=f"^{CB_TEMPLATES}:view:\\d+"),
            ],
            States.TEMPLATE_NEW_EXERCISE_BODY_PART.value: [
                CallbackQueryHandler(template_add_exercise_new_body, pattern=f"^{CB_TEMPLATES}:ex_body:\\d+:\\d+$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(conv_new_ex)
    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(template_create_start, pattern=f"^{CB_TEMPLATES}:create$"),
        ],
        states={
            States.TEMPLATE_NAME.value: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, template_create_name),
            ],
            States.TEMPLATE_DESC.value: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, template_create_desc),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(conv)
