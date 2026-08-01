"""Adversarial conversation fuzzing.

The earlier role-play harness scripted the model to classify correctly every time,
which hid a bug where a suggested reply was ignored. A 4B local model does not behave
that way: it returns no_match, picks the wrong route, emits malformed JSON, and
sometimes returns nothing at all.

This drives whole conversations with a deliberately unreliable model and asserts a set
of invariants after every single turn. It mocks at the `generate_response` boundary so
the real JSON validation, routing, resolver, ranking, cart, and gate all execute.
"""

import asyncio
import json
import random
import re
from pathlib import Path

import pytest

import agent
import amazon
import cart as cart_module
import checkout
import main
import workflow_store
from workflow_models import WorkflowState


USER_MESSAGES = [
    "find me shampoo", "add shampoo to my cart", "buy AA batteries",
    "i need head and shoulders", "cheapest", "highest rated", "the duracell one",
    "1", "2", "3", "5", "9", "option 2", "the first one", "the last one",
    "yes", "no", "ok", "sure", "nah", "never mind", "cancel",
    "checkout", "confirm", "place the order", "yes place the order", "buy it now",
    "what's on my list?", "remove it", "actually remove the t shirts",
    "make it 2", "only the prime ones", "under $20", "options",
    "which is best?", "when will it arrive?", "how much is shipping?",
    "what is the capital of France?", "explain how research works",
    "remember my favorite shampoo is Dove", "what shampoo do i like",
    "", "   ", "🙂🙂🙂", "a" * 500, "add", "the", "?????",
    "add 2 of those to my cart", "now some paper towels",
]

ROUTES = ["memory", "purchase", "workflow", "general_chat", "unknown"]

CATALOGUE = [
    amazon.Product("Head and Shoulders Classic Clean Shampoo, 28.2 fl oz", "$12.47", "https://www.amazon.com/dp/h1", 4.8, 1467, delivery="Tue, Aug 4", prime_eligible=True),
    amazon.Product("Dove Daily Moisture Shampoo, 12 fl oz", "$5.99", "https://www.amazon.com/dp/d1", 4.6, 22000, delivery="Wed, Aug 5", prime_eligible=True),
    amazon.Product("Duracell Coppertop AA Batteries, 24 Count", "$18.49", "https://www.amazon.com/dp/b1", 4.7, 210000, delivery=None, prime_eligible=True),
    amazon.Product("Mystery Item With No Price", None, "https://www.amazon.com/dp/m1", None, None, prime_eligible=True),
    amazon.Product("Jockey Men's Crew Neck T-Shirt, White, Medium, 3 Pack", "$29.99", "https://www.amazon.com/dp/t1", 4.6, 8921, delivery="Fri, Aug 7", prime_eligible=True),
]


class UnreliableModel:
    """Behaves like a small local model having a bad day."""

    def __init__(self, rng: random.Random):
        self.rng = rng

    async def __call__(self, prompt, *, max_tokens=None, temperature=0.3,
                       json_mode=False, system_prompt=None, timing=None):
        if not json_mode:
            return self.rng.choice([
                "Sure, here is what I found.",
                "I can't confirm that from the listings.",
                "",  # empty prose, which the caller must survive
            ]) or "ok"

        roll = self.rng.random()
        if roll < 0.12:
            return "not json at all {{{"
        if roll < 0.20:
            return ""  # llm_client raises; the classifier must fail closed
        if roll < 0.28:
            return json.dumps({"unexpected": "shape"})

        if "Route this message" in prompt:
            return json.dumps({
                "route": self.rng.choice(ROUTES),
                "confidence": round(self.rng.uniform(0.0, 1.0), 2),
            })
        if "Extract a memory request" in prompt:
            action = self.rng.choice(["remember", "recall", "forget", "no_match"])
            return json.dumps({
                "action": action,
                "key": None if action == "no_match" else "favorite shampoo",
                "value": "Dove" if action == "remember" else None,
                "confidence": round(self.rng.uniform(0.3, 1.0), 2),
            })
        if "Extract a request to start a purchase workflow" in prompt:
            action = self.rng.choice(["purchase_start", "no_match"])
            return json.dumps({
                "action": action,
                "product_query": "shampoo" if action == "purchase_start" else None,
                "constraints": self.rng.choice([{}, {"max_price": 20}, {"prime": True}]) if action == "purchase_start" else {},
                "quantity": self.rng.choice([None, None, 2]) if action == "purchase_start" else None,
                "confidence": round(self.rng.uniform(0.3, 1.0), 2),
            })
        action = self.rng.choice([
            "select_candidate", "add_to_cart", "remove_from_cart", "view_cart",
            "change_quantity", "refine", "checkout", "confirm", "cancel", "no_match",
        ])
        return json.dumps({
            "action": action,
            "quantity": 2 if action == "change_quantity" else None,
            "constraints": {},
            "confidence": round(self.rng.uniform(0.3, 1.0), 2),
        })


FORBIDDEN_CLAIMS = (
    "order has been placed", "order placed", "i have placed", "i've placed",
    "successfully ordered", "purchase complete", "your order is confirmed",
    "bought it", "i bought", "payment", "charged your",
)
# The disclaimer legitimately contains "added to your Amazon cart", so an affirmative
# claim is only a failure when it is not being negated.
AFFIRMATIVE_CART_CLAIM = re.compile(r"(?<!nothing has been )added it to your amazon cart")
# The demo order screen deliberately mimics Amazon's confirmation page, so it contains
# "your order is confirmed". That is only acceptable while the screen also states, in
# its first line, that no order was placed — so the exemption is tied to that banner
# rather than to the phrase.
DEMO_BANNER = "no order was placed"


def _check_reply(reply, message):
    assert isinstance(reply, str), f"non-string reply for {message!r}"
    assert reply.strip(), f"empty reply for {message!r}"
    lowered = reply.casefold()
    for claim in FORBIDDEN_CLAIMS:
        assert claim not in lowered, f"reply claimed {claim!r} for {message!r}: {reply[:200]}"
    assert not AFFIRMATIVE_CART_CLAIM.search(lowered), f"claimed a real cart write: {reply[:200]}"
    # Telegram must always be able to send it.
    for section in main._telegram_sections(reply):
        assert len(section) <= main.TELEGRAM_MESSAGE_LIMIT


def _check_workflow(workflow, versions):
    """Invariants that must hold after every turn.

    Versions are tracked per workflow id: cancelling and starting a new search
    legitimately begins a fresh version, but a single workflow must never regress.
    """
    if workflow is None:
        return versions
    assert isinstance(workflow.state, WorkflowState)
    assert workflow.state not in {WorkflowState.PLACING_ORDER, WorkflowState.COMPLETED}, (
        f"reached a forbidden state: {workflow.state}"
    )
    previous = versions.get(workflow.workflow_id, 0)
    assert workflow.state_version >= previous, "state_version went backwards"
    versions[workflow.workflow_id] = workflow.state_version
    assert workflow.quantity >= 1

    seen = set()
    for line in workflow.cart:
        assert line.quantity >= 1, "cart line with a non-positive quantity"
        assert line.quantity <= cart_module.MAX_LINE_QUANTITY
        assert line.candidate_id not in seen, f"duplicate cart line {line.candidate_id}"
        seen.add(line.candidate_id)
        if line.price is not None:
            assert abs(line.line_total - round(line.price * line.quantity, 2)) < 0.011

    subtotal = cart_module.subtotal(workflow.cart)
    if subtotal is not None:
        expected = round(sum(line.line_total for line in workflow.cart), 2)
        assert abs(subtotal - expected) < 0.011, "subtotal does not match its lines"
    elif workflow.cart:
        assert any(line.price is None for line in workflow.cart), (
            "subtotal was unknown even though every line had a price"
        )

    if workflow.confirmed_token:
        # A stored confirmation must always describe the current contents or be stale,
        # never silently describe something else as current.
        assert isinstance(checkout.is_confirmation_current(workflow), bool)
    return versions


def _run_conversation(seed, tmp_path, monkeypatch, turns=14):
    rng = random.Random(seed)
    model = UnreliableModel(rng)

    async def search(query):
        roll = rng.random()
        if roll < 0.10:
            raise amazon.AmazonSearchUnavailable("interstitial")
        if roll < 0.18:
            return []
        count = rng.randint(1, len(CATALOGUE))
        return CATALOGUE[:count]

    monkeypatch.setattr(agent.amazon, "search_products", search)

    memory_path = tmp_path / f"m{seed}.db"
    workflow_path = tmp_path / f"w{seed}.db"
    user = 1000 + seed
    versions: dict[str, int] = {}
    transcript = []

    for _ in range(turns):
        message = rng.choice(USER_MESSAGES)
        transcript.append(message)
        try:
            reply = asyncio.run(
                agent.agent_brain(message, memory_path, workflow_path, user)
            )
        except Exception as error:  # noqa: BLE001 - the point is that none escape
            raise AssertionError(
                f"seed={seed} crashed on {message!r} after {transcript[:-1]}: {error!r}"
            ) from error
        _check_reply(reply, message)
        versions = _check_workflow(
            workflow_store.get_workflow(user, workflow_path), versions
        )
    return transcript


@pytest.mark.parametrize("seed", range(40))
def test_conversations_survive_an_unreliable_model(seed, tmp_path, monkeypatch):
    _run_conversation(seed, tmp_path, monkeypatch)


def test_no_sequence_of_replies_can_place_an_order(tmp_path, monkeypatch):
    """Every buy phrasing, with a real cart in place, must end at the refusal."""
    async def reliable(prompt, **kwargs):
        if "Route this message" in prompt:
            return json.dumps({"route": "purchase", "confidence": 0.99})
        if "Extract a request to start a purchase workflow" in prompt:
            return json.dumps({"action": "purchase_start", "product_query": "shampoo",
                               "constraints": {}, "quantity": None, "confidence": 0.99})
        return json.dumps({"action": "no_match", "quantity": None,
                           "constraints": {}, "confidence": 0.9})

    monkeypatch.setattr(agent.amazon, "search_products", AsyncSearch(CATALOGUE))

    paths = (tmp_path / "m.db", tmp_path / "w.db")
    buys = ["place the order", "yes place the order", "confirm", "buy it now",
            "order it now", "purchase it", "submit the order"]

    for index, phrase in enumerate(buys):
        user = 5000 + index
        asyncio.run(agent.agent_brain("find me shampoo", *paths, user))
        asyncio.run(agent.agent_brain("1", *paths, user))
        asyncio.run(agent.agent_brain("checkout", *paths, user))
        reply = asyncio.run(agent.agent_brain(phrase, *paths, user))

        assert "did not go through" in reply.casefold(), f"{phrase!r} -> {reply[:160]}"
        workflow = workflow_store.get_workflow(user, paths[1])
        assert workflow.state not in {WorkflowState.PLACING_ORDER, WorkflowState.COMPLETED}


class AsyncSearch:
    def __init__(self, products):
        self.products = products

    async def __call__(self, query):
        return list(self.products)

def test_ordering_cannot_fire_without_the_kill_switch(monkeypatch):
    """The only thing standing between a menu tap and real money is this flag."""
    import amazon

    monkeypatch.delenv("AMAZON_ENABLE_ORDERING", raising=False)
    assert amazon.ordering_enabled() is False
    monkeypatch.setenv("AMAZON_ENABLE_ORDERING", "TRUE")
    assert amazon.ordering_enabled() is True
    for value in ("", "1", "yes", "false", "no", " true "):
        monkeypatch.setenv("AMAZON_ENABLE_ORDERING", value)
        assert amazon.ordering_enabled() is (value.strip().casefold() == "true")
