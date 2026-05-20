"""End-to-end test against a real Anthropic API call.

Run with:
    LIVE_TESTS=1 ANTHROPIC_API_KEY=sk-ant-... make test-live

Opt-in only: skipped automatically unless both LIVE_TESTS=1 and
ANTHROPIC_API_KEY are set in the environment. Uses claude-haiku-4-5 to keep
the per-execution cost in the cents range.
"""

from __future__ import annotations

import os

import pytest
from langchain_core.messages import HumanMessage
from pydantic import SecretStr
from pytest_mock import MockerFixture
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from store_assistant.agent.graph import build_graph
from store_assistant.config import Settings
from store_assistant.data.models import ConversationSummary, Store
from store_assistant.data.repository import StoreRepository, SummaryRepository

LIVE_PASSPHRASE = "open-sesame"
LIVE_MODEL = "claude-haiku-4-5"

_LIVE_ENABLED = (
    os.environ.get("LIVE_TESTS") == "1"
    and bool(os.environ.get("ANTHROPIC_API_KEY"))
)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not _LIVE_ENABLED,
        reason="LIVE_TESTS=1 and ANTHROPIC_API_KEY required to run live e2e tests",
    ),
]


def _live_settings() -> Settings:
    return Settings(
        anthropic_api_key=SecretStr(os.environ["ANTHROPIC_API_KEY"]),
        postgres_user="u",
        postgres_password=SecretStr("p"),
        postgres_db="d",
        app_passphrase=SecretStr(LIVE_PASSPHRASE),
        app_llm_model=LIVE_MODEL,
        app_max_graph_iterations=20,
    )


async def test_full_conversation_lifecycle(
    async_session: AsyncSession,
    mocker: MockerFixture,
) -> None:
    settings = _live_settings()

    store_repo = StoreRepository(async_session)
    summary_repo = SummaryRepository(async_session)

    save_spy = mocker.spy(store_repo, "save_store")
    get_spy = mocker.spy(store_repo, "get_store_by_name")

    graph = build_graph(settings, store_repo, summary_repo)
    config = {"configurable": {"thread_id": "live-e2e-1"}}

    await graph.ainvoke(
        {
            "messages": [
                HumanMessage(
                    content=(
                        "Please save a store called \"Joe's Grocery\" "
                        "with phone 919-555-1234."
                    )
                )
            ]
        },
        config=config,
    )

    await graph.ainvoke(
        {
            "messages": [
                HumanMessage(
                    content=(
                        "Please retrieve the store record for \"Joe's Grocery\". "
                        f"My passphrase is {LIVE_PASSPHRASE}."
                    )
                )
            ]
        },
        config=config,
    )

    final = await graph.ainvoke(
        {"messages": [HumanMessage(content="I'm done, thanks.")]},
        config=config,
    )

    save_spy.assert_called_once_with(
        "Joe's Grocery", "+19195551234", thread_id="live-e2e-1"
    )
    assert get_spy.call_count >= 1
    assert get_spy.call_args.args[0] == "Joe's Grocery"

    raw_store = (
        await async_session.execute(
            select(Store).where(Store.name == "Joe's Grocery")
        )
    ).scalar_one()
    assert raw_store.phone_e164 == "+19195551234"

    summaries = (
        await async_session.execute(select(ConversationSummary))
    ).scalars().all()
    assert len(summaries) >= 1

    assert final.get("terminated") is True


async def test_validation_loop_then_save(async_session: AsyncSession) -> None:
    settings = _live_settings()
    store_repo = StoreRepository(async_session)
    summary_repo = SummaryRepository(async_session)
    graph = build_graph(settings, store_repo, summary_repo)
    config = {"configurable": {"thread_id": "live-e2e-validation"}}

    await graph.ainvoke(
        {
            "messages": [
                HumanMessage(
                    content="Save Sunrise Grocers with phone abc-def-ghij."
                )
            ]
        },
        config=config,
    )
    assert await store_repo.exists("Sunrise Grocers") is False

    await graph.ainvoke(
        {"messages": [HumanMessage(content="Try 919-555-2222 instead.")]},
        config=config,
    )

    saved = await store_repo.get_store_by_name("Sunrise Grocers")
    assert saved is not None
    assert saved.phone_e164 == "+19195552222"


async def test_termination_phrase_completes_conversation(
    async_session: AsyncSession,
) -> None:
    settings = _live_settings()
    store_repo = StoreRepository(async_session)
    summary_repo = SummaryRepository(async_session)
    graph = build_graph(settings, store_repo, summary_repo)
    config = {"configurable": {"thread_id": "live-e2e-termination"}}

    await graph.ainvoke(
        {"messages": [HumanMessage(content="Save Termination Test, 415-555-1212.")]},
        config=config,
    )
    final = await graph.ainvoke(
        {"messages": [HumanMessage(content="thanks I'm good")]},
        config=config,
    )

    assert final.get("terminated") is True
    summaries = (
        await async_session.execute(select(ConversationSummary))
    ).scalars().all()
    assert len(summaries) == 1


async def test_off_scope_three_times_terminates(async_session: AsyncSession) -> None:
    settings = _live_settings()
    store_repo = StoreRepository(async_session)
    summary_repo = SummaryRepository(async_session)
    graph = build_graph(settings, store_repo, summary_repo)
    config = {"configurable": {"thread_id": "live-e2e-off-scope"}}

    for prompt in [
        "What's the weather in Boston?",
        "Tell me a joke.",
        "Sing me a song.",
    ]:
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=prompt)]},
            config=config,
        )

    assert result.get("terminated") is True
    summaries = (
        await async_session.execute(select(ConversationSummary))
    ).scalars().all()
    assert len(summaries) == 1


async def test_mid_flow_context_switch(
    async_session: AsyncSession,
    mocker: MockerFixture,
) -> None:
    settings = _live_settings()
    store_repo = StoreRepository(async_session)
    summary_repo = SummaryRepository(async_session)
    save_spy = mocker.spy(store_repo, "save_store")
    get_spy = mocker.spy(store_repo, "get_store_by_name")
    graph = build_graph(settings, store_repo, summary_repo)
    config = {"configurable": {"thread_id": "live-e2e-context-switch"}}

    await store_repo.save_store("Existing Cafe", "+14155550000")

    await graph.ainvoke(
        {
            "messages": [
                HumanMessage(
                    content=(
                        "I want to save Brand New Store, but actually first, "
                        f"please retrieve Existing Cafe. My passphrase is {LIVE_PASSPHRASE}."
                    )
                )
            ]
        },
        config=config,
    )

    assert any(
        call.args == ("Existing Cafe",) for call in get_spy.call_args_list
    )
    assert not any(
        "Brand New Store" in str(call.args) for call in save_spy.call_args_list
    )
