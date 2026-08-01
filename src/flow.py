"""Builds the numbered menu for each point in the conversation, and renders it.

Every reply the agent sends carries a menu, and the menu is stored on the workflow so a
numeric reply resolves against exactly what the user saw. This is what removes the
guesswork: the agent no longer has to work out whether "employee" means *narrow to that
brand* or *choose that product*, because the user picks a number instead.

Nothing here searches, stores, or mutates. It assembles choices and hands back text.
"""

import cart
import menu
import product_display
from menu import MenuAction, MenuOption
from workflow_models import Candidate, PurchaseWorkflow


def _list_option(workflow: PurchaseWorkflow) -> list[MenuOption]:
    if not workflow.cart:
        return []
    count = cart.item_count(workflow.cart)
    return [MenuOption(MenuAction.VIEW_LIST, f"View your list ({count} item{'' if count == 1 else 's'})")]


def results_menu(workflow: PurchaseWorkflow, candidates: list[Candidate]) -> tuple[list[MenuOption], list[MenuOption]]:
    """Products first so their numbers match the printed list, then the actions."""
    picks = [
        MenuOption(MenuAction.SELECT, product_display.display_title(candidate.title), candidate.candidate_id)
        for candidate in candidates
    ]
    actions = [
        # The label states what the next message may contain. Without it the user
        # picked "Narrow", was asked "what should I narrow by?", and only then learned
        # what was accepted — an extra turn to deliver information the menu could have
        # carried in the first place.
        MenuOption(MenuAction.NARROW, "Narrow these — by brand, budget, or keyword"),
        MenuOption(MenuAction.NEW_SEARCH, "Search for something else"),
        *_list_option(workflow),
        MenuOption(MenuAction.CANCEL, "Start over"),
    ]
    return picks, actions


def no_match_menu(workflow: PurchaseWorkflow) -> list[MenuOption]:
    """Offered when a narrowing matched nothing.

    Deliberately carries no product choices. Reprinting the results that failed the
    filter directly under "nothing matched" invited the user to pick one of them, and
    made it look as though the narrowing had quietly succeeded.
    """
    return [
        MenuOption(MenuAction.SHOW_OPTIONS, "Show the results I had before"),
        MenuOption(MenuAction.NARROW, "Try a different brand, budget, or keyword"),
        MenuOption(MenuAction.NEW_SEARCH, "Search for something else"),
        *_list_option(workflow),
        MenuOption(MenuAction.CANCEL, "Start over"),
    ]


def variant_menu(workflow: PurchaseWorkflow) -> list[MenuOption]:
    """Pick which child of a variation listing is actually wanted.

    A variation parent has no single identity — scent, size, and pack are chosen on
    the product page — so adding one either fails or is ambiguous about what arrives.
    Choosing here resolves to a child ASIN, and the number the user types is the thing
    that gets added.
    """
    return [
        MenuOption(MenuAction.CHOOSE_VARIANT, label, asin)
        for asin, label, _ in workflow.pending_variants
    ] + [
        MenuOption(MenuAction.SHOW_OPTIONS, "None of these — back to the results"),
        MenuOption(MenuAction.CANCEL, "Start over"),
    ]


QUANTITY_CHOICES = (1, 2, 3, 4, 5)


def quantity_menu(workflow: PurchaseWorkflow, candidate_id: str) -> list[MenuOption]:
    """How many of one item. Explicit, so a quantity can never change by itself."""
    return [
        MenuOption(MenuAction.SET_QUANTITY, f"{count}", f"{candidate_id}:{count}")
        for count in QUANTITY_CHOICES
    ] + [MenuOption(MenuAction.VIEW_LIST, "Leave it as it is")]


def change_quantity_menu(workflow: PurchaseWorkflow) -> list[MenuOption]:
    """Which item's quantity to change."""
    return [
        MenuOption(
            MenuAction.SET_QUANTITY,
            f"{product_display.display_title(line.title)} — now {line.quantity}",
            line.candidate_id,
        )
        for line in workflow.cart
    ] + [MenuOption(MenuAction.VIEW_LIST, "Leave everything as it is")]


def cart_menu(workflow: PurchaseWorkflow) -> list[MenuOption]:
    options = []
    if workflow.cart:
        options.append(MenuOption(MenuAction.CHECKOUT, "Check out"))
    options.append(MenuOption(MenuAction.KEEP_SHOPPING, "Add something else"))
    if workflow.cart:
        options.append(MenuOption(MenuAction.SET_QUANTITY, "Change a quantity"))
        options.append(MenuOption(MenuAction.REMOVE, "Remove an item"))
    options.append(MenuOption(MenuAction.CANCEL, "Start over"))
    return options


def remove_menu(workflow: PurchaseWorkflow) -> list[MenuOption]:
    """Removing costs money, so each line shows what it is worth as well as its name."""
    return [
        MenuOption(
            MenuAction.REMOVE,
            f"{product_display.display_title(line.title)} — {line.price_text or 'price not shown'}",
            line.candidate_id,
        )
        for line in workflow.cart
    ] + [MenuOption(MenuAction.VIEW_LIST, "Keep everything")]


def ready_to_order_menu(workflow: PurchaseWorkflow) -> list[MenuOption]:
    """The last screen before an order would be placed.

    "View your list" is dropped here: the cart contents are printed directly above, so
    the option only re-showed what the user was already looking at.
    """
    return [
        MenuOption(MenuAction.PLACE_ORDER, "Place the order"),
        MenuOption(MenuAction.REMOVE, "Remove an item"),
        MenuOption(MenuAction.KEEP_SHOPPING, "Shop for something else"),
        MenuOption(MenuAction.CANCEL, "Start over"),
    ]


def done_menu(workflow: PurchaseWorkflow) -> list[MenuOption]:
    return [
        MenuOption(MenuAction.KEEP_SHOPPING, "Shop for something else"),
        MenuOption(MenuAction.CANCEL, "Start over"),
    ]


def store(workflow: PurchaseWorkflow, options: list[MenuOption]) -> list[MenuOption]:
    """Remember the choices so a number means what the user just read."""
    workflow.pending_menu = list(options)
    return options


def render_only(options: list[MenuOption], heading: str = "What next?") -> str:
    return menu.render(options, heading=heading)
