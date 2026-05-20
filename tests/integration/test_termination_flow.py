from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from store_assistant.agent.graph import build_graph
from store_assistant.config import Settings
from store_assistant.data.models import ConversationSummary
from store_assistant.data.repository import StoreRepository, SummaryRepository
from tests.conftest import (
    TEST_PASSPHRASE,
    GraphFixture,
    MockChatModel,
    make_text_response,
)


class TestTerminationFlow:
    async def test_explicit_done_utterance_terminates_with_summary(
        self, graph_fixture: GraphFixture
    ) -> None:
        graph_fixture.llm.queue(
            make_text_response("User completed the conversation without saving anything."),
        )

        result = await graph_fixture.graph.ainvoke(
            {"messages": [HumanMessage(content="I'm done, thanks")]},
            config=graph_fixture.config,
        )

        assert result.get("terminated") is True
        assert result.get("summary") is not None
        assert "completed" in str(result["summary"]).lower()

    async def test_three_off_scope_messages_terminate_with_summary(
        self, graph_fixture: GraphFixture
    ) -> None:
        graph_fixture.llm.queue(
            make_text_response(
                "I can only help with saving and retrieving store records."
            ),
            make_text_response("Still off-topic; please stay on the store records task."),
            make_text_response(
                "User asked off-topic questions repeatedly; conversation ended."
            ),
        )

        first = await graph_fixture.graph.ainvoke(
            {"messages": [HumanMessage(content="What's the weather today?")]},
            config=graph_fixture.config,
        )
        assert first.get("terminated") is not True
        assert first.get("off_scope_count") == 1

        second = await graph_fixture.graph.ainvoke(
            {"messages": [HumanMessage(content="Tell me a joke please")]},
            config=graph_fixture.config,
        )
        assert second.get("terminated") is not True
        assert second.get("off_scope_count") == 2

        third = await graph_fixture.graph.ainvoke(
            {"messages": [HumanMessage(content="Sing me a song")]},
            config=graph_fixture.config,
        )
        assert third.get("terminated") is True
        assert third.get("summary") is not None

    async def test_summary_persisted_to_db_on_termination(
        self,
        graph_fixture: GraphFixture,
        async_session: AsyncSession,
    ) -> None:
        graph_fixture.llm.queue(
            make_text_response("Conversation ended without any saved records."),
        )

        await graph_fixture.graph.ainvoke(
            {"messages": [HumanMessage(content="Quit")]},
            config=graph_fixture.config,
        )

        rows = (await async_session.execute(select(ConversationSummary))).scalars().all()
        assert len(rows) == 1
        assert "ended" in rows[0].summary_text.lower()

    @pytest.mark.asyncio
    async def test_max_iterations_safeguard_terminates(
        self,
        async_session: AsyncSession,
        mock_llm: MockChatModel,
    ) -> None:
        settings = Settings(
            anthropic_api_key=SecretStr("test"),
            postgres_user="u",
            postgres_password=SecretStr("p"),
            postgres_db="d",
            app_passphrase=SecretStr(TEST_PASSPHRASE),
            app_max_graph_iterations=1,
        )
        store_repo = StoreRepository(async_session)
        summary_repo = SummaryRepository(async_session)
        graph = build_graph(settings, store_repo, summary_repo, llm=mock_llm)

        mock_llm.queue(
            make_text_response("Hi there."),
            make_text_response("Conversation reached the iteration safeguard."),
        )

        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="hello there")]},
            config={"configurable": {"thread_id": "max-iter-test"}},
        )

        assert result.get("terminated") is True
        assert result.get("summary") is not None
