# Amazon AI Purchasing Agent — Project State

Last updated: 2026-07-30

Status: Active development with a dirty, uncommitted worktree. The conversational preview loop is now complete end to end in automated tests: clarify → search → filter → rank → present → answer questions → refine → select → cancel. Cart, checkout, checkout confirmation, and order placement remain unimplemented and unreachable.

This is the current implementation handoff. Historic decisions remain append-only in `Handoff Files/DECISIONS.md` (ADR-001 through ADR-046).

## What is verified, and what is not

Verified in this session (automated level only):

- **185 tests pass** (`.venv/bin/python -m pytest -q`), up from 96. Syntax, imports, and `git diff --check` also pass.
- The full preview conversation was exercised against mocked LM Studio and mocked Amazon and its user-visible output inspected directly, not only asserted by substring.
- Deterministic behavior with no model call: option numbers, `yes`, `no`, and `cancel` while a workflow is active.
- Deterministic ranking: a cheap request orders by price per item, falls back to total price when a pack size is missing, and states which basis it used.

Not verified or not implemented:

- **No live verification was performed in this session.** LM Studio, Amazon, and Telegram were not contacted at any point. No bot was started and no live probe script was run.
- No manual Telegram conversation has confirmed the new behavior end to end. This is the main outstanding gap.
- No cart, checkout preview, price-total confirmation, payment, ordering, order-history lookup, or purchase-history storage exists. **No code path in this repository can place an order.** `amazon.py` exposes only `search_products()` and a manual sign-in helper; `AmazonWorkflowGateway` is a `Protocol` with no implementation and no callers.
- Amazon delivery estimates and availability are still not extracted; product-detail pages are not fetched.
- Searches still open a visible browser window.

Verified previously and unchanged: LM Studio serves `qwen3.5-4b-mlx` with the corrected Qwen template; semantic routing uses strict compact JSON with typed validation and soft/hard timeouts; the model executes no memory, browser, workflow, or purchase action; read-only Amazon search against a manually signed-in persistent profile returns canonical product records.

## Actual runtime call flow

```text
Telegram Update
  → src/main.py: authorization, metadata-only log, “Thinking…”, final reply/edit
  → src/agent.py

1. explicit colon aliases
     remember:/recall:/forget:  → memory.py            → data/memory.db
     search: <query>            → amazon.py            → product_evaluator.py → LM Studio prose

2. deterministic workflow reply (only when a workflow is active)
     workflow_reply.py → cancel / select position / affirm / decline   [no model call]

3. semantic interpretation
     intent_classifier.py → llm_client.py → validated SemanticAction
       ├─ memory action    → memory.py
       ├─ purchase start   → amazon.py → Candidate conversion
       │                     → ranking.apply_constraints → ranking.rank
       │                     → workflow_store.py → product_display.py
       ├─ workflow action  → refine  → ranking (re-filter/re-order stored results)
       │                   → select  → candidate_resolver.py
       │                   → cancel / change_quantity / confirm
       └─ general/unknown

4. pending-question fallback → re-ask instead of answering something unrelated

5. general conversation → response_policy.py + llm_client.py,
     with product_evaluator.candidate_context() supplying the options on screen
```

Important actual-path distinctions:

- `product_evaluator.evaluate_products()` is **not** called by the natural-language purchase path. It is active only through the explicit `search:` alias.
- Candidates are persisted in displayed order, so a reply of "3" always means the third line shown.
- A refinement re-filters and re-orders results already retrieved; it does not run a second Amazon search.
- `timing.py` measures the semantic path. Explicit aliases and deterministic workflow replies return before the semantic task and produce no full timing record.

## Production module inventory

| Module | Lines | Lifecycle | Responsibility | Safety boundary / limitation |
| --- | --- | --- | --- | --- |
| `main.py` | 83 | Active entry point | Telegram transport, authorization, startup configuration validation, 4,096-character sectioning. | Logs only user ID and message length. Authorization fails closed: an unset ID becomes `0`, matching no user. |
| `agent.py` | 569 | Active orchestrator | Routing, workflow decisions, and the only action executor. | Sole action executor. Database paths resolve per call. Purchase work is read-only search plus preview state. |
| `llm_client.py` | 138 | Active LM Studio boundary | OpenAI-compatible communication with LM Studio. | Prefers visible content; accepts `reasoning_content` only when it parses as exactly one JSON object. **Known duplication:** two near-parallel request paths (streaming when timed, non-streaming otherwise). |
| `intent_classifier.py` | 250 | Active semantic interpreter | Router then specialist, returning a validated `SemanticAction`. | Validators are pure functions over parsed JSON. No tool, storage, Telegram, or browser access. |
| `workflow_reply.py` | 87 | Active deterministic interpreter (**new**) | Reads unambiguous replies to the agent's own question without a model call. | Deliberately strict: every significant word must belong to one vocabulary, or it defers to the model. Returns an intent only; executes nothing. |
| `ranking.py` | 164 | Active decision policy (**new**) | Hard constraint filtering and inspectable ordering, as pure functions. | Never invents a value to sort by. A candidate missing the compared fact is kept and listed last, and the basis plus any caveat are reported to the user. |
| `product_display.py` | 122 | Active presentation (**new**) | Concise display titles, fact lines, candidate-aware next-step hints, spaced Telegram output. | Shortens and arranges stored facts; adds none. Enforces the preview disclaimer on every candidate message. |
| `candidate_resolver.py` | 149 | Active selection helper | Resolves comparisons, positions, and described words against stored candidates. | Zero or ambiguous matches produce clarification and never guess. Lenient by design: only reached after the model classified a selection. |
| `memory.py` | 55 | Active persistence | Explicit key/value memory. | No preference inference, purchase history, or model-controlled write path. |
| `amazon.py` | 329 | Active read-only browser boundary | Playwright persistent-context search and public-result extraction. | Rejects repository-local profile paths and advertising URLs. `AmazonWorkflowGateway` is a type-only future interface with no implementation. |
| `product_evaluator.py` | 91 | Active on two narrow paths | `evaluate_products()` for the `search:` alias; `candidate_context()` serializes stored candidates for conversation. | **Mismatch:** evaluator prose reaches Telegram unvalidated on the alias path; no deterministic fact-claim checker exists. |
| `workflow_models.py` | 115 | Active model | Typed workflow and candidate records. | Excludes payment data, cookies, and addresses. Deserializes field-tolerantly (ADR-041). |
| `workflow_store.py` | 107 | Active persistence | One workflow per user; `transition()` is the only state-change path. | Enforces expiry after 24 hours (ADR-046). Does not yet reject illegal transitions. |
| `timing.py` | 96 | Active diagnostics | Request-scoped latency measurement. | Observability only; never alters routing. |
| `response_policy.py` | 46 | Active prompt policy | System prompts, token limits, sentence-safe normalization. | Separates conversational and product-fact contracts. |
| `request_context.py` | 15 | Active on the alias path | Immutable request metadata. | `requires_confirmation` and `future_order_history_candidate` remain unset placeholders. |

## Test coverage map

Last verified full run: **185 passed**. All external boundaries mocked; temporary SQLite throughout; `tests/conftest.py` makes writing to `data/` structurally impossible.

| Test file | Tests | Responsibility covered |
| --- | --- | --- |
| `tests/test_workflow_reply.py` | 35 | Affirmatives, refusals, cancellations, explicit positions, out-of-range numbers, empty candidate lists, and the mixed sentences that must defer to the model. |
| `tests/test_ranking.py` | 23 | Pack-count reading, unit price, sort-preference detection, unit vs total price fallback, missing prices and ratings, tie-breaking by review count, and constraint filtering that drops only proven violations. |
| `tests/test_candidate_resolution.py` | 20 | Numeric and ordinal selection, natural brand references, pack-count variants, comparisons, ambiguity, out-of-range numbers, empty lists, and missing facts. |
| `tests/test_conversation_continuity.py` | 18 | Clarifying question answered by the next message, unclassified answers still used, general chat not hijacking a pending question, refusal closing the workflow, deterministic selection, single-candidate confirmation, question answering with candidate facts, refinement narrowing and reordering, refinement matching nothing, and workflow expiry. |
| `tests/test_complete_response_flow.py` | 14 | LM Studio content handling, JSON-mode request shape, guarded reasoning fallback, empty responses, graceful model-error text, Telegram sectioning. |
| `tests/test_product_display.py` | 13 | Display-title shortening and pack-size preservation, truncation marking, fact lines, candidate-aware hints, spacing, filter and caveat notes, empty result sets. |
| `tests/test_agent_memory_commands.py` | 12 | Explicit memory aliases, validated semantic memory actions, whole-word shopping markers, clarification persistence, semantic timeouts, `search:` delegation. |
| `tests/test_intent_classifier.py` | 11 | Router-then-specialist prompting, token budget, route/action validation, confidence handling, scalar constraints, general-chat short circuit. |
| `tests/test_amazon_profile.py` | 10 | Profile configuration, visible default, bounded close, result-card metadata, absent fields, query-tab reuse, canonical URL filtering, selector regression. |
| `tests/test_purchase_workflow.py` | 10 | Preview start, real-record conversion, Amazon failure safety, legacy mock invalidation, workflow actions, state-version advancement, tolerant deserialization, workflow isolation. |
| `tests/test_semantic_evaluation_corpus.py` | 7 | Offline semantic-contract corpus. |
| `tests/test_memory.py` | 6 | SQLite memory behavior and persistence. |
| `tests/test_product_evaluator.py` | 3 | Structured facts to the evaluator without searching Amazon. |
| `tests/test_response_policy.py` | 3 | Prompt separation and sentence-safe normalization. |

## Features completed this session

1. **Conversational continuity (ADR-043).** The agent persists the questions it asks. A clarifying question creates an `awaiting_request_clarification` workflow, and the next message answers it — including when the model returns no confident classification. Unambiguous replies (`yes`, `no`, `cancel`, `3`, `option 2`) are executed deterministically with no model call. An unclassified reply re-asks the pending question instead of answering something unrelated.
2. **Deterministic ranking and hard filtering (ADR-044).** `cheap` requests order by price per item, falling back to total price with the limitation stated. Extracted constraints (`max_price`, `min_rating`, `prime`) are now actually applied, and removals are reported. Previously the user's stated budget was silently ignored.
3. **Product-fact conversation (ADR-045).** Questions about the options on screen now travel with those options serialized as structured context. Previously the model was asked about products it could not see.
4. **In-place refinement.** "Only the Prime ones" now narrows the current results and re-presents them, instead of replying "start a new search after cancelling this workflow". A refinement matching nothing is reported and not persisted, so the user is never stranded.
5. **Readable output.** Concise fact-preserving display titles, blank-line spacing, unit prices, and next-step hints offering only replies that apply to the specific candidates.
6. **Workflow expiry (ADR-046).** An abandoned workflow no longer blocks every later purchase request forever.

## Bugs fixed this session

- `candidate_resolver.explicit_position()` returned `0` for "last" when no candidates were stored, which would have indexed into an empty list from the new fast path.
- The deterministic fast path initially read "cancel the second one and show me batteries" as selecting option 2. It now requires the message to contain nothing but position words.
- A refinement previously dead-ended the conversation.
- A stale workflow permanently blocked new purchases.
- Ranking basis was reported as "Amazon's own result order" after a refinement that preserved a previous sort; refinements now say "in their previous order" and "Narrowed to" rather than claiming a new search.

## Remaining technical debt

- `llm_client.py` keeps two near-parallel request paths. Unifying them changes the live-verified LM Studio interaction, so it is left until live re-verification is possible.
- `AmazonWorkflowGateway` has no implementers and its `place_confirmed_order(confirmation_version)` shape predates ADR-026's idempotency and audit requirements. Redesign it alongside the cart milestone.
- `workflow_store.transition()` records state changes consistently but does not reject illegal transitions.
- `product_evaluator.evaluate_products()` output reaches Telegram as unvalidated model prose on the `search:` alias path.
- `Candidate.brand` is never populated, so brand matching relies on title text.
- `agent.py` is 569 lines. It is coherent, but the purchase-workflow half is the natural next extraction if it keeps growing.
- Stop-word, affirmation, and pack-size vocabularies are English and hand-maintained.

## Remaining blockers

These need something I cannot supply:

1. **Manual Telegram verification.** Everything above is unit-verified with mocked boundaries. Nothing has been confirmed in a real conversation against real LM Studio and real Amazon results.
2. **Delivery estimates.** Writing extraction selectors requires real Amazon result HTML to inspect; guessing selectors would produce silently wrong delivery claims.
3. **Invisible search.** `AMAZON_BROWSER_HEADLESS` already exists. Making headless the default requires confirming the authenticated profile still works headlessly, which needs a live signed-in run.
4. **Using stored memory in purchasing** ("buy my usual toothpaste") requires an explicit policy decision. ADR-021 separates purchase history from preferences and forbids unreviewed inference, so this needs a new ADR before implementation, not a code change.
5. **Cart and checkout** remain gated behind ADR-026 and must not begin until the preview loop is manually verified.

## Required next architectural checkpoint

**Manually verify the complete preview conversation in Telegram, then decide the memory-in-purchasing policy.**

Suggested manual script, in one Telegram conversation:

1. `find me a good deal` → expect the clarifying question.
2. `AA batteries` → expect ranked candidates with unit prices and spacing.
3. `which has the most reviews?` → expect an answer using only the shown options.
4. `only the Prime ones` → expect "Narrowed to N", with removals reported.
5. `3` → expect an instant selection with no model latency.
6. `cancel` → expect the workflow to close.

Capture the actual replies and latencies. Any wrong answer at step 3 indicates the candidate context is not reaching the model; any slow reply at step 5 indicates the deterministic path was bypassed.

## Commands and verification levels

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m py_compile src/*.py scripts/*.py tests/*.py
PYTHONPATH=src .venv/bin/python -c "import main, agent, llm_client, memory, amazon, product_evaluator, request_context, intent_classifier, candidate_resolver, workflow_models, workflow_store, timing, ranking, product_display, workflow_reply"
git diff --check
```

Live services are deliberate and manual only; see `README.md`. Do not start Telegram polling for automated verification, and avoid duplicate bot instances. Automated tests do not prove LM Studio, Amazon, or Telegram integration.

## Worktree and ADR status

`git status` is intentionally dirty and nothing has been committed. This session added `src/ranking.py`, `src/product_display.py`, `src/workflow_reply.py`, `tests/test_ranking.py`, `tests/test_product_display.py`, `tests/test_workflow_reply.py`, and `tests/test_conversation_continuity.py`, and appended **ADR-043 through ADR-046**. ADR-046 refines ADR-030 and ADR-043 extends ADR-029/ADR-033; the original entries are retained per the append-only rule.
