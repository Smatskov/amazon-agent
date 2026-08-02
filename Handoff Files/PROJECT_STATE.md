# Amazon AI Purchasing Agent — Project State

Last updated: 2026-08-02

Status: The shopping conversation is menu-driven and entirely deterministic. Search →
numbered results → pick a variant → list → check out (**writes the real Amazon cart**)
→ **Place the order (submits a real order)**.

**No language model participates in shopping, and none can write anything the user
sees** (ADR-051). **Ordering is implemented and has completed a real purchase**
(ADR-061, ADR-063). It is off unless `AMAZON_ENABLE_ORDERING=true`, under a price
ceiling, with an append-only audit log and a per-attempt screenshot trace.

Decisions are append-only in `Handoff Files/DECISIONS.md` (ADR-001 through ADR-068).
Live findings and open bugs are in `Handoff Files/OPEN_ISSUES.md`.

## What is verified, and what is not

**A real order was placed end to end on 2026-08-02** — order `112-3910624-2541021`,
$12.61, Ziploc freezer bags, confirmed in Amazon order history. The pipeline cleared
the add-on carousel, reached the review page, found the order control and clicked it.

**It was reported to the user as a failure.** A 3-D Secure page after the click was
read as "no order", and the reply asserted "nothing was bought and nothing was
charged". That is ISSUE-060, now fixed (ADR-068), and it is the most important thing
in this document: the build's other failure paths fail closed, and this one failed
open.

Verified:

- **645 tests pass** (`.venv/bin/python -m pytest -q`), including 93 regressions in
  `test_uat_session5.py` pinning every reported failure from sessions 5–7, plus
  ~11,000 fuzzed conversation turns against a deliberately unreliable model.
- **Live against real Amazon**: search, relevance filtering, price ordering, variation
  reading, cart add, cart read, cart remove, the full checkout walk, and one completed
  order.
- **Prime detection** checked against this account before it became a filter: 5 of 6
  results carried the badge, so the rule removes a minority rather than everything.

Corrected earlier conclusions — both were wrong and both mattered:

- **`max_auth_age=900` is not a ceiling on unattended ordering.** That came from
  probes that were all headless. The same profile and session reach checkout cleanly
  in a visible browser (ADR-063).
- **Sponsored markers are useless for filtering ads.** The placement carried none while
  the genuine products did (ADR-053).

Not verified:

- **The free/fastest delivery selection has never been observed firing.** With a valid
  default card, checkout skipped the shipping step entirely. Coded and unit-tested,
  not live-confirmed (ADR-066).
- **Card verification and expired-session handling** are coded from observed markup but
  have not been hit since being implemented.
- Order history is readable, but the evidence-backed auto-add remains undesigned
  (ISSUE-035).

## Routing contract

Deterministic, evaluated top to bottom. Only step 5 reaches a language model.

```text
1. colon aliases          remember: / recall: / forget:
2. reset                  reset, start over, clear
3. menu choice            a number read off the last menu          menu.choose()
                          also "1,2" (multi) and "6 under $10" (choice + argument)
4. control word           cancel, checkout, confirm, buy phrasing  workflow_reply.py
5. memory phrasing        the only gate that reaches the model     intent_classifier.py
6. narrowing instruction  when the user asked to narrow            ranking.parse_constraint
7. reference on screen    "the duracell", "cheapest" — short references only
8. state question         cart, total, results, attributes         state_answer.py
9. anything else          SEARCH AMAZON with the raw message       ← the default
```

Step 9 is the inversion that fixed the session-4 failures: Amazon's own search
understands ordinary phrasing where the local model did not.

## What the agent will and will not do

| | |
| --- | --- |
| Suggests only Prime-eligible products | ADR-065 — never inferred; no badge means dropped |
| Resolves a variation listing to one child ASIN before adding | ADR-058 |
| Orders cheapest-per-item unless asked otherwise | ADR-054 |
| Writes the real Amazon cart at checkout | ADR-059 |
| Submits a real order behind a kill switch and ceiling | ADR-061, ADR-063 |
| Picks free + fastest delivery when offered a choice | ADR-066 — *unobserved* |
| **Never types a password or card number** | Hands the wall back to the user |
| **Never accepts a paid Amazon offer** | `NEVER_CLICK` — the Prime upsell mid-checkout |
| **Never claims an order failed when it does not know** | ADR-068 |

## Module inventory

| Module | Lines | Responsibility | Boundary / limitation |
| --- | --- | --- | --- |
| `amazon.py` | 1515 | Search, cart, variations, checkout walk, order placement, audit log, trace. | The only module that knows how to submit an order. Cart clicks `#add-to-cart-button` by exact id; the order control must be the *enabled* one. Visible browser for ordering. |
| `agent.py` | 1326 | Routing, workflow decisions, the only action executor. | Transitions to `COMPLETED` only on a confirmed order. Storage paths resolve per call. Per-user lock serialises bursts. |
| `ranking.py` | 533 | Prime filter, relevance, constraints, ordering, variant sorting. | Never invents a value to sort by. Reports its basis and caveats. |
| `product_display.py` | 460 | All Telegram HTML output. | Escapes everything from Amazon or the user. Never asserts an unknown order outcome. |
| `candidate_resolver.py` | 201 | Resolves short references to stored candidates. | ≤3 significant words; longer phrasing is a search, not a reference (ADR-055). |
| `flow.py` | 194 | Builds the menu for each point in the conversation. | Pure assembly. |
| `workflow_models.py` | 173 | Typed workflow, candidate, cart-line and menu records. | Field-tolerant; an unknown menu action is dropped, not raised (ADR-041, ADR-060). |
| `menu.py` | 142 | Numbered choices, multi-choice, choice-with-argument. | Escapes labels; product titles are untrusted text. |
| `main.py` | 140 | Telegram transport, authorization, HTML send, product photos. | Logs user id and message length only. Authorization fails closed. |
| `workflow_reply.py` | 127 | Control words and buy phrasing. | Strict: a mixed sentence defers rather than guessing. |
| `state_answer.py` | 124 | Questions about stored state. | Fixed templates over stored data. |
| `workflow_store.py` | 107 | One workflow per user; `transition()` is the only state change. | 24-hour expiry (ADR-046). Does not reject illegal transitions. |
| `intent_classifier.py` | 106 | Validated memory extraction (**only model use**). | Fails closed on bad JSON, low confidence, model error or timeout. |
| `llm_client.py` | 91 | LM Studio boundary. | Reached only by `intent_classifier`. Single request path. |
| `cart.py` | 78 | The user's chosen items as pure operations. | One unknown price makes the whole subtotal unknown. |
| `checkout.py` | 78 | Order summary and confirmation fingerprint. | Contacts nothing. Ordering lives in `amazon.py`. |
| `memory.py` | 55 | Explicit key/value memory. | No preference inference. |

Deleted across sessions 6–7: `product_evaluator.py`, `request_context.py`,
`response_policy.py`, `examples.py`, `request_mode.py`, `timing.py`, the single-pick
recommendation, `amazon.add_to_cart` (duplicated `add_many_to_cart`), and
`checkout.place_order` (which existed only to raise).

## Test coverage

**645 passing.** External boundaries are blocked in `tests/conftest.py`: no test can
open a browser (`amazon.async_playwright`), reach LM Studio, or place an order —
`AMAZON_ENABLE_ORDERING` is forced off regardless of the real `.env` (ADR-064).

| Test file | Tests | Covers |
| --- | --- | --- |
| `test_adversarial.py` | 234 | Hostile input, prompt injection in titles, money arithmetic, abused transitions, boundary failures. |
| `test_uat_session5.py` | 93 | Every reported failure from sessions 5–7: junk results, price ordering, brand-only titles, narrowing, the stale-candidate hijack, variations, ordering outcomes, the false-failure incident. |
| `test_fuzz_conversations.py` | 42 | Whole conversations against an unreliable model, invariants after every turn. |
| `test_workflow_reply.py` | 35 | Control words, buy phrasing, positions, and the mixed sentences that must defer. |
| `test_product_display.py` | 32 | Titles, escaping, results vs list, no raw markdown. |
| `test_no_model_prose.py` | 27 | **The invariant**: no shopping message reaches the model. |
| `test_menu_flow.py` | 24 | Menu resolution and persistence. |
| `test_cart_and_checkout.py` | 24 | Cart arithmetic, confirmation invalidation, the ordering kill switch and ceiling. |
| `test_ranking.py` | 23 | Pack counts, unit price, sorting, delivery, constraints. |
| `test_agent_memory_commands.py` | 23 | Memory aliases and natural phrasing. |
| `test_multi_item_and_reset.py` | 21 | Multi-item cart push, reset, concurrent bursts. |
| `test_candidate_resolution.py` | 20 | Numeric, ordinal, brand and pack references; ambiguity. |
| `test_amazon_profile.py` | 19 | Profile config, headless default, extraction, retry. |
| `test_shopping_flow.py` | 14 | Search to order. |
| `test_purchase_workflow.py` | 8 | Workflow creation from real records, versioning, tolerant persistence. |
| `test_memory.py` | 6 | SQLite memory. |

## Required next checkpoint

**Re-run a real order with the ISSUE-060 fix in place.** The previous order succeeded
but was reported as a failure; the fix has not been exercised against a live 3-D
Secure step.

1. Restart the bot — `.env` and code are read at import.
2. `reset` in Telegram: the stale workflow still shows an item that was already bought.
3. One cheap Prime item → check out → Place the order.
4. Expect either `PLACED order_id=…` or `PLACED confirmed-via-history order_id=…` in
   `orders.log`. Anything reported as unknown must now say so without claiming nothing
   was charged.

Then, in priority order:

- **ISSUE-051** — card and address are read-only; the address book is behind a
  re-auth wall. Decide whether the checkout-page selectors (which *are* reachable)
  should drive it instead.
- **ISSUE-035** — decide what evidence, if any, could justify an auto-add.
- Observe the delivery selection actually firing (ADR-066).

## Commands

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m py_compile src/*.py tests/*.py scripts/*.py
PYTHONPATH=src .venv/bin/python -c "import main, agent, amazon, cart, checkout, ranking, product_display, workflow_reply, menu, flow, state_answer, candidate_resolver, workflow_models, workflow_store, intent_classifier, memory, llm_client"
git diff --check
PYTHONPATH=src .venv/bin/python src/main.py                        # run the bot
PYTHONPATH=src .venv/bin/python scripts/amazon_profile_login.py    # manual sign-in
PYTHONPATH=src .venv/bin/python -u scripts/amazon_live_probe.py "AA batteries"
PYTHONPATH=src .venv/bin/python -u scripts/amazon_dom_probe.py --search "iphone case"
```

Ordering evidence, written automatically on every attempt:

```bash
cat "$HOME/Library/Application Support/Amazon Agent/orders.log"
ls -t "$HOME/Library/Application Support/Amazon Agent/checkout-traces"/*/    # screenshots per step
```

LM Studio is not required for shopping. Avoid duplicate bot instances.

## Remaining technical debt

- `amazon.py` is 1,515 lines and now spans search, cart, variations and ordering. The
  ordering half is the natural extraction.
- `agent.py` is 1,326 lines; the purchase-flow half is the other one.
- `workflow_store.transition()` does not reject illegal transitions.
- `Candidate.brand` is never populated; matching relies on title text.
- Stop-word, plural, pack-size, variant-size and constraint vocabularies are English
  and hand-maintained.
- Prices are captured at search time and can go stale; the ceiling re-check at
  checkout is the only guard.
- The checkout walk depends on Amazon's markup and will need re-mapping when it
  changes. The screenshot trace exists so that re-mapping is a read, not a guess.
