from __future__ import annotations

import pytest

from store_assistant.agent.intents import (
    detect_off_scope_utterance,
    detect_termination_utterance,
)


class TestDetectTermination:
    @pytest.mark.parametrize(
        "text",
        [
            "I'm done",
            "im done",
            "I am done",
            "I am done thanks",
            "we're done",
            "all done",
            "I'm good",
            "I am good thanks",
            "that's it",
            "that's all for now",
            "that is all",
            "nothing else",
            "no more stores",
            "no more records",
            "Goodbye",
            "thanks bye",
            "thank you, bye",
            "End this conversation",
            "end conversation",
            "quit",
            "exit",
            "bye",
            "BYE!",
        ],
    )
    def test_matches_common_completion_phrases(self, text: str) -> None:
        assert detect_termination_utterance(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "save Joe's Grocery",
            "what's the phone for Joe's?",
            "thanks for your help",
            "great",
            "ok",
            "",
            None,
        ],
    )
    def test_does_not_match_non_termination(self, text: str | None) -> None:
        assert detect_termination_utterance(text) is False


class TestDetectOffScope:
    @pytest.mark.parametrize(
        "text",
        ["What's the weather?", "Tell me a joke", "Sing me a song"],
    )
    def test_matches_off_topic(self, text: str) -> None:
        assert detect_off_scope_utterance(text) is True

    @pytest.mark.parametrize(
        "text",
        ["Save Joe's Grocery", "Look up Bob's Bakery", "I'm done"],
    )
    def test_does_not_match_on_topic(self, text: str) -> None:
        assert detect_off_scope_utterance(text) is False
