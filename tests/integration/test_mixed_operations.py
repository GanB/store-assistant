from __future__ import annotations

from langchain_core.messages import HumanMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from store_assistant.data.models import ConversationSummary
from tests.conftest import (
    TEST_PASSPHRASE,
    GraphFixture,
    make_retrieve_call,
    make_save_call,
    make_text_response,
)

THREAD_A = "thread-a"
THREAD_B = "thread-b"


class TestMixedOperations:
    async def test_save_then_retrieve_then_done(
        self,
        graph_fixture: GraphFixture,
        async_session: AsyncSession,
    ) -> None:
        graph_fixture.llm.queue(
            make_save_call("Mixed Store", "919-555-7777", call_id="t1"),
            make_text_response("Saved Mixed Store."),
            make_retrieve_call("Mixed Store", TEST_PASSPHRASE, call_id="t2"),
            make_text_response("Phone for Mixed Store is +19195557777."),
            make_text_response("User saved one store and retrieved it before ending."),
        )

        save_result = await graph_fixture.graph.ainvoke(
            {"messages": [HumanMessage(content="Save Mixed Store, 919-555-7777")]},
            config=graph_fixture.config,
        )
        assert save_result.get("terminated") is not True
        assert await graph_fixture.store_repo.exists("Mixed Store") is True

        retrieve_result = await graph_fixture.graph.ainvoke(
            {
                "messages": [
                    HumanMessage(
                        content=f"Now look up Mixed Store. Passphrase {TEST_PASSPHRASE}"
                    )
                ]
            },
            config=graph_fixture.config,
        )
        assert retrieve_result.get("terminated") is not True
        last_retrieve = next(
            msg
            for msg in reversed(retrieve_result["messages"])
            if msg.type == "ai" and msg.content
        )
        assert "+19195557777" in str(last_retrieve.content)

        done_result = await graph_fixture.graph.ainvoke(
            {"messages": [HumanMessage(content="That's all, thanks bye")]},
            config=graph_fixture.config,
        )
        assert done_result.get("terminated") is True
        assert done_result.get("summary") is not None

        rows = (await async_session.execute(select(ConversationSummary))).scalars().all()
        assert len(rows) == 1

    async def test_parallel_threads_do_not_cross_contaminate(
        self,
        graph_fixture: GraphFixture,
    ) -> None:
        graph_fixture.llm.queue(
            make_save_call("Alpha Store", "+12015550001", call_id="a1"),
            make_text_response("Saved Alpha Store."),
            make_save_call("Bravo Store", "+13125550002", call_id="b1"),
            make_text_response("Saved Bravo Store."),
        )

        config_a = {"configurable": {"thread_id": THREAD_A}}
        config_b = {"configurable": {"thread_id": THREAD_B}}

        await graph_fixture.graph.ainvoke(
            {"messages": [HumanMessage(content="Save Alpha Store, 201-555-0001")]},
            config=config_a,
        )
        await graph_fixture.graph.ainvoke(
            {"messages": [HumanMessage(content="Save Bravo Store, 312-555-0002")]},
            config=config_b,
        )

        state_a = await graph_fixture.graph.aget_state(config_a)
        state_b = await graph_fixture.graph.aget_state(config_b)

        text_a = " ".join(str(m.content) for m in state_a.values.get("messages", []))
        text_b = " ".join(str(m.content) for m in state_b.values.get("messages", []))

        assert "Alpha Store" in text_a
        assert "Bravo Store" not in text_a
        assert "Bravo Store" in text_b
        assert "Alpha Store" not in text_b

        assert await graph_fixture.store_repo.exists("Alpha Store") is True
        assert await graph_fixture.store_repo.exists("Bravo Store") is True
