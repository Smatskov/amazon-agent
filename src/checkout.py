"""Checkout preparation and the confirmation gate that precedes an order.

The gate exists to make the final, irreversible step deliberate. Two rules matter:

1. A confirmation applies to *exact* order contents. Changing anything invalidates it
   (ADR-026), which `confirmation_token()` enforces by hashing the contents.
2. Order placement is not implemented. `place_order()` exists so that the refusal is
   explicit and testable rather than an accidental gap, and it always raises.

Nothing in this module contacts Amazon. The subtotal is arithmetic over prices that a
read-only search already returned; shipping, tax, and delivery are not known and are
reported as unknown rather than estimated.
"""

from dataclasses import dataclass
import hashlib
import json

import cart
from workflow_models import CartLine, PurchaseWorkflow


# Facts a real checkout would show that this agent cannot see yet. They are listed to
# the user so a subtotal is never mistaken for an order total.
UNKNOWN_AT_CHECKOUT = (
    "shipping cost",
    "tax",
    "delivery date",
    "the delivery address Amazon would use",
)


class OrderPlacementDisabled(RuntimeError):
    """Raised if anything ever attempts to place an order."""


@dataclass(frozen=True, slots=True)
class CheckoutSummary:
    """Exactly what the user is being asked to confirm."""

    lines: list[CartLine]
    item_count: int
    subtotal: float | None
    token: str
    unknown: tuple[str, ...] = UNKNOWN_AT_CHECKOUT

    @property
    def is_empty(self) -> bool:
        return not self.lines


def confirmation_token(workflow: PurchaseWorkflow) -> str:
    """A stable fingerprint of the exact order contents.

    Quantity, price, and item identity all feed the hash, so any edit produces a
    different token and a previous confirmation stops matching.
    """
    contents = [
        {
            "candidate_id": line.candidate_id,
            "title": line.title,
            "price": line.price,
            "quantity": line.quantity,
        }
        for line in workflow.cart
    ]
    payload = json.dumps(contents, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def summarize(workflow: PurchaseWorkflow) -> CheckoutSummary:
    return CheckoutSummary(
        lines=list(workflow.cart),
        item_count=cart.item_count(workflow.cart),
        subtotal=cart.subtotal(workflow.cart),
        token=confirmation_token(workflow),
    )


def is_confirmation_current(workflow: PurchaseWorkflow) -> bool:
    """True only when the user confirmed these exact contents."""
    return bool(workflow.confirmed_token) and workflow.confirmed_token == confirmation_token(workflow)


def place_order(workflow: PurchaseWorkflow) -> None:
    """Never places an order.

    Kept as a named, always-raising boundary so the absence of ordering is asserted by
    a test rather than assumed. Implementing this requires the safety controls in
    ADR-026: authorization, price limits, duplicate prevention, idempotency, audit
    records, and verification of the external result.
    """
    raise OrderPlacementDisabled(
        "Order placement is not implemented. No code path may submit an Amazon order."
    )
