# Coordinates the AI agent by deciding what actions to take and delegating work to other modules.

import asyncio
from pathlib import Path
import re
from time import perf_counter

from llm_client import generate_response
import amazon
import candidate_resolver
import intent_classifier
import memory
import product_display
import product_evaluator
import ranking
from request_context import RequestContext
from response_policy import (
    GENERAL_RESPONSE_MAX_TOKENS,
    PURCHASING_AGENT_SYSTEM_PROMPT,
    normalize_general_response,
)
from timing import RequestTiming
import workflow_reply
import workflow_store
from workflow_models import Candidate, PurchaseWorkflow, WorkflowState


MEMORY_USAGE = (
    "Memory usage: remember: <key> = <value>; "
    "recall: <key>; forget: <key>."
)
SEARCH_USAGE = "Search usage: search: <query>."
SEMANTIC_SOFT_TIMEOUT_SECONDS = 20.0
SEMANTIC_HARD_TIMEOUT_SECONDS = 120.0
MEMORY_CLASSIFICATION_FAILURE = "I couldn't understand that memory request safely."
LOCAL_MODEL_FAILURE = (
    "I couldn't reach the local AI model. "
    "Make sure LM Studio and its server are running."
)
SHOPPING_FALLBACK_MARKERS = (
    "buy",
    "find",
    "search",
    "shop",
    "cheap",
    "cheapest",
    "best option",
    "best options",
    "price",
    "prices",
    "deal",
    "deals",
)
# Whole-word matching only: substring matching made "research" and "idealism"
# look like shopping requests.
_SHOPPING_MARKER_PATTERN = re.compile(
    r"\b(?:%s)\b" % "|".join(re.escape(marker) for marker in SHOPPING_FALLBACK_MARKERS)
)


def _memory_response(
    action: str,
    key: str | None,
    value: str | None,
    memory_database_path: str | Path,
) -> str:
    """Execute only a validated memory action; parsing and classification stay side-effect free."""
    if action == "remember":
        if not key or not value:
            return MEMORY_CLASSIFICATION_FAILURE
        memory.remember(key, value, memory_database_path)
        return f"Remembered '{key}'."
    if action == "recall":
        if not key:
            return MEMORY_CLASSIFICATION_FAILURE
        stored_value = memory.recall(key, memory_database_path)
        return (
            f"Memory for '{key}': {stored_value}"
            if stored_value
            else f"Nothing is stored for '{key}'."
        )
    if action == "forget":
        if not key:
            return MEMORY_CLASSIFICATION_FAILURE
        memory.forget(key, memory_database_path)
        return f"Forgot '{key}'."
    return MEMORY_CLASSIFICATION_FAILURE


def _memory_instruction(message: str) -> tuple[str, str, str | None] | None:
    """Parse an explicit memory command without interpreting ordinary messages."""
    text = message.strip()
    command, separator, details = text.partition(":")
    normalized_command = command.strip().lower()

    if normalized_command not in {"remember", "recall", "forget"}:
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


def _looks_like_shopping_request(message: str) -> bool:
    """Block semantic no-matches from becoming ungrounded shopping advice."""
    return bool(_SHOPPING_MARKER_PATTERN.search(message.casefold()))


CLARIFICATION_QUESTION = "Which product should I search for on Amazon?"


def _ask_what_to_search(
    telegram_user_id: int, message: str, workflow_database_path: str | Path
) -> str:
    """Ask what to search for, and persist the question so the answer has meaning.

    Previously the agent asked a clarifying question and stored nothing, so the reply
    arrived with no context and was treated as an unrelated message.
    """
    workflow = PurchaseWorkflow.new(telegram_user_id, message, "")
    workflow_store.transition(
        workflow,
        WorkflowState.AWAITING_REQUEST_CLARIFICATION,
        pending_question=CLARIFICATION_QUESTION,
    )
    workflow_store.save_workflow(workflow, workflow_database_path)
    return f"I can search Amazon for you. {CLARIFICATION_QUESTION}"


def _is_awaiting_clarification(workflow: PurchaseWorkflow | None) -> bool:
    return bool(workflow) and workflow.state == WorkflowState.AWAITING_REQUEST_CLARIFICATION


def _reask_pending_question(workflow: PurchaseWorkflow) -> str:
    """Repeat the outstanding question instead of answering something else."""
    if workflow.candidates:
        return (
            f"I'm still on your search for '{workflow.normalized_product_goal}'.\n\n"
            f"{product_display.next_step_hint(workflow.candidates)}"
        )
    return workflow.pending_question or CLARIFICATION_QUESTION


def _apply_workflow_reply(
    workflow: PurchaseWorkflow,
    reply: workflow_reply.WorkflowReply,
    workflow_database_path: str | Path,
) -> str:
    """Execute an unambiguous reply without waiting for the local model."""
    if reply.intent is workflow_reply.ReplyIntent.CANCEL:
        return _cancel_workflow(workflow, workflow_database_path)

    if _is_awaiting_clarification(workflow):
        # A bare yes or no is not a product name, so the question still stands.
        if reply.intent is workflow_reply.ReplyIntent.DECLINE:
            return _cancel_workflow(workflow, workflow_database_path)
        return CLARIFICATION_QUESTION

    if reply.intent is workflow_reply.ReplyIntent.SELECT_POSITION:
        return _select_candidate(
            workflow, workflow.candidates[reply.position - 1], workflow_database_path
        )
    if reply.intent is workflow_reply.ReplyIntent.AFFIRM:
        if len(workflow.candidates) == 1:
            return _select_candidate(workflow, workflow.candidates[0], workflow_database_path)
        return _reask_pending_question(workflow)
    return (
        "No problem — tell me what to look for instead, or say 'cancel' to stop this search."
    )


def _refine_candidates(
    workflow: PurchaseWorkflow,
    action: intent_classifier.SemanticAction,
    message: str,
    workflow_database_path: str | Path,
) -> str:
    """Re-filter and re-order the results already retrieved, without a new search.

    A refinement is a normal conversational move ("only the Prime ones", "cheaper"),
    so it must narrow the list in place rather than telling the user to start over.
    """
    merged = {**workflow.constraints, **action.constraints}
    outcome = ranking.apply_constraints(workflow.candidates, merged)
    if not outcome.kept:
        # Never persist a constraint that leaves nothing; the user would be stranded.
        return (
            "None of the results I already have meet that. Say 'cancel' to search "
            "again with a different description, or pick from the current options."
        )

    preference = ranking.requested_sort(message)
    if preference is ranking.SortPreference.RELEVANCE:
        ranked = ranking.RankedCandidates(outcome.kept, ranking.PREVIOUS_ORDER)
    else:
        ranked = ranking.rank(outcome.kept, preference)

    workflow.constraints = merged | {"latest_refinement": message}
    workflow.candidates = ranked.candidates
    workflow_store.transition(
        workflow,
        WorkflowState.AWAITING_PRODUCT_SELECTION,
        pending_question="Which candidate would you like?",
    )
    workflow_store.save_workflow(workflow, workflow_database_path)
    return product_display.present_candidates(
        workflow.normalized_product_goal,
        ranked,
        removed=outcome.removed,
        removal_reasons=outcome.reasons,
        refined=True,
    )


def _cancel_workflow(workflow: PurchaseWorkflow, workflow_database_path: str | Path) -> str:
    workflow_store.transition(workflow, WorkflowState.CANCELLED)
    workflow_store.save_workflow(workflow, workflow_database_path)
    return "Cancelled the current purchase workflow."


def _select_candidate(
    workflow: PurchaseWorkflow, candidate: Candidate, workflow_database_path: str | Path
) -> str:
    workflow.selected_candidate_id = candidate.candidate_id
    workflow_store.transition(
        workflow,
        WorkflowState.PAUSED,
        pending_question="Checkout preview is not implemented yet.",
    )
    workflow_store.save_workflow(workflow, workflow_database_path)
    return (
        f"Selected {product_display.display_title(candidate.title)} "
        f"(quantity {workflow.quantity}).\n\n"
        "Cart, checkout preview, and ordering are intentionally not implemented yet, "
        "so nothing has been bought. Say 'cancel' when you want to start something else."
    )


async def _general_response(message: str, workflow: PurchaseWorkflow | None = None) -> str:
    """Generate bounded conversation, supplying the options on screen as facts."""
    prompt = message
    if workflow and workflow.candidates:
        prompt = (
            f"{message}\n\n"
            "Context — the numbered Amazon results currently shown to this user. Answer "
            "from these facts when the question is about them, and never add facts that "
            "are not listed:\n"
            f"{product_evaluator.candidate_context(workflow.candidates)}"
        )
    response = await generate_response(
        prompt,
        max_tokens=GENERAL_RESPONSE_MAX_TOKENS,
        system_prompt=PURCHASING_AGENT_SYSTEM_PROMPT,
    )
    return normalize_general_response(response)


async def agent_brain(
    message: str,
    memory_database_path: str | Path | None = None,
    workflow_database_path: str | Path | None = None,
    telegram_user_id: int = 0,
) -> str:
    """Coordinate a complete model response without exposing LM Studio to Telegram."""
    timing = RequestTiming.start()
    # Storage locations are resolved per call, not bound at import, so tests and
    # future deployments can redirect them without rewriting the entry point.
    if memory_database_path is None:
        memory_database_path = memory.DEFAULT_DATABASE_PATH
    if workflow_database_path is None:
        workflow_database_path = (
            workflow_store.DEFAULT_WORKFLOW_DATABASE_PATH
            if Path(memory_database_path) == Path(memory.DEFAULT_DATABASE_PATH)
            else Path(memory_database_path).parent / "workflows.db"
        )
    instruction = _memory_instruction(message)

    if instruction:
        command, key, value = instruction
        if command == "invalid":
            return MEMORY_USAGE
        return _memory_response(command, key, value, memory_database_path)

    search_query = _search_query(message)
    if search_query is not None:
        if not search_query:
            return SEARCH_USAGE
        return await _search_and_evaluate(message, search_query)

    active_workflow = workflow_store.get_active_workflow(
        telegram_user_id, workflow_database_path
    )
    if active_workflow and _is_legacy_mock_workflow(active_workflow):
        workflow_store.transition(active_workflow, WorkflowState.FAILED)
        workflow_store.save_workflow(active_workflow, workflow_database_path)
        print("[WORKFLOW] discarded legacy candidate state without Amazon source URLs")
        active_workflow = None

    # An unambiguous answer to a question the agent just asked is handled here, before
    # any model call, so "3" or "cancel" is instant and cannot be misrouted.
    if active_workflow:
        reply = workflow_reply.interpret(message, active_workflow.candidates)
        if reply.is_confident:
            print(f"[ROUTING] deterministic workflow reply intent={reply.intent}")
            return _apply_workflow_reply(active_workflow, reply, workflow_database_path)

    timing.mark_prepare_complete()
    semantic_task = asyncio.create_task(
        intent_classifier.interpret_message(
            message,
            workflow_summary=_workflow_summary(active_workflow),
            pending_question=active_workflow.pending_question if active_workflow else None,
            timing=timing,
        )
    )
    try:
        semantic_action = await asyncio.wait_for(asyncio.shield(semantic_task), timeout=SEMANTIC_SOFT_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        print("[TIMING] semantic interpretation exceeded soft timeout (20s)")
        try:
            semantic_action = await asyncio.wait_for(
                semantic_task,
                timeout=SEMANTIC_HARD_TIMEOUT_SECONDS - SEMANTIC_SOFT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            semantic_task.cancel()
            await asyncio.gather(semantic_task, return_exceptions=True)
            print("[SEMANTIC DEBUG] interpretation reached hard timeout (120s)")
            timing.log()
            return LOCAL_MODEL_FAILURE
    except asyncio.CancelledError:
        semantic_task.cancel()
        await asyncio.gather(semantic_task, return_exceptions=True)
        raise

    try:
        action_started_at = perf_counter()
        print(
            "[ROUTING] "
            f"route={semantic_action.route} action={semantic_action.action} "
            f"confidence={semantic_action.confidence:.2f} "
            f"classification_valid={semantic_action.classification_valid}"
        )
        if semantic_action.route == "memory" and semantic_action.action in {"remember", "recall", "forget"}:
            return _memory_response(semantic_action.action, semantic_action.key, semantic_action.value, memory_database_path)
        if semantic_action.route == "purchase" and semantic_action.action == "purchase_start":
            # A pending clarification is answered by this request, not blocked by it.
            if active_workflow and not _is_awaiting_clarification(active_workflow):
                return f"You already have an active purchase workflow for '{active_workflow.normalized_product_goal}'. Say 'cancel' before starting another one."
            return await _start_purchase_workflow(
                telegram_user_id,
                message,
                semantic_action.product_query or "product",
                workflow_database_path,
                constraints=semantic_action.constraints,
                quantity=semantic_action.quantity,
                existing=active_workflow,
            )
        if active_workflow and semantic_action.route == "workflow" and semantic_action.action != "no_match":
            return _continue_purchase_workflow(active_workflow, semantic_action, message, workflow_database_path)
        if _is_awaiting_clarification(active_workflow) and semantic_action.route != "general_chat":
            # The agent asked what to search for, so this reply is the answer.
            return await _start_purchase_workflow(
                telegram_user_id,
                message,
                message.strip(),
                workflow_database_path,
                existing=active_workflow,
            )
        if (
            not active_workflow
            and semantic_action.route in {"purchase", "unknown"}
            and _looks_like_shopping_request(message)
        ):
            return _ask_what_to_search(telegram_user_id, message, workflow_database_path)
        if active_workflow and semantic_action.route in {"workflow", "unknown"}:
            # A pending question deserves the question again, not an unrelated answer.
            return _reask_pending_question(active_workflow)
        return await _general_response(message, active_workflow)
    except Exception as error:
        print(f"LM Studio error: {error}")
        return LOCAL_MODEL_FAILURE
    finally:
        timing.add_action(action_started_at)
        timing.log()


def _price_amount(price_text: str | None) -> float | None:
    """Convert one displayed Amazon price to a sortable amount without inventing one."""
    if not price_text:
        return None
    match = re.search(r"\d[\d,]*(?:\.\d{1,2})?", price_text)
    if not match:
        return None
    try:
        return float(match.group().replace(",", ""))
    except ValueError:
        return None


def _candidates_from_products(products: list[amazon.Product]) -> list[Candidate]:
    """Persist only facts returned by the isolated read-only Amazon boundary."""
    return [
        Candidate(
            candidate_id=f"amazon-result-{index}",
            title=product.title,
            brand=None,
            price=_price_amount(product.price),
            rating=product.rating,
            price_text=product.price,
            review_count=product.review_count,
            prime_eligible=product.prime_eligible,
            source_url=product.url,
        )
        for index, product in enumerate(products, start=1)
    ]


async def _start_purchase_workflow(
    telegram_user_id: int,
    message: str,
    goal: str,
    workflow_database_path: str | Path,
    *,
    constraints: dict[str, str | int | float | bool] | None = None,
    quantity: int | None = None,
    existing: PurchaseWorkflow | None = None,
) -> str:
    """Search Amazon read-only before persisting selectable product candidates."""
    try:
        products = await amazon.search_products(goal)
    except amazon.AmazonSearchUnavailable:
        return (
            "Amazon did not return usable search results right now. "
            "I have not started a purchase workflow; please try again later."
        )
    except Exception as error:
        print(f"Amazon search error: {error}")
        return (
            "I couldn't search Amazon right now. "
            "I have not started a purchase workflow; please try again later."
        )

    candidates = _candidates_from_products(products)
    if not candidates:
        return (
            f"I couldn't find visible Amazon results for '{goal}'. "
            "I have not started a purchase workflow. Try a broader description?"
        )

    outcome = ranking.apply_constraints(candidates, constraints)
    if not outcome.kept:
        return (
            f"Every Amazon result for '{goal}' failed your requirements "
            f"({', '.join(outcome.reasons)}). I have not started a purchase workflow. "
            "Want to relax a requirement?"
        )
    ranked = ranking.rank(outcome.kept, ranking.requested_sort(message))

    workflow = existing or PurchaseWorkflow.new(telegram_user_id, message, goal)
    workflow.normalized_product_goal = goal
    workflow.constraints = constraints or {}
    if quantity:
        workflow.quantity = quantity
    # Stored in displayed order so a reply of "3" always means the third line shown.
    workflow.candidates = ranked.candidates
    workflow_store.transition(
        workflow,
        WorkflowState.AWAITING_PRODUCT_SELECTION,
        pending_question="Which candidate would you like?",
    )
    workflow_store.save_workflow(workflow, workflow_database_path)
    return product_display.present_candidates(
        goal, ranked, removed=outcome.removed, removal_reasons=outcome.reasons
    )


def _continue_purchase_workflow(
    workflow: PurchaseWorkflow,
    action: intent_classifier.SemanticAction,
    message: str,
    workflow_database_path: str | Path,
) -> str:
    """Interpret stored workflow context without cart, checkout, or purchase actions."""
    if action.action == "cancel":
        return _cancel_workflow(workflow, workflow_database_path)
    if action.action == "change_quantity":
        if not action.quantity or action.quantity < 1:
            return "Please provide a quantity of at least one."
        workflow.quantity = action.quantity
        workflow_store.save_workflow(workflow, workflow_database_path)
        return f"Updated the workflow quantity to {workflow.quantity}."
    if action.action == "refine":
        return _refine_candidates(workflow, action, message, workflow_database_path)
    if action.action == "confirm":
        return "Checkout confirmation is not available yet; no cart or order action has been started."

    resolution = candidate_resolver.resolve_candidate_reference(message, workflow.candidates)
    if not resolution.candidate:
        return resolution.message or "Please choose one of the presented options."
    return _select_candidate(workflow, resolution.candidate, workflow_database_path)


def _workflow_summary(workflow: PurchaseWorkflow | None) -> str:
    """Expose only compact, non-sensitive workflow facts to semantic interpretation."""
    if not workflow:
        return ""
    candidates = ", ".join(candidate.title for candidate in workflow.candidates)
    return (
        f"goal={workflow.normalized_product_goal}; state={workflow.state}; "
        f"quantity={workflow.quantity}; candidates={candidates}"
    )


def _is_legacy_mock_workflow(workflow: PurchaseWorkflow) -> bool:
    """Prevent old fabricated candidate records from remaining selectable after upgrade."""
    return bool(workflow.candidates) and all(
        not candidate.source_url for candidate in workflow.candidates
    )


async def _search_and_evaluate(message: str, search_query: str) -> str:
    """Execute the explicit `search:` alias flow after agent-owned routing."""
    context = RequestContext(
        original_user_request=message,
        intent="amazon_search",
        search_query=search_query,
        confidence=1.0,
    )
    try:
        products = await amazon.search_products(search_query)
    except amazon.AmazonSearchUnavailable:
        return "Amazon did not return usable search results right now. Please try again later."
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
