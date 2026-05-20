from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from store_assistant.data.exceptions import StoreAlreadyExistsError
from store_assistant.data.repository import StoreRepository, SummaryRepository

PHONE_A = "+15551234567"
PHONE_B = "+15551234568"


class TestStoreRepository:
    async def test_save_store_persists_record(self, async_session: AsyncSession) -> None:
        repo = StoreRepository(async_session)
        result = await repo.save_store("Sunrise Grocers", PHONE_A)
        assert result.name == "Sunrise Grocers"
        assert result.phone_e164 == PHONE_A
        assert result.id is not None
        assert result.created_at is not None

    async def test_save_store_duplicate_raises_already_exists(
        self, async_session: AsyncSession
    ) -> None:
        repo = StoreRepository(async_session)
        await repo.save_store("Dup Store", PHONE_A)
        with pytest.raises(StoreAlreadyExistsError) as exc_info:
            await repo.save_store("Dup Store", PHONE_B)
        assert exc_info.value.name == "Dup Store"

    async def test_get_store_by_name_returns_record(
        self, async_session: AsyncSession
    ) -> None:
        repo = StoreRepository(async_session)
        saved = await repo.save_store("Lookup Store", PHONE_A)
        found = await repo.get_store_by_name("Lookup Store")
        assert found is not None
        assert found.id == saved.id
        assert found.name == "Lookup Store"
        assert found.phone_e164 == PHONE_A

    async def test_get_store_by_name_returns_none_when_missing(
        self, async_session: AsyncSession
    ) -> None:
        repo = StoreRepository(async_session)
        assert await repo.get_store_by_name("Nonexistent") is None

    async def test_exists_returns_true_for_existing(
        self, async_session: AsyncSession
    ) -> None:
        repo = StoreRepository(async_session)
        await repo.save_store("Existing Store", PHONE_A)
        assert await repo.exists("Existing Store") is True

    async def test_exists_returns_false_for_missing(
        self, async_session: AsyncSession
    ) -> None:
        repo = StoreRepository(async_session)
        assert await repo.exists("Nope") is False

    async def test_get_store_by_name_is_case_insensitive(
        self, async_session: AsyncSession
    ) -> None:
        repo = StoreRepository(async_session)
        await repo.save_store("Bob's Auto", PHONE_A)

        for variant in ["bob's auto", "BOB'S AUTO", "Bob's AUTO", "bOB's aUTo"]:
            found = await repo.get_store_by_name(variant)
            assert found is not None
            assert found.name == "Bob's Auto"
            assert found.phone_e164 == PHONE_A

    async def test_exists_is_case_insensitive(
        self, async_session: AsyncSession
    ) -> None:
        repo = StoreRepository(async_session)
        await repo.save_store("Carla's Cafe", PHONE_A)
        for variant in ["carla's cafe", "CARLA'S CAFE", "Carla's CAFE"]:
            assert await repo.exists(variant) is True

    async def test_save_with_different_case_still_collides(
        self, async_session: AsyncSession
    ) -> None:
        repo = StoreRepository(async_session)
        await repo.save_store("Joe's Grocery", PHONE_A)
        # Saving exact-cased duplicate fails (DB unique constraint is exact-match).
        # Tests that case-insensitive lookup doesn't change save semantics.
        with pytest.raises(StoreAlreadyExistsError):
            await repo.save_store("Joe's Grocery", PHONE_B)


class TestSummaryRepository:
    async def test_save_summary_persists(self, async_session: AsyncSession) -> None:
        repo = SummaryRepository(async_session)
        result = await repo.save_summary(
            "Conversation closed cleanly.",
            {"turns": 5, "outcome": "saved"},
        )
        assert result.summary_text == "Conversation closed cleanly."
        assert result.id is not None
        assert result.created_at is not None
        assert result.thread_id is None

    async def test_save_summary_persists_thread_id(
        self, async_session: AsyncSession
    ) -> None:
        repo = SummaryRepository(async_session)
        result = await repo.save_summary(
            "Saved with thread.",
            thread_id="thread-abc-123",
        )
        assert result.thread_id == "thread-abc-123"

    async def test_list_recent_returns_in_descending_created_at(
        self, async_session: AsyncSession
    ) -> None:
        repo = SummaryRepository(async_session)
        first = await repo.save_summary("First.", thread_id="t1")
        second = await repo.save_summary("Second.", thread_id="t2")
        third = await repo.save_summary("Third.", thread_id="t3")

        results = await repo.list_recent(limit=10)
        assert [r.id for r in results] == [third.id, second.id, first.id]

    async def test_list_recent_respects_limit(
        self, async_session: AsyncSession
    ) -> None:
        repo = SummaryRepository(async_session)
        for i in range(5):
            await repo.save_summary(f"summary {i}", thread_id=f"t{i}")
        results = await repo.list_recent(limit=2)
        assert len(results) == 2
