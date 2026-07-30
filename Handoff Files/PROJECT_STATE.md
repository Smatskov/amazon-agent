# Amazon AI Purchasing Agent — Project State

Last updated: 2026-07-30

Status: The shopping conversation is complete through to the order gate: clarify → search → filter → rank → present → answer questions → refine → add to list → adjust quantities → remove → checkout summary → confirm → **refused**.

**Order placement is not implemented and is unreachable.** The list the agent builds is its own SQLite state; nothing is ever added to the user's Amazon cart.

Historic decisions remain append-only in `Handoff Files/DECISIONS.md` (ADR-001 through ADR-050).

## What is verified, and what is not

Verified in this session (automated level only):

- **232 tests pass** (`.venv/bin/python -m pytest -q`). Syntax, imports, and `git diff --check` also pass.
- Three multi-turn conversations were **role-played end to end** against mocked LM Studio and mocked Amazon, with the user-visible output read as a person would read it. This found five bugs the unit tests did not (below).
- Deterministic with no model call: option numbers, `yes`, `no`, `cancel`, `checkout`, and every buy-phrasing.
- The order gate: confirming records approval and then refuses. A test asserts no module outside `checkout.py` even mentions `place_order`, and that `agent.py` never names `PLACING_ORDER` or `COMPLETED`.

Not verified or not implemented:

- **No live verification was performed.** LM Studio, Amazon, and Telegram were not contacted at any point in this session. No bot was started and no live probe script was run.
- **Headless search is unverified against real Amazon.** The default flipped to background mode (ADR-047). If searches begin failing, set `AMAZON_BROWSER_HEADLESS=false` and re-run `scripts/amazon_profile_login.py`. This is the highest-risk unverified change in this session.
- No writing to the real Amazon cart, no payment, no ordering, no order-history lookup, no purchase-history storage. **No code path in this repository can place an order.** `amazon.py` exposes only `search_products()` and a manual sign-in helper; `AmazonWorkflowGateway` is a `Protocol` with no implementation and no callers.
- Amazon delivery estimates, availability, and shipping costs are still not extracted. The agent now says so when asked rather than ignoring the question.

Verified previously and unchanged: LM Studio serves `qwen3.5-4b-mlx` with the corrected Qwen template; semantic routing uses strict compact JSON with typed validation and soft/hard timeouts; the model executes no memory, browser, workflow, or purchase action; read-only Amazon search against a manually signed-in persistent profile returns canonical product records.

## What role-playing found that the unit tests did not

Each came from reading a simulated conversation as a user; each now has a regression test:

1. **Candidate ids collided across searches.** Ids were `amazon-result-{index}`, so the first result of a second search merged into an unrelated line already on the list — adding a t-shirt silently raised the quantity of a shampoo. Ids now come from the ASIN (ADR-050).
2. **"yes place the order" was answered by the language model.** It classified as general chat. With a real model the reply could have been "Your order has been placed." Buy-phrasing is now deterministic (ADR-049).
3. **Display titles dropped colour and size.** "Jockey Men's Classic Crew Neck T-Shirt, White, Medium, 3 Pack" rendered as "…T-Shirt, 3 Pack".
4. **"remove the t shirts" did not match "T-Shirt"** — plural/singular mismatch in token matching.
5. **"add it to the cart" could not resolve "it"**, and a second product request was refused with "you already have an active workflow" — the worst moment in the conversation.

Also fixed: "add 2 of those to my cart" used to reply "Updated the workflow quantity to 2", which reads as though a cart action succeeded; and questions about the list reached the model with no knowledge of the list.

## Actual runtime call flow

```text
Telegram Update
  → src/main.py: authorization, metadata-only log, “Thinking…”, final reply/edit
  → src/agent.py

1. explicit colon aliases
     remember:/recall:/forget:  → memory.py            → data/memory.db
     search: <query>            → amazon.py            → product_evaluator.py → LM Studio prose

2. deterministic workflow reply (only when a workflow is active)   [no model call]
     workflow_reply.py → cancel
                       → confirm_order → checkout gate → REFUSED
                       → checkout      → checkout.summarize
                       → select position / affirm / decline

3. semantic interpretation
     intent_classifier.py → llm_client.py → validated SemanticAction
       ├─ memory action    → memory.py
       ├─ purchase start   → amazon.py → Candidate conversion (ASIN identity)
       │                     → ranking.apply_constraints → ranking.rank
       │                     → workflow_store.py → product_display.py
       ├─ workflow action  → refine           → ranking (re-filter stored results)
       │                   → select/add       → candidate_resolver.py → cart.add
       │                   → remove/view/qty  → cart.py
       │                   → checkout         → checkout.summarize
       │                   → confirm          → gate → REFUSED
       │                   → cancel
       └─ general/unknown

4. pending-question fallback → re-ask instead of answering something unrelated

5. general conversation → response_policy.py + llm_client.py, with
     product_evaluator.candidate_context() and .cart_context() supplying the
     options on screen and the current list
```

Important actual-path distinctions:

- `product_evaluator.evaluate_products()` is **not** called by the natural-language purchase path. It is active only through the explicit `search:` alias.
- Candidates are persisted in displayed order, so a reply of "3" always means the third line shown.
- A refinement re-filters and re-orders results already retrieved; it does not run a second Amazon search.
- `timing.py` measures the semantic path. Explicit aliases and deterministic workflow replies return before the semantic task and produce no full timing record.
- Selecting an option and adding it to the list are one step, because they are one intent.
- A second purchase request searches again and keeps the list, so one conversation can gather several products.
- Candidate identity is the ASIN from the product URL, never the result's position (ADR-050).

## Production module inventory

| Module | Lines | Lifecycle | Responsibility | Safety boundary / limitation |
| --- | --- | --- | --- | --- |
| `main.py` | 83 | Active entry point | Telegram transport, authorization, startup configuration validation, 4,096-character sectioning. | Logs only user ID and message length. Authorization fails closed: an unset ID becomes `0`, matching no user. |
| `agent.py` | 799 | Active orchestrator | Routing, workflow decisions, and the only action executor. | Sole action executor. Purchase work is read-only search plus a local list. Never references `PLACING_ORDER` or `COMPLETED`. |
| `cart.py` | 78 | Active list operations (**new**) | Pure add/remove/quantity/subtotal over stored candidate facts. | Touches nothing outside the workflow record. One unknown price makes the whole subtotal unknown rather than quietly smaller. |
| `checkout.py` | 95 | Active order gate (**new**) | Order summary, confirmation fingerprint, and the refusal to order. | `place_order()` exists only to raise `OrderPlacementDisabled`. Any change to contents invalidates a confirmation (ADR-026). |
| `llm_client.py` | 138 | Active LM Studio boundary | OpenAI-compatible communication with LM Studio. | Prefers visible content; accepts `reasoning_content` only when it parses as exactly one JSON object. **Known duplication:** two near-parallel request paths (streaming when timed, non-streaming otherwise). |
| `intent_classifier.py` | 268 | Active semantic interpreter | Router then specialist, returning a validated `SemanticAction`. | Validators are pure functions over parsed JSON. No tool, storage, Telegram, or browser access. |
| `workflow_reply.py` | 106 | Active deterministic interpreter (**new**) | Reads unambiguous replies to the agent's own question without a model call. | Deliberately strict: every significant word must belong to one vocabulary, or it defers to the model. Returns an intent only; executes nothing. |
| `ranking.py` | 164 | Active decision policy (**new**) | Hard constraint filtering and inspectable ordering, as pure functions. | Never invents a value to sort by. A candidate missing the compared fact is kept and listed last, and the basis plus any caveat are reported to the user. |
| `product_display.py` | 198 | Active presentation | Display titles, fact lines, candidate-aware hints, list and order-summary rendering. | Shortens and arranges stored facts; adds none. Enforces the preview disclaimer on every candidate message. |
| `candidate_resolver.py` | 163 | Active selection helper | Resolves comparisons, positions, and described words against stored candidates. | Zero or ambiguous matches produce clarification and never guess. Lenient by design: only reached after the model classified a selection. |
| `memory.py` | 55 | Active persistence | Explicit key/value memory. | No preference inference, purchase history, or model-controlled write path. |
| `amazon.py` | 335 | Active read-only browser boundary | Playwright persistent-context search and public-result extraction. | Rejects repository-local profile paths and advertising URLs. `AmazonWorkflowGateway` is a type-only future interface with no implementation. |
| `product_evaluator.py` | 107 | Active on two narrow paths | `evaluate_products()` for the `search:` alias; `candidate_context()` serializes stored candidates for conversation. | **Mismatch:** evaluator prose reaches Telegram unvalidated on the alias path; no deterministic fact-claim checker exists. |
| `workflow_models.py` | 142 | Active model | Typed workflow and candidate records. | Excludes payment data, cookies, and addresses. Deserializes field-tolerantly (ADR-041). |
| `workflow_store.py` | 107 | Active persistence | One workflow per user; `transition()` is the only state-change path. | Enforces expiry after 24 hours (ADR-046). Does not yet reject illegal transitions. |
| `timing.py` | 96 | Active diagnostics | Request-scoped latency measurement. | Observability only; never alters routing. |
| `response_policy.py` | 46 | Active prompt policy | System prompts, token limits, sentence-safe normalization. | Separates conversational and product-fact contracts. |
| `request_context.py` | 15 | Active on the alias path | Immutable request metadata. | `requires_confirmation` and `future_order_history_candidate` remain unset placeholders. |

## Test coverage map

Last verified full run: **232 passed**. All external boundaries mocked; temporary SQLite throughout; `tests/conftest.py` makes writing to `data/` structurally impossible.

| Test file | Tests | Responsibility covered |
| --- | --- | --- |
| `tests/test_workflow_reply.py` | 35 | Affirmatives, refusals, cancellations, buy-phrasing, checkout phrasing, explicit positions, out-of-range numbers, empty lists, and the mixed sentences that must defer to the model. |
| `tests/test_shopping_flow.py` | 24 | Picking, quantities, two searches into one list, removal by description, candidate identity, delivery-question acknowledgement, list context reaching the model, checkout summary, every buy-phrasing reaching the refusal, declining, and confirmation invalidation. |
| `tests/test_ranking.py` | 23 | Pack counts, unit price, sort detection, unit vs total fallback, missing prices and ratings, tie-breaking, constraint filtering. |
| `tests/test_candidate_resolution.py` | 20 | Numeric and ordinal selection, brand references, pack-count variants, comparisons, ambiguity, out-of-range numbers, empty lists, missing facts. |
| `tests/test_cart_and_checkout.py` | 19 | Cart arithmetic and bounds, unknown-price subtotals, summary contents, confirmation invalidation on any change, and the guarantees that `place_order` always raises, nothing calls it, and `amazon.py` exposes no ordering capability. |
| `tests/test_conversation_continuity.py` | 18 | Clarifying questions answered by the next message, refusals, deterministic selection, question answering with facts, refinement, workflow expiry. |
| `tests/test_product_display.py` | 16 | Display titles preserving colour, size, and pack count while dropping marketing copy; fact lines; candidate-aware hints; spacing; filter and caveat notes. |
| `tests/test_complete_response_flow.py` | 14 | LM Studio content handling, JSON mode, guarded reasoning fallback, empty responses, model-error text, Telegram sectioning. |
| `tests/test_agent_memory_commands.py` | 12 | Memory aliases, semantic memory actions, whole-word shopping markers, clarification persistence, timeouts, `search:` delegation. |
| `tests/test_intent_classifier.py` | 11 | Router-then-specialist prompting, validation, confidence, scalar constraints, general-chat short circuit. |
| `tests/test_amazon_profile.py` | 11 | Profile configuration, background default, always-visible sign-in, bounded close, card metadata, query-tab reuse, canonical URL filtering. |
| `tests/test_purchase_workflow.py` | 10 | Preview start, real-record conversion, Amazon failure safety, legacy invalidation, state-version advancement, tolerant deserialization, second search reusing the workflow. |
| `tests/test_semantic_evaluation_corpus.py` | 7 | Offline semantic-contract corpus. |
| `tests/test_memory.py` | 6 | SQLite memory behavior. |
| `tests/test_response_policy.py` | 3 | Prompt separation and normalization. |
| `tests/test_product_evaluator.py` | 3 | Structured facts to the evaluator without searching Amazon. |

## Features completed this session

1. **Background search (ADR-047).** `AMAZON_BROWSER_HEADLESS` now defaults to true, so no window appears on each message. Manual sign-in always opens visibly regardless of the setting.
2. **A shopping list (ADR-048).** Picking an option adds it. Quantities can be changed, items removed by description, and the list survives further searches — so one conversation can gather several products. Every list message states that nothing was added to the real Amazon cart.
3. **Checkout summary.** Exact contents, per-line totals, a subtotal, and an explicit list of what Amazon has not supplied (shipping, tax, delivery date, address) with "the real total will be higher".
4. **The order gate (ADR-049).** Buy-phrasing is matched deterministically before any model call. Confirming records the approval, then refuses. `checkout.place_order()` exists only to raise.
5. **Confirmation invalidation (ADR-026).** A confirmation is a fingerprint of exact contents; changing anything invalidates it.
6. **Variant-preserving titles.** Colour, size, and pack count survive shortening; marketing copy does not.
7. **Delivery questions are acknowledged** rather than silently ignored.
8. **List facts reach conversation**, so "what's on my list?" cannot be answered from nothing.

## Remaining technical debt

- `llm_client.py` keeps two near-parallel request paths. Unifying them changes the live-verified LM Studio interaction, so it waits for live re-verification.
- `agent.py` is 799 lines. The cart and checkout handlers are the natural next extraction — probably a `purchase_flow.py` — but the split is better made once the flow stops changing.
- `AmazonWorkflowGateway` still has no implementers and its shape predates ADR-026's requirements.
- `workflow_store.transition()` records state changes consistently but does not reject illegal transitions.
- `Candidate.brand` is never populated, so brand matching relies on title text.
- Stop-word, affirmation, plural, and pack-size handling are English and hand-maintained.
- Prices are captured at search time and can go stale before checkout; there is no re-check.

## Remaining blockers

1. **Manual Telegram verification**, especially of headless search against the real signed-in profile. This is the first thing to do.
2. **Delivery estimates** need real Amazon result HTML to write selectors against; guessing would produce silently wrong delivery claims.
3. **Writing to the real Amazon cart** needs live selector verification with the user present. On a product page "Add to Cart" sits beside "Buy Now", so an unverified selector is an unacceptable risk. This needs its own ADR and the ADR-026 controls.
4. **Using stored memory in purchasing** ("buy my usual toothpaste") needs a policy decision first; ADR-021 forbids unreviewed preference inference.

## Required next architectural checkpoint

**Verify the built flow in a real Telegram conversation, headless, against real Amazon.**

Suggested script, in one conversation:

1. `find me cheap AA batteries` — expect ranked results, unit prices, and **no browser window**.
2. `which has the most reviews?` — expect an answer using only the shown options.
3. `2` — expect an instant add with no model latency, and a subtotal.
4. `now some paper towels` — expect a second search that keeps the list.
5. `1` — expect two items and a combined subtotal.
6. `remove the paper towels` — expect the right item removed.
7. `checkout` — expect the summary with unknowns named.
8. `place the order` — expect the refusal.
9. `cancel`.

Capture the replies and latencies. A failure at step 1 points at headless mode; a slow reply at step 3 means the deterministic path was bypassed; a wrong item at step 6 points at description matching.

## Commands and verification levels

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m py_compile src/*.py scripts/*.py tests/*.py
PYTHONPATH=src .venv/bin/python -c "import main, agent, llm_client, memory, amazon, product_evaluator, request_context, intent_classifier, candidate_resolver, workflow_models, workflow_store, timing, ranking, product_display, workflow_reply, cart, checkout"
git diff --check
```

Live services are deliberate and manual only; see `README.md`. Do not start Telegram polling for automated verification, and avoid duplicate bot instances. Automated tests do not prove LM Studio, Amazon, or Telegram integration.

## Worktree and ADR status

The preview-loop work is committed. This session added `src/cart.py`, `src/checkout.py`,
`tests/test_cart_and_checkout.py`, and `tests/test_shopping_flow.py`, and appended
**ADR-047 through ADR-050**. ADR-047 refines ADR-039 (visible browser), ADR-048 refines
ADR-030 (one workflow per user); the original entries are retained per the append-only rule.
