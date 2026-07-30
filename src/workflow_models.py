"""Typed, persistent-safe models for the read-only purchasing workflow."""

from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


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
    delivery_label: str | None = None
    rating: float | None = None
    option_label: str = "Amazon result"
    available: bool = True
    price_text: str | None = None
    review_count: int | None = None
    prime_eligible: bool | None = None
    source_url: str | None = None


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

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["state"] = self.state.value
        return record

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "PurchaseWorkflow":
        data = _known_fields(cls, record)
        data["state"] = WorkflowState(data["state"])
        data["candidates"] = [
            Candidate(**_known_fields(Candidate, candidate))
            for candidate in record.get("candidates") or []
        ]
        return cls(**data)
