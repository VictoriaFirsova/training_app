import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.sql import func
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
from bot.messages import RESTART_MSG
from bot.keyboards import (
    BODY_PARTS,
    CB_WORKOUT,
    CB_DONE,
    main_menu,
    workout_in_progress_keyboard,
    workout_pick_exercise_keyboard,
    workout_confirm_create_exercise_keyboard,
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


async def _find_matching_exercises(
    session, user_id: int, name: str, template_exercise_ids: list[int] | None
) -> list[Exercise]:
    """Ищет упражнения по имени: точное совпадение, вхождение, по словам."""
    base_filter = Exercise.user_id == user_id
    if template_exercise_ids:
        base_filter = base_filter & (Exercise.id.in_(template_exercise_ids))
    # 1. Точное совпадение
    result = await session.execute(
        select(Exercise).where(base_filter, func.lower(Exercise.name) == name.lower())
    )
    exact = list(result.scalars().unique().all())
    if exact:
        return exact
    # 2. Вхождение: «жим» находит «жим лежа»
    if len(name) >= 2:
        result = await session.execute(
            select(Exercise).where(
                base_filter,
                func.lower(Exercise.name).like(f"%{name.lower()}%"),
            ).order_by(Exercise.name)
        )
        contains = list(result.scalars().unique().all())
        if contains:
            return contains
    # 3. По словам
    words = [w for w in name.split() if len(w) >= 2]
    if words:
        conditions = [func.lower(Exercise.name).like(f"%{w.lower()}%") for w in words]
        result = await session.execute(
            select(Exercise).where(base_filter, or_(*conditions)).order_by(Exercise.name)
        )
        return list(result.scalars().unique().all())
    return []


async def _find_or_create_exercise(
    session, user_id: int, name: str, template_exercise_ids: list[int] | None = None
) -> Exercise:
    """Ищет упражнение по имени или создаёт новое. Упражнения существуют отдельно."""
    matches = await _find_matching_exercises(session, user_id, name, template_exercise_ids)
    if matches:
        return matches[0]
    body = "Другое" if template_exercise_ids else "Свободная"
    ex = Exercise(user_id=user_id, name=name, body_part=body)
    session.add(ex)
    await session.flush()
    logger.info("_find_or_create_exercise | NEW user=%s ex_id=%s name=%r", user_id, ex.id, name)
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
    uid = update.effective_user.id if update.effective_user else 0
    logger.info("workout_start | user=%s session_id=%s template_id=%s", uid, session_id, template_id)
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
        await update.message.reply_text(RESTART_MSG, reply_markup=main_menu())
        return ConversationHandler.END

    text = update.message.text.strip()
    uid = update.effective_user.id if update.effective_user else 0
    logger.info("workout_input | user=%s session_id=%s text=%r", uid, session_id, text[:50])
    if context.user_data.get("awaiting_voice_correction"):
        if text.lower() in {"отмена", "cancel"}:
            _clear_pending_voice(context)
            await update.message.reply_text("Исправление отменено.")
            return States.WORKOUT_INPUT.value
        saved = await _save_parsed_input(update, context, text, session_id)
        if saved == SAVE_PENDING:
            _clear_pending_voice(context)
            return States.WORKOUT_INPUT.value
        if not saved:
            logger.warning("workout_input | user=%s parse_failed (voice_correction) text=%r", uid, text[:50])
            await update.message.reply_text(
                "Не удалось разобрать исправленный текст.\n"
                "Введите еще раз или напишите «отмена».",
            )
            return States.WORKOUT_INPUT.value
        _clear_pending_voice(context)
        logger.info("workout_input | user=%s saved (voice_correction) result=%s", uid, saved[:60] if saved else "")
        await update.message.reply_text(f"✅ {saved}")
        return States.WORKOUT_INPUT.value

    saved = await _save_parsed_input(update, context, text, session_id)
    if saved == SAVE_PENDING:
        logger.info("workout_input | user=%s SAVE_PENDING (pick/create)", uid)
        return States.WORKOUT_INPUT.value
    if not saved:
        logger.warning("workout_input | user=%s parse_failed text=%r", uid, text[:50])
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
        await update.message.reply_text(RESTART_MSG, reply_markup=main_menu())
        return ConversationHandler.END

    voice_file = await update.message.voice.get_file()
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temp_file:
        temp_path = Path(temp_file.name)

    try:
        await voice_file.download_to_drive(custom_path=str(temp_path))
        recognized_text = transcribe_audio(str(temp_path))
    except Exception as exc:
        logger.exception("workout_voice_input | Ошибка распознавания голоса: %s", exc)
        await update.message.reply_text(
            "Не удалось распознать голос. Попробуйте еще раз или введите текстом.",
        )
        return States.WORKOUT_INPUT.value
    finally:
        temp_path.unlink(missing_ok=True)

    if not recognized_text:
        logger.info("workout_voice_input | user=%s empty transcription", update.effective_user.id if update.effective_user else 0)
        await update.message.reply_text("Голос распознан пусто. Попробуйте еще раз.")
        return States.WORKOUT_INPUT.value

    uid = update.effective_user.id if update.effective_user else 0
    logger.info("workout_voice_input | user=%s recognized=%r", uid, recognized_text[:60])
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

    saved = await _save_parsed_input(update, context, recognized_text, session_id)
    if saved == SAVE_PENDING:
        _clear_pending_voice(context)
        return States.WORKOUT_INPUT.value
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
        reply_markup=None,
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


def _clear_pending_pick(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("pending_workout_pick", None)


async def _do_save_workout_log(
    session, session_id: int, exercise_id: int, sets: int, reps: int | None, weight_kg: float
) -> tuple[str, str]:
    """Сохраняет запись и возвращает (имя_упражнения, body_part)."""
    ex_result = await session.execute(select(Exercise).where(Exercise.id == exercise_id))
    ex = ex_result.scalar_one_or_none()
    log = ExerciseLog(
        session_id=session_id,
        exercise_id=exercise_id,
        sets=sets,
        reps=reps,
        weight_kg=weight_kg,
    )
    session.add(log)
    name = ex.name if ex else str(exercise_id)
    body = getattr(ex, "body_part", "—")
    logger.info("exercise_log | session_id=%s exercise_id=%s %s %s×%s", session_id, exercise_id, name, sets, weight_kg)
    return name, body


async def workout_pick_exercise(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Выбор существующего упражнения из списка совпадений."""
    query = update.callback_query
    if not query or not query.data:
        return States.WORKOUT_INPUT.value
    await query.answer()
    parts = query.data.split(":")
    if len(parts) < 4 or parts[1] != "pick_ex":
        return States.WORKOUT_INPUT.value
    session_id = int(parts[2])
    ex_id = int(parts[3])
    pending = context.user_data.pop("pending_workout_pick", None)
    if not pending:
        await query.edit_message_text(RESTART_MSG, reply_markup=main_menu())
        return States.WORKOUT_INPUT.value
    async with get_session() as session:
        name, body = await _do_save_workout_log(
            session, session_id, ex_id,
            pending["sets"], pending.get("reps"), pending["weight_kg"],
        )
    reps_str = f"×{pending.get('reps')}" if pending.get("reps") else ""
    await query.edit_message_text(
        f"✅ {name} ({body}): {pending['sets']}{reps_str}×{pending['weight_kg']} кг"
    )
    return States.WORKOUT_INPUT.value


async def workout_pick_create(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показать выбор части тела перед созданием нового упражнения (из списка совпадений)."""
    query = update.callback_query
    if not query or not query.data:
        return States.WORKOUT_INPUT.value
    await query.answer()
    parts = query.data.split(":")
    if len(parts) < 3 or parts[1] != "pick_new":
        return States.WORKOUT_INPUT.value
    session_id = int(parts[2])
    pending = context.user_data.get("pending_workout_pick")
    if not pending or not update.effective_user:
        await query.edit_message_text(RESTART_MSG, reply_markup=main_menu())
        return States.WORKOUT_INPUT.value
    name = pending["name"]
    await query.edit_message_text(
        f"Выберите часть тела для «{name}»:",
        reply_markup=workout_confirm_create_exercise_keyboard(session_id, name),
    )
    return States.WORKOUT_INPUT.value


async def workout_pick_create_body(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Создание нового упражнения с выбранной частью тела и сохранение записи."""
    query = update.callback_query
    if not query or not query.data:
        return States.WORKOUT_INPUT.value
    await query.answer()
    parts = query.data.split(":")
    if len(parts) < 4 or parts[1] != "pick_new_body":
        return States.WORKOUT_INPUT.value
    session_id = int(parts[2])
    try:
        body_idx = int(parts[3])
        body_part = BODY_PARTS[body_idx] if 0 <= body_idx < len(BODY_PARTS) else "Другое"
    except (ValueError, IndexError):
        body_part = "Другое"
    pending = context.user_data.pop("pending_workout_pick", None)
    if not pending or not update.effective_user:
        await query.edit_message_text(RESTART_MSG, reply_markup=main_menu())
        return States.WORKOUT_INPUT.value
    async with get_session() as session:
        result = await session.execute(
            select(WorkoutSession)
            .options(selectinload(WorkoutSession.template).selectinload(WorkoutTemplate.template_exercises))
            .where(WorkoutSession.id == session_id)
        )
        wrk = result.scalar_one_or_none()
        if not wrk:
            await query.edit_message_text(RESTART_MSG, reply_markup=main_menu())
            return States.WORKOUT_INPUT.value
        template_exercise_ids = None
        if wrk.template and wrk.template.template_exercises:
            template_exercise_ids = [te.exercise_id for te in wrk.template.template_exercises]
        user = await _get_user(session, update.effective_user.id, update.effective_user.username)
        ex = Exercise(user_id=user.id, name=pending["name"], body_part=body_part)
        session.add(ex)
        await session.flush()
        logger.info("workout_pick_create_body | user=%s ex_id=%s name=%r body=%s", user.id, ex.id, ex.name, body_part)
        name, body = await _do_save_workout_log(
            session, session_id, ex.id,
            pending["sets"], pending.get("reps"), pending["weight_kg"],
        )
    reps_str = f"×{pending.get('reps')}" if pending.get("reps") else ""
    await query.edit_message_text(
        f"✅ {name} ({body}) — создано и записано: {pending['sets']}{reps_str}×{pending['weight_kg']} кг"
    )
    return States.WORKOUT_INPUT.value


async def workout_pick_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена выбора упражнения."""
    query = update.callback_query
    if query:
        await query.answer()
        _clear_pending_pick(context)
        await query.edit_message_text("Отменено. Введите упражнение заново.")
    return States.WORKOUT_INPUT.value


SAVE_PENDING = "_PENDING_PICK"


async def _save_parsed_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, session_id: int
) -> str | None:
    parsed = parse_exercise_line(text)
    if not parsed:
        logger.debug("_save_parsed_input | parse_exercise_line failed text=%r", text[:50])
        return None
    if not update.effective_user or not update.effective_chat:
        logger.warning("_save_parsed_input | no user or chat")
        return None

    async with get_session() as session:
        result = await session.execute(
            select(WorkoutSession)
            .options(selectinload(WorkoutSession.template).selectinload(WorkoutTemplate.template_exercises))
            .where(WorkoutSession.id == session_id)
        )
        wrk_session = result.scalar_one_or_none()
        if not wrk_session:
            logger.warning("_save_parsed_input | session_id=%s not found", session_id)
            return None
        template_exercise_ids = None
        if wrk_session.template and wrk_session.template.template_exercises:
            template_exercise_ids = [te.exercise_id for te in wrk_session.template.template_exercises]
        user = await _get_user(session, update.effective_user.id, update.effective_user.username)
        # Нормализуем название: убираем пробелы и пунктуацию для поиска
        search_name = parsed.name.strip().rstrip(".,;:!? ")
        matches = await _find_matching_exercises(session, user.id, search_name, template_exercise_ids)

        # Если в шаблоне 0 совпадений — ищем по всем упражнениям пользователя
        if not matches and template_exercise_ids:
            matches = await _find_matching_exercises(session, user.id, search_name, None)

        uid = update.effective_user.id
        logger.info("_save_parsed_input | session_id=%s user=%s name=%r matches=%s", session_id, uid, parsed.name, len(matches))
        # Точное совпадение — сохраняем сразу
        if len(matches) == 1 and matches[0].name.lower() == search_name.lower():
            ex = matches[0]
            log = ExerciseLog(
                session_id=session_id,
                exercise_id=ex.id,
                sets=parsed.sets,
                reps=parsed.reps,
                weight_kg=parsed.weight_kg,
            )
            session.add(log)
            reps_str = f"×{parsed.reps}" if parsed.reps else ""
            return f"{ex.name} ({ex.body_part}): {parsed.sets}{reps_str}×{parsed.weight_kg} кг"

        # Частичное совпадение (1 или больше) — спрашиваем подтверждение
        if len(matches) >= 1:
            context.user_data["pending_workout_pick"] = {
                "session_id": session_id,
                "name": parsed.name,
                "sets": parsed.sets,
                "reps": parsed.reps,
                "weight_kg": parsed.weight_kg,
            }
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Найдено несколько упражнений. Выберите или создайте новое:",
                reply_markup=workout_pick_exercise_keyboard(session_id, matches, parsed.name),
            )
            return SAVE_PENDING

        # 0 совпадений — предлагаем создать
        context.user_data["pending_workout_pick"] = {
            "session_id": session_id,
            "name": parsed.name,
            "sets": parsed.sets,
            "reps": parsed.reps,
            "weight_kg": parsed.weight_kg,
        }
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"Упражнение «{parsed.name}» не найдено. Выберите часть тела для нового упражнения:",
            reply_markup=workout_confirm_create_exercise_keyboard(session_id, parsed.name),
        )
        return SAVE_PENDING


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
            wrk.ended_at = datetime.now(timezone.utc)
            n_logs = len(wrk.exercise_logs)
            logger.info("workout_done | session_id=%s logs_count=%s", session_id, n_logs)
            lines = ["Тренировка завершена!\n", "Записи за тренировку:"]
            for idx, log in enumerate(wrk.exercise_logs, start=1):
                r = f"×{log.reps}" if log.reps else ""
                lines.append(f"{idx}. {log.exercise.name}: {log.sets}{r}×{log.weight_kg} кг")
            await query.edit_message_text("\n".join(lines), reply_markup=main_menu())
        else:
            logger.warning("workout_done | session_id=%s not found", session_id)
            await query.edit_message_text(RESTART_MSG, reply_markup=main_menu())

    context.user_data.pop("workout_session_id", None)
    context.user_data.pop("workout_template_id", None)
    _clear_pending_voice(context)
    _clear_pending_pick(context)
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
                await query.message.reply_text(RESTART_MSG, reply_markup=main_menu())
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
    _clear_pending_pick(context)
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
                CallbackQueryHandler(workout_pick_exercise, pattern=f"^{CB_WORKOUT}:pick_ex:\\d+:\\d+$"),
                CallbackQueryHandler(workout_pick_create, pattern=f"^{CB_WORKOUT}:pick_new:\\d+$"),
                CallbackQueryHandler(workout_pick_create_body, pattern=f"^{CB_WORKOUT}:pick_new_body:\\d+:\\d+$"),
                CallbackQueryHandler(workout_pick_cancel, pattern=f"^{CB_WORKOUT}:pick_cancel:\\d+$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
    )
    application.add_handler(conv)
