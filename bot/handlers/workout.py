import logging
import tempfile
from datetime import datetime
from pathlib import Path

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

from bot.handlers.common import get_or_create_user
from bot.keyboards import (
    CB_WORKOUT,
    CB_DONE,
    main_menu,
    workout_in_progress_keyboard,
    workout_voice_review_keyboard,
    workout_template_select_keyboard,
)
from bot.states import States
from db.database import get_session
from db.models import Exercise, ExerciseLog, TemplateExercise, User, WorkoutSession, WorkoutTemplate
from services.parser import parse_exercise_line
from services.speech import transcribe_audio

logger = logging.getLogger(__name__)


async def _get_user(session, telegram_id: int, username: str | None) -> User:
    return await get_or_create_user(session, telegram_id, username)


async def _find_or_create_exercise(
    session, user_id: int, name: str, template_exercise_ids: list[int] | None = None
) -> Exercise:
    """Ищет упражнение по имени или создаёт новое. Упражнения существуют отдельно."""
    if template_exercise_ids:
        result = await session.execute(
            select(Exercise).where(
                Exercise.id.in_(template_exercise_ids),
                Exercise.name == name,
            )
        )
        ex = result.scalars().first()
        if ex:
            return ex
        ex = Exercise(user_id=user_id, name=name, body_part="Другое")
    else:
        result = await session.execute(
            select(Exercise).where(Exercise.user_id == user_id, Exercise.name == name)
        )
        ex = result.scalars().first()
        if ex:
            return ex
        ex = Exercise(user_id=user_id, name=name, body_part="Свободная")
    session.add(ex)
    await session.flush()
    return ex


async def workout_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data or ""
    parts = data.split(":")
    if len(parts) < 2:
        return
    if not update.effective_user:
        return

    # wo:start:template_id (из карточки тренировки) или wo:use:id / wo:free
    if parts[1] == "start":
        template_id = int(parts[2]) if len(parts) > 2 else None
    elif parts[1] == "use":
        template_id = int(parts[2])
    elif parts[1] == "free":
        template_id = None
    else:
        return

    async with get_session() as session:
        user = await _get_user(session, update.effective_user.id, update.effective_user.username)
        session_obj = WorkoutSession(user_id=user.id, template_id=template_id)
        session.add(session_obj)
        await session.flush()
        session_id = session_obj.id

    context.user_data["workout_session_id"] = session_id
    context.user_data["workout_template_id"] = template_id
    await query.edit_message_text(
        "Тренировка начата.\n\n"
        "Вводите упражнения в формате:\n"
        "• жим 4×8×80\n"
        "• присед 3 подхода по 100 кг\n"
        "• становая 5×5×120\n\n"
        "Каждое упражнение — с новой строки или отдельным сообщением.\n"
        "Когда закончите — нажмите «Завершить тренировку».",
        reply_markup=workout_in_progress_keyboard(session_id),
    )
    return States.WORKOUT_INPUT.value


async def workout_start_show_templates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать выбор: свободная или по шаблону."""
    query = update.callback_query
    if not query or query.data != f"{CB_WORKOUT}:start":
        return
    await query.answer()
    if not update.effective_user:
        return
    async with get_session() as session:
        user = await _get_user(session, update.effective_user.id, update.effective_user.username)
        result = await session.execute(
            select(WorkoutTemplate).where(WorkoutTemplate.user_id == user.id).order_by(WorkoutTemplate.name)
        )
        templates = list(result.scalars().all())
    await query.edit_message_text(
        "Выберите тренировку или начните свободную:",
        reply_markup=workout_template_select_keyboard(templates),
    )


async def workout_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        return States.WORKOUT_INPUT.value
    session_id = context.user_data.get("workout_session_id")
    if not session_id:
        await update.message.reply_text("Сессия тренировки не найдена. Начните заново.", reply_markup=main_menu())
        return ConversationHandler.END

    text = update.message.text.strip()
    if context.user_data.get("awaiting_voice_correction"):
        if text.lower() in {"отмена", "cancel"}:
            _clear_pending_voice(context)
            await update.message.reply_text("Исправление отменено.")
            return States.WORKOUT_INPUT.value
        saved = await _save_parsed_input(update=update, text=text, session_id=session_id)
        if not saved:
            await update.message.reply_text(
                "Не удалось разобрать исправленный текст.\n"
                "Введите еще раз или напишите «отмена».",
            )
            return States.WORKOUT_INPUT.value
        _clear_pending_voice(context)
        await update.message.reply_text(f"✅ {saved}")
        return States.WORKOUT_INPUT.value

    saved = await _save_parsed_input(
        update=update,
        text=text,
        session_id=session_id,
    )
    if not saved:
        await update.message.reply_text(
            "Не удалось распознать. Примеры:\nжим 4×8×80\nжим 5 по 15\nприсед 3 по 10 с 100 кг",
        )
        return States.WORKOUT_INPUT.value

    await update.message.reply_text(f"✅ {saved}")
    return States.WORKOUT_INPUT.value


async def workout_voice_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.voice:
        return States.WORKOUT_INPUT.value

    session_id = context.user_data.get("workout_session_id")
    if not session_id:
        await update.message.reply_text("Сессия тренировки не найдена. Начните заново.", reply_markup=main_menu())
        return ConversationHandler.END

    voice_file = await update.message.voice.get_file()
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temp_file:
        temp_path = Path(temp_file.name)

    try:
        await voice_file.download_to_drive(custom_path=str(temp_path))
        recognized_text = transcribe_audio(str(temp_path))
    except Exception as exc:
        logger.exception("Ошибка распознавания голосового сообщения: %s", exc)
        await update.message.reply_text(
            "Не удалось распознать голос. Попробуйте еще раз или введите текстом.",
        )
        return States.WORKOUT_INPUT.value
    finally:
        temp_path.unlink(missing_ok=True)

    if not recognized_text:
        await update.message.reply_text("Голос распознан пусто. Попробуйте еще раз.")
        return States.WORKOUT_INPUT.value

    context.user_data["pending_voice_text"] = recognized_text
    context.user_data["awaiting_voice_correction"] = False
    await update.message.reply_text(
        "🎤 Распознано:\n"
        f"{recognized_text}\n\n"
        "Проверьте текст перед сохранением:",
        reply_markup=workout_voice_review_keyboard(),
    )
    return States.WORKOUT_INPUT.value


async def workout_voice_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query:
        return States.WORKOUT_INPUT.value
    await query.answer()
    recognized_text = context.user_data.get("pending_voice_text")
    session_id = context.user_data.get("workout_session_id")
    if not recognized_text or not session_id:
        await query.edit_message_text("Нет распознанного текста для сохранения.")
        _clear_pending_voice(context)
        return States.WORKOUT_INPUT.value

    saved = await _save_parsed_input(update=update, text=recognized_text, session_id=session_id)
    if not saved:
        await query.edit_message_text(
            "Не удалось разобрать распознанный текст.\n"
            "Нажмите «Исправить» и введите руками.",
            reply_markup=workout_voice_review_keyboard(),
        )
        return States.WORKOUT_INPUT.value

    await query.edit_message_text(f"🎤 Распознано: {recognized_text}\n✅ {saved}")
    _clear_pending_voice(context)
    return States.WORKOUT_INPUT.value


async def workout_voice_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query:
        return States.WORKOUT_INPUT.value
    await query.answer()
    recognized_text = context.user_data.get("pending_voice_text")
    if not recognized_text:
        await query.edit_message_text("Нет текста для исправления.")
        return States.WORKOUT_INPUT.value
    context.user_data["awaiting_voice_correction"] = True
    await query.edit_message_text(
        "Введите исправленный текст вручную.\n"
        "Для отмены напишите «отмена».",
    )
    # Отправляем отдельным сообщением, чтобы удобнее было скопировать и поправить 1-2 символа.
    if query.message:
        await query.message.reply_text("Скопируйте строку ниже и исправьте:")
        await query.message.reply_text(
            recognized_text,
        )
    return States.WORKOUT_INPUT.value


async def workout_voice_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query:
        return States.WORKOUT_INPUT.value
    await query.answer()
    _clear_pending_voice(context)
    await query.edit_message_text("Распознанный голос отменен и не сохранен.")
    return States.WORKOUT_INPUT.value


def _clear_pending_voice(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("pending_voice_text", None)
    context.user_data.pop("awaiting_voice_correction", None)


async def _save_parsed_input(update: Update, text: str, session_id: int) -> str | None:
    parsed = parse_exercise_line(text)
    if not parsed:
        return None
    if not update.effective_user:
        return None

    async with get_session() as session:
        result = await session.execute(
            select(WorkoutSession)
            .options(selectinload(WorkoutSession.template).selectinload(WorkoutTemplate.template_exercises))
            .where(WorkoutSession.id == session_id)
        )
        wrk_session = result.scalar_one_or_none()
        if not wrk_session:
            return None
        template_exercise_ids = None
        if wrk_session.template and wrk_session.template.template_exercises:
            template_exercise_ids = [te.exercise_id for te in wrk_session.template.template_exercises]
        user = await _get_user(session, update.effective_user.id, update.effective_user.username)
        ex = await _find_or_create_exercise(session, user.id, parsed.name, template_exercise_ids)
        log = ExerciseLog(
            session_id=session_id,
            exercise_id=ex.id,
            sets=parsed.sets,
            reps=parsed.reps,
            weight_kg=parsed.weight_kg,
        )
        session.add(log)
        body_part = ex.body_part

    reps_str = f"×{parsed.reps}" if parsed.reps else ""
    return f"{parsed.name} ({body_part}): {parsed.sets}{reps_str}×{parsed.weight_kg} кг"


async def workout_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.data:
        return ConversationHandler.END
    await query.answer()
    parts = query.data.split(":")
    if len(parts) < 3 or parts[1] != CB_DONE:
        return ConversationHandler.END
    session_id = int(parts[2])

    async with get_session() as session:
        result = await session.execute(
            select(WorkoutSession)
            .options(selectinload(WorkoutSession.exercise_logs).selectinload(ExerciseLog.exercise))
            .where(WorkoutSession.id == session_id)
        )
        wrk = result.scalar_one_or_none()
        if wrk:
            wrk.ended_at = datetime.utcnow()
            lines = ["Тренировка завершена!\n", "Записи за тренировку:"]
            for idx, log in enumerate(wrk.exercise_logs, start=1):
                r = f"×{log.reps}" if log.reps else ""
                lines.append(f"{idx}. {log.exercise.name}: {log.sets}{r}×{log.weight_kg} кг")
            await query.edit_message_text("\n".join(lines), reply_markup=main_menu())
        else:
            await query.edit_message_text("Сессия не найдена.", reply_markup=main_menu())

    context.user_data.pop("workout_session_id", None)
    context.user_data.pop("workout_template_id", None)
    return ConversationHandler.END


async def workout_undo_last(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.data:
        return States.WORKOUT_INPUT.value
    await query.answer()
    parts = query.data.split(":")
    if len(parts) < 3 or parts[1] != "undo":
        return States.WORKOUT_INPUT.value
    session_id = int(parts[2])
    if not update.effective_user:
        return States.WORKOUT_INPUT.value

    async with get_session() as session:
        # Проверяем, что сессия принадлежит текущему пользователю.
        user = await _get_user(session, update.effective_user.id, update.effective_user.username)
        wrk_result = await session.execute(
            select(WorkoutSession).where(
                WorkoutSession.id == session_id,
                WorkoutSession.user_id == user.id,
            )
        )
        wrk_session = wrk_result.scalar_one_or_none()
        if not wrk_session:
            if query.message:
                await query.message.reply_text("Сессия не найдена.")
            return States.WORKOUT_INPUT.value

        log_result = await session.execute(
            select(ExerciseLog)
            .where(ExerciseLog.session_id == session_id)
            .order_by(ExerciseLog.id.desc())
            .limit(1)
        )
        last_log = log_result.scalar_one_or_none()
        if not last_log:
            if query.message:
                await query.message.reply_text("Нечего отменять: записей пока нет.")
            return States.WORKOUT_INPUT.value

        ex_result = await session.execute(select(Exercise).where(Exercise.id == last_log.exercise_id))
        ex = ex_result.scalar_one_or_none()
        ex_name = ex.name if ex else f"ID {last_log.exercise_id}"
        reps_str = f"×{last_log.reps}" if last_log.reps else ""
        await session.delete(last_log)

    if query.message:
        await query.message.reply_text(
            f"↩ Удалена последняя запись: {ex_name}: {last_log.sets}{reps_str}×{last_log.weight_kg} кг"
        )
    return States.WORKOUT_INPUT.value


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        await update.message.reply_text("Тренировка отменена.", reply_markup=main_menu())
    context.user_data.pop("workout_session_id", None)
    context.user_data.pop("workout_template_id", None)
    _clear_pending_voice(context)
    return ConversationHandler.END


def setup_workout_handlers(application):
    application.add_handler(
        CallbackQueryHandler(workout_start_show_templates, pattern=f"^{CB_WORKOUT}:start$"),
    )
    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(workout_start, pattern=rf"^{CB_WORKOUT}:(use|free|start)(:\d+)?$"),
        ],
        states={
            States.WORKOUT_INPUT.value: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, workout_input),
                MessageHandler(filters.VOICE, workout_voice_input),
                CallbackQueryHandler(workout_done, pattern=f"^{CB_WORKOUT}:{CB_DONE}:\\d+"),
                CallbackQueryHandler(workout_undo_last, pattern=f"^{CB_WORKOUT}:undo:\\d+$"),
                CallbackQueryHandler(workout_voice_confirm, pattern=f"^{CB_WORKOUT}:voice_confirm$"),
                CallbackQueryHandler(workout_voice_edit, pattern=f"^{CB_WORKOUT}:voice_edit$"),
                CallbackQueryHandler(workout_voice_cancel, pattern=f"^{CB_WORKOUT}:voice_cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
    )
    application.add_handler(conv)
