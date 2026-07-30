"""The deterministic fast path must be certain, or defer to the model."""

import pytest

from workflow_models import Candidate
from workflow_reply import ReplyIntent, interpret


def _candidates(count=3):
    return [
        Candidate(f"a-{index}", f"Item {index}", None, 1.0 + index, source_url="https://www.amazon.com/dp/x")
        for index in range(1, count + 1)
    ]


@pytest.mark.parametrize(
    "message", ["yes", "Yes!", "yeah", "yea", "yep", "sure", "ok", "okay", "yes please", "go ahead", "do it"]
)
def test_affirmatives_are_recognised(message):
    assert interpret(message, _candidates()).intent is ReplyIntent.AFFIRM


@pytest.mark.parametrize("message", ["no", "Nope.", "nah", "no thanks"])
def test_refusals_are_recognised(message):
    assert interpret(message, _candidates()).intent is ReplyIntent.DECLINE


@pytest.mark.parametrize("message", ["cancel", "Cancel.", "never mind", "forget it", "stop", "start over"])
def test_cancellations_are_recognised(message):
    assert interpret(message, _candidates()).intent is ReplyIntent.CANCEL


@pytest.mark.parametrize("message, position", [("2", 2), ("option 3", 3), ("#1", 1), ("the second one", 2)])
def test_explicit_positions_are_recognised(message, position):
    reply = interpret(message, _candidates())

    assert reply.intent is ReplyIntent.SELECT_POSITION
    assert reply.position == position


@pytest.mark.parametrize(
    "message",
    [
        "no, do you have anything cheaper",
        "yes but make it two packs",
        "ok what about the duracell",
        "cancel the second one and show me batteries",
        "what is the capital of France",
        "the duracell one",
        "",
        "   ",
    ],
)
def test_anything_ambiguous_defers_to_semantic_interpretation(message):
    """A mixed sentence is not a plain yes, no, or cancel."""
    reply = interpret(message, _candidates())

    assert reply.intent is ReplyIntent.NONE
    assert reply.is_confident is False


def test_out_of_range_position_is_not_a_confident_reply():
    assert interpret("9", _candidates(3)).intent is ReplyIntent.NONE


def test_position_words_are_safe_when_no_candidates_are_stored():
    """A clarification workflow has no candidate list to index into."""
    for message in ("the last one", "1", "option 2"):
        assert interpret(message, []).intent is ReplyIntent.NONE
