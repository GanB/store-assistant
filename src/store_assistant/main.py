from __future__ import annotations

import argparse
import asyncio
import os
import sys
from uuid import uuid4

import structlog
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from store_assistant.agent.checkpointer import (
    CheckpointerNotConfiguredError,
    production_checkpointer,
)
from store_assistant.agent.graph import build_graph
from store_assistant.config import Settings, get_settings
from store_assistant.data.migration_check import (
    MigrationOutOfSyncError,
    assert_db_at_head,
)
from store_assistant.data.repository import StoreRepository, SummaryRepository
from store_assistant.logging_config import configure_logging, get_logger

USER_PROMPT = "You: "
ASSISTANT_LABEL = "Assistant: "

ENV_TRACING_V2 = "LANGCHAIN_TRACING_V2"
ENV_LANGCHAIN_API_KEY = "LANGCHAIN_API_KEY"
ENV_LANGCHAIN_PROJECT = "LANGCHAIN_PROJECT"


def _enable_langsmith_tracing(settings: Settings, log: structlog.stdlib.BoundLogger) -> None:
    if not settings.langsmith_tracing or settings.langsmith_api_key is None:
        return
    os.environ[ENV_TRACING_V2] = "true"
    os.environ[ENV_LANGCHAIN_API_KEY] = settings.langsmith_api_key.get_secret_value()
    os.environ[ENV_LANGCHAIN_PROJECT] = settings.langsmith_project
    log.info(
        "tracing.langsmith.enabled",
        project=settings.langsmith_project,
    )


def _last_assistant_text(messages: list[BaseMessage]) -> str | None:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            content = msg.content
            if isinstance(content, str) and content.strip():
                return content
    return None


async def _validate_startup(settings: Settings, log: structlog.stdlib.BoundLogger) -> int:
    """Returns 0 on success, non-zero exit code on failure."""
    try:
        await assert_db_at_head(settings)
    except MigrationOutOfSyncError as exc:
        log.error(
            "startup.migration_check_failed",
            current=exc.current,
            expected=exc.expected,
        )
        print(f"Migration check failed: {exc}")
        return 1
    if settings.agent_checkpoint_db_url is None:
        log.error("startup.checkpointer_not_configured")
        print(
            "AGENT_CHECKPOINT_DB_URL is not set. Set it in .env and re-run "
            "(see ADR-015 for details)."
        )
        return 1
    return 0


async def _run_repl(thread_id: str) -> int:
    settings = get_settings()
    configure_logging(settings.app_log_level)
    log = get_logger("store_assistant.cli")

    structlog.contextvars.bind_contextvars(thread_id=thread_id)
    _enable_langsmith_tracing(settings, log)
    log.info("cli.startup", thread_id=thread_id)

    rc = await _validate_startup(settings, log)
    if rc != 0:
        return rc

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    print(f"Store Assistant — thread {thread_id}")
    print("Type your message. Press Ctrl+C or send 'quit' to exit.\n")

    try:
        async with (
            session_factory() as session,
            production_checkpointer(settings) as (checkpointer, _pool),
        ):
            store_repo = StoreRepository(session)
            summary_repo = SummaryRepository(session)
            graph = build_graph(
                settings,
                store_repo,
                summary_repo,
                checkpointer=checkpointer,
            )
            config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

            while True:
                try:
                    user_input = await asyncio.to_thread(input, USER_PROMPT)
                except (EOFError, KeyboardInterrupt):
                    print("\nGoodbye.")
                    break

                user_input = user_input.strip()
                if not user_input:
                    continue

                try:
                    result = await graph.ainvoke(
                        {"messages": [HumanMessage(content=user_input)]},
                        config=config,
                    )
                except CheckpointerNotConfiguredError as exc:
                    log.error("cli.checkpointer_not_configured", error=str(exc))
                    print(f"Checkpointer not configured: {exc}")
                    return 1
                except Exception:
                    log.exception("cli.graph_invocation_failed")
                    print("Error: something went wrong handling that message.")
                    continue

                reply = _last_assistant_text(result.get("messages", []))
                if reply:
                    print(f"{ASSISTANT_LABEL}{reply}")

                if result.get("terminated"):
                    log.info("cli.conversation_ended")
                    print("Conversation ended.")
                    break
    finally:
        await engine.dispose()
        structlog.contextvars.clear_contextvars()

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="store-assistant")
    parser.add_argument(
        "--thread-id",
        default=None,
        help="Thread id for the conversation. A new uuid is generated if omitted.",
    )
    args = parser.parse_args()
    thread_id: str = args.thread_id or str(uuid4())
    return asyncio.run(_run_repl(thread_id))


if __name__ == "__main__":
    sys.exit(main())
