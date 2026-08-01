# Amazon AI Purchasing Agent — Project State

Last updated: 2026-08-01

Status: The shopping conversation is menu-driven and entirely deterministic. Search → numbered results → pick a variant → list → check out (**writes the real Amazon cart**) → **Place the order (submits a real order)**.

**No language model participates in shopping, and none can write anything the user sees** (ADR-051). **Ordering is implemented** (ADR-061) and is off unless `AMAZON_ENABLE_ORDERING=true`, under a price ceiling, with an audit log.

**Ordering cannot succeed until you have signed in to Amazon recently.** Amazon requires a fresh sign-in before checkout (`max_auth_age=900`, verified live); the agent never authenticates and reports that redirect as your step.

Decisions are append-only in `Handoff Files/DECISIONS.md` (ADR-001 through ADR-062). Live findings and open bugs are in `Handoff Files/OPEN_ISSUES.md`.

## What is verified, and what is not

Verified in this session:

- **609 tests pass** (`.venv/bin/python -m pytest -q`), including 58 regressions pinning every UAT session 5 and 6 failure, plus **11,000 fuzzed conversation turns** against a deliberately unreliable model.
- **Live against real Amazon, read-only** — the search pipeline was re-verified end to end after the fixes:
  - `oral b toothbrush 4 pack` returns five *distinguishable* titles (was five lines reading `Oral-B`).
  - `melatonin 10mg` drops the `One Medical Membership — $99.00` placement (`dropped as unrelated: 1`).
  - Ordering is cheapest-per-item: `$6.42 ($1.60 ea)` leads, basis reported as `price per item`.
- A read-only DOM probe established the facts behind the fixes, and **reversed one of them**: the ad placement carries no sponsored marker while the genuine products do, so marker-based ad filtering would have removed the good results (ADR-053).

Not verified or not implemented:

- **No manual Telegram session has been run since these fixes.** The search path is live-verified; the conversation path is not.
- **Quantity cannot be changed** (ISSUE-023). Adding two of an item is still impossible.
- Order history is built but unverifiable: the test account has zero orders. This blocks the evidence-backed auto-add the user asked for (ISSUE-035).
- Removing an item after confirming does not remove it from the real Amazon cart (ISSUE-017).
- A pack size stated in the query is not enforced (ISSUE-034).

## Routing contract

Deterministic, evaluated top to bottom. Only step 5 reaches a language model.

```text
1. colon aliases          remember: / recall: / forget:
2. reset                  reset, start over, clear
3. menu choice            a number read off the last menu          menu.choose()
4. control word           cancel, checkout, confirm, buy phrasing  workflow_reply.py
5. memory phrasing        the only gate that reaches the model     intent_classifier.py
6. narrowing instruction  when the user asked to narrow            ranking.parse_constraint
7. reference on screen    "the duracell", "cheapest", "the larger size"
8. state question         cart, total, results, attributes         state_answer.py
9. anything else          SEARCH AMAZON with the raw message       ← the default
```

Step 9 is the inversion that fixed the last round of failures. Amazon's own search understands ordinary phrasing: "alright, i need a new iphone 17 charger" returns iPhone 17 chargers, where the model claimed they did not exist.

## Module inventory

| Module | Lines | Responsibility | Boundary / limitation |
| --- | --- | --- | --- |
| `agent.py` | 1224 | Routing, workflow decisions, the only action executor. | Never references `PLACING_ORDER` or `COMPLETED`. Storage paths resolve per call. Per-user lock serialises message bursts. |
| `amazon.py` | 902 | Read-only search, cart add/remove, persistent profile. | Layout-independent selectors. Cart clicks `#add-to-cart-button` by exact id; `_refuse_ordering_url()` blocks checkout URLs; success read from Amazon's cart badge. One search retry for cold start. |
| `ranking.py` | 474 | Filtering, ordering, the single-pick recommendation, constraint parsing. | Never invents a value to sort by. Reports its basis and caveats. |
| `product_display.py` | 407 | All Telegram HTML output. | Escapes everything from Amazon or the user. Shortens titles while keeping colour, size and pack count. No review counts. |
| `candidate_resolver.py` | 201 | Resolves references to stored candidates. | Ambiguity produces a question, never a guess. |
| `workflow_models.py` | 173 | Typed workflow, candidate, cart-line and menu records. | Field-tolerant deserialisation (ADR-041). No payment data, cookies or addresses. |
| `llm_client.py` | 92 | LM Studio boundary. | Reached only by `intent_classifier`. Two near-parallel request paths remain (debt). |
| `workflow_reply.py` | 127 | Control words and buy phrasing. | Strict: a mixed sentence defers rather than guessing. |
| `state_answer.py` | 124 | Questions about stored state (**new**). | Fixed templates over stored data. Says plainly it is not checking the real Amazon cart. |
| `intent_classifier.py` | 107 | Validated memory extraction (**only model use**). | Fails closed on bad JSON, low confidence, model error or timeout. |
| `workflow_store.py` | 107 | One workflow per user; `transition()` is the only state change. | 24-hour expiry (ADR-046). Does not reject illegal transitions. |
| `flow.py` | 152 | Builds the menu for each point in the conversation (**new**). | Pure assembly. |
| `main.py` | 131 | Telegram transport, authorization, HTML send with plain-text fallback. | Logs user id and message length only. Authorization fails closed. |
| `checkout.py` | 84 | Order summary and confirmation fingerprint. | Contacts nothing. Ordering lives in `amazon.py`. |
| `menu.py` | 142 | Numbered choices and reading one back (**new**). | Escapes labels; product titles are untrusted text. |
| `cart.py` | 78 | The user's chosen items as pure operations. | One unknown price makes the whole subtotal unknown. |
| `memory.py` | 55 | Explicit key/value memory. | No preference inference. |

Deleted in this session, each existing only to let the model talk about products: `product_evaluator.py`, `request_context.py`, `response_policy.py`, `examples.py` and its corpus, `scripts/live_semantic_probe.py`, and the router plus purchase and workflow specialists in `intent_classifier.py`.

## Test coverage

**609 passing.** External boundaries are blocked in `tests/conftest.py`: no test can open a browser (`amazon.async_playwright`) or reach LM Studio (`llm_client.generate_response`), and default database paths are redirected so nothing writes to `data/`.

| Test file | Tests | Covers |
| --- | --- | --- |
| `test_uat_session5.py` | 64 | **UAT session 5**: junk results, price ordering, brand-only titles, narrowing by brand, the stale-candidate hijack, menu lifetime, cart reconciliation. |
| `test_adversarial.py` | 237 | Hostile input, prompt injection in Amazon titles, money arithmetic, abused state transitions, boundary failures. |
| `test_fuzz_conversations.py` | 42 | Whole conversations against an unreliable model, invariants after every turn. |
| `test_workflow_reply.py` | 35 | Control words, buy phrasing, positions, and the mixed sentences that must defer. |
| `test_product_display.py` | 31 | Titles, escaping, results vs list, no raw markdown, scaling. |
| `test_no_model_prose.py` | 27 | **The invariant**: no shopping message reaches the model; no module outside `intent_classifier` mentions `generate_response`. |
| `test_cart_and_checkout.py` | 25 | Cart arithmetic, confirmation invalidation, and that `place_order` always raises. |
| `test_menu_flow.py` | 24 | Menu resolution and persistence, plus the UAT transcripts. |
| `test_ranking.py` | 23 | Pack counts, unit price, sorting, delivery, constraints. |
| `test_agent_memory_commands.py` | 23 | Memory aliases, natural phrasing, and that shopping never consults the model. |
| `test_multi_item_and_reset.py` | 21 | Multi-item cart push, reset, concurrent bursts. |
| `test_candidate_resolution.py` | 20 | Numeric, ordinal, brand and pack references; ambiguity. |
| `test_amazon_profile.py` | 19 | Profile config, headless default, extraction, retry. |
| `test_shopping_flow.py` | 14 | Search to order gate. |
| `test_purchase_workflow.py` | 8 | Workflow creation from real records, versioning, tolerant persistence. |
| `test_memory.py` | 6 | SQLite memory. |

## Required next checkpoint

**A manual Telegram session replaying the UAT session 5 transcript.** The search path is
live-verified; the conversation path around it is not.

1. `melatonin 10mg` → no $99 membership, cheapest first
2. `4` (Narrow) → `natures bounty` → Nature's Bounty results, not "nothing matches"
3. `oral b toothbrush 4 pack` → five distinguishable titles
4. `5` (Search for something else) → then answer with a product → a **search**, not
   "more than one option matches that description"
5. Type a number from the results still on screen → it must still select
6. Add two items, `Remove an item` → prices shown beside each
7. `Check out` → `Confirm` → confirms the add-to-cart wait (ISSUE-033) and shows the
   cart-reconciliation warning if anything else is in your Amazon cart

Then, in order: **ISSUE-023** (quantity — no way to add two of anything),
**ISSUE-017** (removal does not reach the real Amazon cart), **ISSUE-035** (decide what
evidence, if any, could justify an auto-add).

## Commands

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m py_compile src/*.py tests/*.py scripts/*.py
PYTHONPATH=src .venv/bin/python -c "import main, agent, amazon, cart, checkout, ranking, product_display, workflow_reply, menu, flow, state_answer, candidate_resolver, workflow_models, workflow_store, intent_classifier, memory, llm_client"
git diff --check
PYTHONPATH=src .venv/bin/python src/main.py            # run the bot
PYTHONPATH=src .venv/bin/python scripts/amazon_profile_login.py   # manual sign-in
PYTHONPATH=src .venv/bin/python -u scripts/amazon_live_probe.py "AA batteries"
PYTHONPATH=src .venv/bin/python -u scripts/amazon_dom_probe.py --search "iphone case"
```

LM Studio is not required for shopping. Avoid duplicate bot instances.

## Remaining technical debt

- `agent.py` is 1,224 lines; the purchase-flow half is still the natural extraction.
- `workflow_store.transition()` does not reject illegal transitions.
- `Candidate.brand` is never populated; matching relies on title text.
- Stop-word, plural, pack-size and constraint vocabularies are English and hand-maintained.
- Prices are captured at search time and can go stale before checkout; there is no re-check.
