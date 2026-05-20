from __future__ import annotations


class DataError(Exception):
    """Base class for all data layer errors."""


class StoreNotFoundError(DataError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Store not found: {name!r}")
        self.name = name


class StoreAlreadyExistsError(DataError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Store already exists: {name!r}")
        self.name = name


class DatabaseConnectionError(DataError):
    def __init__(self, detail: str) -> None:
        super().__init__(f"Database connection error: {detail}")
        self.detail = detail
