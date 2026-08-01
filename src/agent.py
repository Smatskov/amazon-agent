# Coordinates the AI agent by deciding what actions to take and delegating work to other modules.

import asyncio
import hashlib
from pathlib import Path
import re

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
import state_answer
import workflow_reply
import workflow_store
from workflow_models import Candidate, PurchaseWorkflow, WorkflowState


MEMORY_USAGE = (
    "Memory usage: remember: <key> = <value>; "
    "recall: <key>; forget: <key>."
)
MEMORY_CLASSIFICATION_FAILURE = "I couldn't understand that memory request safely."
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
    workflow.pending_photos = [
        [c.image_url, f"{i} · {product_display.display_title(c.title)} — "
                      f"{c.price_text or 'price not shown'}"]
        for i, c in enumerate(ranked.candidates, 1)
        if c.image_url
    ]
    workflow_store.transition(
        workflow, WorkflowState.AWAITING_PRODUCT_SELECTION, pending_question="Which one?"
    )
    workflow_store.save_workflow(workflow, workflow_database_path)
    return product_display.present_results(
        workflow.normalized_product_goal, ranked, actions, removed=removed, refined=refined
    )


async def _execute_many_menu_choices(
    workflow: PurchaseWorkflow,
    options: list[menu.MenuOption],
    workflow_database_path: str | Path,
    telegram_user_id: int,
) -> str:
    """Apply several picks at once.

    Only removals combine meaningfully — "1,2" means drop both. Anything else falls
    back to the first pick rather than running two unrelated actions in a row.
    """
    removals = [option for option in options if option.action is MenuAction.REMOVE and option.payload]
    if len(removals) == len(options) and removals:
        for option in removals:
            workflow.cart = cart.remove(workflow.cart, option.payload)
        workflow.confirmed_token = None
        workflow_store.transition(
            workflow, WorkflowState.PREPARING_CART, pending_question="Anything else?"
        )
        return _show_cart(workflow, workflow_database_path)
    return await _execute_menu_choice(
        workflow, options[0], workflow_database_path, telegram_user_id
    )


async def _execute_menu_choice(
    workflow: PurchaseWorkflow,
    option: menu.MenuOption,
    workflow_database_path: str | Path,
    telegram_user_id: int,
    *,
    argument: str = "",
) -> str:
    """Run exactly what the user picked. No model, no guessing."""
    action = option.action
    if argument and action is MenuAction.NARROW:
        # "6 under 10" is a choice and its instruction in one message.
        workflow_store.transition(
            workflow, WorkflowState.REFINING_SEARCH, pending_question="Narrow how?"
        )
        return await _narrow(workflow, argument, workflow_database_path)
    if argument and action in {MenuAction.NEW_SEARCH, MenuAction.KEEP_SHOPPING}:
        return await _start_purchase_workflow(
            telegram_user_id, argument, _search_terms(argument),
            workflow_database_path, existing=workflow,
        )
    if action is MenuAction.CANCEL:
        return _cancel_workflow(workflow, workflow_database_path)
    if action is MenuAction.VIEW_LIST:
        return _show_cart(workflow, workflow_database_path)
    if action is MenuAction.CHECKOUT:
        return await _begin_checkout(workflow, workflow_database_path)
    if action is MenuAction.CONFIRM:
        return await _confirm_order(workflow, workflow_database_path)
    if action is MenuAction.PLACE_ORDER:
        return await _place_order(workflow, workflow_database_path)
    if action is MenuAction.SELECT:
        candidate = next(
            (c for c in workflow.candidates if c.candidate_id == option.payload), None
        )
        if candidate is None:
            return _show_cart(workflow, workflow_database_path)
        return await _select_or_ask_variant(workflow, candidate, workflow_database_path)
    if action is MenuAction.CHOOSE_VARIANT:
        return await _add_variant(workflow, option.payload, workflow_database_path)
    if action is MenuAction.SET_QUANTITY:
        return _set_quantity(workflow, option.payload, workflow_database_path)
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
        _keep_only_product_menu(workflow)
        workflow_store.transition(
            workflow, WorkflowState.REFINING_SEARCH, pending_question="Narrow how?"
        )
        workflow_store.save_workflow(workflow, workflow_database_path)
        # The menu label already said what is accepted, so this only prompts.
        return "Go ahead — a brand, a budget like <b>under $20</b>, or any keyword."
    # NEW_SEARCH and KEEP_SHOPPING both mean: tell me what to look for next.
    _keep_only_product_menu(workflow)
    workflow_store.transition(
        workflow,
        WorkflowState.AWAITING_REQUEST_CLARIFICATION,
        pending_question=CLARIFICATION_QUESTION,
    )
    workflow_store.save_workflow(workflow, workflow_database_path)
    return "What should I look for?"


def _keep_only_product_menu(workflow: PurchaseWorkflow) -> None:
    """Keep a menu whose numbers still mean something on the user's screen.

    Clearing the menu here used to break the one guarantee the numbered design rests
    on (ADR-052): the results were still visible in the chat, so "3" still looked like
    a valid choice, but the agent had forgotten the menu and answered "which product
    should I search for?" instead. A results menu stays live because those numbers
    still point at products the user can see; a cart or checkout menu is dropped,
    because reusing "1 · Check out" after moving on would act on a stale intent.
    """
    if not any(option.action is MenuAction.SELECT for option in workflow.pending_menu):
        flow.store(workflow, [])


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
        # The items are already in the Amazon cart, so this is the order step, not a
        # request to check out again. Answering "say checkout first" here told the user
        # to redo something they had just done.
        if workflow.state == WorkflowState.PAUSED and workflow.cart:
            return await _place_order(workflow, workflow_database_path)
        return await _confirm_order(workflow, workflow_database_path)
    if reply.intent is workflow_reply.ReplyIntent.CHECKOUT:
        return await _begin_checkout(workflow, workflow_database_path)
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
    """Re-query Amazon with the narrowing folded into the query, not just as a filter.

    Filtering results already retrieved can only ever remove; it cannot find. Asking
    to narrow melatonin to "Nature's Bounty" re-ran the identical query and filtered
    the same five results, so a brand that was not already in the top five could never
    be found however it was spelled. The brand belongs in the query Amazon receives.
    """
    keyword = constraints.get("keyword")
    goal = workflow.normalized_product_goal
    query = f"{keyword} {goal}".strip() if keyword else goal
    # A budget goes to Amazon as a price ceiling. Applying it only to results already
    # retrieved reported "nothing matches" for "under 10" when Amazon had six Dove
    # body washes from $5.47 one page away.
    def _bound(name):
        value = constraints.get(name)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    try:
        products = await amazon.search_products(
            query, max_price=_bound("max_price"), min_price=_bound("min_price")
        )
    except Exception as error:  # noqa: BLE001 - fall back to filtering what we have
        print(f"Amazon re-search error: {error}")
        return None

    candidates = ranking.relevance(_candidates_from_products(products), query).kept
    outcome = ranking.apply_constraints(candidates, constraints)
    if not outcome.kept:
        return None

    ranked = ranking.rank(outcome.kept, ranking.default_sort(message))
    workflow.normalized_product_goal = query
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
        # Nothing matched, so nothing is shown. Printing the results that just failed
        # the filter under a "nothing matches" heading read as a successful narrowing
        # and invited the user to pick one of the very items they had excluded.
        workflow_store.transition(
            workflow,
            WorkflowState.AWAITING_PRODUCT_SELECTION,
            pending_question="What would you like to do instead?",
        )
        options = flow.store(workflow, flow.no_match_menu(workflow))
        workflow_store.save_workflow(workflow, workflow_database_path)
        return (
            "I couldn't find any products matching "
            f"<b>{product_display.text(message.strip())}</b>. I checked the results I "
            "already had and searched Amazon for it directly — nothing came back.\n\n"
            + flow.render_only(options)
        )

    workflow.constraints = constraints
    ranked = ranking.rank(outcome.kept, ranking.default_sort(message))
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
    if resolution.message:
        return _with_menu(workflow, resolution.message)
    return _reask_pending_question(workflow, workflow_database_path)


def _with_menu(workflow: PurchaseWorkflow, text: str) -> str:
    """Never ask the user to choose without showing what there is to choose from.

    A question like "which option do you mean?" arriving on its own leaves the user
    scrolling back through the conversation to find numbers the agent may no longer
    honour. Every reply carries its menu (ADR-052).
    """
    if not workflow.pending_menu:
        return text
    return f"{text}\n\n{flow.render_only(workflow.pending_menu, 'Pick a number:')}"


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


async def _select_or_ask_variant(
    workflow: PurchaseWorkflow,
    candidate: Candidate,
    workflow_database_path: str | Path,
) -> str:
    """Resolve a variation listing to one buyable child before adding anything.

    A search result can be a variation parent whose scent, size, and pack are only
    chosen on the product page. Adding one is either refused by Amazon or ambiguous
    about what would actually arrive, so the choice is made here, as a numbered menu,
    and what gets added is the child ASIN the user picked.
    """
    try:
        variants = (
            await amazon.read_variants(candidate.source_url) if candidate.source_url else []
        )
    except Exception as error:  # noqa: BLE001 - never let a lookup block a selection
        print(f"[AMAZON] variant lookup failed: {error}")
        variants = []
    if len(variants) < 2:
        # One option, or none stated: nothing to choose, so add what was picked.
        return _select_candidate(workflow, candidate, workflow_database_path)

    workflow.pending_variants = [[v.asin, v.label, v.url] for v in variants]
    workflow.selected_candidate_id = candidate.candidate_id
    options = flow.store(workflow, flow.variant_menu(workflow))
    workflow_store.transition(
        workflow,
        WorkflowState.AWAITING_PRODUCT_SELECTION,
        pending_question="Which version?",
    )
    workflow_store.save_workflow(workflow, workflow_database_path)
    return (
        f"<b>{product_display.text(product_display.display_title(candidate.title))}</b> "
        f"comes in {len(variants)} versions. Which one?\n\n"
        + flow.render_only(options, "Choose:")
    )


async def _add_variant(
    workflow: PurchaseWorkflow, asin: str | None, workflow_database_path: str | Path
) -> str:
    """Add the exact child the user chose, priced from its own product page."""
    chosen = next(
        (row for row in workflow.pending_variants if row and row[0] == asin), None
    )
    if not chosen:
        return _show_cart(workflow, workflow_database_path)
    variant_asin, label, url = chosen[0], chosen[1], chosen[2]
    parent = next(
        (c for c in workflow.candidates if c.candidate_id == workflow.selected_candidate_id),
        None,
    )
    details = await amazon.read_product(url)
    title = (details.title if details and details.title else
             f"{parent.title if parent else 'Item'} — {label}")
    price_text = details.price if details else None
    candidate = Candidate(
        candidate_id=f"amazon-{variant_asin}",
        title=title,
        brand=None,
        price=_price_amount(price_text),
        price_text=price_text,
        delivery_label=parent.delivery_label if parent else None,
        source_url=url,
    )
    # The chosen variant replaces the parent among the candidates so a later reference
    # resolves to the thing that was actually added.
    workflow.candidates = [candidate] + [
        c for c in workflow.candidates if c.candidate_id != candidate.candidate_id
    ]
    workflow.pending_variants = []
    return _select_candidate(workflow, candidate, workflow_database_path)


def _set_quantity(
    workflow: PurchaseWorkflow, payload: str | None, workflow_database_path: str | Path
) -> str:
    """Change how many of one item are on the list.

    Quantity became unreachable when the semantic path was removed (ISSUE-023), so
    adding two of anything was impossible. It is a menu choice like everything else:
    explicit, echoed back, and never changed by inference.
    """
    if not payload:
        if not workflow.cart:
            return "There is nothing on your list to change."
        options = flow.store(workflow, flow.change_quantity_menu(workflow))
        workflow_store.save_workflow(workflow, workflow_database_path)
        return "Which item?\n\n" + flow.render_only(options, "Choose:")

    # Split from the right: a candidate id is "amazon-<ASIN>", but a stored menu is
    # untrusted input and must not be able to raise inside message handling.
    candidate_id, _, count = payload.rpartition(":")
    if not count.isdigit():
        candidate_id, count = payload, ""
    if not count:
        options = flow.store(workflow, flow.quantity_menu(workflow, candidate_id))
        workflow_store.save_workflow(workflow, workflow_database_path)
        return "How many?\n\n" + flow.render_only(options, "Choose:")

    workflow.cart = cart.set_quantity(workflow.cart, candidate_id, int(count))
    # Any change to the contents invalidates a previous confirmation (ADR-026).
    workflow.confirmed_token = None
    workflow_store.transition(
        workflow, WorkflowState.PREPARING_CART, pending_question="Anything else?"
    )
    return _show_cart(workflow, workflow_database_path)


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


async def _begin_checkout(workflow: PurchaseWorkflow, workflow_database_path: str | Path) -> str:
    """Checking out shows the summary and puts the items in the real Amazon cart.

    Splitting this in two asked the user to approve a list they had already assembled
    and already reviewed, then asked again on the next screen. The list itself is the
    review; the cart is not an order, and everything in it stays removable.
    """
    if not workflow.cart:
        return "There is nothing to check out yet. Search for something and pick an option first."
    if workflow.state == WorkflowState.PAUSED and checkout.is_confirmation_current(workflow):
        # Already pushed and unchanged since. Re-running would duplicate the cart write.
        summary = checkout.summarize(workflow)
        options = flow.store(workflow, flow.ready_to_order_menu(workflow))
        workflow_store.save_workflow(workflow, workflow_database_path)
        return product_display.present_ready_to_order(
            summary, "These are already in your Amazon cart.", options,
            workflow.amazon_cart, workflow.destination,
        )
    return await _confirm_order(workflow, workflow_database_path, reviewed=True)


async def _confirm_order(
    workflow: PurchaseWorkflow, workflow_database_path: str | Path, *, reviewed: bool = False
) -> str:
    """The confirmation gate.

    Confirming is the user's explicit approval, so it is the one point where the real
    Amazon cart is written. It stops there: the order itself is never submitted.
    """
    if not reviewed and workflow.state != WorkflowState.AWAITING_CHECKOUT_CONFIRMATION:
        if not workflow.cart:
            return "There is nothing to confirm yet. Pick something first."
        return "Say 'checkout' first so I can show you the exact summary to confirm."

    workflow.confirmed_token = checkout.confirmation_token(workflow)
    summary = checkout.summarize(workflow)

    transfer = await _push_to_amazon_cart(workflow)
    await _read_real_cart(workflow)
    destination = await amazon.read_destination()
    workflow.destination = [destination.address_label, destination.card_label]
    workflow_store.transition(
        workflow,
        WorkflowState.PAUSED,
        pending_question="Review the cart, then place the order.",
    )
    # Replace the checkout menu. Leaving it in place meant "1" confirmed a second time
    # and pushed the same items to the real Amazon cart again.
    options = flow.store(workflow, flow.ready_to_order_menu(workflow))
    workflow_store.save_workflow(workflow, workflow_database_path)
    return product_display.present_ready_to_order(
        summary, transfer, options, workflow.amazon_cart, workflow.destination
    )


async def _place_order(
    workflow: PurchaseWorkflow, workflow_database_path: str | Path
) -> str:
    """Submit the real Amazon order, and report exactly what happened.

    The two outcomes are deliberately asymmetric. A placed order clears the list,
    because those items are bought and leaving them would invite ordering them twice.
    A failed order changes nothing at all: the list survives, so the user can fix the
    cause and try again without rebuilding what they had.
    """
    if not workflow.cart:
        return "There is nothing to order — your list is empty."

    summary = checkout.summarize(workflow)
    ordered = list(workflow.amazon_cart)
    shipping = list(workflow.destination)
    result = await amazon.place_order()

    if not result.placed:
        # Nothing is cleared and nothing is transitioned: the workflow is exactly as
        # it was, so every option on the failure menu still acts on real items.
        options = flow.store(workflow, flow.order_failed_menu(workflow))
        workflow_store.save_workflow(workflow, workflow_database_path)
        return product_display.present_order_failed(
            summary, options, result.detail,
            needs_sign_in=result.needs_sign_in, declined=result.declined,
        )

    workflow.cart = []
    workflow.amazon_cart = []
    workflow.candidates = []
    workflow.confirmed_token = None
    workflow.selected_candidate_id = None
    workflow.pending_photos = []
    workflow.destination = []
    options = flow.store(workflow, flow.done_menu(workflow))
    workflow_store.transition(
        workflow, WorkflowState.COMPLETED, pending_question="Anything else?"
    )
    workflow_store.save_workflow(workflow, workflow_database_path)
    return product_display.present_order_placed(
        summary, options, ordered, shipping, result.order_id, result.order_url
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
        f"🆕 <b>ADDED FROM YOUR LIST</b> — {len(added)} of {len(results)} item(s)"
        if added
        else "<b>Nothing from your list could be added to your Amazon cart.</b>"
    ]
    for result in added:
        title = next(
            (line.title for line in workflow.cart if line.source_url == result.url), result.url
        )
        price = next(
            (line.price_text for line in workflow.cart if line.source_url == result.url), None
        )
        lines.append(
            f"• <b>{product_display.text(product_display.display_title(title))}</b>"
            f"{'  ' + product_display.text(price) if price else ''}"
        )
    for result in failed:
        title = next(
            (line.title for line in workflow.cart if line.source_url == result.url),
            result.url,
        )
        lines.append(f"• Not added: {product_display.display_title(title)} ({result.detail})")

    strangers = await _items_not_from_this_list(workflow)
    if strangers:
        lines.append(
            f"\n⚠️ <b>ALREADY IN YOUR AMAZON CART</b>\n"
            f"<i>{len(strangers)} item(s) came from somewhere else, so the real total is "
            "higher than the subtotal above.</i>"
        )
        lines.extend(
            f"• <b>{product_display.text(product_display.display_title(title))}</b>"
            f"{'  ' + product_display.text(price) if price else ''}"
            for title, price in strangers
        )
    return "\n".join(lines)


async def _read_real_cart(workflow: PurchaseWorkflow) -> None:
    """Record the whole Amazon cart, marking which lines this conversation added.

    The subtotal the user was shown described the agent's list, but the order they
    would place is the entire cart: two items and $35.23 on screen while six items
    and a larger total were actually sitting there.
    """
    ours = {
        asin
        for line in workflow.cart
        if line.source_url and (asin := amazon.asin_from_url(line.source_url))
    }
    try:
        real = await amazon.read_cart()
    except Exception as error:  # noqa: BLE001 - a failed read must never break the reply
        print(f"[AMAZON] could not read the cart back: {error}")
        workflow.amazon_cart = []
        return
    workflow.amazon_cart = [
        [item.title, item.price, bool((asin := amazon.asin_from_url(item.url)) and asin in ours)]
        for item in real
    ]


async def _items_not_from_this_list(workflow: PurchaseWorkflow) -> list[tuple[str, str | None]]:
    """Name anything in the real Amazon cart that this conversation did not add.

    The confirmation summary describes the agent's own list, but the order the user
    would actually place is the whole Amazon cart. A single item left there from an
    earlier session — a $1.98 pack of coffee filters, in the case that prompted this —
    silently rides along, and the summary the user approved never mentioned it. That
    is tolerable while ordering is refused, and unacceptable once it is not, so the
    discrepancy is surfaced at the moment of approval rather than discovered later.
    """
    ours = {
        asin
        for line in workflow.cart
        if line.source_url and (asin := amazon.asin_from_url(line.source_url))
    }
    try:
        real = await amazon.read_cart()
    except Exception as error:  # noqa: BLE001 - a failed read must never break the reply
        print(f"[AMAZON] could not read the cart back: {error}")
        return []
    return [
        (item.title, item.price)
        for item in real
        if (asin := amazon.asin_from_url(item.url)) and asin not in ours
    ]


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


def take_pending_photos(
    telegram_user_id: int, workflow_database_path: str | Path | None = None
) -> list[list[str]]:
    """Hand the transport the images for the reply just produced, once.

    Photos are presentation, so the agent does not send them; it only says which ones
    belong to what it just wrote. Clearing on read means a gallery can never appear
    beside a later message about different products.
    """
    if workflow_database_path is None:
        workflow_database_path = workflow_store.DEFAULT_WORKFLOW_DATABASE_PATH
    workflow = workflow_store.get_active_workflow(telegram_user_id, workflow_database_path)
    if not workflow or not workflow.pending_photos:
        return []
    photos = list(workflow.pending_photos)
    workflow.pending_photos = []
    workflow_store.save_workflow(workflow, workflow_database_path)
    return photos


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
        # Several picks at once: "1,2" removes both.
        several = menu.choose_many(message, active_workflow.pending_menu)
        if several is not None:
            print(f"[ROUTING] menu multi-choice x{len(several)}")
            return await _execute_many_menu_choices(
                active_workflow, several, workflow_database_path, telegram_user_id
            )
        # A pick that carries its own instruction: "6 dont want to pay over 10 bucks".
        with_argument = menu.choose_with_argument(message, active_workflow.pending_menu)
        if with_argument is not None:
            option, argument = with_argument
            print(f"[ROUTING] menu choice {option.action} with argument")
            return await _execute_menu_choice(
                active_workflow,
                option,
                workflow_database_path,
                telegram_user_id,
                argument=argument,
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
    #
    # A message arriving while the agent is waiting for "what should I look for?" is
    # the answer to that question, so it goes straight to Amazon. It must not be
    # matched against the previous search's candidates: those results are what the
    # user just asked to move on from, and matching against them turned an answer
    # into "more than one option matches that description".
    if active_workflow and active_workflow.candidates and not _is_awaiting_clarification(
        active_workflow
    ):
        # A reference to something already on screen beats starting a new search.
        resolution = candidate_resolver.resolve_candidate_reference(
            message, active_workflow.candidates
        )
        if resolution.candidate:
            print("[ROUTING] reference to a shown option")
            return await _select_or_ask_variant(
                active_workflow, resolution.candidate, workflow_database_path
            )
        if resolution.ambiguous:
            return _with_menu(active_workflow, resolution.message)
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
            unit_price_text=product.unit_price,
            image_url=product.image_url,
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

    # Amazon mixes unrelated placements into organic results, so anything sharing no
    # word with the request is dropped before the user ever sees a number beside it.
    relevant = ranking.relevance(candidates, goal)
    # Amazon answers any string, so a sentence it could not parse still returns real
    # listings — "6 dont want to pay over 10 bucks" came back with "A Smell of Honey,
    # $19.99". Nothing sharing a single word with the request means the request was
    # not understood as a product. The results are still offered, because discarding
    # them would be its own kind of wrong, but they are never presented as an answer.
    nothing_related = not relevant.kept
    candidates = candidates if nothing_related else relevant.kept
    outcome = ranking.apply_constraints(candidates, constraints)
    if not outcome.kept:
        return (
            f"Every Amazon result for '{goal}' failed your requirements "
            f"({', '.join(outcome.reasons)}). I have not started a purchase workflow. "
            "Want to relax a requirement?"
        )
    ranked = ranking.rank(outcome.kept, ranking.default_sort(message))

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

    # Every search shows the options. Naming a single "best match" for a command like
    # "order me water bottles" hid the other four behind one pick the user had no way
    # to judge, and there is no evidence source that could justify choosing for them:
    # order history is empty and the agent has none of its own (ISSUE-035).
    results = _show_results(workflow, ranked, workflow_database_path, removed=outcome.removed)
    if nothing_related:
        results = (
            f"⚠️ <b>I didn't understand \"{product_display.text(goal)}\" as a product.</b>\n"
            "Nothing Amazon returned looks related to it, so treat the list below with "
            "suspicion. Try naming the product on its own (<b>toothbrushes</b>) or with "
            "a budget (<b>toothbrushes under $10</b>).\n\n"
        ) + results
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




