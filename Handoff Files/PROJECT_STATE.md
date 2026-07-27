# Amazon AI Purchasing Agent — Project State

Last updated: 2026-07-27

Status: Active development

Current milestone: The agent classifies natural-language intent through LM Studio, validates structured intent before agent-owned routing, and preserves explicit memory/search commands as aliases. Purchasing automation has not started.

This document is the current source of truth for starting a new project conversation. It describes the repository as it exists now, not historical implementation details.

## 1. Project Mission

Build a personal AI purchasing agent controlled through Telegram. The long-term goal is for the user to send a natural-language purchasing request, have the agent apply preferences and safety rules, eventually search and evaluate Amazon products, and report the outcome through Telegram.

The current milestone is deliberately much smaller: a Telegram message reaches a local language model through LM Studio and the completed answer returns to Telegram.

## 2. Hardware and Environment

- Development machine: Apple Silicon Mac with 8 GB unified memory.
- Local model runtime: LM Studio.
- Selected development model: `Qwen3.5-4B-MLX-4bit`.
- Verified LM Studio model identifier: `qwen3.5-4b-mlx`.
- Python virtual environment: `.venv/` using Python 3.13.14.
- Project location: `~/amazon-agent`.

The 8 GB unified-memory limit is important. The selected 4-bit 4B model leaves more practical headroom than a larger model for macOS, LM Studio, the Telegram bot, and later browser automation.

## 3. Current Verified Architecture

```text
Telegram user
    ↓
main.py
    ↓
agent.py
    ├── explicit memory/search aliases → existing memory or search route
    └── natural-language message → intent_classifier.py → validated IntentResult
                                      ├── memory intent → memory.py → SQLite
                                      ├── search intent → amazon.py → Playwright Chromium → Amazon public search results
                                      │                    ↓ structured `Product` objects
                                      │                RequestContext → product_evaluator.py → llm_client.py
                                      ├── reorder/buy intent → safe non-executing response
                                      └── general/unknown → llm_client.py → LM Studio OpenAI-compatible local API
                                                                            ↓
                                                                    Qwen3.5-4B-MLX-4bit
```

LM Studio successfully loads the selected model. Its OpenAI-compatible local API runs at the configured `LLM_BASE_URL`. Telegram-to-local-model-to-Telegram inference has been verified end to end.

The OpenAI Python SDK is used only as a client for LM Studio's OpenAI-compatible local API. This project does not use it as an OpenAI cloud-model dependency.

The classifier determines only what the user wants; it cannot access tools, memory, Amazon, or Telegram. Its structured output is validated before `agent.py` routes it, and `agent.py` remains responsible for every route. Explicit memory/search commands remain supported aliases. Search continues to use the isolated browser tool, then a `RequestContext` and structured facts flow to `product_evaluator.py`.

## 4. Exact Current Module Responsibilities

### `src/main.py` — Telegram boundary and startup

- Loads Telegram configuration from the environment.
- Starts the Telegram application and registers the text-message handler.
- Rejects messages from users other than the configured authorized user.
- Logs sender and message metadata to the terminal.
- Sends the initial `Thinking…` placeholder.
- Calls `agent_brain()` and displays the completed response.
- Splits a completed response into 4,096-character Telegram-safe sections when necessary. The original placeholder becomes the first section; remaining sections are sent as additional messages only after generation is complete.

### `src/agent.py` — orchestration boundary

- Provides `agent_brain(message)` as the application-level agent entry point.
- Preserves explicit, case-insensitive `remember:`, `recall:`, `forget:`, and `search:` commands as aliases.
- Routes valid memory instructions to `memory.py` and returns deterministic responses without calling LM Studio.
- Rejects malformed memory instructions with usage guidance.
- Calls `intent_classifier.py` for all non-alias messages, then validates and routes its `IntentResult`.
- Routes natural memory intents to the existing memory functions and natural search intents to the existing read-only search flow.
- Returns safe, non-executing responses for classified reorder and buy intents.
- Creates a `RequestContext` with only the confirmed current search fields, then passes it with structured results to `product_evaluator.py`.
- Returns the evaluator recommendation exactly as before; evaluator metadata does not change Telegram behavior.
- Delegates all other messages to `generate_response()` exactly as before.
- Logs underlying model/server errors to the terminal.
- Returns a friendly user-facing error message when the local model cannot be reached or fails.

### `src/llm_client.py` — language-model communication boundary

- Loads `LLM_BASE_URL` and `LLM_MODEL` from environment configuration instead of hardcoding them.
- Creates the existing `AsyncOpenAI` client with LM Studio as its base URL and a 60-second timeout.
- Sends a normal, non-streaming chat completion request.
- Returns stripped visible `message.content` only; reasoning fields are not shown to Telegram users.
- Raises a clear error for missing or whitespace-only visible model output.

### `src/memory.py` — local memory storage boundary

- Uses Python's built-in `sqlite3` module for simple string key/value storage.
- Provides `remember()`, `recall()`, and `forget()`.
- Defaults to `data/memory.db`, while accepting a database-path argument for isolated use and tests.
- Creates and queries its SQLite table internally; no other module contains its SQL or setup details.
- Is called only by `agent.py` for explicit and classified natural-language memory instructions.

### `src/amazon.py` — Amazon browser-tool boundary

- Owns every Amazon interaction through Playwright; no other module imports or controls browser automation.
- Exposes async `search_products(query: str)`, which launches headless Chromium, opens Amazon public search results, and returns up to five visible `Product` dataclasses.
- Returns only the initial structured fields: title, price, URL, optional rating, optional review count, optional availability, and Prime eligibility when visible.
- Does not sign in, add products to a cart, begin checkout, purchase, scrape reviews, or write any storage.

### `src/product_evaluator.py` — product-comparison boundary

- Accepts a `RequestContext` and structured `amazon.Product` objects.
- Builds a fact-bounded comparison prompt and communicates only with `llm_client.generate_response()`.
- Asks the local model to compare price, rating, review count, availability, Prime eligibility when provided, value for money, and likely fit.
- Requires reasoning, tradeoffs, a top choice, and a budget alternative when appropriate.
- Returns `EvaluationResult` with recommendation text and a metadata-only reorder-style-request signal. It does not access order history, invent prior products, or change user-visible behavior.
- Does not interact with Telegram, Amazon, Playwright, storage, cart, checkout, or purchasing.

### `src/request_context.py` — request-metadata boundary

- Defines immutable `RequestContext` for shared orchestration facts: original user request, intent, search query, confidence, confirmation requirement, and an optional future order-history candidate.
- The current search route populates only the applicable request, intent, query, and routing-confidence fields. Confirmation is false and the future order-history candidate remains unset.

### `src/intent_classifier.py` — intent-classification boundary

- Sends a schema-bound classification request to LM Studio through `llm_client.generate_response()`.
- Returns validated `IntentResult` records containing only intent metadata; it never executes tools, accesses memory, accesses Amazon, or makes recommendations.
- Supports `general_chat`, `memory_remember`, `memory_recall`, `memory_forget`, `amazon_search`, `amazon_reorder`, `amazon_buy`, and `unknown` intents.
- Safely falls back to `general_chat` for malformed, invalid, or unavailable classification responses. Low-confidence actionable intents are downgraded to `unknown`.

## 5. Current Repository Structure

### Verified files and directories

```text
amazon-agent/
├── .env                       # Exists; ignored; never inspect or commit secret values
├── .env.example               # Lists all required variable names without values
├── .gitignore
├── .venv/                     # Local Python virtual environment; ignored
├── Handoff Files/
│   ├── PROJECT_STATE.md
│   └── DECISIONS.md
├── README.md                  # Present but currently empty
├── requirements-dev.txt        # Pinned development test dependency
├── pytest.ini                 # Lets pytest import modules from src/
├── src/
│   ├── main.py
│   ├── agent.py
│   ├── llm_client.py
│   ├── memory.py
│   ├── amazon.py
│   ├── product_evaluator.py
│   ├── request_context.py
│   └── intent_classifier.py
├── config/                    # Present; no tracked project files verified inside
├── data/                      # Present; no tracked project files verified inside
└── tests/
    ├── test_agent_memory_commands.py
    ├── test_complete_response_flow.py
    ├── test_intent_classifier.py
    ├── test_memory.py
    └── test_product_evaluator.py
```

`requirements-dev.txt` is the current development dependency manifest. No production dependency manifest was found (`requirements.txt`, `pyproject.toml`, `Pipfile`, `poetry.lock`, and `uv.lock` are absent).

### Planned but not implemented

- Preference-storage design beyond explicit key/value commands.
- Product evaluation beyond the initial fact-bounded local-model comparison.
- Natural-language intent extraction quality and manual verification of classifications.
- Amazon order-history lookup and reorder workflows; only non-executable TODO integration placeholders exist today.
- Amazon login, cart, checkout, purchase execution, and order-status handling.
- Configuration validation at startup.
- A production dependency manifest.
- Production reliability features such as health checks, structured logs, and automatic restart.

## 6. Installed Dependencies That Were Verified

The following packages were verified with `.venv/bin/python -m pip show` on 2026-07-24:

- `openai` 2.47.0 — OpenAI-compatible client used to call LM Studio locally.
- `python-telegram-bot` 22.8 — Telegram integration.
- `python-dotenv` 1.2.2 — loads local environment configuration.
- `pydantic` 2.13.4 — installed dependency; not yet used by the current source modules.
- `playwright` 1.61.0 — used by `amazon.py` for the initial read-only Chromium search tool.
- `pytest` 9.1.1 — development test runner, pinned in `requirements-dev.txt`.

## 7. Environment Configuration

The code reads these environment-variable names from `.env`:

- `TELEGRAM_BOT_TOKEN`
- `AUTHORIZED_TELEGRAM_USER_ID`
- `LLM_BASE_URL`
- `LLM_MODEL`

Do not store secrets in source control or paste their values into documentation, terminal logs, or chat. `.env.example` lists all four required names with empty values.

## 8. Exact Current Behavior

For an authorized Telegram text message:

1. `main.py` prints existing sender/message metadata to the terminal.
2. It sends one `Thinking…` placeholder message.
3. `agent.py` first preserves these explicit, case-insensitive aliases:
   - `remember: <key> = <value>` stores trimmed strings and returns `Remembered '<key>'.`
   - `recall: <key>` returns `Memory for '<key>': <value>` or `Nothing is stored for '<key>'.`
   - `forget: <key>` safely removes the key and returns `Forgot '<key>'.`
   - `search: <query>` calls `amazon.search_products()`, creates a `RequestContext`, then passes both to `product_evaluator.evaluate_products()`.
4. For all other messages, `intent_classifier.py` returns validated JSON intent metadata before `agent.py` routes the request.
   - Natural memory intents use the existing memory functions when the classifier supplies the required key/value entities.
   - Natural Amazon search intents use the existing read-only search and evaluation flow when the classifier supplies a query.
   - Classified reorder and buy intents return safe messages; they do not access history or execute purchasing steps.
   - `general_chat`, `unknown`, malformed JSON, and classifier failures use the existing ordinary local-model path.
5. Malformed explicit memory commands return `Memory usage: remember: <key> = <value>; recall: <key>; forget: <key>.` An empty explicit search query returns `Search usage: search: <query>.`
6. Search failures return a friendly error without evaluating results. Evaluation failures return a friendly local-model error. Search results and recommendations are not stored as preferences or purchase history.
7. `main.py` replaces `Thinking…` with the final response.
8. If the completed response is longer than Telegram's 4,096-character text limit, `main.py` places the first section in the placeholder and sends the remaining complete sections as additional messages.

Streaming is not part of the current implementation. Temporary streaming diagnostics have been removed.

## 9. Verification Completed

- LM Studio loaded `Qwen3.5-4B-MLX-4bit` and served the configured local OpenAI-compatible API.
- The model identifier `qwen3.5-4b-mlx` was verified.
- Telegram-to-local-model-to-Telegram inference was verified end to end.
- The complete-response Telegram behavior was tested successfully through Telegram on 2026-07-24.
- A direct, non-mocked call to `generate_response()` passed against LM Studio on 2026-07-24 and returned 17 non-empty visible characters. The request used the configured local endpoint and did not expose response content or reasoning fields.
- The current source was inspected on 2026-07-24.
- `tests/test_complete_response_flow.py` was added and verified with five mocked tests on 2026-07-24. It covers visible completed content, empty/whitespace model output, the friendly agent error, and long completed-response sectioning.
- `tests/test_memory.py` was added and verified with six temporary-database tests on 2026-07-24. It covers storing and recalling values, updates, missing-key recall, forgetting existing and missing keys, and persistence across separate SQLite connections.
- `tests/test_agent_memory_commands.py` was added and verified with seven temporary-database/mocked-LLM tests on 2026-07-24. It covers valid remember, recall, missing recall, and forget commands; malformed commands and empty keys; and the ordinary LLM fallback.
- `tests/test_agent_memory_commands.py` now includes two mocked Amazon-search command tests. They cover routing structured results to the product evaluator and rejecting an empty query without calling Amazon or LM Studio.
- `tests/test_product_evaluator.py` verifies with mocked LLM and Amazon-search boundaries that the evaluator receives structured products, does not invoke Amazon search, and builds a fact-bounded comparison prompt.
- `tests/test_product_evaluator.py` now verifies `RequestContext` creation and reorder-style metadata detection without history access. The search-route test verifies that the context and structured products reach the evaluator; existing memory and ordinary-message tests remain unchanged.
- `tests/test_intent_classifier.py` mocks LM Studio and covers every supported intent, malformed JSON fallback, and low-confidence actionable-intent handling.
- Agent tests cover natural memory and search routing while preserving explicit-command alias coverage.
- `.venv/bin/python -m pytest` passed: 35 passed in 0.44s on 2026-07-27. Tests do not perform live Amazon requests.
- `.venv/bin/python -m py_compile src/main.py src/agent.py src/llm_client.py src/memory.py src/amazon.py src/product_evaluator.py src/request_context.py src/intent_classifier.py` passed on 2026-07-27.
- `PYTHONPATH=src .venv/bin/python -c "import main, agent, llm_client, memory, amazon, product_evaluator, request_context, intent_classifier"` passed on 2026-07-27.
- `git diff --check` passed on 2026-07-27.
- The virtual environment and installed package versions listed above were inspected on 2026-07-24.

## 10. Known Limitations and Risks

- The Mac, LM Studio, and loaded model must remain available for replies to work.
- 8 GB unified memory limits model size and may create pressure once Chromium/Playwright is introduced.
- The test baseline covers unit behavior only; it does not replace manual Telegram or LM Studio integration testing.
- `requirements-dev.txt` pins pytest only; production dependency installation is not yet reproducible from a manifest.
- Missing or malformed environment variables do not yet receive explicit startup validation.
- Telegram/network/API failures around placeholder edits are not yet given dedicated retry handling.
- Memory is available only through explicit commands; there is no automatic extraction, conversation history, embeddings, semantic search, or preference policy.
- The explicit memory flows have not yet been manually verified through Telegram; no Telegram polling or LM Studio service was started during automated verification.
- The Amazon search tool has unit coverage but has not been verified against live Amazon. Amazon may change result-page selectors, show CAPTCHAs, restrict automated browsing, or fail when Chromium or network access is unavailable.
- Search results are limited to visible initial product fields, including Prime eligibility only when Amazon exposes it. They do not include review scraping or complete product data.
- The evaluator is prompt-constrained to structured facts, but a local model can still produce an inaccurate recommendation; live review is required before relying on its output.
- Natural-language routing depends on local-model structured JSON. Invalid, malformed, failed, or low-confidence actionable classifications safely fall back to ordinary chat or `unknown`; manual verification is still required.
- Classified buy and reorder intents are recognized but deliberately non-executing. They do not access order history, login, cart, checkout, or purchasing.
- Reorder-style metadata is a limited phrase match only. It is neither authorization nor evidence of a prior purchase, and no order-history lookup or reorder action exists.
- TODO comments identify future Amazon order-history lookup, reorder workflow, successful-checkout purchase-history recording, preference-inference policy, and duplicate-order prevention integration points; none is implemented.
- The tool has no login, cart, checkout, purchase, purchase-history, or preference-writing capability.
- Any future purchasing workflow is financially consequential and requires explicit safety, confirmation, audit, and authorization design before implementation.

## 11. Current Roadmap

1. Manually verify explicit aliases and natural-language memory, search, reorder, buy, and general-chat classifications through Telegram.
2. Add configuration validation and reproducible startup hardening.
3. Create a production dependency manifest after the startup configuration contract is defined.
4. Deliberately select a checkpoint to extend preference storage.
5. Improve read-only search and recommendation behavior only after live verification of the initial tool and evaluator output.
6. Add confirmation, audit, price-limit, and duplicate-prevention safeguards.
7. Consider browser automation for cart and purchase stages only after the earlier safety stages are verified.

### Accepted future data-design direction (not implemented)

- Purchase history will use a dedicated data store or a clearly separated data model from user preferences.
- Record a completed purchase only after its order is successfully completed. Do not treat search results, recommendations, viewed products, cart-only items, abandoned checkouts, failed orders, or cancelled orders as completed purchase history.
- Completed purchase records should eventually include the product identifier, product name, quantity, price paid, order date/time, order identifier when available, vendor/marketplace, and final order status.
- A single purchase must not automatically become a user preference. Preferences may come from explicit user statements, future reviewable rules, or repeated evidence only if a later policy explicitly allows it.
- Users must eventually be able to inspect, correct, and delete preferences and purchase-history records independently.
- Purchase history must support future duplicate prevention, reorder logic, auditability, and order-status workflows.

Do not begin Amazon automation until the current milestone is documented and the next checkpoint is selected deliberately.

## 12. Immediate Next Checkpoint

Manually verify explicit aliases and natural-language examples through Telegram: `remember: <key> = <value>`, `recall: <key>`, `forget: <key>`, `search: AA batteries`, “Remember that I prefer Sensodyne,” “Find AA batteries under twenty dollars,” “Order AA batteries,” and “What’s the capital of France?” Confirm natural memory and search use their existing safe routes, buy/reorder remain non-executing, and ordinary chat still works.

Do not start later roadmap work during this checkpoint. After verification, update this document with the observed results before selecting the next milestone.

## 13. Development Workflow and Learning Preferences

- Inspect current files before changing them; do not assume older documentation is accurate.
- Keep the Big 3 boundaries intact: Telegram in `main.py`, coordination in `agent.py`, and model-server communication in `llm_client.py`.
- Prefer small, verified changes. Use the project's virtual-environment Python for syntax and import checks.
- Do not start the Telegram bot during automated validation unless explicitly requested; manual Telegram testing remains a separate step.
- Use beginner-friendly comments for module boundaries and non-obvious logic, but do not comment every obvious line.
- After a significant concept, use a Teach-It-Back checkpoint:
  1. Explain the concept.
  2. Ask the user to restate it in their own words.
  3. Correct only meaningful misunderstandings.
  4. Continue.
- A separate personal learning library exists at `~/engineering-notes/`. It stores mental models and explanations, and is not production project documentation.
