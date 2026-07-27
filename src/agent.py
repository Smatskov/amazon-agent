# Coordinates the AI agent by deciding what actions to take and delegating work to other modules.

from pathlib import Path

from llm_client import generate_response
import amazon
import intent_classifier
import memory
import product_evaluator
from request_context import RequestContext


MEMORY_USAGE = (
    "Memory usage: remember: <key> = <value>; "
    "recall: <key>; forget: <key>."
)
SEARCH_USAGE = "Search usage: search: <query>."


def _memory_instruction(message: str) -> tuple[str, str, str | None] | None:
    """Parse an explicit memory command without interpreting ordinary messages."""
    text = message.strip()
    command, separator, details = text.partition(":")
    normalized_command = command.strip().lower()

    memory_commands = {"remember", "recall", "forget"}
    if normalized_command not in memory_commands:
        first_word, _, remaining_text = text.partition(" ")
        first_word = first_word.lower()
        natural_memory_prefixes = {
            "remember": ("that ",),
            "forget": ("my ",),
        }
        if any(
            remaining_text.lower().startswith(prefix)
            for prefix in natural_memory_prefixes.get(first_word, ())
        ):
            return None
        if first_word in memory_commands:
            return "invalid", "", None
        return None
    if not separator:
        return "invalid", "", None

    if normalized_command == "remember":
        key, value_separator, value = details.partition("=")
        key = key.strip()
        value = value.strip()
        if not value_separator or not key or not value:
            return "invalid", "", None
        return "remember", key, value

    key = details.strip()
    if not key:
        return "invalid", "", None
    return normalized_command, key, None


def _search_query(message: str) -> str | None:
    """Return an explicit Amazon query without interpreting ordinary messages."""
    text = message.strip()
    command, separator, details = text.partition(":")
    if command.strip().lower() != "search":
        return None
    if not separator:
        return ""
    return details.strip()


async def agent_brain(
    message: str, memory_database_path: str | Path = memory.DEFAULT_DATABASE_PATH
) -> str:
    """Coordinate a complete model response without exposing LM Studio to Telegram."""
    instruction = _memory_instruction(message)

    if instruction:
        command, key, value = instruction
        if command == "invalid":
            return MEMORY_USAGE
        if command == "remember":
            memory.remember(key, value, memory_database_path)
            return f"Remembered '{key}'."
        if command == "recall":
            value = memory.recall(key, memory_database_path)
            if value is None:
                return f"Nothing is stored for '{key}'."
            return f"Memory for '{key}': {value}"
        memory.forget(key, memory_database_path)
        return f"Forgot '{key}'."

    search_query = _search_query(message)
    if search_query is not None:
        if not search_query:
            return SEARCH_USAGE
        return await _search_and_evaluate(message, search_query, "amazon_search", 1.0)

    intent_result = await intent_classifier.classify_intent(message)
    if intent_result.intent == "memory_remember":
        key = intent_result.entities.get("key")
        value = intent_result.entities.get("value")
        if not key or not value:
            return MEMORY_USAGE
        memory.remember(key, value, memory_database_path)
        return f"Remembered '{key}'."
    if intent_result.intent == "memory_recall":
        key = intent_result.entities.get("key")
        if not key:
            return MEMORY_USAGE
        value = memory.recall(key, memory_database_path)
        return f"Memory for '{key}': {value}" if value else f"Nothing is stored for '{key}'."
    if intent_result.intent == "memory_forget":
        key = intent_result.entities.get("key")
        if not key:
            return MEMORY_USAGE
        memory.forget(key, memory_database_path)
        return f"Forgot '{key}'."
    if intent_result.intent == "amazon_search":
        query = (
            intent_result.extracted_search_query
            or intent_result.entities.get("search_query")
        )
        if not query:
            return SEARCH_USAGE
        return await _search_and_evaluate(
            message, query, intent_result.intent, intent_result.confidence
        )
    if intent_result.intent == "amazon_reorder":
        return "Reordering is not available yet because order history is not implemented."
    if intent_result.intent == "amazon_buy":
        return "Buying is not available yet. I can help you search for products instead."

    try:
        return await generate_response(message)
    except Exception as error:
        print(f"LM Studio error: {error}")
        return (
            "I couldn't reach the local AI model. "
            "Make sure LM Studio and its server are running."
        )


async def _search_and_evaluate(
    message: str, search_query: str, intent: str, confidence: float
) -> str:
    """Execute the existing read-only search flow after agent-owned routing."""
    context = RequestContext(
        original_user_request=message,
        intent=intent,
        search_query=search_query,
        confidence=confidence,
    )
    try:
        products = await amazon.search_products(search_query)
    except Exception as error:
        print(f"Amazon search error: {error}")
        return "I couldn't search Amazon right now. Please try again later."
    try:
        evaluation = await product_evaluator.evaluate_products(context, products)
        # TODO: Add an explicit, confirmed reorder workflow for reorder metadata.
        # TODO: Check duplicate-order safeguards before any future financial action.
        return evaluation.recommendation
    except Exception as error:
        print(f"Product evaluation error: {error}")
        return (
            "I couldn't evaluate the Amazon search results right now. "
            "Make sure LM Studio and its server are running."
        )
