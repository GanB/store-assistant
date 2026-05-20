from __future__ import annotations

from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from store_assistant.data.models import Base
from store_assistant.data.repository import StoreRepository

PHONE = "+12015550000"


@pytest_asyncio.fixture
async def file_db_path(tmp_path: Path) -> Path:
    return tmp_path / "persistence.sqlite"


async def _open_engine(db_path: Path) -> AsyncEngine:
    return create_async_engine(f"sqlite+aiosqlite:///{db_path}")


async def _open_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


class TestPersistence:
    async def test_store_survives_engine_dispose_and_reopen(
        self,
        file_db_path: Path,
    ) -> None:
        engine = await _open_engine(file_db_path)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = await _open_factory(engine)
        async with factory() as session:
            await StoreRepository(session).save_store("Restart Test", PHONE)
        await engine.dispose()

        engine_2 = await _open_engine(file_db_path)
        factory_2 = await _open_factory(engine_2)
        try:
            async with factory_2() as session:
                found = await StoreRepository(session).get_store_by_name(
                    "Restart Test"
                )
                assert found is not None
                assert found.phone_e164 == PHONE
        finally:
            await engine_2.dispose()
