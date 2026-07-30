"""The user's chosen items, as pure operations over stored candidate facts.

This is a *preview* basket held in the agent's own database. It is deliberately not
Amazon's cart: nothing here contacts Amazon or changes anything in the user's
account. Every value is copied from a candidate that a read-only Amazon search
returned, so a line can only show what Amazon actually displayed.
"""

from workflow_models import Candidate, CartLine


MAX_LINE_QUANTITY = 99


def add(cart: list[CartLine], candidate: Candidate, quantity: int = 1) -> list[CartLine]:
    """Add an item, combining with an existing line for the same candidate."""
    quantity = _bounded(quantity)
    existing = find(cart, candidate.candidate_id)
    if existing:
        return set_quantity(cart, candidate.candidate_id, existing.quantity + quantity)
    return [
        *cart,
        CartLine(
            candidate_id=candidate.candidate_id,
            title=candidate.title,
            price=candidate.price,
            quantity=quantity,
            price_text=candidate.price_text,
            source_url=candidate.source_url,
        ),
    ]


def remove(cart: list[CartLine], candidate_id: str) -> list[CartLine]:
    return [line for line in cart if line.candidate_id != candidate_id]


def set_quantity(cart: list[CartLine], candidate_id: str, quantity: int) -> list[CartLine]:
    """Set a line quantity; zero or less removes the line."""
    if quantity < 1:
        return remove(cart, candidate_id)
    quantity = _bounded(quantity)
    return [
        CartLine(
            candidate_id=line.candidate_id,
            title=line.title,
            price=line.price,
            quantity=quantity,
            price_text=line.price_text,
            source_url=line.source_url,
        )
        if line.candidate_id == candidate_id
        else line
        for line in cart
    ]


def find(cart: list[CartLine], candidate_id: str) -> CartLine | None:
    return next((line for line in cart if line.candidate_id == candidate_id), None)


def item_count(cart: list[CartLine]) -> int:
    return sum(line.quantity for line in cart)


def subtotal(cart: list[CartLine]) -> float | None:
    """Total of every line, or None when any line has no price to add up.

    A partial total would read as the full cost of the order, so an unknown price
    makes the whole subtotal unknown rather than quietly smaller than reality.
    """
    if not cart or any(line.price is None for line in cart):
        return None
    return round(sum(line.line_total for line in cart), 2)


def _bounded(quantity: int) -> int:
    return max(1, min(int(quantity), MAX_LINE_QUANTITY))
