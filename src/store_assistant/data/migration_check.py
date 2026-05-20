from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import create_async_engine

from store_assistant.config import Settings


class MigrationOutOfSyncError(RuntimeError):
    def __init__(self, current: str | None, expected: str | None) -> None:
        super().__init__(
            f"Database not at the expected migration head: "
            f"current={current!r}, expected={expected!r}. "
            f"Run `make db-migrate` and try again."
        )
        self.current = current
        self.expected = expected


def _expected_head() -> str | None:
    repo_root = Path(__file__).resolve().parents[3]
    config = Config(str(repo_root / "alembic.ini"))
    config.set_main_option("script_location", str(repo_root / "alembic"))
    script = ScriptDirectory.from_config(config)
    return script.get_current_head()


async def assert_db_at_head(settings: Settings) -> None:
    expected = _expected_head()
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            current = await conn.run_sync(
                lambda sync_conn: MigrationContext.configure(sync_conn).get_current_revision()
            )
    finally:
        await engine.dispose()

    if current != expected:
        raise MigrationOutOfSyncError(current=current, expected=expected)
