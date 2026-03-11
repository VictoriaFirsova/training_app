import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import DATABASE_URL
from db.models import Base

logger = logging.getLogger(__name__)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def init_database() -> None:
    db_info = "postgres" if "postgresql" in str(engine.url) else "sqlite"
    logger.info("Инициализация БД: %s", db_info)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        logger.debug("Таблицы созданы/проверены")
        url_str = str(engine.url)
        if "postgresql" in url_str:
            # Миграция: TIMESTAMP → TIMESTAMP WITH TIME ZONE (Тбилиси UTC+4)
            for table, cols in [
                ("users", ["created_at"]),
                ("exercises", ["created_at"]),
                ("workout_templates", ["created_at"]),
                ("workout_sessions", ["started_at", "ended_at"]),
                ("exercise_logs", ["created_at"]),
            ]:
                for col in cols:
                    try:
                        await conn.exec_driver_sql(
                            f'ALTER TABLE {table} ALTER COLUMN {col} '
                            f"TYPE TIMESTAMP WITH TIME ZONE USING {col} AT TIME ZONE 'UTC'"
                        )
                    except Exception as e:
                        logger.debug("Миграция %s.%s: %s", table, col, e)
        elif "sqlite" in url_str:
            result = await conn.exec_driver_sql("PRAGMA table_info(exercises)")
            columns = [row[1] for row in result.fetchall()]
            if "body_part" not in columns:
                await conn.exec_driver_sql(
                    "ALTER TABLE exercises ADD COLUMN body_part TEXT NOT NULL DEFAULT 'Другое'"
                )
                if "group_name" in columns:
                    await conn.exec_driver_sql(
                        "UPDATE exercises SET body_part = group_name WHERE group_name IS NOT NULL AND group_name != ''"
                    )
            if "group_name" in columns:
                try:
                    await conn.exec_driver_sql("ALTER TABLE exercises DROP COLUMN group_name")
                except Exception:
                    pass


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
            logger.debug("DB commit OK")
        except Exception as e:
            await session.rollback()
            logger.warning("DB rollback из-за: %s", e, exc_info=True)
            raise
        finally:
            await session.close()
