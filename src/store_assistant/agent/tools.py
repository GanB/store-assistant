from __future__ import annotations

import hmac
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool

from store_assistant.agent.tool_results import GetStorePhoneResult, SaveStoreResult
from store_assistant.agent.tool_schemas import GetStorePhoneInput, SaveStoreInput
from store_assistant.config import Settings
from store_assistant.data.exceptions import StoreAlreadyExistsError
from store_assistant.data.repository import StoreRepository
from store_assistant.logging_config import get_logger
from store_assistant.validation.exceptions import PhoneValidationError
from store_assistant.validation.phone import validate_and_normalize_phone

SAVE_STORE_NAME = "save_store"
SAVE_STORE_DESCRIPTION = (
    "Save a new store record. Use this when the user wants to register a new store. "
    "Requires the store's display name and a phone number; the phone number will be "
    "validated and normalized to E.164 format. Returns success=False with a message "
    "if the phone is invalid or if a store with that name already exists."
)

GET_STORE_PHONE_NAME = "get_store_phone"
GET_STORE_PHONE_DESCRIPTION = (
    "Retrieve a store's phone number by name. Requires a passphrase that the user "
    "must supply. Do not invent, guess, or default the passphrase. Returns "
    "success=False with reason='auth_failed' if the passphrase is wrong, or "
    "reason='not_found' if no store matches the name."
)

REASON_AUTH_FAILED = "auth_failed"
REASON_NOT_FOUND = "not_found"

_log = get_logger("store_assistant.agent.tools")


def make_save_store_tool(
    settings: Settings,
    store_repo: StoreRepository,
) -> StructuredTool:
    _ = settings

    async def save_store(
        name: str,
        phone: str,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        thread_id = config.get("configurable", {}).get("thread_id")
        _log.info(
            "agent.tool_called",
            tool_name=SAVE_STORE_NAME,
            arg_keys=["name", "phone"],
        )
        name = name.strip()
        try:
            normalized = validate_and_normalize_phone(phone)
        except PhoneValidationError as exc:
            _log.info(
                "agent.tool_failed",
                tool_name=SAVE_STORE_NAME,
                error_class="PhoneValidationError",
                reason=exc.reason,
            )
            return SaveStoreResult(
                success=False,
                message=(
                    f"Phone number invalid: {exc.reason}. "
                    "Please provide a valid US phone number."
                ),
                normalized_phone=None,
            ).model_dump()

        try:
            await store_repo.save_store(name, normalized, thread_id=thread_id)
        except StoreAlreadyExistsError:
            _log.info(
                "agent.tool_failed",
                tool_name=SAVE_STORE_NAME,
                error_class="StoreAlreadyExistsError",
            )
            return SaveStoreResult(
                success=False,
                message=f"Store '{name}' already exists.",
                normalized_phone=None,
            ).model_dump()

        _log.info("agent.tool_succeeded", tool_name=SAVE_STORE_NAME, success=True)
        return SaveStoreResult(
            success=True,
            message=f"Store '{name}' saved successfully.",
            normalized_phone=normalized,
        ).model_dump()

    return StructuredTool.from_function(
        coroutine=save_store,
        name=SAVE_STORE_NAME,
        description=SAVE_STORE_DESCRIPTION,
        args_schema=SaveStoreInput,
    )


def make_get_store_phone_tool(
    settings: Settings,
    store_repo: StoreRepository,
) -> StructuredTool:
    async def get_store_phone(name: str, passphrase: str) -> dict[str, Any]:
        _log.info(
            "agent.tool_called",
            tool_name=GET_STORE_PHONE_NAME,
            arg_keys=["name", "passphrase"],
        )
        name = name.strip()
        expected = settings.app_passphrase.get_secret_value().encode("utf-8")
        provided = passphrase.encode("utf-8")
        if not hmac.compare_digest(provided, expected):
            _log.info(
                "agent.tool_failed",
                tool_name=GET_STORE_PHONE_NAME,
                error_class="AuthFailed",
                reason=REASON_AUTH_FAILED,
            )
            return GetStorePhoneResult(
                success=False,
                message="Passphrase incorrect.",
                phone_e164=None,
                reason=REASON_AUTH_FAILED,
            ).model_dump()

        store = await store_repo.get_store_by_name(name)
        if store is None:
            _log.info(
                "agent.tool_failed",
                tool_name=GET_STORE_PHONE_NAME,
                error_class="StoreNotFound",
                reason=REASON_NOT_FOUND,
            )
            return GetStorePhoneResult(
                success=False,
                message=f"Store '{name}' not found.",
                phone_e164=None,
                reason=REASON_NOT_FOUND,
            ).model_dump()

        _log.info(
            "agent.tool_succeeded",
            tool_name=GET_STORE_PHONE_NAME,
            success=True,
        )
        return GetStorePhoneResult(
            success=True,
            message=f"Phone for '{name}': {store.phone_e164}",
            phone_e164=store.phone_e164,
            reason=None,
        ).model_dump()

    return StructuredTool.from_function(
        coroutine=get_store_phone,
        name=GET_STORE_PHONE_NAME,
        description=GET_STORE_PHONE_DESCRIPTION,
        args_schema=GetStorePhoneInput,
    )
