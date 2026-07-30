"""Evaluate structured products without interacting with Amazon or Telegram."""

from dataclasses import asdict, dataclass
import json
import re

from amazon import Product
from llm_client import generate_response
import ranking
from request_context import RequestContext
from response_policy import PRODUCT_EVALUATION_MAX_TOKENS, PRODUCT_EVALUATION_SYSTEM_PROMPT
from workflow_models import Candidate


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """The current recommendation plus metadata reserved for future workflows."""

    recommendation: str
    appears_to_be_reorder: bool


def candidate_context(candidates: list[Candidate]) -> str:
    """Serialize the options currently shown so the model can answer about them.

    A question like "what's the difference between the first two?" is answerable only
    if the facts travel with it. Without this the model was asked about products it
    could not see, which is exactly how invented product facts get produced.
    """
    return json.dumps(
        [
            {
                "option": index,
                "title": candidate.title,
                "price": candidate.price_text,
                "unit_price": None if unit is None else round(unit, 2),
                "rating": candidate.rating,
                "review_count": candidate.review_count,
                "prime_eligible": candidate.prime_eligible,
            }
            for index, candidate in enumerate(candidates, start=1)
            for unit in [ranking.unit_price(candidate)]
        ]
    )


def _appears_to_request_reorder(context: RequestContext) -> bool:
    """Recognize clear reorder phrasing without looking up or assuming history."""
    request_text = " ".join(
        value
        for value in (context.original_user_request, context.search_query)
        if value
    ).lower()
    return bool(
        re.search(r"\breorder\b|\bfrom last time\b|\b(?:my )?usual\b", request_text)
    )


def _evaluation_prompt(
    context: RequestContext, products: list[Product], appears_to_be_reorder: bool
) -> str:
    """Build a fact-bounded comparison request for the local language model."""
    product_data = json.dumps([asdict(product) for product in products])
    return (
        "Evaluate the supplied Amazon product records for the user's request. "
        "Use only the structured product data below; do not invent or assume missing "
        "product facts. Compare price, rating, review count, availability, Prime "
        "eligibility only when it is provided, value for money, and likely fit for the "
        "user's request. Explain your reasoning and tradeoffs, recommend the top choice, "
        "and recommend one budget alternative when appropriate. If the records do not "
        "support a recommendation, say so clearly. Do not suggest cart, checkout, or "
        "purchasing actions. A reorder signal does not authorize order-history access or "
        "allow assumptions about prior products; do not invent past-order facts. "
        f"User request: {context.original_user_request}\n"
        f"Search query: {context.search_query}\n"
        f"Reorder-request signal: {appears_to_be_reorder}\n"
        f"Structured products: {product_data}"
    )


async def evaluate_products(
    context: RequestContext, products: list[Product]
) -> EvaluationResult:
    """Return the local model's recommendation for structured product facts only."""
    appears_to_be_reorder = _appears_to_request_reorder(context)
    recommendation = await generate_response(
        _evaluation_prompt(context, products, appears_to_be_reorder),
        max_tokens=PRODUCT_EVALUATION_MAX_TOKENS,
        system_prompt=PRODUCT_EVALUATION_SYSTEM_PROMPT,
    )
    return EvaluationResult(recommendation, appears_to_be_reorder)
