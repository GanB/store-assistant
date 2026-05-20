from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr
from pytest_mock import MockerFixture

from store_assistant.agent.tool_results import GetStorePhoneResult, SaveStoreResult
from store_assistant.agent.tools import (
    make_get_store_phone_tool,
    make_save_store_tool,
)
from store_assistant.config import Settings
from store_assistant.data.exceptions import StoreAlreadyExistsError
from store_assistant.data.repository import StoreRepository
from store_assistant.data.schemas import StoreRead

PASSPHRASE = "open-sesame"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        anthropic_api_key=SecretStr("test-key"),
        postgres_user="u",
        postgres_password=SecretStr("p"),
        postgres_db="d",
        app_passphrase=SecretStr(PASSPHRASE),
    )


@pytest.fixture
def mock_store_repo() -> AsyncMock:
    return AsyncMock(spec=StoreRepository)


def _store_read(name: str, phone: str) -> StoreRead:
    return StoreRead(
        id=uuid4(),
        name=name,
        phone_e164=phone,
        created_at=datetime.now(tz=UTC),
    )


class TestSaveStoreTool:
    async def test_save_store_with_valid_phone_persists(
        self, settings: Settings, mock_store_repo: AsyncMock
    ) -> None:
        tool = make_save_store_tool(settings, mock_store_repo)
        raw = await tool.ainvoke({"name": "Sunrise Grocers", "phone": "+19195550134"})
        result = SaveStoreResult.model_validate(raw)
        assert result.success is True
        assert result.normalized_phone == "+19195550134"
        mock_store_repo.save_store.assert_called_once_with(
            "Sunrise Grocers", "+19195550134", thread_id=None
        )

    async def test_save_store_with_invalid_phone_does_not_call_repo(
        self, settings: Settings, mock_store_repo: AsyncMock
    ) -> None:
        tool = make_save_store_tool(settings, mock_store_repo)
        raw = await tool.ainvoke({"name": "Bad Phone Store", "phone": "abc-def-ghij"})
        result = SaveStoreResult.model_validate(raw)
        assert result.success is False
        assert "Phone number invalid" in result.message
        mock_store_repo.save_store.assert_not_called()

    async def test_save_store_handles_duplicate(
        self, settings: Settings, mock_store_repo: AsyncMock
    ) -> None:
        mock_store_repo.save_store.side_effect = StoreAlreadyExistsError("Dup Store")
        tool = make_save_store_tool(settings, mock_store_repo)
        raw = await tool.ainvoke({"name": "Dup Store", "phone": "+19195550134"})
        result = SaveStoreResult.model_validate(raw)
        assert result.success is False
        assert "already exists" in result.message
        assert result.normalized_phone is None

    async def test_save_store_normalizes_phone(
        self, settings: Settings, mock_store_repo: AsyncMock
    ) -> None:
        tool = make_save_store_tool(settings, mock_store_repo)
        await tool.ainvoke({"name": "Format Test", "phone": "919-555-0134"})
        mock_store_repo.save_store.assert_called_once_with(
            "Format Test", "+19195550134", thread_id=None
        )


class TestGetStorePhoneTool:
    async def test_get_store_phone_with_correct_passphrase_returns_phone(
        self, settings: Settings, mock_store_repo: AsyncMock
    ) -> None:
        mock_store_repo.get_store_by_name.return_value = _store_read(
            "Sunrise Grocers", "+19195550134"
        )
        tool = make_get_store_phone_tool(settings, mock_store_repo)
        raw = await tool.ainvoke({"name": "Sunrise Grocers", "passphrase": PASSPHRASE})
        result = GetStorePhoneResult.model_validate(raw)
        assert result.success is True
        assert result.phone_e164 == "+19195550134"
        assert result.reason is None
        mock_store_repo.get_store_by_name.assert_called_once_with("Sunrise Grocers")

    async def test_get_store_phone_with_wrong_passphrase_does_not_call_repo(
        self, settings: Settings, mock_store_repo: AsyncMock
    ) -> None:
        tool = make_get_store_phone_tool(settings, mock_store_repo)
        raw = await tool.ainvoke({"name": "Sunrise Grocers", "passphrase": "wrong"})
        result = GetStorePhoneResult.model_validate(raw)
        assert result.success is False
        assert result.reason == "auth_failed"
        assert result.phone_e164 is None
        mock_store_repo.get_store_by_name.assert_not_called()

    async def test_get_store_phone_returns_not_found(
        self, settings: Settings, mock_store_repo: AsyncMock
    ) -> None:
        mock_store_repo.get_store_by_name.return_value = None
        tool = make_get_store_phone_tool(settings, mock_store_repo)
        raw = await tool.ainvoke({"name": "Missing Store", "passphrase": PASSPHRASE})
        result = GetStorePhoneResult.model_validate(raw)
        assert result.success is False
        assert result.reason == "not_found"
        assert result.phone_e164 is None

    async def test_passphrase_comparison_uses_constant_time(
        self,
        settings: Settings,
        mock_store_repo: AsyncMock,
        mocker: MockerFixture,
    ) -> None:
        spy = mocker.patch(
            "store_assistant.agent.tools.hmac.compare_digest",
            return_value=True,
        )
        mock_store_repo.get_store_by_name.return_value = _store_read(
            "Any Store", "+19195550134"
        )
        tool = make_get_store_phone_tool(settings, mock_store_repo)
        await tool.ainvoke({"name": "Any Store", "passphrase": "anything"})
        spy.assert_called_once()
