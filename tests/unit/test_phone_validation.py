from __future__ import annotations

import pytest

from store_assistant.validation.exceptions import PhoneValidationError
from store_assistant.validation.phone import validate_and_normalize_phone


class TestValidPhoneNumbers:
    def test_valid_us_number_with_dashes(self) -> None:
        assert validate_and_normalize_phone("919-555-0134") == "+19195550134"

    def test_valid_us_number_with_parens(self) -> None:
        assert validate_and_normalize_phone("(919) 555-0134") == "+19195550134"

    def test_valid_us_number_e164(self) -> None:
        assert validate_and_normalize_phone("+19195550134") == "+19195550134"

    def test_valid_international(self) -> None:
        assert validate_and_normalize_phone("+442071838750") == "+442071838750"

    def test_normalized_format_is_e164(self) -> None:
        for raw in (
            "919-555-0134",
            "(919) 555-0134",
            "919.555.0134",
            "9195550134",
            "+1 919 555 0134",
        ):
            assert validate_and_normalize_phone(raw) == "+19195550134"


class TestInvalidPhoneNumbers:
    def test_invalid_too_short(self) -> None:
        with pytest.raises(PhoneValidationError) as exc_info:
            validate_and_normalize_phone("123")
        assert exc_info.value.reason

    def test_invalid_letters_in_number(self) -> None:
        with pytest.raises(PhoneValidationError) as exc_info:
            validate_and_normalize_phone("abcd-efg-hijk")
        assert exc_info.value.reason

    def test_invalid_empty_string(self) -> None:
        with pytest.raises(PhoneValidationError) as exc_info:
            validate_and_normalize_phone("")
        assert "empty" in exc_info.value.reason

    def test_invalid_whitespace_only(self) -> None:
        with pytest.raises(PhoneValidationError) as exc_info:
            validate_and_normalize_phone("   \t  ")
        assert "empty" in exc_info.value.reason

    def test_invalid_none(self) -> None:
        with pytest.raises(PhoneValidationError) as exc_info:
            validate_and_normalize_phone(None)
        assert "required" in exc_info.value.reason

    def test_invalid_just_punctuation(self) -> None:
        with pytest.raises(PhoneValidationError) as exc_info:
            validate_and_normalize_phone("---")
        assert exc_info.value.reason

    def test_invalid_all_zeros(self) -> None:
        with pytest.raises(PhoneValidationError) as exc_info:
            validate_and_normalize_phone("0000000000")
        assert exc_info.value.reason

    def test_invalid_too_long(self) -> None:
        with pytest.raises(PhoneValidationError) as exc_info:
            validate_and_normalize_phone("20155512345678")
        assert exc_info.value.reason

    def test_valid_with_leading_and_trailing_whitespace(self) -> None:
        assert validate_and_normalize_phone("  +1 919 555 0134  ") == "+19195550134"
