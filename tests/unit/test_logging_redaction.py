from __future__ import annotations

from store_assistant.logging_config import REDACTED_PLACEHOLDER, RedactingProcessor


class TestRedactingProcessor:
    def setup_method(self) -> None:
        self.proc = RedactingProcessor()

    def test_passphrase_redacted(self) -> None:
        result = self.proc(
            None,
            "info",
            {"event": "tool_called", "passphrase": "open-sesame"},
        )
        assert result["passphrase"] == REDACTED_PLACEHOLDER
        assert result["event"] == "tool_called"

    def test_phone_redacted(self) -> None:
        result = self.proc(
            None,
            "info",
            {"event": "save_attempt", "phone": "+19195550134"},
        )
        assert result["phone"] == REDACTED_PLACEHOLDER

        result_e164 = self.proc(
            None,
            "info",
            {"event": "save_attempt", "phone_e164": "+19195550134"},
        )
        assert result_e164["phone_e164"] == REDACTED_PLACEHOLDER

    def test_api_key_redacted(self) -> None:
        result = self.proc(
            None,
            "info",
            {
                "event": "startup",
                "api_key": "sk-ant-123456789",
                "anthropic_api_key": "sk-ant-abcdef",
            },
        )
        assert result["api_key"] == REDACTED_PLACEHOLDER
        assert result["anthropic_api_key"] == REDACTED_PLACEHOLDER

    def test_password_and_token_and_secret_redacted(self) -> None:
        result = self.proc(
            None,
            "info",
            {
                "event": "x",
                "password": "p",
                "access_token": "t",
                "client_secret": "s",
            },
        )
        assert result["password"] == REDACTED_PLACEHOLDER
        assert result["access_token"] == REDACTED_PLACEHOLDER
        assert result["client_secret"] == REDACTED_PLACEHOLDER

    def test_nested_redaction(self) -> None:
        event = {
            "event": "tool_called",
            "args": {
                "name": "Sunrise Grocers",
                "phone": "+19195550134",
                "metadata": {
                    "passphrase": "open-sesame",
                    "iteration": 3,
                },
            },
        }
        result = self.proc(None, "info", event)
        assert result["args"]["name"] == "Sunrise Grocers"
        assert result["args"]["phone"] == REDACTED_PLACEHOLDER
        assert result["args"]["metadata"]["passphrase"] == REDACTED_PLACEHOLDER
        assert result["args"]["metadata"]["iteration"] == 3

    def test_list_with_dicts_redacted(self) -> None:
        event = {
            "event": "batch",
            "items": [
                {"name": "a", "phone": "+1"},
                {"name": "b", "phone": "+2"},
            ],
        }
        result = self.proc(None, "info", event)
        assert result["items"][0]["phone"] == REDACTED_PLACEHOLDER
        assert result["items"][1]["phone"] == REDACTED_PLACEHOLDER
        assert result["items"][0]["name"] == "a"

    def test_normal_fields_not_redacted(self) -> None:
        event = {
            "event": "agent.tool_called",
            "tool_name": "save_store",
            "iteration": 2,
            "success": True,
            "arg_keys": ["name", "phone"],
            "store_id": "abc-123",
        }
        result = self.proc(None, "info", event)
        assert result["tool_name"] == "save_store"
        assert result["iteration"] == 2
        assert result["success"] is True
        assert result["arg_keys"] == ["name", "phone"]
        assert result["store_id"] == "abc-123"
