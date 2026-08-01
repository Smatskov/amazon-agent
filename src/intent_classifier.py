"""Validated extraction of a memory request. The only remaining use of the model.

Shopping no longer consults a language model at all: menus, references, and searching
are deterministic, and Amazon's own search understands ordinary phrasing better than a
small local model rewrote it. What is left is natural-language memory phrasing
("remember my favourite shampoo is Dove"), which has no other source of truth.

This module never executes anything. It returns a validated request that `agent.py`
may act on, and returns no-match on any doubt: invalid JSON, missing fields, low
confidence, a model error, or a timeout.
"""

from dataclasses import dataclass
import json
from typing import Any

from llm_client import generate_response


MEMORY_ACTIONS = {"remember", "recall", "forget"}
MIN_ACTION_CONFIDENCE = 0.65
SEMANTIC_OUTPUT_TOKENS = 128
# Memory phrasing must not hold up a reply. Shopping never waits on the model at all.


@dataclass(frozen=True, slots=True)
class MemoryRequest:
    """A validated request for agent-owned work, never an executed operation."""

    action: str = "no_match"
    key: str | None = None
    value: str | None = None
    confidence: float = 0.0

    @property
    def is_actionable(self) -> bool:
        return self.action in MEMORY_ACTIONS


NO_MATCH = MemoryRequest()


def _json_object(raw: str) -> dict[str, Any] | None:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _confidence(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if 0 <= value <= 1 else None


def _string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _prompt(message: str) -> str:
    return (
        "Return exactly one JSON object. No markdown, explanation, or reasoning. "
        "Extract a memory request with action, key, value, confidence. "
        "action is remember, recall, forget, or no_match. Normalize the memory key to a concise "
        "lowercase user-facing concept. remember requires key and value; recall/forget require key "
        "and value null; no_match requires key and value null. Do not answer or use memory. "
        f"Message: {message}"
    )


def validate(data: dict[str, Any] | None) -> MemoryRequest:
    """Pure validation over already-parsed JSON. Fails closed on anything unexpected."""
    if not data or set(data) != {"action", "key", "value", "confidence"}:
        return NO_MATCH
    action = data.get("action")
    key = _string(data.get("key"))
    value = _string(data.get("value"))
    confidence = _confidence(data.get("confidence"))
    if action not in MEMORY_ACTIONS | {"no_match"} or confidence is None:
        return NO_MATCH
    if action == "no_match":
        return NO_MATCH
    if not key or (action == "remember" and not value) or (action != "remember" and value is not None):
        return NO_MATCH
    if confidence < MIN_ACTION_CONFIDENCE:
        return NO_MATCH
    return MemoryRequest(action, key, value, confidence)


async def interpret_memory(message: str) -> MemoryRequest:
    """Return a validated memory request, or no-match. Never raises."""
    try:
        raw = await generate_response(
            _prompt(message),
            max_tokens=SEMANTIC_OUTPUT_TOKENS,
            temperature=0,
            json_mode=True,
        )
    except Exception as error:  # noqa: BLE001 - an unavailable model is a no-match
        print(f"[MEMORY] extraction unavailable: {error!r}")
        return NO_MATCH
    return validate(_json_object(raw))
