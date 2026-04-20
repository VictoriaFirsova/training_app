"""Автозавершение тренировок без нажатия «Завершить» (висящие сессии с ended_at IS NULL)."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, select
from telegram.ext import Application

from config import WORKOUT_AUTO_END_HOURS, WORKOUT_AUTO_END_INTERVAL_SEC
from db.database import get_session
from db.models import ExerciseLog, WorkoutSession

logger = logging.getLogger(__name__)


async def close_stale_workout_sessions() -> int:
    """
    Старые незавершённые сессии (старше WORKOUT_AUTO_END_HOURS):
    без записей — удаляет; с записями — ставит ended_at.
    """
    hours = WORKOUT_AUTO_END_HOURS
    if hours <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    has_logs = exists(
        select(ExerciseLog.id).where(ExerciseLog.session_id == WorkoutSession.id)
    )
    async with get_session() as session:
        empty_result = await session.execute(
            select(WorkoutSession).where(
                WorkoutSession.ended_at.is_(None),
                WorkoutSession.started_at < cutoff,
                ~has_logs,
            )
        )
        empty_rows = list(empty_result.scalars().all())
        for w in empty_rows:
            await session.delete(w)
        n_empty = len(empty_rows)

        now = datetime.now(timezone.utc)
        with_logs_result = await session.execute(
            select(WorkoutSession).where(
                WorkoutSession.ended_at.is_(None),
                WorkoutSession.started_at < cutoff,
                has_logs,
            )
        )
        with_logs = list(with_logs_result.scalars().all())
        for w in with_logs:
            w.ended_at = now
        return n_empty + len(with_logs)


async def stale_workout_background_loop(_application: Application) -> None:
    """Периодическая проверка; задача отменяется при остановке Application."""
    interval = max(60, WORKOUT_AUTO_END_INTERVAL_SEC)
    await asyncio.sleep(30)
    while True:
        try:
            n = await close_stale_workout_sessions()
            if n:
                logger.info("Автозавершение тренировок: обработано сессий (пустые удалены / непустые закрыты): %s", n)
        except Exception:
            logger.exception("Ошибка автозавершения тренировок")
        await asyncio.sleep(interval)
