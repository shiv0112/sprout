"""Unit tests for the trivial-message fast path in chat_backend.main.

The fast path bypasses the multi-agent planner for messages like "hi"
or "thanks" so the user gets a direct response with zero LLM ceremony.
Pin the matching rules so a future "improvement" can't accidentally
let trivial chitchat slip back into the planner pipeline (which costs
real Mistral tokens and produces noisy "Plan: GreetingAgent" UI events).
"""

from __future__ import annotations

import pytest

from kiln_chat_backend.main import _trivial_response


@pytest.mark.parametrize(
    "user_input",
    [
        "hi",
        "Hi",
        "HI",
        "hi!",
        "hi.",
        "  hi  ",
        "hello",
        "hey",
        "thanks",
        "thank you",
        "ok",
        "ping",
    ],
)
def test_known_trivial_messages_get_canned_response(user_input: str) -> None:
    response = _trivial_response(user_input)
    assert response is not None, f"Expected canned response for {user_input!r}"
    assert len(response) > 0


def test_punctuation_and_case_are_normalised() -> None:
    """Variants like 'HI!!!', 'Hi.', '  hi  ' all hit the same canned reply."""
    base = _trivial_response("hi")
    assert base is not None
    for variant in ("HI!!!", "Hi.", "  hi  ", "hi?"):
        assert _trivial_response(variant) == base


def test_long_messages_starting_with_hi_go_to_planner() -> None:
    """A 30+ char message starting with 'hi' must go through the real planner.

    Otherwise 'hi can you fetch the weather in Tokyo for me right now' would
    short-circuit to the canned greeting, which would be very wrong.
    """
    long_msg = "hi can you fetch the weather in Tokyo for me right now please"
    assert _trivial_response(long_msg) is None


def test_unknown_short_messages_return_none() -> None:
    """Random short input that isn't a known greeting still goes to the planner."""
    assert _trivial_response("blorp") is None
    assert _trivial_response("foo bar") is None


def test_empty_string_returns_none() -> None:
    assert _trivial_response("") is None
    assert _trivial_response("   ") is None


def test_ping_returns_pong() -> None:
    """Liveness probe sanity check."""
    assert _trivial_response("ping") == "pong"
