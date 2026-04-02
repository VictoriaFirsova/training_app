"""Одноразовая рассылка пользователям после нового деплоя на Railway."""

import asyncio
import logging
import os

from sqlalchemy import select
from telegram.error import Forbidden, TelegramError
from telegram.ext import Application

from config import DEPLOY_NOTIFY_TEXT
from db.database import get_session
from db.models import AppMeta, User

logger = logging.getLogger(__name__)

_APP_META_DEPLOY_KEY = "last_deploy_broadcast_id"


def _current_deployment_id() -> str | None:
    return (os.getenv("RAILWAY_DEPLOYMENT_ID") or os.getenv("RAILWAY_GIT_COMMIT_SHA") or "").strip() or None


def _deploy_notify_enabled() -> bool:
    v = os.getenv("DEPLOY_NOTIFY", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


async def maybe_broadcast_deploy_notice(application: Application) -> None:
    if not _deploy_notify_enabled():
        logger.info("Рассылка после деплоя отключена (DEPLOY_NOTIFY=0).")
        return
    deployment_id = _current_deployment_id()
    if not deployment_id:
        logger.debug("Нет RAILWAY_DEPLOYMENT_ID / RAILWAY_GIT_COMMIT_SHA — рассылка пропущена (локальный запуск).")
        return

    async with get_session() as session:
        result = await session.execute(select(AppMeta).where(AppMeta.key == _APP_META_DEPLOY_KEY))
        row = result.scalar_one_or_none()
        if row and row.value == deployment_id:
            logger.info("Рассылка для деплоя %s уже была, пропуск.", deployment_id[:12])
            return
        u_result = await session.execute(select(User.telegram_id))
        telegram_ids = list(u_result.scalars().all())

    if not telegram_ids:
        logger.info("Нет пользователей в БД — рассылка не нужна.")
        async with get_session() as session:
            await _set_deploy_meta(session, deployment_id)
        return

    text = DEPLOY_NOTIFY_TEXT.strip() or "Бот обновлён. Нажмите /start, если что-то зависло."
    bot = application.bot
    ok, fail = 0, 0
    for tid in telegram_ids:
        try:
            await bot.send_message(chat_id=tid, text=text)
            ok += 1
        except Forbidden:
            fail += 1
            logger.debug("Рассылка: пользователь %s заблокировал бота", tid)
        except TelegramError as e:
            fail += 1
            logger.warning("Рассылка: не удалось отправить %s: %s", tid, e)
        await asyncio.sleep(0.04)

    async with get_session() as session:
        await _set_deploy_meta(session, deployment_id)

    logger.info(
        "Рассылка после деплоя %s: отправлено %s, ошибок/блок %s",
        deployment_id[:12],
        ok,
        fail,
    )


async def _set_deploy_meta(session, deployment_id: str) -> None:
    result = await session.execute(select(AppMeta).where(AppMeta.key == _APP_META_DEPLOY_KEY))
    row = result.scalar_one_or_none()
    if row:
        row.value = deployment_id
    else:
        session.add(AppMeta(key=_APP_META_DEPLOY_KEY, value=deployment_id))
