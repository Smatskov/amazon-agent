# Coordinates the AI agent by deciding what actions to take and delegating work to other modules.

import asyncio
import hashlib
from pathlib import Path
import re
from time import perf_counter

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
import ranking
import request_mode
import state_answer
from timing import RequestTiming
import workflow_reply
import workflow_store
from workflow_models import Candidate, PurchaseWorkflow, WorkflowState


MEMORY_USAGE = (
    "Memory usage: remember: <key> = <value>; "
    "recall: <key>; forget: <key>."
)
MEMORY_CLASSIFICATION_FAILURE = "I couldn't understand that memory request safely."
LOCAL_MODEL_FAILURE = (
    "I couldn't reach the local AI model. "
    "Make sure LM Studio and its server are running."
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


DELIVERY_QUESTION = re.compile(
    r"\b(?:how long|when will|when would|when do|arrive|arrives|arrival|delivery|"
    r"delivered|deliver|get here|ship by|shipping time)\b"
)


def _asks_about_delivery(message: str) -> bool:
    return bool(DELIVERY_QUESTION.search(message.casefold()))




# The only gate that still reaches a language model. Everything else is deterministic.
MEMORY_HINT = re.compile(
    r"\b(?:remember|recall|forget|memoris|memoriz)\w*\b"
    r"|\bwhat(?:'s| is| was)?\s+my\b|\bmy (?:favou?rite|usual|preferred)\b"
)
CLARIFICATION_QUESTION = "Which product should I search for on Amazon?"
RESET_COMMAND = re.compile(
    r"^(?:/)?(?:reset|start over|start again|clear|clear (?:my )?list|"
    r"forget everything|new search|wipe)[.!]?$"
)




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


async def _narrow(
    workflow: PurchaseWorkflow, message: str, workflow_database_path: str | Path
) -> str:
    """Apply a narrowing instruction, searching again if too little would be left."""
    constraints = {**workflow.constraints, **ranking.parse_constraint(message)}
    outcome = ranking.apply_constraints(workflow.candidates, constraints)

    if len(outcome.kept) < MIN_USEFUL_RESULTS and workflow.normalized_product_goal:
        fresh = await _search_again(workflow, constraints, message, workflow_database_path)
        if fresh is not None:
            return fresh

    if not outcome.kept:
        workflow_store.transition(
            workflow, WorkflowState.AWAITING_PRODUCT_SELECTION, pending_question="Which one?"
        )
        ranked = ranking.RankedCandidates(workflow.candidates, ranking.PREVIOUS_ORDER)
        return (
            "Nothing I found matches that.\n\n"
            + _show_results(workflow, ranked, workflow_database_path)
        )

    workflow.constraints = constraints
    preference = ranking.requested_sort(message)
    ranked = (
        ranking.RankedCandidates(outcome.kept, ranking.PREVIOUS_ORDER)
        if preference is ranking.SortPreference.RELEVANCE
        else ranking.rank(outcome.kept, preference)
    )
    workflow.candidates = ranked.candidates
    return _show_results(
        workflow, ranked, workflow_database_path, removed=outcome.removed, refined=True
    )


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


def _is_legacy_mock_workflow(workflow: PurchaseWorkflow) -> bool:
    """Prevent old fabricated candidate records from remaining selectable after upgrade."""
    return bool(workflow.candidates) and all(
        not candidate.source_url for candidate in workflow.candidates
    )


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

    if MEMORY_HINT.search(message.casefold()):
        request = await intent_classifier.interpret_memory(message)
        if request.is_actionable:
            print(f"[ROUTING] memory {request.action}")
            return _memory_response(
                request.action, request.key, request.value, memory_database_path
            )

    # The user asked to narrow, so this message is the narrowing instruction.
    if active_workflow and active_workflow.state == WorkflowState.REFINING_SEARCH:
        print("[ROUTING] narrowing instruction")
        return await _narrow(active_workflow, message, workflow_database_path)

    # Steps 3-5 of the routing contract. Nothing below reaches a language model, and
    # nothing below can produce model-written text.
    if active_workflow and active_workflow.candidates:
        # A reference to something already on screen beats starting a new search.
        resolution = candidate_resolver.resolve_candidate_reference(
            message, active_workflow.candidates
        )
        if resolution.candidate:
            print("[ROUTING] reference to a shown option")
            return _select_candidate(
                active_workflow, resolution.candidate, workflow_database_path
            )
        if resolution.ambiguous:
            return resolution.message
        if DEICTIC_REFERENCE.match(message.strip()):
            # "the larger size" points at what is on screen. Searching Amazon for those
            # words returns nonsense, so ask which number instead.
            print("[ROUTING] unresolved reference to a shown option")
            return (
                "I'm not sure which one you mean.\n\n"
                + flow.render_only(active_workflow.pending_menu, "Pick a number:")
            )

    question = state_answer.answer(message, active_workflow)
    if question is not None:
        print("[ROUTING] question about stored state")
        return question

    if NOT_SHOPPING.search(message.casefold()):
        print("[ROUTING] not a shopping request")
        return _not_a_shopping_request(active_workflow, workflow_database_path)

    # Everything else is a search. Amazon's own search understands ordinary phrasing --
    # "alright, i need a new iphone 17 charger" returns iPhone 17 chargers -- so the raw
    # message is a better query than anything a small local model rewrites it into.
    print("[ROUTING] search")
    return await _start_purchase_workflow(
        telegram_user_id,
        message,
        _search_terms(message),
        workflow_database_path,
        existing=active_workflow,
    )


# Openers that are plainly not shopping. Kept deliberately short: anything not matched
# here is searched, which is the safe default for a shopping agent.
NOT_SHOPPING = re.compile(
    r"^(?:hi|hey|hello|thanks|thank you|ok|okay|sure|lol|test|ping)[.!]?$"
    r"|^(?:who|why|when|where)\s+(?:is|are|was|were|do|does|did)\b"
    r"|^what\s+(?:is|are)\s+(?:the\s+)?(?:capital|weather|time|date|meaning)\b"
    r"|\bcapital of\b|\bweather\b|\btell me a joke\b"
)
# Words that describe the asking, not the product. Stripped so the Amazon query is the
# thing wanted rather than the sentence around it.
REQUEST_NOISE = re.compile(
    r"\b(?:alright|okay|ok|so|well|hey|please|thanks|actually|just|now|can you|could you|"
    r"would you|i need|i want|i'd like|i would like|go ahead and|for me|to my cart|"
    r"in my cart|to the cart|my cart)\b",
    re.IGNORECASE,
)


LEADING_ARTICLE = re.compile(r"^(?:a|an|the|some|new|another)\s+(?:new\s+)?", re.IGNORECASE)
# Short phrases that point at something already on screen rather than naming a product.
DEICTIC_REFERENCE = re.compile(
    r"^(?:the|that|this|those|these)\s+\w+(?:\s+\w+)?$"
    r"|^(?:the\s+)?(?:other|larger|bigger|smaller|last|next)\s*(?:one|size|option)?$",
    re.IGNORECASE,
)


def _search_terms(message: str) -> str:
    """Trim the request wrapper, keeping the product words.

    Amazon copes with the raw sentence, so this only tidies it. If trimming would leave
    nothing, the original message is used unchanged rather than searching for "".
    """
    trimmed = REQUEST_NOISE.sub(" ", message)
    trimmed = re.sub(r"[^\w\s$.&'-]", " ", trimmed)
    trimmed = " ".join(trimmed.split()).strip(" -")
    # Articles last: punctuation removal can expose a leading "a"/"the".
    trimmed = LEADING_ARTICLE.sub("", trimmed).strip()
    return trimmed or message.strip()


def _not_a_shopping_request(
    workflow: PurchaseWorkflow | None, workflow_database_path: str | Path
) -> str:
    """One fixed reply. No model, so there is nothing to invent."""
    reply = (
        "I'm a shopping agent — I search Amazon and build a list for you.\n\n"
        "Tell me a product to look for, like <b>bug spray</b> or <b>iphone 17 charger</b>."
    )
    if workflow and workflow.pending_menu:
        return f"{reply}\n\n{flow.render_only(workflow.pending_menu, 'Or pick up where we were:')}"
    return reply


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




