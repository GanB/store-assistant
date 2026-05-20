from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import structlog
from structlog.types import EventDict, WrappedLogger

REDACTED_PLACEHOLDER = "[REDACTED]"

REDACT_KEY_SUBSTRINGS: tuple[str, ...] = (
    "passphrase",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "phone",
)


class RedactingProcessor:
    def __init__(self, key_substrings: tuple[str, ...] = REDACT_KEY_SUBSTRINGS) -> None:
        self._keys = tuple(k.lower() for k in key_substrings)

    def __call__(
        self,
        logger: WrappedLogger,
        method_name: str,
        event_dict: EventDict,
    ) -> EventDict:
        del logger, method_name
        return self._redact_mapping(event_dict)

    def _should_redact(self, key: str) -> bool:
        lowered = key.lower()
        return any(needle in lowered for needle in self._keys)

    def _redact_mapping(self, source: Mapping[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in source.items():
            if self._should_redact(key):
                out[key] = REDACTED_PLACEHOLDER
            else:
                out[key] = self._redact_value(value)
        return out

    def _redact_value(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return self._redact_mapping(value)
        if isinstance(value, list):
            return [self._redact_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._redact_value(item) for item in value)
        return value


def configure_logging(level: str = "INFO") -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", level=log_level, force=True)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            RedactingProcessor(),
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
