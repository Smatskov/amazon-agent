"""Typed, persistent-safe models for the read-only purchasing workflow."""

from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from menu import MenuOption


class WorkflowState(StrEnum):
    IDLE = "idle"
    AWAITING_REQUEST_CLARIFICATION = "awaiting_request_clarification"
    CHECKING_PURCHASE_HISTORY = "checking_purchase_history"
    AWAITING_REPURCHASE_CONFIRMATION = "awaiting_repurchase_confirmation"
    SEARCHING_PRODUCTS = "searching_products"
    PRESENTING_CANDIDATES = "presenting_candidates"
    AWAITING_PRODUCT_SELECTION = "awaiting_product_selection"
    REFINING_SEARCH = "refining_search"
    PREPARING_CART = "preparing_cart"
    PREPARING_CHECKOUT = "preparing_checkout"
    AWAITING_CHECKOUT_CONFIRMATION = "awaiting_checkout_confirmation"
    PLACING_ORDER = "placing_order"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    PAUSED = "paused"


TERMINAL_STATES = {WorkflowState.COMPLETED, WorkflowState.CANCELLED, WorkflowState.FAILED}


def _known_fields(model, record: dict[str, Any]) -> dict[str, Any]:
    """Drop keys a stored record has but this version of the model does not.

    Persisted workflows outlive the code that wrote them, so adding or removing a
    field must not make an existing row unreadable.
    """
    names = {model_field.name for model_field in fields(model)}
    return {key: value for key, value in record.items() if key in names}


@dataclass(frozen=True, slots=True)
class Candidate:
    """Product facts copied from one read-only Amazon search result."""

    candidate_id: str
    title: str
    brand: str | None
    price: float | None
    # As Amazon stated it on the card, e.g. "Tue, Aug 4". Never inferred.
    delivery_label: str | None = None
    rating: float | None = None
    option_label: str = "Amazon result"
    available: bool = True
    price_text: str | None = None
    review_count: int | None = None
    prime_eligible: bool | None = None
    source_url: str | None = None
    # Amazon's own per-unit price, copied verbatim ("$2.27/fluid ounce"). Never
    # computed here: dividing a price by a size read out of a title invents a fact.
    unit_price_text: str | None = None
    image_url: str | None = None


@dataclass(frozen=True, slots=True)
class CartLine:
    """One item the user chose, copied from a stored candidate.

    Prices are copied, never recomputed from a remembered value, so a line can only
    ever show what Amazon actually displayed for that result.
    """

    candidate_id: str
    title: str
    price: float | None
    quantity: int
    price_text: str | None = None
    source_url: str | None = None

    @property
    def line_total(self) -> float | None:
        return None if self.price is None else round(self.price * self.quantity, 2)


@dataclass(slots=True)
class PurchaseWorkflow:
    telegram_user_id: int
    workflow_id: str
    state: WorkflowState
    state_version: int
    original_request: str
    normalized_product_goal: str
    constraints: dict[str, Any] = field(default_factory=dict)
    pending_question: str | None = None
    candidates: list[Candidate] = field(default_factory=list)
    selected_candidate_id: str | None = None
    # The numbered choices last shown. Persisted so the number the user sees is the
    # number the agent resolves against, even across a restart.
    pending_menu: list[MenuOption] = field(default_factory=list)
    # Images for the results just shown, as (url, caption) pairs. Presentation state,
    # consumed once by the transport and cleared, so a later reply cannot resend a
    # gallery for products that are no longer on screen.
    pending_photos: list[list[str]] = field(default_factory=list)
    # What the real Amazon cart held when it was last read, as [title, price, mine].
    # The order the user would place is the whole cart, so the last two screens are
    # built from this rather than from the agent's own list.
    amazon_cart: list[list] = field(default_factory=list)
    # Where an order would ship and what would pay: [address, card]. Display only.
    destination: list = field(default_factory=list)
    # Children of a variation listing awaiting a choice: [asin, label, url].
    pending_variants: list[list] = field(default_factory=list)
    cart: list[CartLine] = field(default_factory=list)
    # Identifies the exact order contents the user last confirmed. Any change to the
    # cart produces a different token, which invalidates the confirmation (ADR-026).
    confirmed_token: str | None = None
    quantity: int = 1
    conversation_summary: str = ""
    created_at: str = ""
    updated_at: str = ""
    completion_status: str | None = None

    @classmethod
    def new(cls, telegram_user_id: int, request: str, goal: str) -> "PurchaseWorkflow":
        now = datetime.now(UTC).isoformat()
        return cls(
            telegram_user_id=telegram_user_id,
            workflow_id=str(uuid4()),
            state=WorkflowState.SEARCHING_PRODUCTS,
            state_version=1,
            original_request=request,
            normalized_product_goal=goal,
            created_at=now,
            updated_at=now,
        )

    @property
    def is_active(self) -> bool:
        return self.state not in TERMINAL_STATES

    def selected_candidate(self) -> Candidate | None:
        return next(
            (candidate for candidate in self.candidates if candidate.candidate_id == self.selected_candidate_id),
            None,
        )

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "PurchaseWorkflow":
        data = _known_fields(cls, record)
        data["state"] = WorkflowState(data["state"])
        data["candidates"] = [
            Candidate(**_known_fields(Candidate, candidate))
            for candidate in record.get("candidates") or []
        ]
        data["cart"] = [
            CartLine(**_known_fields(CartLine, line)) for line in record.get("cart") or []
        ]
        # A retired action deserializes to None and is dropped, so a stored menu can
        # never make an entire workflow unreadable.
        restored = (
            MenuOption.from_record(option)
            for option in record.get("pending_menu") or []
            if isinstance(option, dict)
        )
        data["pending_menu"] = [option for option in restored if option is not None]
        return cls(**data)

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["state"] = self.state.value
        record["pending_menu"] = [option.to_record() for option in self.pending_menu]
        return record
