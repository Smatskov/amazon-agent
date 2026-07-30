"""Deterministic reading of a reply sent while a purchase workflow is active.

A short answer to a question the agent just asked is the most common message in the
conversation, the most latency-sensitive, and the one where a wrong guess is most
visible. Those replies are handled here without a model call.

This module is deliberately narrow. It recognises only unambiguous replies —
confirmation, refusal, cancellation, and an explicitly stated option number. Anything
else returns `NONE` and falls through to semantic interpretation, so a sentence like
"no, do you have anything cheaper" is never mistaken for a plain refusal.
"""

from dataclasses import dataclass
from enum import StrEnum
import re

from candidate_resolver import explicit_position
from workflow_models import Candidate


class ReplyIntent(StrEnum):
    AFFIRM = "affirm"
    DECLINE = "decline"
    CANCEL = "cancel"
    SELECT_POSITION = "select_position"
    NONE = "none"


AFFIRM_WORDS = frozenset(
    "yes yeah yea yep yup sure ok okay correct right affirmative definitely absolutely".split()
)
DECLINE_WORDS = frozenset("no nope nah negative".split())
CANCEL_WORDS = frozenset("cancel stop quit abort nevermind".split())
AFFIRM_PHRASES = frozenset({"go ahead", "do it", "sounds good", "that works", "lets do it"})
CANCEL_PHRASES = frozenset({"never mind", "forget it", "start over", "cancel that", "not anymore"})
# Politeness that carries no intent of its own and must not block a match.
FILLER_WORDS = frozenset("please thanks thank you just well hmm um then lets let".split())
# A position is only acted on without the model when the message says nothing else.
# `candidate_resolver` may be lenient because the model has already classified the
# message as a selection; here nothing has classified it yet.
POSITION_WORDS = frozenset(
    "option number no choice item the one first 1st second 2nd third 3rd fourth 4th fifth 5th last".split()
)
PUNCTUATION = re.compile(r"[^a-z0-9' ]+")
WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class WorkflowReply:
    intent: ReplyIntent = ReplyIntent.NONE
    position: int | None = None

    @property
    def is_confident(self) -> bool:
        return self.intent is not ReplyIntent.NONE


def interpret(message: str, candidates: list[Candidate]) -> WorkflowReply:
    """Return a confident reply intent, or NONE to defer to semantic interpretation."""
    normalized = WHITESPACE.sub(" ", PUNCTUATION.sub(" ", message.casefold())).strip()
    if not normalized:
        return WorkflowReply()

    if normalized in CANCEL_PHRASES:
        return WorkflowReply(ReplyIntent.CANCEL)
    if normalized in AFFIRM_PHRASES:
        return WorkflowReply(ReplyIntent.AFFIRM)

    words = [word for word in normalized.split() if word not in FILLER_WORDS]
    if not words:
        return WorkflowReply()

    position = explicit_position(normalized, len(candidates))
    if position is not None and all(
        word in POSITION_WORDS or word.isdigit() for word in words
    ):
        return WorkflowReply(ReplyIntent.SELECT_POSITION, position)

    # Every significant word must belong to one vocabulary; a mixed sentence is not a
    # plain yes, no, or cancel.
    if all(word in CANCEL_WORDS for word in words):
        return WorkflowReply(ReplyIntent.CANCEL)
    if all(word in DECLINE_WORDS for word in words):
        return WorkflowReply(ReplyIntent.DECLINE)
    if all(word in AFFIRM_WORDS for word in words):
        return WorkflowReply(ReplyIntent.AFFIRM)
    return WorkflowReply()
