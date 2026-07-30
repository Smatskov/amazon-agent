# Architecture

## Boundaries

- `main.py` owns Telegram transport, authorization, and startup configuration validation.
- `agent.py` owns orchestration and is the only action executor.
- `llm_client.py` owns model communication.
- `intent_classifier.py` returns validated structured interpretation only.
- `memory.py` owns preference memory; `workflow_store.py` owns persistent purchasing workflow state.
- `amazon.py` owns all Amazon/Playwright operations.
- `product_evaluator.py` compares supplied product records with a fact-bounded prompt, and serializes stored candidates as model-facing context.
- `ranking.py` owns deterministic hard filtering and ordering; `product_display.py` owns Telegram presentation of candidates.
- `workflow_reply.py` reads unambiguous replies to the agent's own question without a model call; `candidate_resolver.py` resolves references to stored candidates after the model has classified a selection.
- `request_context.py` carries request facts; `workflow_models.py` carries workflow records.
- `response_policy.py` owns prompt and response-format policy; `timing.py` owns request-scoped latency measurement.

Ordering, budget checks, and option numbers are arithmetic and policy, so they are deterministic code. The model classifies intent and explains; it never sorts, filters, or selects.

LLMs may interpret language but may not execute memory/Amazon operations, place orders, or invent Amazon facts. The agent validates model output. Amazon returns typed verified facts. Workflow state determines legal actions; code, never a prompt, enforces the confirmation gate.

There is no `dialogue_interpreter.py`; hierarchical semantic extraction in `intent_classifier.py` replaced it (ADR-033). `product_evaluator.py` does not rank or filter deterministically — no deterministic ranking module exists yet, and it is reached only through the explicit `search:` alias.

## Message routing order

```text
1. explicit colon aliases        remember:/recall:/forget:/search:
2. deterministic workflow reply   yes / no / cancel / "3" — no model call (ADR-043)
3. semantic interpretation        router → specialist → validated SemanticAction
4. pending-question fallback      re-ask rather than answer something unrelated
5. general conversation           candidate facts supplied when a workflow is active
```

## Two search paths

These are deliberately different and must not be conflated:

```text
search: <query>       → amazon.search_products → product_evaluator → LM Studio prose
natural purchase start → amazon.search_products → Candidate records → workflow_store
```

Only the second path creates a workflow. Only the first path invokes the evaluator.

## Persistent workflow state machine

Initial policy is one active workflow per Telegram user, persisted in SQLite across restarts. States are `idle`, `awaiting_request_clarification`, `checking_purchase_history`, `awaiting_repurchase_confirmation`, `searching_products`, `presenting_candidates`, `awaiting_product_selection`, `refining_search`, `preparing_cart`, `preparing_checkout`, `awaiting_checkout_confirmation`, `placing_order`, `completed`, `cancelled`, `failed`, and `paused`.

Current behavior asks a clarifying question when a shopping request has no product (persisted as `awaiting_request_clarification`), creates a workflow from real read-only Amazon results, filters and ranks them deterministically, presents typed candidates, answers questions about them from stored facts, narrows them on refinement, stores selection and quantity, and allows cancellation. It never enters cart, checkout-confirmation, placement, or completed-order transitions; those states are type-level placeholders for later milestones. Every state change goes through `workflow_store.transition()` so `state_version` stays a reliable basis for the future confirmation gate. A workflow untouched for 24 hours stops counting as active (ADR-046). Purchase history remains separate from preferences.

## Stored data

Each workflow stores Telegram user ID, workflow ID, state/version, original request, normalized goal, constraints, pending question, candidates, selected candidate, quantity, safe conversation summary, timestamps, and terminal status. It never stores payment data, cookies, secrets, or full addresses.

Records are deserialized field-tolerantly: unknown keys in a stored payload are ignored rather than raising, so a schema change cannot make an existing row unreadable.
