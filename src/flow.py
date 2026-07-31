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
        MenuOption(MenuAction.NARROW, "Narrow these results"),
        MenuOption(MenuAction.NEW_SEARCH, "Search for something else"),
        *_list_option(workflow),
        MenuOption(MenuAction.CANCEL, "Start over"),
    ]
    return picks, actions


def recommendation_menu(
    workflow: PurchaseWorkflow, pick: Candidate, runner_up: Candidate | None, total: int
) -> list[MenuOption]:
    options = [
        MenuOption(MenuAction.SELECT, f"Add {product_display.display_title(pick.title)}", pick.candidate_id),
    ]
    if runner_up is not None:
        options.append(
            MenuOption(
                MenuAction.SELECT,
                f"Add the runner-up: {product_display.display_title(runner_up.title)}",
                runner_up.candidate_id,
            )
        )
    if total > (2 if runner_up is not None else 1):
        options.append(MenuOption(MenuAction.SHOW_OPTIONS, f"See all {total} options"))
    options.append(MenuOption(MenuAction.NEW_SEARCH, "Search for something else"))
    options.extend(_list_option(workflow))
    options.append(MenuOption(MenuAction.CANCEL, "Start over"))
    return options


def cart_menu(workflow: PurchaseWorkflow) -> list[MenuOption]:
    options = []
    if workflow.cart:
        options.append(MenuOption(MenuAction.CHECKOUT, "Check out"))
    options.append(MenuOption(MenuAction.KEEP_SHOPPING, "Add something else"))
    if workflow.cart:
        options.append(MenuOption(MenuAction.REMOVE, "Remove an item"))
    options.append(MenuOption(MenuAction.CANCEL, "Start over"))
    return options


def checkout_menu() -> list[MenuOption]:
    return [
        MenuOption(MenuAction.CONFIRM, "Confirm — put these in my Amazon cart"),
        MenuOption(MenuAction.KEEP_SHOPPING, "Add something else first"),
        MenuOption(MenuAction.CANCEL, "Start over"),
    ]


def remove_menu(workflow: PurchaseWorkflow) -> list[MenuOption]:
    return [
        MenuOption(MenuAction.REMOVE, product_display.display_title(line.title), line.candidate_id)
        for line in workflow.cart
    ] + [MenuOption(MenuAction.VIEW_LIST, "Keep everything")]


def done_menu(workflow: PurchaseWorkflow) -> list[MenuOption]:
    return [
        MenuOption(MenuAction.KEEP_SHOPPING, "Shop for something else"),
        MenuOption(MenuAction.VIEW_LIST, "View your list"),
        MenuOption(MenuAction.CANCEL, "Start over"),
    ]


def store(workflow: PurchaseWorkflow, options: list[MenuOption]) -> list[MenuOption]:
    """Remember the choices so a number means what the user just read."""
    workflow.pending_menu = list(options)
    return options


def render_only(options: list[MenuOption], heading: str = "What next?") -> str:
    return menu.render(options, heading=heading)
