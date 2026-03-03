from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import DATABASE_URL
from db.models import Base

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
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Мягкая миграция SQLite: group_name → body_part (PostgreSQL использует create_all)
        if "sqlite" in str(engine.url):
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
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
