"""Shared request metadata for orchestration stages that need common context."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Known request facts; future workflow data remains optional and unset today."""

    original_user_request: str
    intent: str | None = None
    search_query: str | None = None
    confidence: float | None = None
    requires_confirmation: bool = False
    future_order_history_candidate: bool | None = None
