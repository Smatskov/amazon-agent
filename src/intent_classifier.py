"""Validated, hierarchical semantic interpretation with no tool access."""

from dataclasses import dataclass, field
import json
from time import perf_counter
from typing import Any, Callable

from llm_client import generate_response
from timing import RequestTiming


ROUTES = {"memory", "purchase", "workflow", "general_chat", "unknown"}
MEMORY_ACTIONS = {"remember", "recall", "forget"}
WORKFLOW_ACTIONS = {
    "select_candidate",
    "add_to_cart",
    "remove_from_cart",
    "view_cart",
    "change_quantity",
    "refine",
    "checkout",
    "confirm",
    "cancel",
}
# Actions a quantity may accompany. It is required by change_quantity and optional for
# the two cart actions; every other action must carry none.
QUANTITY_ACTIONS = {"change_quantity", "add_to_cart", "remove_from_cart"}
MIN_ACTION_CONFIDENCE = 0.65
SEMANTIC_OUTPUT_TOKENS = 256


@dataclass(frozen=True, slots=True)
class SemanticAction:
    """A validated request for agent-owned work, never an executed operation."""

    route: str
    action: str = "no_match"
    confidence: float = 0.0
    key: str | None = None
    value: str | None = None
    product_query: str | None = None
    constraints: dict[str, str | int | float | bool] = field(default_factory=dict)
    quantity: int | None = None
    classification_valid: bool = True


def _no_match() -> SemanticAction:
    return SemanticAction("unknown", classification_valid=False)


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


def _valid_constraints(value: Any) -> bool:
    """Accept only an object of scalar constraints the agent can act on safely."""
    return isinstance(value, dict) and all(
        isinstance(key, str) and isinstance(item, (str, int, float, bool))
        for key, item in value.items()
    )


def _valid_quantity(value: Any) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool) and value > 0)


# --- Prompts: compact, JSON-only, and free of conversational instructions. ---


def _router_prompt(message: str, workflow_summary: str, pending_question: str | None) -> str:
    return (
        "Return exactly one JSON object. No markdown, explanation, or reasoning. "
        "Route this message: {\"route\":\"memory|purchase|workflow|general_chat|unknown\","
        "\"confidence\":0.0}. Do not answer, use tools, or infer fields. "
        "memory=stored personal facts; purchase=starting a product request; "
        "workflow=answering an active purchase question; general_chat=ordinary conversation. "
        f"Active workflow: {workflow_summary or 'none'}. Pending question: {pending_question or 'none'}. "
        "Capabilities: memory, preview purchase workflow, general chat. "
        f"Message: {message}"
    )


def _memory_prompt(message: str) -> str:
    return (
        "Return exactly one JSON object. No markdown, explanation, or reasoning. "
        "Extract a memory request with action, key, value, confidence. "
        "action is remember, recall, forget, or no_match. Normalize the memory key to a concise "
        "lowercase user-facing concept. remember requires key and value; recall/forget require key "
        "and value null; no_match requires key and value null. Do not answer or use memory. "
        f"Message: {message}"
    )


def _purchase_prompt(message: str) -> str:
    return (
        "Return exactly one JSON object. No markdown, explanation, or reasoning. "
        "Extract a request to start a purchase workflow with action, product_query, "
        "constraints, quantity, confidence. action is purchase_start or no_match. product_query is a concise "
        "product phrase. constraints is an object containing only explicit request constraints with scalar values. "
        "quantity is a positive integer or null. For no_match use null product_query, {}, and null quantity. "
        "Do not answer, search, buy, or use tools. "
        f"Message: {message}"
    )


def _workflow_prompt(message: str, workflow_summary: str, pending_question: str | None) -> str:
    return (
        "Return exactly one JSON object. No markdown, explanation, or reasoning. "
        "Interpret a reply to the active preview purchase workflow with action, quantity, "
        "constraints, confidence. action is select_candidate, add_to_cart, remove_from_cart, "
        "view_cart, change_quantity, refine, checkout, confirm, cancel, or no_match. "
        "select_candidate=choosing an option; add_to_cart=adding it to the basket; "
        "checkout=asking to check out; confirm=approving the final order summary. "
        "quantity is a positive integer for change_quantity, optional for add_to_cart and "
        "remove_from_cart, and null otherwise. constraints is an object. "
        "Do not answer or execute anything. "
        f"Workflow: {workflow_summary}. Pending question: {pending_question or 'none'}. Message: {message}"
    )


# --- Validation: pure functions over already-parsed JSON, no timing or I/O. ---


def _route(data: dict[str, Any] | None) -> tuple[str, float] | None:
    if not data or set(data) != {"route", "confidence"}:
        return None
    route = data.get("route")
    confidence = _confidence(data.get("confidence"))
    if route not in ROUTES or confidence is None:
        return None
    return route, confidence


def _memory_action(data: dict[str, Any] | None) -> SemanticAction:
    if not data or set(data) != {"action", "key", "value", "confidence"}:
        return _no_match()
    action = data.get("action")
    key = _string(data.get("key"))
    value = _string(data.get("value"))
    confidence = _confidence(data.get("confidence"))
    if action not in MEMORY_ACTIONS | {"no_match"} or confidence is None:
        return _no_match()
    if action == "no_match":
        return SemanticAction("memory") if key is None and value is None else _no_match()
    if not key or (action == "remember" and not value) or (action != "remember" and value is not None):
        return _no_match()
    if confidence < MIN_ACTION_CONFIDENCE:
        return SemanticAction("memory")
    return SemanticAction("memory", action, confidence, key, value)


def _purchase_action(data: dict[str, Any] | None) -> SemanticAction:
    if not data or set(data) != {"action", "product_query", "constraints", "quantity", "confidence"}:
        return _no_match()
    action = data.get("action")
    product = _string(data.get("product_query"))
    constraints = data.get("constraints")
    quantity = data.get("quantity")
    confidence = _confidence(data.get("confidence"))
    if action not in {"purchase_start", "no_match"} or confidence is None:
        return _no_match()
    if not _valid_constraints(constraints) or not _valid_quantity(quantity):
        return _no_match()
    if action == "no_match":
        return (
            SemanticAction("purchase")
            if product is None and not constraints and quantity is None
            else _no_match()
        )
    if not product or confidence < MIN_ACTION_CONFIDENCE:
        return SemanticAction("purchase")
    return SemanticAction(
        "purchase", action, confidence,
        product_query=product, constraints=constraints, quantity=quantity,
    )


def _workflow_action(data: dict[str, Any] | None) -> SemanticAction:
    if not data or set(data) != {"action", "quantity", "constraints", "confidence"}:
        return _no_match()
    action = data.get("action")
    quantity = data.get("quantity")
    constraints = data.get("constraints")
    confidence = _confidence(data.get("confidence"))
    if action not in WORKFLOW_ACTIONS | {"no_match"} or confidence is None:
        return _no_match()
    if not _valid_constraints(constraints) or not _valid_quantity(quantity):
        return _no_match()
    if action == "no_match" or confidence < MIN_ACTION_CONFIDENCE:
        return SemanticAction("workflow")
    if action == "change_quantity" and quantity is None:
        return _no_match()
    if action not in QUANTITY_ACTIONS and quantity is not None:
        return _no_match()
    return SemanticAction("workflow", action, confidence, constraints=constraints, quantity=quantity)


# --- Orchestration: the only place that measures time or calls the model. ---


async def _semantic_json(prompt: str, timing: RequestTiming | None) -> str:
    return await generate_response(
        prompt,
        max_tokens=SEMANTIC_OUTPUT_TOKENS,
        temperature=0,
        json_mode=True,
        timing=timing,
    )


def _interpret(raw: str, validate: Callable[[dict[str, Any] | None], Any], timing: RequestTiming | None):
    """Parse then validate one model response, recording both stage timings once."""
    parse_started_at = perf_counter()
    data = _json_object(raw)
    if timing:
        timing.add_parse(parse_started_at)
    validation_started_at = perf_counter()
    result = validate(data)
    if timing:
        timing.add_validation(validation_started_at)
    return result


async def interpret_message(
    message: str,
    *,
    workflow_summary: str = "",
    pending_question: str | None = None,
    timing: RequestTiming | None = None,
) -> SemanticAction:
    """Route first, then ask only the needed semantic specialist for validated JSON."""
    try:
        router_prompt = _router_prompt(message, workflow_summary, pending_question)
        routed = _interpret(await _semantic_json(router_prompt, timing), _route, timing)
        if not routed:
            return _no_match()
        route, confidence = routed
        if route in {"general_chat", "unknown"} or confidence < MIN_ACTION_CONFIDENCE:
            return SemanticAction(route, confidence=confidence)

        if route == "memory":
            prompt, validate = _memory_prompt(message), _memory_action
        elif route == "purchase":
            prompt, validate = _purchase_prompt(message), _purchase_action
        else:
            prompt = _workflow_prompt(message, workflow_summary, pending_question)
            validate = _workflow_action
        return _interpret(await _semantic_json(prompt, timing), validate, timing)
    except Exception as error:
        print(f"[SEMANTIC DEBUG] interpretation failed: {error!r}")
        return _no_match()
