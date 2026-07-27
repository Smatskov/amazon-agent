"""Classify user goals without executing tools or accessing application state."""

from dataclasses import dataclass
import json
from typing import Any

from llm_client import generate_response


SUPPORTED_INTENTS = {
    "general_chat",
    "memory_remember",
    "memory_recall",
    "memory_forget",
    "amazon_search",
    "amazon_reorder",
    "amazon_buy",
    "unknown",
}
MIN_ACTION_CONFIDENCE = 0.6


@dataclass(frozen=True, slots=True)
class IntentResult:
    """Validated intent metadata that agent.py may use for routing only."""

    intent: str
    confidence: float
    entities: dict[str, str]
    reasoning: str | None = None
    requires_confirmation: bool = False
    extracted_product_name: str | None = None
    extracted_search_query: str | None = None


def _general_chat_result() -> IntentResult:
    return IntentResult("general_chat", 0.0, {})


def _classification_prompt(message: str) -> str:
    """Request one schema-bound JSON object, never an answer or tool action."""
    return (
        "Classify the user's intent. Do not answer the user, recommend products, access "
        "memory, access Amazon, execute tools, or describe actions. Return exactly one "
        "JSON object and no markdown or extra text with these fields: intent, confidence, "
        "entities, reasoning, requires_confirmation, extracted_product_name, and "
        "extracted_search_query. intent must be one of: general_chat, memory_remember, "
        "memory_recall, memory_forget, amazon_search, amazon_reorder, amazon_buy, unknown. "
        "confidence must be a number from 0 to 1. entities must be an object with string "
        "values only; use key and value for memory intents when identifiable. Set "
        "requires_confirmation true for amazon_buy and amazon_reorder. Use null for "
        "unavailable optional string fields. User message: "
        f"{message}"
    )


def _optional_string(value: Any) -> str | None:
    return value if value is None or isinstance(value, str) else None


def _validated_result(raw_result: str) -> IntentResult:
    """Convert strict JSON to a safe routing result, or fall back to general chat."""
    try:
        data = json.loads(raw_result)
    except (json.JSONDecodeError, TypeError):
        return _general_chat_result()

    if not isinstance(data, dict):
        return _general_chat_result()

    intent = data.get("intent")
    confidence = data.get("confidence")
    entities = data.get("entities")
    requires_confirmation = data.get("requires_confirmation", False)
    reasoning = _optional_string(data.get("reasoning"))
    product_name = _optional_string(data.get("extracted_product_name"))
    search_query = _optional_string(data.get("extracted_search_query"))

    if (
        intent not in SUPPORTED_INTENTS
        or not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not 0 <= confidence <= 1
        or not isinstance(entities, dict)
        or not all(isinstance(key, str) and isinstance(value, str) for key, value in entities.items())
        or not isinstance(requires_confirmation, bool)
        or reasoning is None and data.get("reasoning") is not None
        or product_name is None and data.get("extracted_product_name") is not None
        or search_query is None and data.get("extracted_search_query") is not None
    ):
        return _general_chat_result()

    if intent not in {"general_chat", "unknown"} and confidence < MIN_ACTION_CONFIDENCE:
        return IntentResult("unknown", float(confidence), entities)

    return IntentResult(
        intent=intent,
        confidence=float(confidence),
        entities=entities,
        reasoning=reasoning,
        requires_confirmation=requires_confirmation,
        extracted_product_name=product_name,
        extracted_search_query=search_query,
    )


async def classify_intent(message: str) -> IntentResult:
    """Classify a message through LM Studio and safely validate the JSON response."""
    try:
        response = await generate_response(_classification_prompt(message))
    except Exception:
        return _general_chat_result()
    return _validated_result(response)
