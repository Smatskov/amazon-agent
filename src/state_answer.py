"""Answers questions about what the agent already knows, without a language model.

"Is there anything in my cart?" used to reach the model, which answered "I don't have
access to your shopping cart" while the list sat in the database beside it. Questions
about stored state are answered here from that state, in fixed templates.

Returns None when the message is not one of these questions, so the caller falls
through to searching. Nothing here searches, stores, or mutates.
"""

import re

import cart
import product_display
from workflow_models import PurchaseWorkflow


LIST_QUESTION = re.compile(
    r"\b(?:what(?:'s| is| are)?|anything|something|do i have|have i got|show me)\b[^?]*"
    r"\b(?:cart|basket|list|added|selected|picked)\b"
    r"|^(?:cart|list|basket|my list|my cart)\??$",
    re.IGNORECASE,
)
TOTAL_QUESTION = re.compile(
    r"\b(?:how much|what(?:'s| is)? the (?:total|subtotal|cost|price))\b", re.IGNORECASE
)
RESULTS_QUESTION = re.compile(
    r"\b(?:what did you (?:show|find)|show (?:me )?(?:the )?(?:results|options) again|"
    r"what were the options|repeat the options)\b",
    re.IGNORECASE,
)
# "which of these is organic / unscented / ..." — answerable from the stored titles.
ATTRIBUTE_QUESTION = re.compile(
    r"\bwhich (?:of these |one )?(?:is|are|has|have)\s+([a-z][a-z -]{2,30})\b", re.IGNORECASE
)
NOT_VERIFIABLE = (
    "That's only what the listing titles say — I can't verify it beyond that."
)


def answer(message: str, workflow: PurchaseWorkflow | None) -> str | None:
    """Return a templated answer, or None when this is not a state question."""
    text = (message or "").strip()
    if not text:
        return None

    if TOTAL_QUESTION.search(text):
        return _total(workflow)
    if LIST_QUESTION.search(text):
        return _list(workflow)
    if RESULTS_QUESTION.search(text):
        return _results(workflow)

    attribute = ATTRIBUTE_QUESTION.search(text)
    if attribute and workflow and workflow.candidates:
        return _attribute(workflow, attribute.group(1).strip())
    return None


def _list(workflow: PurchaseWorkflow | None) -> str:
    lines = workflow.cart if workflow else []
    if not lines:
        return (
            "Your list is empty — nothing added yet.\n\n"
            "<i>This is the list I hold. I'm not checking your Amazon cart.</i>"
        )
    count = cart.item_count(lines)
    body = "\n".join(
        f"{index} · <b>{product_display.text(product_display.display_title(line.title))}</b>"
        f"\n    {product_display.text(line.price_text or 'price not shown')}"
        + (f" · ×{line.quantity}" if line.quantity > 1 else "")
        for index, line in enumerate(lines, start=1)
    )
    subtotal = cart.subtotal(lines)
    total = "unavailable — an item showed no price" if subtotal is None else f"${subtotal:.2f}"
    return (
        f"🧺 <b>Your list</b> — {count} item{'' if count == 1 else 's'}\n\n{body}\n\n"
        f"<b>Subtotal:</b> {total}\n\n"
        "<i>This is the list I hold. I'm not checking your Amazon cart.</i>"
    )


def _total(workflow: PurchaseWorkflow | None) -> str:
    lines = workflow.cart if workflow else []
    if not lines:
        return "Your list is empty, so there's nothing to total yet."
    subtotal = cart.subtotal(lines)
    if subtotal is None:
        return (
            "I can't total your list — at least one item didn't show a price.\n\n"
            "<i>Shipping, tax and delivery aren't included in any case.</i>"
        )
    count = cart.item_count(lines)
    return (
        f"<b>Subtotal:</b> ${subtotal:.2f} for {count} item{'' if count == 1 else 's'}.\n\n"
        "<i>Items only — shipping, tax and delivery aren't included.</i>"
    )


def _results(workflow: PurchaseWorkflow | None) -> str:
    if not workflow or not workflow.candidates:
        return "I haven't shown you any results yet. Tell me what to look for."
    body = "\n\n".join(
        product_display.candidate_line(index, candidate)
        for index, candidate in enumerate(workflow.candidates, start=1)
    )
    goal = product_display.text(workflow.normalized_product_goal)
    return f"🔎 <b>Results for {goal}</b>\n\n{body}"


def _attribute(workflow: PurchaseWorkflow, attribute: str) -> str:
    """Answer from the stored titles, and say that is all it proves."""
    needle = attribute.casefold().split()[0]
    matches = [
        index
        for index, candidate in enumerate(workflow.candidates, start=1)
        if needle in candidate.title.casefold()
    ]
    shown = product_display.text(attribute)
    if not matches:
        return f"None of the titles mention <b>{shown}</b>.\n\n<i>{NOT_VERIFIABLE}</i>"
    listed = ", ".join(str(index) for index in matches)
    plural = "s" if len(matches) > 1 else ""
    return f"Option{plural} {listed} mention <b>{shown}</b> in the title.\n\n<i>{NOT_VERIFIABLE}</i>"
