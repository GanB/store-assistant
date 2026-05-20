from __future__ import annotations

import phonenumbers
from phonenumbers import NumberParseException

from store_assistant.validation.exceptions import PhoneValidationError

DEFAULT_REGION = "US"


def validate_and_normalize_phone(raw: str | None, default_region: str = DEFAULT_REGION) -> str:
    if raw is None:
        raise PhoneValidationError("phone number is required")

    cleaned = raw.strip()
    if not cleaned:
        raise PhoneValidationError("phone number is empty")

    try:
        parsed = phonenumbers.parse(cleaned, default_region)
    except NumberParseException as exc:
        raise PhoneValidationError(_describe_parse_error(exc)) from exc

    if not phonenumbers.is_possible_number(parsed):
        raise PhoneValidationError("number is not a possible phone number")

    if not phonenumbers.is_valid_number(parsed):
        raise PhoneValidationError("number is not a valid phone number")

    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def _describe_parse_error(exc: NumberParseException) -> str:
    code = exc.error_type
    if code == NumberParseException.INVALID_COUNTRY_CODE:
        return "invalid country code"
    if code == NumberParseException.NOT_A_NUMBER:
        return "input does not look like a phone number"
    if code == NumberParseException.TOO_SHORT_AFTER_IDD:
        return "number is too short after the international prefix"
    if code == NumberParseException.TOO_SHORT_NSN:
        return "number is too short"
    if code == NumberParseException.TOO_LONG:
        return "number is too long"
    return "phone number could not be parsed"
