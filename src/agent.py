# Coordinates the AI agent by deciding what actions to take and delegating work to other modules.

import asyncio
import hashlib
from pathlib import Path
import re
from time import perf_counter

from llm_client import generate_response
import amazon
import candidate_resolver
import cart
import checkout
import flow
import intent_classifier
import memory
import menu
from menu import MenuAction
import product_display
import product_evaluator
import ranking
from request_context import RequestContext
import request_mode
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
        # A bare "remember" is ordinary English, not a malformed developer command.
        return None

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
        # A bare "search" is a request, not the colon alias. Returning usage text here
        # made the agent answer an ordinary word with developer documentation.
        return None
    return details.strip()


DELIVERY_QUESTION = re.compile(
    r"\b(?:how long|when will|when would|when do|arrive|arrives|arrival|delivery|"
    r"delivered|deliver|get here|ship by|shipping time)\b"
)


def _asks_about_delivery(message: str) -> bool:
    return bool(DELIVERY_QUESTION.search(message.casefold()))


def _looks_like_shopping_request(message: str) -> bool:
    """Block semantic no-matches from becoming ungrounded shopping advice."""
    return bool(_SHOPPING_MARKER_PATTERN.search(message.casefold()))


CLARIFICATION_QUESTION = "Which product should I search for on Amazon?"
RESET_COMMAND = re.compile(
    r"^(?:/)?(?:reset|start over|start again|clear|clear (?:my )?list|"
    r"forget everything|new search|wipe)[.!]?$"
)


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


def _show_cart(workflow: PurchaseWorkflow, workflow_database_path: str | Path) -> str:
    options = flow.store(workflow, flow.cart_menu(workflow))
    workflow_store.save_workflow(workflow, workflow_database_path)
    return product_display.present_cart(workflow.cart, cart.subtotal(workflow.cart), options)


def _show_results(
    workflow: PurchaseWorkflow,
    ranked,
    workflow_database_path: str | Path,
    *,
    removed: int = 0,
    refined: bool = False,
) -> str:
    picks, actions = flow.results_menu(workflow, ranked.candidates)
    flow.store(workflow, picks + actions)
    workflow_store.transition(
        workflow, WorkflowState.AWAITING_PRODUCT_SELECTION, pending_question="Which one?"
    )
    workflow_store.save_workflow(workflow, workflow_database_path)
    return product_display.present_results(
        workflow.normalized_product_goal, ranked, actions, removed=removed, refined=refined
    )


async def _execute_menu_choice(
    workflow: PurchaseWorkflow,
    option: menu.MenuOption,
    workflow_database_path: str | Path,
    telegram_user_id: int,
) -> str:
    """Run exactly what the user picked. No model, no guessing."""
    action = option.action
    if action is MenuAction.CANCEL:
        return _cancel_workflow(workflow, workflow_database_path)
    if action is MenuAction.VIEW_LIST:
        return _show_cart(workflow, workflow_database_path)
    if action is MenuAction.CHECKOUT:
        return _begin_checkout(workflow, workflow_database_path)
    if action is MenuAction.CONFIRM:
        return await _confirm_order(workflow, workflow_database_path)
    if action is MenuAction.SELECT:
        candidate = next(
            (c for c in workflow.candidates if c.candidate_id == option.payload), None
        )
        if candidate is None:
            return _show_cart(workflow, workflow_database_path)
        return _select_candidate(workflow, candidate, workflow_database_path)
    if action is MenuAction.REMOVE:
        if option.payload:
            workflow.cart = cart.remove(workflow.cart, option.payload)
            workflow.confirmed_token = None
            workflow_store.transition(
                workflow, WorkflowState.PREPARING_CART, pending_question="Anything else?"
            )
            return _show_cart(workflow, workflow_database_path)
        options = flow.store(workflow, flow.remove_menu(workflow))
        workflow_store.save_workflow(workflow, workflow_database_path)
        return "Which item should I remove?\n\n" + flow.render_only(options, "Choose:")
    if action is MenuAction.SHOW_OPTIONS:
        ranked = ranking.RankedCandidates(workflow.candidates, ranking.PREVIOUS_ORDER)
        return _show_results(workflow, ranked, workflow_database_path)
    if action is MenuAction.NARROW:
        flow.store(workflow, [])
        workflow_store.transition(
            workflow, WorkflowState.REFINING_SEARCH, pending_question="Narrow how?"
        )
        workflow_store.save_workflow(workflow, workflow_database_path)
        return (
            "What should I narrow by? Type a budget like <b>under $20</b>, a brand, "
            "or anything else to match."
        )
    # NEW_SEARCH and KEEP_SHOPPING both mean: tell me what to look for next.
    flow.store(workflow, [])
    workflow_store.transition(
        workflow,
        WorkflowState.AWAITING_REQUEST_CLARIFICATION,
        pending_question=CLARIFICATION_QUESTION,
    )
    workflow_store.save_workflow(workflow, workflow_database_path)
    return "What should I look for?"


def _reask_pending_question(workflow: PurchaseWorkflow, workflow_database_path: str | Path) -> str:
    """Repeat the outstanding question instead of answering something else."""
    if workflow.state == WorkflowState.AWAITING_CHECKOUT_CONFIRMATION:
        return "I'm waiting on your confirmation of the order summary. Reply 'confirm' or 'cancel'."
    if workflow.state == WorkflowState.PREPARING_CART and workflow.cart:
        return (
            _show_cart(workflow, workflow_database_path)
        )
    if workflow.candidates:
        return (
            f"I'm still on your search for '{product_display.text(workflow.normalized_product_goal)}'.\n\n"
            + flow.render_only(workflow.pending_menu, "Choose:")
        )
    return workflow.pending_question or CLARIFICATION_QUESTION


async def _apply_workflow_reply(
    workflow: PurchaseWorkflow,
    reply: workflow_reply.WorkflowReply,
    workflow_database_path: str | Path,
    message: str = "",
) -> str:
    """Execute an unambiguous reply without waiting for the local model."""
    if reply.intent is workflow_reply.ReplyIntent.CANCEL:
        return _cancel_workflow(workflow, workflow_database_path)
    # Order and checkout phrasing is answered by the gate itself, never by the model.
    if reply.intent is workflow_reply.ReplyIntent.CONFIRM_ORDER:
        return await _confirm_order(workflow, workflow_database_path)
    if reply.intent is workflow_reply.ReplyIntent.CHECKOUT:
        return _begin_checkout(workflow, workflow_database_path)
    if reply.intent is workflow_reply.ReplyIntent.COMPARE:
        return _resolve_or_research(workflow, message, workflow_database_path)
    if reply.intent is workflow_reply.ReplyIntent.SHOW_OPTIONS:
        ranked = ranking.RankedCandidates(workflow.candidates, ranking.PREVIOUS_ORDER)
        return _show_results(workflow, ranked, workflow_database_path)

    if _is_awaiting_clarification(workflow):
        # A bare yes or no is not a product name, so the question still stands.
        if reply.intent is workflow_reply.ReplyIntent.DECLINE:
            return _cancel_workflow(workflow, workflow_database_path)
        return CLARIFICATION_QUESTION

    if workflow.state == WorkflowState.AWAITING_CHECKOUT_CONFIRMATION:
        # "yes" here means approving an order, so it must reach the gate itself.
        if reply.intent is workflow_reply.ReplyIntent.AFFIRM:
            return await _confirm_order(workflow, workflow_database_path)
        if reply.intent is workflow_reply.ReplyIntent.DECLINE:
            workflow_store.transition(
                workflow,
                WorkflowState.PREPARING_CART,
                pending_question="Add anything else, or check out?",
            )
            workflow_store.save_workflow(workflow, workflow_database_path)
            return (
                "Not confirmed — nothing was ordered.\n\n"
                f"{_show_cart(workflow, workflow_database_path)}"
            )

    if reply.intent is workflow_reply.ReplyIntent.SELECT_POSITION:
        return _select_candidate(
            workflow, workflow.candidates[reply.position - 1], workflow_database_path
        )
    if reply.intent is workflow_reply.ReplyIntent.AFFIRM:
        # "yes" accepts a pick the agent already named, or the only option there is.
        pending = _last_referenced_candidate(workflow)
        if pending and not cart.find(workflow.cart, pending.candidate_id):
            return _select_candidate(workflow, pending, workflow_database_path)
        if len(workflow.candidates) == 1:
            return _select_candidate(workflow, workflow.candidates[0], workflow_database_path)
        return _reask_pending_question(workflow, workflow_database_path)
    return (
        "No problem — tell me what to look for instead, or say 'cancel' to stop this search."
    )


MIN_USEFUL_RESULTS = 3


async def _refine_candidates(
    workflow: PurchaseWorkflow,
    action: intent_classifier.SemanticAction,
    message: str,
    workflow_database_path: str | Path,
    telegram_user_id: int = 0,
) -> str:
    """Narrow the current results, searching again when filtering would leave too few.

    "Only the Prime ones" is a filter over what is already on screen. A budget is not:
    the user wants options that meet it, and re-filtering five results down to two
    unrelated leftovers is the wrong answer.
    """
    merged = {**workflow.constraints, **action.constraints}
    outcome = ranking.apply_constraints(workflow.candidates, merged)

    if len(outcome.kept) < MIN_USEFUL_RESULTS and workflow.normalized_product_goal:
        fresh = await _search_again(workflow, merged, message, workflow_database_path)
        if fresh is not None:
            return fresh

    if not outcome.kept:
        # Never persist a constraint that leaves nothing; the user would be stranded.
        return (
            "Nothing I found meets that. Try a different description, or say "
            "<b>start over</b>."
        )

    preference = ranking.requested_sort(message)
    if preference is ranking.SortPreference.RELEVANCE:
        ranked = ranking.RankedCandidates(outcome.kept, ranking.PREVIOUS_ORDER)
    else:
        ranked = ranking.rank(outcome.kept, preference)

    workflow.constraints = merged | {"latest_refinement": message}
    workflow.candidates = ranked.candidates
    return _show_results(
        workflow, ranked, workflow_database_path, removed=outcome.removed, refined=True
    )


async def _search_again(
    workflow: PurchaseWorkflow,
    constraints: dict,
    message: str,
    workflow_database_path: str | Path,
) -> str | None:
    """Re-query Amazon for the same goal, then apply the constraint to fresh results."""
    try:
        products = await amazon.search_products(workflow.normalized_product_goal)
    except Exception as error:  # noqa: BLE001 - fall back to filtering what we have
        print(f"Amazon re-search error: {error}")
        return None

    candidates = _candidates_from_products(products)
    outcome = ranking.apply_constraints(candidates, constraints)
    if not outcome.kept:
        return None

    preference = ranking.requested_sort(message)
    ranked = (
        ranking.RankedCandidates(outcome.kept, ranking.AMAZON_ORDER)
        if preference is ranking.SortPreference.RELEVANCE
        else ranking.rank(outcome.kept, preference)
    )
    workflow.constraints = constraints | {"latest_refinement": message}
    workflow.candidates = ranked.candidates
    return _show_results(
        workflow, ranked, workflow_database_path, removed=outcome.removed, refined=True
    )


REQUEST_PREFIX = re.compile(
    r"^(?:i\s+)?(?:need|want|would like|am looking for|looking for|get me|get|find me|find|"
    r"show me|show|search for|search|buy me|buy|order|add)\s+", re.IGNORECASE
)
# Words that mean "act on what is already here", not "search for something new".
NON_PRODUCT_WORDS = frozenset(
    """yes no ok okay sure thanks please cancel stop first second third fourth fifth last
    one two three four five it that this them those these cheaper cheapest rated review
    reviews price prices what which how why when who where does do is are the a an and or
    but hmm actually maybe about something else other others another different instead
    anything else something option options list cart them all any more some""".split()
)


def _new_product_query(message: str, workflow: PurchaseWorkflow) -> str | None:
    """Return a search query when the message names a product not already shown.

    "i need head and shoulders" during a shampoo search is a new search, not an
    unanswerable reply. Anything that only refers to what is on screen returns None.
    """
    stripped = REQUEST_PREFIX.sub("", message.strip()).strip()
    if not stripped:
        return None
    words = [word for word in re.findall(r"[a-z0-9']+", stripped.casefold())]
    meaningful = [word for word in words if word not in NON_PRODUCT_WORDS and len(word) > 2]
    if not meaningful:
        return None
    # If every meaningful word already appears in the results, the user is referring to
    # them rather than asking for something else.
    shown = " ".join(candidate.title.casefold() for candidate in workflow.candidates)
    if all(word in shown for word in meaningful):
        return None
    return stripped


def _resolve_or_research(
    workflow: PurchaseWorkflow, message: str, workflow_database_path: str | Path
) -> str:
    """Try the deterministic resolver, and only then give up.

    The resolver already understands "cheapest" and brand names. It used to sit behind
    the classifier, so a `no_match` from the local model re-offered the very word the
    agent had just suggested. This is the path that guarantees suggested words work.
    """
    resolution = candidate_resolver.resolve_candidate_reference(message, workflow.candidates)
    if resolution.candidate:
        return _select_candidate(workflow, resolution.candidate, workflow_database_path)
    return resolution.message or _reask_pending_question(workflow, workflow_database_path)


def _cancel_workflow(workflow: PurchaseWorkflow, workflow_database_path: str | Path) -> str:
    workflow_store.transition(workflow, WorkflowState.CANCELLED)
    workflow_store.save_workflow(workflow, workflow_database_path)
    return "Cancelled the current purchase workflow."


def _select_candidate(
    workflow: PurchaseWorkflow,
    candidate: Candidate,
    workflow_database_path: str | Path,
    quantity: int | None = None,
) -> str:
    """Choosing an option puts it on the list; picking and adding are one step."""
    # A quantity stated before picking ("make it two") applies to this item, then the
    # default returns to one so it does not silently follow every later item.
    already_listed = cart.find(workflow.cart, candidate.candidate_id)
    if already_listed and quantity is None:
        # "add it" for something already listed is a restatement, not a request for
        # a second one; silently doubling the quantity would be a costly surprise.
        return (
            f"{product_display.display_title(candidate.title)} is already on your list "
            f"(qty {already_listed.quantity}).\n\n"
            f"{_show_cart(workflow, workflow_database_path)}"
        )

    quantity = quantity or workflow.quantity
    workflow.quantity = 1
    workflow.selected_candidate_id = candidate.candidate_id
    workflow.cart = cart.add(workflow.cart, candidate, quantity)
    # Any change to the contents invalidates a previous confirmation (ADR-026).
    workflow.confirmed_token = None
    workflow_store.transition(
        workflow,
        WorkflowState.PREPARING_CART,
        pending_question="Add anything else, or check out?",
    )
    workflow_store.save_workflow(workflow, workflow_database_path)
    line = cart.find(workflow.cart, candidate.candidate_id)
    return (
        f"Added {product_display.display_title(candidate.title)} "
        f"(qty {line.quantity}).\n\n"
        f"{_show_cart(workflow, workflow_database_path)}"
    )


def _cart_as_candidates(workflow: PurchaseWorkflow) -> list[Candidate]:
    """Let the existing resolver name a cart line the same way it names a result."""
    return [
        Candidate(
            candidate_id=line.candidate_id,
            title=line.title,
            brand=None,
            price=line.price,
            price_text=line.price_text,
        )
        for line in workflow.cart
    ]


def _remove_from_cart(
    workflow: PurchaseWorkflow, message: str, workflow_database_path: str | Path
) -> str:
    if not workflow.cart:
        return "Your list is already empty."
    if len(workflow.cart) == 1:
        target = workflow.cart[0].candidate_id
    else:
        resolution = candidate_resolver.resolve_candidate_reference(
            message, _cart_as_candidates(workflow)
        )
        if not resolution.candidate:
            return f"{resolution.message}\n\n{_show_cart(workflow, workflow_database_path)}"
        target = resolution.candidate.candidate_id

    removed = cart.find(workflow.cart, target)
    workflow.cart = cart.remove(workflow.cart, target)
    workflow.confirmed_token = None
    workflow_store.transition(
        workflow,
        WorkflowState.PREPARING_CART,
        pending_question="Add anything else, or check out?",
    )
    workflow_store.save_workflow(workflow, workflow_database_path)
    return (
        f"Removed {product_display.display_title(removed.title)}.\n\n"
        f"{_show_cart(workflow, workflow_database_path)}"
    )


def _begin_checkout(workflow: PurchaseWorkflow, workflow_database_path: str | Path) -> str:
    if not workflow.cart:
        return "There is nothing to check out yet. Search for something and pick an option first."
    summary = checkout.summarize(workflow)
    workflow_store.transition(
        workflow,
        WorkflowState.AWAITING_CHECKOUT_CONFIRMATION,
        pending_question="Confirm this order summary?",
    )
    workflow_store.save_workflow(workflow, workflow_database_path)
    options = flow.store(workflow, flow.checkout_menu())
    workflow_store.save_workflow(workflow, workflow_database_path)
    return product_display.present_checkout(summary, options)


async def _confirm_order(workflow: PurchaseWorkflow, workflow_database_path: str | Path) -> str:
    """The confirmation gate.

    Confirming is the user's explicit approval, so it is the one point where the real
    Amazon cart is written. It stops there: the order itself is never submitted.
    """
    if workflow.state != WorkflowState.AWAITING_CHECKOUT_CONFIRMATION:
        if not workflow.cart:
            return "There is nothing to confirm yet. Pick something first."
        return "Say 'checkout' first so I can show you the exact summary to confirm."

    workflow.confirmed_token = checkout.confirmation_token(workflow)
    summary = checkout.summarize(workflow)
    subtotal = "unknown" if summary.subtotal is None else f"${summary.subtotal:.2f}"
    header = f"Confirmed: {summary.item_count} item(s), items subtotal {subtotal}."

    transfer = await _push_to_amazon_cart(workflow)
    workflow_store.transition(
        workflow,
        WorkflowState.PAUSED,
        pending_question="Open Amazon to complete the order.",
    )
    # Replace the checkout menu. Leaving it in place meant "1" confirmed a second time
    # and pushed the same items to the real Amazon cart again.
    options = flow.store(workflow, flow.done_menu(workflow))
    workflow_store.save_workflow(workflow, workflow_database_path)
    return (
        f"{header}\n\n{transfer}\n\n"
        "I cannot place this order. Ordering is deliberately not implemented — no code "
        "path here can submit a purchase. Open Amazon to complete it.\n\n"
        + flow.render_only(options)
    )


async def _push_to_amazon_cart(workflow: PurchaseWorkflow) -> str:
    """Move the approved list into the user's real Amazon cart, reporting reality."""
    items = [
        (line.source_url, line.quantity) for line in workflow.cart if line.source_url
    ]
    if not items:
        return "Nothing on the list had an Amazon link, so I could not add anything."
    try:
        results = await amazon.add_many_to_cart(items)
    except amazon.AmazonCartUnavailable as error:
        return f"I could not add these to your Amazon cart ({error}). The list is saved."
    except Exception as error:  # noqa: BLE001 - the reply must never be an exception
        print(f"Amazon cart error: {error}")
        return "I couldn't reach your Amazon cart just now. The list is saved."

    added = [result for result in results if result.added]
    failed = [result for result in results if not result.added]
    lines = [
        f"Added {len(added)} of {len(results)} item(s) to your real Amazon cart."
        if added
        else "I could not add anything to your Amazon cart."
    ]
    for result in failed:
        title = next(
            (line.title for line in workflow.cart if line.source_url == result.url),
            result.url,
        )
        lines.append(f"• Not added: {product_display.display_title(title)} ({result.detail})")
    return "\n".join(lines)


async def _general_response(message: str, workflow: PurchaseWorkflow | None = None) -> str:
    """Generate bounded conversation, supplying the options on screen as facts."""
    prompt = message
    if workflow and (workflow.candidates or workflow.cart):
        context = []
        if workflow.candidates:
            context.append(
                "Numbered Amazon results currently shown: "
                f"{product_evaluator.candidate_context(workflow.candidates)}"
            )
        # Without this the model answers "what's in my cart?" from nothing and can
        # contradict the list the agent just printed.
        context.append(
            "Items on this user's list right now: "
            f"{product_evaluator.cart_context(workflow.cart)}"
            if workflow.cart
            else "This user's list is currently empty."
        )
        joined = "\n".join(context)
        prompt = (
            f"{message}\n\n"
            "Context. Answer from these facts when the question is about them, and never "
            f"add facts that are not listed:\n{joined}"
        )
    response = await generate_response(
        prompt,
        max_tokens=GENERAL_RESPONSE_MAX_TOKENS,
        system_prompt=PURCHASING_AGENT_SYSTEM_PROMPT,
    )
    return normalize_general_response(response)


_USER_LOCKS: dict[int, asyncio.Lock] = {}


def _user_lock(telegram_user_id: int) -> asyncio.Lock:
    """One lock per user so a burst of messages is handled in order.

    Telegram delivers updates concurrently. Without this, two quick messages both read
    the workflow, both modify it, and the second save silently discards the first --
    losing an item the user just added.
    """
    lock = _USER_LOCKS.get(telegram_user_id)
    if lock is None:
        lock = asyncio.Lock()
        _USER_LOCKS[telegram_user_id] = lock
    return lock


async def agent_brain(
    message: str,
    memory_database_path: str | Path | None = None,
    workflow_database_path: str | Path | None = None,
    telegram_user_id: int = 0,
) -> str:
    """Coordinate a complete model response without exposing LM Studio to Telegram."""
    async with _user_lock(telegram_user_id):
        return await _handle_message(
            message, memory_database_path, workflow_database_path, telegram_user_id
        )


async def _handle_message(
    message: str,
    memory_database_path: str | Path | None,
    workflow_database_path: str | Path | None,
    telegram_user_id: int,
) -> str:
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

    if RESET_COMMAND.match(message.strip().casefold()):
        # Always available, never routed through the model, so the user can always
        # get back to a clean slate no matter what state the conversation is in.
        existing = workflow_store.get_active_workflow(telegram_user_id, workflow_database_path)
        if existing:
            workflow_store.transition(existing, WorkflowState.CANCELLED)
            workflow_store.save_workflow(existing, workflow_database_path)
        return (
            "Reset. Your list here is cleared and I've forgotten the current search.\n\n"
            "Anything already in your Amazon cart stays there — remove it on Amazon if "
            "you don't want it. What would you like to look for?"
        )

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

    # A number the user read off the last menu is the least ambiguous message in the
    # conversation, so it resolves first and never reaches the model.
    if active_workflow and active_workflow.pending_menu:
        picked = menu.choose(message, active_workflow.pending_menu)
        if picked is not None:
            print(f"[ROUTING] menu choice {picked.action}")
            return await _execute_menu_choice(
                active_workflow, picked, workflow_database_path, telegram_user_id
            )
        out_of_range = menu.out_of_range_hint(message, active_workflow.pending_menu)
        if out_of_range:
            return out_of_range

    # An unambiguous answer to a question the agent just asked is handled here, before
    # any model call, so "3" or "cancel" is instant and cannot be misrouted.
    if active_workflow:
        reply = workflow_reply.interpret(message, active_workflow.candidates)
        if reply.is_confident:
            print(f"[ROUTING] deterministic workflow reply intent={reply.intent}")
            return await _apply_workflow_reply(
                active_workflow, reply, workflow_database_path, message
            )

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
            # Shopping is iterative: a second request searches again and keeps the
            # list, so several products can be gathered in one conversation.
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
            return await _continue_purchase_workflow(active_workflow, semantic_action, message, workflow_database_path)
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
            # The local model says it does not know. Try the deterministic resolver, and
            # if the message names something not in the current results, search for it
            # instead of trapping the user in the same question.
            resolution = candidate_resolver.resolve_candidate_reference(
                message, active_workflow.candidates
            )
            if resolution.candidate:
                return _select_candidate(
                    active_workflow, resolution.candidate, workflow_database_path
                )
            new_query = _new_product_query(message, active_workflow)
            if new_query:
                return await _start_purchase_workflow(
                    telegram_user_id,
                    message,
                    new_query,
                    workflow_database_path,
                    existing=active_workflow,
                )
            if resolution.ambiguous:
                return resolution.message
            return _reask_pending_question(active_workflow, workflow_database_path)
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


def _candidate_id(product: amazon.Product) -> str:
    """Identify a candidate by Amazon's own product identity, not by its position.

    Position-based ids collided across searches in one conversation, so a product
    from a later search merged into an unrelated line already on the list.
    """
    asin = re.search(r"/dp/([A-Za-z0-9]+)", product.url)
    if asin:
        return f"amazon-{asin.group(1)}"
    digest = hashlib.sha1(product.url.encode("utf-8")).hexdigest()[:12]
    return f"amazon-url-{digest}"


def _candidates_from_products(products: list[amazon.Product]) -> list[Candidate]:
    """Persist only facts returned by the isolated read-only Amazon boundary."""
    return [
        Candidate(
            candidate_id=_candidate_id(product),
            title=product.title,
            brand=None,
            price=_price_amount(product.price),
            delivery_label=product.delivery,
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

    if request_mode.classify(message) is request_mode.RequestMode.COMMAND:
        # An instruction asks for a decision, so name one pick and confirm before
        # adding. The full list stays one word away.
        recommendation = ranking.recommend(ranked.candidates, message)
        if recommendation:
            workflow.selected_candidate_id = recommendation.candidate.candidate_id
            options = flow.store(workflow, flow.recommendation_menu(
                workflow, recommendation.candidate, recommendation.runner_up,
                len(ranked.candidates),
            ))
            workflow_store.transition(
                workflow,
                WorkflowState.AWAITING_PRODUCT_SELECTION,
                pending_question="Add this one?",
            )
            workflow_store.save_workflow(workflow, workflow_database_path)
            return product_display.present_recommendation(
                goal, recommendation, len(ranked.candidates), options
            )

    results = _show_results(workflow, ranked, workflow_database_path, removed=outcome.removed)
    if _asks_about_delivery(message):
        # Ignoring the question the user actually asked reads as evasive.
        results += (
            "\n\nI can't answer the delivery part yet: Amazon search results don't show "
            "delivery dates, and I don't know your address."
        )
    if workflow.cart:
        # Searching again must not look like the earlier picks were lost.
        pass
    return results


async def _continue_purchase_workflow(
    workflow: PurchaseWorkflow,
    action: intent_classifier.SemanticAction,
    message: str,
    workflow_database_path: str | Path,
) -> str:
    """Interpret stored workflow context without cart, checkout, or purchase actions."""
    if action.action == "cancel":
        return _cancel_workflow(workflow, workflow_database_path)
    if action.action == "refine":
        return await _refine_candidates(workflow, action, message, workflow_database_path)
    if action.action == "view_cart":
        return _show_cart(workflow, workflow_database_path)
    if action.action == "remove_from_cart":
        return _remove_from_cart(workflow, message, workflow_database_path)
    if action.action == "checkout":
        return _begin_checkout(workflow, workflow_database_path)
    if action.action == "confirm":
        return await _confirm_order(workflow, workflow_database_path)
    if action.action == "change_quantity":
        return _change_quantity(workflow, action, message, workflow_database_path)

    # select_candidate and add_to_cart both mean "I want this one".
    resolution = candidate_resolver.resolve_candidate_reference(message, workflow.candidates)
    candidate = resolution.candidate or _last_referenced_candidate(workflow)
    if not candidate:
        return resolution.message or "Please choose one of the presented options."
    return _select_candidate(workflow, candidate, workflow_database_path, action.quantity)


def _last_referenced_candidate(workflow: PurchaseWorkflow) -> Candidate | None:
    """Resolve "it" or "that" to whatever the user most recently picked."""
    if not workflow.selected_candidate_id:
        return None
    return next(
        (
            candidate
            for candidate in workflow.candidates
            if candidate.candidate_id == workflow.selected_candidate_id
        ),
        None,
    )


def _change_quantity(
    workflow: PurchaseWorkflow,
    action: intent_classifier.SemanticAction,
    message: str,
    workflow_database_path: str | Path,
) -> str:
    """Change the quantity of a listed item, or of the next item to be added."""
    if not action.quantity or action.quantity < 1:
        return "Please provide a quantity of at least one."

    if not workflow.cart:
        workflow.quantity = action.quantity
        workflow_store.save_workflow(workflow, workflow_database_path)
        return f"I'll use a quantity of {workflow.quantity} for the next item you pick."

    if len(workflow.cart) == 1:
        target = workflow.cart[0].candidate_id
    else:
        resolution = candidate_resolver.resolve_candidate_reference(
            message, _cart_as_candidates(workflow)
        )
        if not resolution.candidate:
            return f"Which item should be quantity {action.quantity}?\n\n{_show_cart(workflow, workflow_database_path)}"
        target = resolution.candidate.candidate_id

    workflow.cart = cart.set_quantity(workflow.cart, target, action.quantity)
    workflow.confirmed_token = None
    workflow_store.transition(
        workflow,
        WorkflowState.PREPARING_CART,
        pending_question="Add anything else, or check out?",
    )
    workflow_store.save_workflow(workflow, workflow_database_path)
    return _show_cart(workflow, workflow_database_path)


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
