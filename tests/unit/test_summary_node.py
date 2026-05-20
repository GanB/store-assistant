from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from sqlalchemy.ext.asyncio import AsyncSession

from store_assistant.agent.nodes import make_generate_summary_node
from store_assistant.agent.state import AgentState
from store_assistant.data.repository import SummaryRepository

THREAD_ID = "thread-test-123"


@pytest.fixture
def mock_summary_repo(async_session: AsyncSession) -> AsyncMock:
    real = SummaryRepository(async_session)
    mock = AsyncMock(spec=real)
    mock.save_summary.return_value = type(
        "Stub",
        (),
        {"id": uuid4(), "summary_text": "stub", "thread_id": THREAD_ID},
    )()
    return mock


def _config() -> RunnableConfig:
    return {"configurable": {"thread_id": THREAD_ID}}


class TestGenerateSummaryNode:
    async def test_calls_repo_with_llm_text_and_thread_id(
        self, mock_summary_repo: AsyncMock
    ) -> None:
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = AIMessage(
            content="User saved one store and ended the conversation."
        )
        node = make_generate_summary_node(mock_llm, mock_summary_repo)

        state: AgentState = AgentState(
            messages=[
                HumanMessage(content="Save Joe's, 919-555-1234"),
                AIMessage(content="Saved."),
            ],
            off_scope_count=0,
            terminated=True,
            summary=None,
            iteration_count=2,
        )

        result = await node(state, _config())

        mock_summary_repo.save_summary.assert_called_once()
        kwargs = mock_summary_repo.save_summary.call_args.kwargs
        assert kwargs["summary_text"] == "User saved one store and ended the conversation."
        assert kwargs["thread_id"] == THREAD_ID

        assert result["summary"] == "User saved one store and ended the conversation."
        assert len(result["messages"]) == 1
        assert isinstance(result["messages"][0], AIMessage)
        assert "Goodbye" in str(result["messages"][0].content)

    async def test_handles_empty_message_history(
        self, mock_summary_repo: AsyncMock
    ) -> None:
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = AIMessage(content="No conversation occurred.")
        node = make_generate_summary_node(mock_llm, mock_summary_repo)

        state: AgentState = AgentState(
            messages=[],
            off_scope_count=0,
            terminated=True,
            summary=None,
            iteration_count=0,
        )

        result = await node(state, _config())
        assert result["summary"] == "No conversation occurred."
        mock_summary_repo.save_summary.assert_called_once()

    async def test_passes_none_thread_id_when_config_missing(
        self, mock_summary_repo: AsyncMock
    ) -> None:
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = AIMessage(content="ok.")
        node = make_generate_summary_node(mock_llm, mock_summary_repo)

        state: AgentState = AgentState(
            messages=[HumanMessage(content="hi")],
            off_scope_count=0,
            terminated=True,
            summary=None,
            iteration_count=1,
        )

        await node(state, {"configurable": {}})

        kwargs = mock_summary_repo.save_summary.call_args.kwargs
        assert kwargs["thread_id"] is None
