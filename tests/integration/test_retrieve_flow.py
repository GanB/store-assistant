from __future__ import annotations

from langchain_core.messages import HumanMessage

from tests.conftest import (
    TEST_PASSPHRASE,
    GraphFixture,
    make_retrieve_call,
    make_save_call,
    make_text_response,
)


class TestRetrieveFlow:
    async def test_retrieve_with_correct_passphrase_returns_phone(
        self, graph_fixture: GraphFixture
    ) -> None:
        await graph_fixture.store_repo.save_store("Sunrise Grocers", "+19195550134")

        graph_fixture.llm.queue(
            make_retrieve_call("Sunrise Grocers", TEST_PASSPHRASE, call_id="t1"),
            make_text_response("The phone number for Sunrise Grocers is +19195550134."),
        )

        prompt = f"What's the phone for Sunrise Grocers? Passphrase {TEST_PASSPHRASE}"
        result = await graph_fixture.graph.ainvoke(
            {"messages": [HumanMessage(content=prompt)]},
            config=graph_fixture.config,
        )

        last_ai = next(
            msg for msg in reversed(result["messages"]) if msg.type == "ai" and msg.content
        )
        assert "+19195550134" in str(last_ai.content)

    async def test_retrieve_with_wrong_passphrase_no_phone_returned(
        self, graph_fixture: GraphFixture
    ) -> None:
        await graph_fixture.store_repo.save_store("Sunrise Grocers", "+19195550134")

        graph_fixture.llm.queue(
            make_retrieve_call("Sunrise Grocers", "wrong-pass", call_id="t1"),
            make_text_response("Sorry, that passphrase is incorrect."),
        )

        result = await graph_fixture.graph.ainvoke(
            {
                "messages": [
                    HumanMessage(
                        content="Phone for Sunrise Grocers? Passphrase wrong-pass"
                    )
                ]
            },
            config=graph_fixture.config,
        )

        for msg in result["messages"]:
            assert "+19195550134" not in str(getattr(msg, "content", ""))

    async def test_retrieve_nonexistent_store_handled_gracefully(
        self, graph_fixture: GraphFixture
    ) -> None:
        graph_fixture.llm.queue(
            make_retrieve_call("Ghost Store", TEST_PASSPHRASE, call_id="t1"),
            make_text_response("I couldn't find a store named Ghost Store."),
        )

        result = await graph_fixture.graph.ainvoke(
            {
                "messages": [
                    HumanMessage(
                        content=f"Look up Ghost Store. Passphrase {TEST_PASSPHRASE}"
                    )
                ]
            },
            config=graph_fixture.config,
        )

        last_ai = next(
            msg for msg in reversed(result["messages"]) if msg.type == "ai" and msg.content
        )
        assert "Ghost Store" in str(last_ai.content) or "couldn" in str(last_ai.content).lower()

    async def test_retrieve_after_save_in_same_conversation(
        self, graph_fixture: GraphFixture
    ) -> None:
        graph_fixture.llm.queue(
            make_save_call("Lookup Test", "919-555-1234", call_id="t1"),
            make_text_response("Saved Lookup Test."),
            make_retrieve_call("Lookup Test", TEST_PASSPHRASE, call_id="t2"),
            make_text_response("Phone for Lookup Test is +19195551234."),
        )

        await graph_fixture.graph.ainvoke(
            {"messages": [HumanMessage(content="Save Lookup Test, 919-555-1234")]},
            config=graph_fixture.config,
        )

        result = await graph_fixture.graph.ainvoke(
            {
                "messages": [
                    HumanMessage(
                        content=f"Now look up Lookup Test. Passphrase {TEST_PASSPHRASE}"
                    )
                ]
            },
            config=graph_fixture.config,
        )

        last_ai = next(
            msg for msg in reversed(result["messages"]) if msg.type == "ai" and msg.content
        )
        assert "+19195551234" in str(last_ai.content)
