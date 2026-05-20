# ruff: noqa: E402
"""Pytest configuration.

Loads `.env` (if present) into the process environment before importing any
application module. The order matters: pydantic-settings reads env vars at
`Settings()` construction time, pytest skip-conditions evaluate them at
collection time, and several tests read `os.environ` directly. Doing the
load below the first import block but above the rest is intentional —
hence the file-level `# ruff: noqa: E402` directive.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE, override=False)

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from store_assistant.agent.graph import build_graph
from store_assistant.config import Settings
from store_assistant.data.models import Base
from store_assistant.data.repository import StoreRepository, SummaryRepository

UNIT_DB_URL = "sqlite+aiosqlite:///:memory:"
TEST_PASSPHRASE = "open-sesame"


@pytest_asyncio.fixture
async def async_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        UNIT_DB_URL,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(
        bind=async_engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )
    async with factory() as session:
        yield session


@pytest.fixture
def settings() -> Settings:
    return Settings(
        anthropic_api_key=SecretStr("test-key"),
        postgres_user="u",
        postgres_password=SecretStr("p"),
        postgres_db="d",
        app_passphrase=SecretStr(TEST_PASSPHRASE),
        app_max_graph_iterations=20,
    )


class MockChatModel:
    def __init__(self) -> None:
        self.responses: list[AIMessage] = []
        self.calls: list[list[BaseMessage]] = []
        self._index: int = 0

    def queue(self, *responses: AIMessage) -> None:
        self.responses.extend(responses)

    @property
    def call_count(self) -> int:
        return len(self.calls)

    async def ainvoke(
        self,
        messages: list[BaseMessage],
        config: Any | None = None,
    ) -> AIMessage:
        del config
        self.calls.append(list(messages))
        if self._index >= len(self.responses):
            raise RuntimeError(
                f"MockChatModel exhausted: call #{self._index + 1} but only "
                f"{len(self.responses)} responses queued."
            )
        response = self.responses[self._index]
        self._index += 1
        return response

    def bind_tools(self, tools: list[BaseTool]) -> MockChatModel:
        del tools
        return self


def make_tool_call(name: str, args: dict[str, Any], call_id: str = "1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": name,
                "args": args,
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def make_save_call(name: str, phone: str, call_id: str = "1") -> AIMessage:
    return make_tool_call("save_store", {"name": name, "phone": phone}, call_id)


def make_retrieve_call(name: str, passphrase: str, call_id: str = "1") -> AIMessage:
    return make_tool_call(
        "get_store_phone",
        {"name": name, "passphrase": passphrase},
        call_id,
    )


def make_text_response(text: str) -> AIMessage:
    return AIMessage(content=text)


@pytest_asyncio.fixture
async def mock_llm() -> MockChatModel:
    return MockChatModel()


@dataclass
class GraphFixture:
    graph: Any
    llm: MockChatModel
    store_repo: StoreRepository
    summary_repo: SummaryRepository
    settings: Settings
    session: AsyncSession
    thread_id: str = field(default_factory=lambda: str(uuid4()))

    @property
    def config(self) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": self.thread_id}}


@pytest_asyncio.fixture
async def graph_fixture(
    async_session: AsyncSession,
    mock_llm: MockChatModel,
    settings: Settings,
) -> GraphFixture:
    store_repo = StoreRepository(async_session)
    summary_repo = SummaryRepository(async_session)
    graph = build_graph(settings, store_repo, summary_repo, llm=mock_llm)
    return GraphFixture(
        graph=graph,
        llm=mock_llm,
        store_repo=store_repo,
        summary_repo=summary_repo,
        settings=settings,
        session=async_session,
    )
