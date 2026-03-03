"""Скрипт инициализации БД. Запуск: python -m db.init_db"""

import asyncio

from db.database import init_database


async def main() -> None:
    await init_database()
    print("База данных создана.")


if __name__ == "__main__":
    asyncio.run(main())
