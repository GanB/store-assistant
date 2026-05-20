from __future__ import annotations

from langchain_core.messages import HumanMessage

from tests.conftest import (
    GraphFixture,
    make_save_call,
    make_text_response,
)


class TestSaveFlow:
    async def test_save_store_happy_path(self, graph_fixture: GraphFixture) -> None:
        graph_fixture.llm.queue(
            make_save_call("Joe's Grocery", "919-555-0134", call_id="t1"),
            make_text_response("Saved Joe's Grocery."),
        )

        result = await graph_fixture.graph.ainvoke(
            {"messages": [HumanMessage(content="Save Joe's Grocery, phone 919-555-0134")]},
            config=graph_fixture.config,
        )

        saved = await graph_fixture.store_repo.get_store_by_name("Joe's Grocery")
        assert saved is not None
        assert saved.phone_e164 == "+19195550134"
        assert result.get("terminated") is not True

    async def test_save_store_invalid_phone_reprompts(
        self, graph_fixture: GraphFixture
    ) -> None:
        graph_fixture.llm.queue(
            make_save_call("Bad Phone Store", "abc-def-ghij", call_id="t1"),
            make_text_response("That phone number doesn't look valid. Please try again."),
            make_save_call("Bad Phone Store", "919-555-0134", call_id="t2"),
            make_text_response("Saved Bad Phone Store."),
        )

        await graph_fixture.graph.ainvoke(
            {"messages": [HumanMessage(content="Save Bad Phone Store with phone abc-def-ghij")]},
            config=graph_fixture.config,
        )
        assert await graph_fixture.store_repo.exists("Bad Phone Store") is False

        await graph_fixture.graph.ainvoke(
            {"messages": [HumanMessage(content="Try 919-555-0134 instead")]},
            config=graph_fixture.config,
        )
        saved = await graph_fixture.store_repo.get_store_by_name("Bad Phone Store")
        assert saved is not None
        assert saved.phone_e164 == "+19195550134"

    async def test_save_multiple_stores_in_one_conversation(
        self, graph_fixture: GraphFixture
    ) -> None:
        graph_fixture.llm.queue(
            make_save_call("Store One", "919-555-0001", call_id="t1"),
            make_text_response("Saved Store One."),
            make_save_call("Store Two", "919-555-0002", call_id="t2"),
            make_text_response("Saved Store Two."),
        )

        await graph_fixture.graph.ainvoke(
            {"messages": [HumanMessage(content="Save Store One, 919-555-0001")]},
            config=graph_fixture.config,
        )
        await graph_fixture.graph.ainvoke(
            {"messages": [HumanMessage(content="Now save Store Two, 919-555-0002")]},
            config=graph_fixture.config,
        )

        one = await graph_fixture.store_repo.get_store_by_name("Store One")
        two = await graph_fixture.store_repo.get_store_by_name("Store Two")
        assert one is not None and one.phone_e164 == "+19195550001"
        assert two is not None and two.phone_e164 == "+19195550002"
