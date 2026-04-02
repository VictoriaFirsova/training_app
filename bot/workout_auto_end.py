"""Автозавершение тренировок без нажатия «Завершить» (висящие сессии с ended_at IS NULL)."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from telegram.ext import Application

from config import WORKOUT_AUTO_END_HOURS, WORKOUT_AUTO_END_INTERVAL_SEC
from db.database import get_session
from db.models import WorkoutSession

logger = logging.getLogger(__name__)


async def close_stale_workout_sessions() -> int:
    """Ставит ended_at = сейчас для сессий, где прошло больше WORKOUT_AUTO_END_HOURS с started_at."""
    hours = WORKOUT_AUTO_END_HOURS
    if hours <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    async with get_session() as session:
        result = await session.execute(
            select(WorkoutSession).where(
                WorkoutSession.ended_at.is_(None),
                WorkoutSession.started_at < cutoff,
            )
        )
        rows = list(result.scalars().all())
        now = datetime.now(timezone.utc)
        for w in rows:
            w.ended_at = now
        return len(rows)


async def stale_workout_background_loop(_application: Application) -> None:
    """Периодическая проверка; задача отменяется при остановке Application."""
    interval = max(60, WORKOUT_AUTO_END_INTERVAL_SEC)
    await asyncio.sleep(30)
    while True:
        try:
            n = await close_stale_workout_sessions()
            if n:
                logger.info("Автозавершение тренировок: закрыто сессий: %s", n)
        except Exception:
            logger.exception("Ошибка автозавершения тренировок")
        await asyncio.sleep(interval)
