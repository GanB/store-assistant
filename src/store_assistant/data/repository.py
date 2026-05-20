from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import CursorResult, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from store_assistant.data.exceptions import StoreAlreadyExistsError
from store_assistant.data.models import ConversationSummary, Store, TurnMetricsRow
from store_assistant.data.schemas import StoreRead, SummaryRead
from store_assistant.logging_config import get_logger
from store_assistant.metrics import TurnMetrics

_log = get_logger("store_assistant.data")


class StoreRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_store(
        self,
        name: str,
        phone_e164: str,
        thread_id: str | None = None,
    ) -> StoreRead:
        _log.info("data.store.save_attempt", name=name, thread_id=thread_id)
        store = Store(name=name, phone_e164=phone_e164, thread_id=thread_id)
        self._session.add(store)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            _log.info("data.store.duplicate_rejected", name=name)
            raise StoreAlreadyExistsError(name) from exc
        await self._session.refresh(store)
        _log.info(
            "data.store.saved",
            store_id=str(store.id),
            thread_id=thread_id,
        )
        return StoreRead.model_validate(store)

    async def get_store_by_name(self, name: str) -> StoreRead | None:
        _log.info("data.store.lookup_attempt", name=name)
        stmt = select(Store).where(func.lower(Store.name) == name.lower())
        result = await self._session.execute(stmt)
        store = result.scalar_one_or_none()
        if store is None:
            _log.info("data.store.not_found", name=name)
            return None
        _log.info("data.store.found", store_id=str(store.id))
        return StoreRead.model_validate(store)

    async def exists(self, name: str) -> bool:
        stmt = select(Store.id).where(func.lower(Store.name) == name.lower())
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None


class SummaryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_summary(
        self,
        summary_text: str,
        conversation_metadata: dict[str, Any] | None = None,
        thread_id: str | None = None,
    ) -> SummaryRead:
        summary = ConversationSummary(
            summary_text=summary_text,
            conversation_metadata=conversation_metadata,
            thread_id=thread_id,
        )
        self._session.add(summary)
        await self._session.commit()
        await self._session.refresh(summary)
        _log.info(
            "data.summary.saved",
            summary_id=str(summary.id),
            thread_id=thread_id,
        )
        return SummaryRead.model_validate(summary)

    async def list_recent(self, limit: int = 20) -> list[SummaryRead]:
        stmt = (
            select(ConversationSummary)
            .order_by(ConversationSummary.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [SummaryRead.model_validate(row) for row in rows]


class MetricsRepository:
    """Writes and reads turn_metrics rows.

    Single-row INSERT on every agent turn from the LLM node; aggregate reads
    drive the session-totals panel. tool_calls is serialised through
    dataclasses.asdict so the on-disk JSONB shape stays in lockstep with
    metrics.ToolCallSummary — no parallel schema definition.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_turn(self, metrics: TurnMetrics) -> None:
        row = TurnMetricsRow(
            thread_id=metrics.thread_id,
            checkpoint_id=metrics.checkpoint_id or None,
            turn_index=metrics.turn_index,
            node_name=metrics.node_name,
            input_tokens=metrics.input_tokens,
            output_tokens=metrics.output_tokens,
            cache_read_tokens=metrics.cache_read_tokens,
            cache_creation_tokens=metrics.cache_creation_tokens,
            cost_usd=metrics.cost_usd,
            latency_ms=metrics.latency_ms,
            tool_calls=[asdict(tc) for tc in metrics.tool_calls],
            langsmith_run_id=metrics.langsmith_run_id,
        )
        self._session.add(row)
        await self._session.commit()
        _log.info(
            "data.metrics.recorded",
            thread_id=metrics.thread_id,
            turn_index=metrics.turn_index,
            input_tokens=metrics.input_tokens,
            output_tokens=metrics.output_tokens,
            cost_usd=str(metrics.cost_usd),
        )

    async def list_for_thread(self, thread_id: str) -> list[TurnMetricsRow]:
        stmt = (
            select(TurnMetricsRow)
            .where(TurnMetricsRow.thread_id == thread_id)
            .order_by(TurnMetricsRow.turn_index.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def delete_for_thread(self, thread_id: str) -> int:
        """Right-to-deletion arm for the metrics table.

        threads.delete_thread orchestrates deletion across the agent_state
        schema and domain tables; this is the parallel for the metrics
        side. Returns the count of rows deleted so callers can audit the
        scrub.
        """
        from sqlalchemy import delete  # noqa: PLC0415 — local to keep import light

        stmt = delete(TurnMetricsRow).where(
            TurnMetricsRow.thread_id == thread_id
        )
        # DML execute() returns a CursorResult at runtime; the static type is
        # the broader Result[Any] which doesn't expose .rowcount.
        result = cast(CursorResult[Any], await self._session.execute(stmt))
        await self._session.commit()
        return int(result.rowcount or 0)

    async def session_totals(self, thread_id: str) -> dict[str, Any]:
        """Aggregates for the session-totals panel.

        Returns a dict with turns, total_input_tokens, total_output_tokens,
        total_cost_usd, avg_latency_ms. Empty thread → all zeros so the UI
        can hide the panel by checking `turns == 0`.
        """
        stmt = select(
            func.count(TurnMetricsRow.id),
            func.coalesce(func.sum(TurnMetricsRow.input_tokens), 0),
            func.coalesce(func.sum(TurnMetricsRow.output_tokens), 0),
            func.coalesce(func.sum(TurnMetricsRow.cost_usd), Decimal("0")),
            func.coalesce(func.avg(TurnMetricsRow.latency_ms), 0),
        ).where(TurnMetricsRow.thread_id == thread_id)
        row = (await self._session.execute(stmt)).one()
        turns_raw, in_tok, out_tok, cost, avg_lat = row
        return {
            "turns": int(turns_raw or 0),
            "total_input_tokens": int(in_tok or 0),
            "total_output_tokens": int(out_tok or 0),
            "total_cost_usd": Decimal(cost) if cost is not None else Decimal("0"),
            "avg_latency_ms": int(avg_lat or 0),
        }
