# Amazon AI Purchasing Agent — Architecture Decisions

This document records durable architectural decisions and their reasoning.

Unlike PROJECT_STATE.md, this file is not replaced every session.

Append new decisions.

If a decision is reversed, retain the old entry and add a new entry explaining the change.

---

## ADR-001 — Telegram Is the Primary User Interface

Status: Accepted

Decision:

Use Telegram as the primary interface for purchase requests, confirmations, clarification questions, and status updates.

Context:

The final goal is to send a product request quickly from an iPhone without opening Amazon or a custom application.

Reasoning:

- Telegram already provides a mature mobile application.
- It works well from an iPhone.
- It supports messages and notifications.
- It removes the need to build a mobile UI.
- It allows the agent to run elsewhere while the phone acts as a remote control.

Tradeoffs:

- Telegram is a third-party dependency.
- The interface is conversation-oriented rather than a full custom UI.
- Telegram availability affects the agent interface.
- Sensitive information should not be exposed unnecessarily through chat.

Consequences:

The iPhone does not run the agent or model.

The architecture is:

iPhone
    ↓
Telegram
    ↓
Python agent running on Mac or future server

---

## ADR-002 — Python Is the Application Language

Status: Accepted

Decision:

Use Python for the Telegram interface, agent logic, memory, tools, and browser automation.

Reasoning:

strong AI and automation ecosystem
Telegram library support
Playwright support
SQLite support
Pydantic support
easy integration with local model APIs
suitable for incremental learning and modular development

Tradeoffs:

Python is less strict than some compiled languages
asynchronous code requires careful handling
packaging and environment management must be maintained

Consequences:

The project uses a Python virtual environment and Python modules separated by responsibility.

---

## ADR-003 — LM Studio Is the Initial Local Model Runtime

Status: Accepted

Decision:

Use LM Studio as the initial local model runtime.

Do not default to Ollama.

Context:

The user intentionally chose:

LM Studio + local model

Reasoning:

local execution
no per-token fees
privacy
educational visibility into the local AI stack
convenient model management
local API support
ability to swap models

Tradeoffs:

current deployment is tied operationally to a Mac application
always-on operation requires LM Studio service availability
future server deployment may use a different runtime
model formats may vary by platform

Consequences:

Application code must communicate through an API boundary rather than directly loading model weights.

---

## ADR-004 — The Model Must Remain Swappable

Status: Accepted

Decision:

Do not couple agent logic directly to DeepSeek, Qwen, Gemma, or another model family.

The application should communicate with an LLM client interface backed initially by LM Studio.

Architecture:

agent logic
      ↓
LLM client/API boundary
      ↓
LM Studio
      ↓
selected model

Reasoning:

local model quality changes rapidly
hardware may change
deployment may move from Mac to server
different models may be better for different workloads
replacing a model should not require rewriting Telegram, memory, or Amazon tools

Tradeoffs:

introduces an abstraction boundary
requires consistent request/response handling
model-specific features may need adapters

Consequences:

Model selection is configuration, not core business logic.

---

## ADR-005 — Telegram and Agent Logic Are Separate Modules

Status: Accepted and implemented

Decision:

Move agent_brain() out of main.py into agent.py.

Before:

main.py
    Telegram logic
    agent logic

After:

main.py
    Telegram interface

agent.py
    agent brain

Reasoning:

separation of concerns
prevents main.py from becoming a giant script
makes agent behavior easier to test
creates a clean location for LM Studio integration
prepares for memory and tools

Tradeoffs:

introduces imports
slightly more files
requires module-path awareness

Verification:

The bot started successfully after importing:

from agent import agent_brain

Telegram returned the expected response.

---

## ADR-006 — Use Environment Variables for Secrets and Identity Configuration

Status: Accepted and implemented

Decision:

Store the Telegram token and authorized user ID in .env.

Known values:

TELEGRAM_BOT_TOKEN=<secret>
AUTHORIZED_TELEGRAM_USER_ID=6012316867

Reasoning:

prevents hardcoding secrets
separates configuration from code
supports future deployment environments
reduces accidental token exposure

Tradeoffs:

environment configuration can be missing
startup validation is needed
.env must remain excluded from source control

Consequences:

The code uses:

load_dotenv()
os.getenv(...)

Future work should add explicit configuration validation.

---

## ADR-007 — Restrict Telegram Access by User ID

Status: Accepted and implemented

Decision:

Only the configured Telegram user ID may interact with the bot.

Current logic:

if update.effective_user.id != AUTHORIZED_USER_ID:
    print("Unauthorized user attempted to access the bot.")
    return

Reasoning:

A purchasing agent is financially sensitive and cannot be exposed as a public bot.

Tradeoffs:

supports only one user initially
user-ID changes require configuration changes
silent rejection provides limited feedback

Consequences:

Authorization exists before purchasing features are introduced.

Further controls will still be required before autonomous transactions.

---

## ADR-008 — Use Playwright for Browser Automation

Status: Accepted in principle; not yet implemented

Decision:

Use Playwright for Amazon browser automation.

Reasoning:

modern browser automation
Chromium support
strong Python API
supports persistent contexts
appropriate for inspecting and controlling dynamic pages
already installed in the virtual environment

Tradeoffs:

selectors may break
Amazon may detect or challenge automation
CAPTCHAs and MFA may interrupt flows
browser automation is less stable than a supported commerce API
policy and terms-of-service considerations must be reviewed

Consequences:

Amazon browser logic must be isolated behind a tool boundary.

The agent model must not manipulate browser internals directly.

---

## ADR-009 — Begin With SQLite for Memory

Status: Proposed and accepted for roadmap; not implemented

Decision:

Use SQLite as the initial persistent memory database.

Reasoning:

free
local
built into Python
simple deployment
sufficient for a single-user personal agent
easy to inspect and back up
avoids premature database infrastructure

Tradeoffs:

limited concurrency
not ideal for distributed deployment
migrations must still be managed
future server scaling may require another database

Consequences:

The memory layer should be abstracted enough that a future database change does not rewrite the agent controller.

---

## ADR-010 — Build Purchasing Automation in Safety Stages

Status: Accepted for roadmap

Decision:

Do not begin with unrestricted autonomous purchasing.

Roll out progressively:

1. Search only
2. Recommend
3. Add to cart
4. Prepare checkout
5. Request confirmation
6. Auto-purchase only under preapproved rules

Reasoning:

Purchasing is financially consequential and browser automation can behave unexpectedly.

Tradeoffs:

longer path to the ultimate experience
requires intermediate workflows
confirmation adds user friction

Consequences:

Dry-run mode, audit logs, price limits, duplicate detection, and confirmation rules must precede autonomous buying.

---

## ADR-011 — The iPhone Is an Interface, Not the Compute Host

Status: Accepted

Decision:

The agent should work from an iPhone through Telegram, but the iPhone will not initially run Python, LM Studio, the model, or Playwright.

Reasoning:

avoids iOS runtime limitations
preserves local model architecture on the Mac
enables quick product requests from anywhere
Telegram already brokers messages over the internet

Tradeoffs:

Mac or future server must remain online
purchases fail when the host is unavailable
remote operation depends on Telegram and host connectivity

Consequences:

Productionization requires an always-running host service, automatic restart, and health monitoring.

---

## ADR-012 — Optimize the Local Model for the Whole System, Not Model Benchmarks Alone

Status: Open pending final model selection

Decision direction:

Choose a model that leaves enough memory for the entire application on an 8 GB Apple Silicon Mac.

Reasoning:

The host must concurrently support:

macOS
LM Studio
local model
Python bot
future Playwright Chromium session
future SQLite and supporting processes

A model that barely loads is not necessarily usable for the complete agent.

Tradeoffs:

a smaller model may have weaker reasoning
a larger model may create swapping, latency, or crashes
the best model family may change quickly

Consequences:

A practical 3B–4B 4-bit model is currently considered more likely than a 9B model, but this decision remains open pending current verification.

When finalized, append a new ADR with:

exact model
exact variant
format
quantization
memory rationale
date selected
fallback model

---

## ADR-013 — Use Qwen3.5-4B-MLX-4bit as the Current Development Model

Status: Accepted and verified

Decision:

Use `Qwen3.5-4B-MLX-4bit` as the current development model, served by LM Studio with the identifier `qwen3.5-4b-mlx`.

Reasoning:

The development Mac has 8 GB of unified memory. This 4-bit 4B model is a practical fit that leaves headroom for macOS, LM Studio, and the Python Telegram application.

Consequences:

Model configuration remains replaceable, but current local development and verification use this model.

---

## ADR-014 — Use the OpenAI Python SDK as an LM Studio-Compatible Client

Status: Accepted and implemented

Decision:

Use the OpenAI Python SDK as a client for LM Studio's OpenAI-compatible local API.

Reasoning:

It provides an async chat-completion client while LM Studio remains the local model server.

Consequences:

This is not an OpenAI cloud-model dependency. The SDK communicates with the configured LM Studio base URL.

---

## ADR-015 — Keep LLM Endpoint and Model Selection in Environment Configuration

Status: Accepted and implemented

Decision:

Read `LLM_BASE_URL` and `LLM_MODEL` from environment configuration instead of hardcoding them in Python.

Reasoning:

The local server endpoint and selected model can change without changing application code.

Consequences:

The configuration must be documented and validated as part of future setup hardening.

---

## ADR-016 — Maintain the Big 3 Module Boundary

Status: Accepted and implemented

Decision:

- `main.py` owns Telegram behavior and application startup.
- `agent.py` owns coordination.
- `llm_client.py` owns model-server communication.

Reasoning:

Separating these responsibilities keeps the current application understandable and allows future memory or tool work without coupling it to Telegram or LM Studio details.

---

## ADR-017 — Use a Complete-Response Telegram Experience

Status: Accepted and implemented

Decision:

Send `Thinking…`, wait for one completed model response, then replace the placeholder with the final answer.

Reasoning:

This is simpler and more reliable for the current user experience than incremental response updates.

Consequences:

Completed answers longer than Telegram's message limit are split into additional complete messages after generation finishes.

---

## ADR-018 — Exclude Streaming From the Current Implementation

Status: Accepted

Decision:

Do not use streaming responses in the current application.

Reasoning:

Streaming added unnecessary complexity for the current UX, including chunk handling, whitespace-only output, edit throttling, and Telegram message-update errors.

Consequences:

Future streaming work requires a deliberate new decision and separate verification; it must not be reintroduced casually.

---

## ADR-019 — Use Beginner-Friendly Architectural Comments

Status: Accepted and implemented

Decision:

Use concise comments to explain module boundaries and non-obvious logic, without commenting every obvious line.

Reasoning:

The codebase is also a learning project. Clear comments make the responsibility boundaries easier to understand while preserving readability.

---

## ADR-020 — Use Pytest With Mocked External Boundaries for the Initial Test Baseline

Status: Accepted and implemented

Decision:

Use pytest for a small unit-test baseline. Mock LM Studio and Telegram-facing boundaries rather than requiring a running model server, network access, or Telegram bot.

Reasoning:

The current complete-response flow can be verified quickly and repeatably without external services. This keeps the first test suite focused on existing behavior and preserves the Big 3 module boundaries.

Consequences:

`requirements-dev.txt` pins pytest for development, and tests exercise the language-model response handling, agent fallback, and completed-response Telegram sectioning in isolation.

---

## ADR-021 — Separate Purchase History From User Preferences

Status: Accepted for roadmap; not yet implemented

Decision:

Purchase history and user preferences are separate concepts and must remain separate in the data model.

Only successfully completed orders enter purchase history.

A purchase does not automatically create or update a preference.

Preference inference must not be introduced without explicit, transparent, reviewable rules.

Context:

A product may be purchased as a gift, experiment, one-time need, or because of price or availability. Adding a product to a cart or attempting checkout does not prove a purchase occurred. Treating purchases as preferences would create inaccurate personalization and unsafe future automation.

Reasoning:

- Improves accuracy by preserving the distinction between recorded transactions and user intent.
- Supports auditability of completed orders and later decisions that use them.
- Enables duplicate prevention without incorrectly treating a prior order as a preference.
- Makes reorder behavior trustworthy by consulting purchase history without claiming preference.
- Makes correction and deletion easier because each data type has a distinct purpose and lifecycle.
- Creates safer future automation by avoiding unreviewed preference inference.
- Provides clearer data ownership and responsibility between preference storage and purchase-history storage.

Tradeoffs:

- Requires more data structures.
- Requires more explicit lifecycle handling.
- Completed-order confirmation must be reliable.
- Preference learning becomes slower and more conservative.

Consequences:

- Future Amazon automation must emit a verified completed-order event before purchase history is written.
- Failed, abandoned, cancelled, and cart-only states must not be written as purchases.
- Preference storage and purchase-history storage must be independently inspectable and editable.
- Future reorder logic may consult purchase history without claiming the user prefers the product.
- Any future preference-learning policy requires a separate ADR or explicit policy decision.

---

## ADR-022 — Isolate Amazon Automation Behind a Dedicated Tool Boundary

Status: Accepted and implemented for read-only search

Decision:

Amazon automation remains isolated behind a dedicated tool boundary.

`src/amazon.py` owns every Amazon and Playwright interaction. `agent.py` may call its explicit interfaces and consume structured results, but it does not control browser details. `main.py` remains Telegram-only, and `llm_client.py` remains LM Studio-only.

Context:

Amazon browsing is operationally fragile and financially consequential once it progresses beyond search. Browser selectors, bot challenges, authentication, and order state must not leak into agent reasoning or Telegram handling.

Reasoning:

- Keeps browser behavior replaceable and independently testable.
- Prevents browser details from coupling to model communication or Telegram handling.
- Makes the read-only search capability a narrow, auditable first stage of the purchasing-safety roadmap.
- Creates a clear location for future safeguards around login, cart, checkout, confirmation, and completed-order events.

Tradeoffs:

- Adds an integration boundary and structured result conversion.
- Browser failures must be handled separately from model failures.
- Future Amazon operations require explicit interfaces rather than direct agent access to browser internals.

Consequences:

- The initial interface is async `search_products(query: str)`, returning up to five structured `Product` records from public Amazon search results.
- The initial implementation does not log in, add items to a cart, begin checkout, purchase, scrape reviews, or store purchase history.
- Future financially consequential operations must be introduced as narrowly scoped tool interfaces with their required safety controls; they must not be added to the read-only search function.

---

## ADR-023 — LLMs Evaluate Products but Do Not Discover Products

Status: Accepted and implemented for read-only search evaluation

Decision:

Amazon is the source of product facts. The evaluator receives structured product data only. The LLM is responsible for reasoning, ranking, and explanation; it must not hallucinate missing product facts.

Context:

The initial Amazon tool can retrieve a limited, explicit set of public search-result fields. A language model can compare those records in natural language, but it cannot reliably discover facts that Amazon did not supply.

Reasoning:

- Separates factual discovery from recommendation reasoning.
- Keeps Amazon interaction in the dedicated browser-tool boundary.
- Makes the data available to the model explicit and auditable.
- Reduces misleading recommendations based on invented specifications, availability, prices, ratings, reviews, or Prime eligibility.

Tradeoffs:

- Recommendations are limited by the narrow initial product schema.
- Missing facts may prevent a confident recommendation or budget alternative.
- Prompt instructions reduce, but cannot fully eliminate, model hallucination.

Consequences:

- `product_evaluator.py` accepts `Product` records and the original user request, then calls only `llm_client.generate_response()`.
- The evaluator prompt directs the model to use only structured fields, explain tradeoffs, recommend a top choice, and offer a budget alternative only when appropriate.
- The evaluator must not call Amazon, Playwright, Telegram, or storage.
- Future product-fact expansion belongs in the Amazon data boundary; future ranking-policy changes should be made explicitly and remain reviewable.

---

## ADR-024 — Carry Explicit Request Context Through Product Evaluation

Status: Accepted and implemented for read-only search

Decision:

Use an immutable `RequestContext` to carry request-level facts from orchestration to product evaluation. The evaluator returns an `EvaluationResult` that keeps recommendation text separate from metadata reserved for future workflows.

Context:

Read-only product evaluation already needs the original request and search query. Future reorder, confirmation, duplicate-prevention, and purchase-history workflows will need additional request facts, but none may be inferred into action today.

Reasoning:

- Makes the data flowing into evaluation explicit and testable.
- Avoids growing positional function arguments as future workflow stages are designed.
- Allows reorder-style wording to be recorded as metadata without granting order-history access or changing Telegram output.
- Keeps future financially consequential data separate from the current read-only search behavior.

Tradeoffs:

- Adds small context and result types for a currently narrow flow.
- Requires callers to populate only facts they actually know.
- Reorder metadata remains intentionally limited and cannot substitute for verified order history.

Consequences:

- The search route populates only current request, intent, search-query, and confidence fields; the future order-history candidate remains unset.
- The evaluator may flag clear reorder wording in `EvaluationResult`, but it must not access Amazon order history, invent products, or alter user-visible behavior.
- Future reorder, purchase-history, preference-inference, and duplicate-order work requires separate policy and safety decisions before implementation.

---

## ADR-025 — Intent Classification Is Separate From Tool Execution

Status: Accepted and implemented for current routing

Decision:

Intent classification determines user goals. Tool execution remains inside `agent.py`. Classifiers never directly invoke tools, and structured intent is validated before routing. Invalid classifications safely fall back to `general_chat`.

Context:

The agent now needs to accept natural-language memory and Amazon requests while preserving the existing explicit commands as aliases. A local language model can identify the likely request type, but it must not be trusted to perform actions or provide unvalidated tool input.

Reasoning:

- Keeps the classifier narrowly responsible for identifying what the user wants.
- Keeps control of memory and Amazon operations in the orchestration boundary.
- Makes model output inspectable and safely rejectable through strict JSON validation.
- Preserves a safe fallback when LM Studio is unavailable, returns malformed data, or is not confident enough for an actionable route.

Tradeoffs:

- Natural-language requests add an LM Studio classification call before routing.
- Entity extraction quality depends on the local model and must be manually verified.
- Low-confidence requests may fall back to ordinary chat instead of taking a potentially useful action.

Consequences:

- `intent_classifier.py` returns only validated `IntentResult` metadata and has no tool, memory, Amazon, Telegram, or purchasing access.
- `agent.py` routes validated memory and search intents to existing interfaces; it returns safe non-executing responses for buy and reorder intents.
- Explicit `remember:`, `recall:`, `forget:`, and `search:` commands remain supported aliases.
- Future classifier expansion must preserve validation and agent-owned execution boundaries.

---

## ADR-026 — Development Purchase Confirmation Gate

Status: Accepted for roadmap; not yet implemented

Decision:

During early development, validation, and the initial production milestone, every purchase must pass through a mandatory explicit user-confirmation gate.

The confirmation gate is a hard architectural boundary immediately before the irreversible `Place Order` action. No code path may bypass it. After an order is prepared, the agent must send a Telegram summary of the selected product, quantity, price, shipping, and estimated total, then wait for explicit user confirmation before placing the order.

Context:

Purchasing is financially consequential. Early workflows require a visible, reviewable pause between preparation and the irreversible external action while the agent, browser automation, product data, and safety controls are being validated.

Reasoning:

- Prevents an accidental or incorrect prepared order from being completed immediately.
- Makes the user-visible cost and delivery details reviewable at the final decision point.
- Creates one auditable control point that future purchase implementations must share.
- Keeps safety policy in deterministic application architecture rather than model behavior alone.

Tradeoffs:

- Adds friction to every early purchase.
- Requires reliable prepared-order summaries and explicit confirmation-state handling.
- Delays fully automated repeat-order behavior until configurable approval policy is designed and verified.

Consequences:

- Future purchase code must prepare an order, present the required Telegram summary, and receive explicit confirmation before the final action.
- No browser, model, workflow, or retry path may directly place an order without passing through the gate.
- The long-term design may allow configurable approval rules, but the initial production milestone always requires explicit user confirmation.

---

## ADR-027 — Structured Natural-Language Memory Intent Boundary

Status: Accepted and implemented

Decision:

Natural-language memory requests must cross a validated structured-intent boundary before `agent.py` reads or changes memory. The classifier returns an intent record with a memory action (`remember`, `recall`, `forget`, or `general_chat`) and nullable key/value fields. `agent.py` performs the memory operation only after validating the required fields: remember requires a non-empty key and value; recall and forget require a non-empty key.

The colon-form commands `remember: <key> = <value>`, `recall: <key>`, and `forget: <key>` remain deterministic developer/debug aliases. Only malformed attempts at those exact colon-form commands receive command usage guidance. Other ordinary or command-like natural language is sent to classification.

Classification is time-bounded in the orchestration layer. A timeout, model failure, malformed JSON, or invalid/ambiguous memory result returns a friendly safe response and must not read, write, or delete memory.

Context:

Manual Telegram verification proved the deterministic explicit commands, but ordinary memory requests were incorrectly intercepted as malformed commands or waited for several minutes on local-model classification. The agent needs natural-language support without granting unvalidated model output authority over persistent user data.

Reasoning:

- Separates probabilistic extraction from deterministic persistence actions.
- Makes the data required for each memory action explicit, testable, and rejectable.
- Preserves reliable short developer/debug commands while allowing ordinary user language to reach classification.
- Prevents an unavailable or slow local model from leaving the Telegram placeholder indefinitely.
- Fails closed for memory mutations rather than guessing a key or value.

Tradeoffs:

- Natural-language memory requests add one local-model classification call and may receive a friendly retry response when extraction is uncertain.
- The 10-second deadline can reject a valid request when the local machine is overloaded.
- A stricter schema requires model prompts and tests to evolve together when the intent contract changes.

Consequences:

- `intent_classifier.py` remains the only model-facing classification component; it never accesses memory or executes actions.
- `agent.py` retains orchestration and is the only component that invokes `memory.py` for classified requests.
- Invalid classifications cannot mutate memory, and valid `general_chat` classifications retain the ordinary LLM response path.
- Future changes to natural-language memory semantics must preserve validation, deterministic explicit aliases, and bounded failure behavior.

---

## ADR-028 — Use Deterministic Extraction Before Memory-Classification Fallback

Status: Accepted and implemented

Decision:

Handle only clear, predefined natural-language memory phrases with a pure deterministic extractor before invoking the structured LLM classifier. The extractor returns the existing validated action/key/value representation and has no storage access. `agent.py` remains the only memory executor.

When deterministic extraction has no confident match, use the existing classifier architecture as fallback. For memory-style fallback requests, use a compact JSON contract containing only `action`, `key`, and `value`; broad classification remains unchanged for general chat and Amazon intents.

Context:

The 10-second classification deadline expired for a clear natural-language remember request before LM Studio returned a response. The diagnostic evidence contained the outbound prompt but no response or validation result. The previous all-intent prompt requested unrelated Amazon, product, confidence, reasoning, and confirmation fields for a simple memory operation, adding avoidable local-model work.

Reasoning:

- Eliminates local-model latency for clear, repeatable memory phrasing.
- Keeps deterministic extraction conservative: ambiguous language does not guess a key, value, or action.
- Preserves the structured model boundary for language outside the narrow deterministic grammar.
- Reduces the fallback prompt and output schema to the facts needed for safe memory routing.
- Retains agent-owned storage execution and the existing timeout fail-safe.

Tradeoffs:

- The deterministic grammar intentionally supports only a small set of phrases and needs explicit expansion for new wording.
- Ambiguous requests can still time out or fail validation rather than taking a guessed action.
- Two extraction paths require regression tests to keep their shared action/key/value contract aligned.

Consequences:

- Clear supported memory requests no longer need LM Studio before reaching `agent.py` memory execution.
- The parser performs no reads, writes, or deletes; it returns metadata only.
- Ambiguous memory requests retain the 10-second deadline and concise terminal-only diagnostics for timeout, model error, invalid JSON, and validation failure.
- Verbose prompt, user-message, and raw-response debug logging is removed from normal development behavior.

---

## ADR-029 — Use a State-Aware Top-Level Conversational Router

Status: Accepted and implemented

`agent.py` routes exact memory commands, deterministic natural memory, state-aware purchase dialogue, structured classifier fallback, and general chat in that order. A parser no-match is not an error and no subsystem may emit another subsystem's usage response. This fixes purchase language entering memory handling while preserving explicit debug commands.

---

## ADR-030 — Persist One Active Preview Workflow Per Telegram User

Status: Accepted and implemented

SQLite stores one active purchasing workflow per authenticated Telegram user, including versioned state, safe request context, mocked candidates, selection, quantity, and timestamps. The workflow survives process restart. A new purchase request does not silently overwrite an active workflow; the user must cancel it first.

---

## ADR-031 — Resolve Candidate References Against Persisted Typed Candidates

Status: Accepted and implemented

Candidate reference resolution is a dedicated deterministic boundary. It may select only a unique stored candidate by ordinal, title/brand fragment, or supported comparison; zero or multiple matches produce focused clarification and never silently guess.

---

## ADR-032 — Define a Mocked Amazon Workflow Interface Before Live Automation

Status: Accepted and implemented

`amazon.py` defines a typed future gateway for order lookup, search, details, cart, checkout inspection, and confirmed placement. Milestone 1 uses mocked typed candidates only and does not invoke Playwright, cart, checkout, or placement through that interface.

---

## ADR-033 — Hierarchical Semantic Extraction Is the Conversational Front Door

Status: Accepted and implemented

Decision:

Replace deterministic natural-language memory and purchase parsers, plus the broad all-intent schema, with hierarchical LLM semantic extraction. A compact router first chooses only `memory`, `purchase`, `workflow`, `general_chat`, or `unknown`. Only a confident actionable route invokes its small domain specialist. Each specialist returns JSON that is strictly validated before `agent.py` performs deterministic work.

Context:

The earlier phrase-based front door failed ordinary grammatical variations such as “What was my favorite toothpaste?” and made future capability growth depend on continually adding patterns. The oversized classifier schema also asked a small local model to solve unrelated extraction problems in every request.

Consequences:

- `intent_classifier.py` has no memory, browser, workflow-store, Telegram, or purchase execution access.
- `agent.py` remains the sole executor for memory and workflow state transitions.
- Invalid JSON, malformed fields, low confidence, model failures, and timeouts become no-match; they never produce usage guidance or execute an action. The agent then continues to general chat.
- Explicit colon-form developer aliases remain deterministic.
- Router output is capped at 32 tokens; specialists are capped at 80–100 tokens and use JSON mode.
- The old natural-language parser and its phrase list were removed. Candidate reference resolution remains deterministic only after a workflow already has stored candidates.
- Live LM Studio and Telegram verification remains pending because the configured local endpoint was unavailable during this change.

---

## ADR-034 — Semantic Interpretation Uses Diagnostic Soft and Safety Hard Timeouts

Status: Accepted and implemented

Decision:

Semantic interpretation has a 20-second soft timeout and a 120-second hard timeout. The soft timeout only emits a timing diagnostic and continues to await the same task. The hard timeout cancels that task, awaits its cancellation, and returns the existing graceful local-model failure response.

Context:

The former short orchestration deadline cancelled semantic interpretation before a local LM Studio response could complete. This made a diagnostic threshold behave as a user-visible failure.

Consequences:

- Telegram retains its existing “Thinking…” placeholder while the semantic task continues after 20 seconds.
- Request-scoped monotonic timings cover preparation, HTTP request setup, first byte, first generated token, model completion, parsing, validation, deterministic action execution, and total elapsed time.
- The timing log compares the LM Studio window with all remaining time, so it explicitly identifies when the majority of measured latency is outside LM Studio.
- The hard deadline prevents indefinite waits while preserving the existing JSON validation, deterministic action execution, memory-mutation, and preview-only purchasing safeguards.

---

## ADR-035 — LM Studio Structured Output Uses Strict Plain JSON With a Guarded Reasoning Fallback

Status: Accepted and implemented

Decision:

Semantic prompts request one compact JSON object directly at temperature zero with a bounded 256-token output budget. The prior permissive native JSON-schema request is not used for semantic extraction. Visible assistant content remains preferred. Only when it is empty may `llm_client.py` use LM Studio `reasoning_content`, and only if the complete trimmed field parses as exactly one JSON object. Typed route and specialist validation remains unchanged in `intent_classifier.py`.

Context:

The configured Qwen/LM Studio response shape placed generated output in `reasoning_content` while `message.content` was empty despite `finish_reason=stop`. The permissive schema accepted `{}`, which provided no semantic fields.

Consequences:

- Reasoning prose, markdown, multiple JSON objects, arrays, prefixes, suffixes, and incomplete output cannot trigger an action.
- A clean JSON object from the compatibility field receives the same downstream validation as visible JSON.
- Memory and workflow mutation remain agent-owned and fail closed on malformed output.

---

## ADR-036 — Default the Installed Qwen Template to a Closed Think Block

Status: Accepted and implemented

Decision:

For the installed `lmstudio-community/Qwen3.5-4B-MLX-4bit` model, default the existing `enable_thinking` template branch to false when the variable is absent, then unload and reload the same model. Preserve a backup of the original template outside the repository.

Context:

The model’s bundled template began generation with an open `<think>` block by default. LM Studio 0.4.20+1 did not pass the template variable through the OpenAI-compatible request and reported that its reasoning control could not be converted to custom model fields. Consequently, normal final output was classified as reasoning and `message.content` was empty for both raw HTTP and SDK requests.

Consequences:

- The same Qwen model and LM Studio runtime now emit final assistant output into `choices[0].message.content`.
- Raw HTTP and OpenAI SDK responses agree; no client mapping workaround is needed.
- The application keeps its strict JSON validation and guarded reasoning fallback as defense in depth.

---

## ADR-037 — Separate Conversational, Semantic, and Product-Fact Prompt Contracts

Status: Accepted and implemented

Decision:

Use a dedicated user-facing purchasing-agent system prompt only for ordinary Telegram conversation. Keep semantic router and specialist calls on compact JSON-only user prompts with no conversational system prompt. Use a distinct fact-bounded system prompt for product evaluation. Bound ordinary conversation to a 180-token request budget and sentence-safe Telegram-sized normalization.

When semantic interpretation does not yield a validated purchase action but a message still has generic shopping signals, return one deterministic clarification instead of allowing an unconstrained general-chat model response.

Context:

Telegram testing showed a shopping request receive a long generic answer containing unsupported retailer-style recommendations. The old fallback called the language model with only the raw user message, even though the model is the language layer of a purchasing agent rather than an independent shopping source.

Consequences:

- Validated shopping requests remain agent-owned preview workflows and cannot use general chat to invent product facts.
- Semantic JSON extraction retains its exact schema, bounded JSON contract, and all existing mutation safeguards.
- Product comparison remains limited to supplied structured records; the new prompt does not authorize search, cart, checkout, or ordering.
- A small commerce-signal fallback is a fail-closed guard, not a product parser or a replacement for semantic routing. It creates no workflow and executes no tool.

---

## ADR-038 — Require Read-Only Amazon Results Before Creating Purchase Candidates

Status: Accepted and implemented

Decision:

Remove fabricated candidate generation from natural-language purchase start. The agent now invokes the existing isolated, unsigned, read-only Amazon search boundary and creates a selectable workflow only from returned product records. A candidate preserves only result fields supplied by that boundary. Amazon interstitials, selector timeouts, empty results, and search failures return a concise no-workflow response.

Context:

The preview workflow used fixed mock products, prices, delivery labels, and ratings. This produced unrelated results — including Sensodyne-labelled AA batteries — and made an ordinary shopping reply look useful despite having no Amazon evidence.

Consequences:

- Product titles, prices, ratings, review counts, Prime indicators, and URLs shown in a workflow must originate in a read-only Amazon result.
- No fabricated candidate may be selected, persisted, or used for price comparisons.
- Legacy persisted candidates that lack a source URL are invalidated before new routing.
- Amazon can return an anti-automation or other interstitial; that condition is surfaced safely and does not broaden access, attempt a bypass, or permit a purchase.

---

## ADR-039 — Use a User-Managed Persistent Profile for Read-Only Amazon Search

Status: Accepted and implemented

Decision:

Use a visible persistent Playwright Chromium profile, stored outside the repository, for manual Amazon sign-in and subsequent read-only product searches. The profile is configurable only through `AMAZON_BROWSER_PROFILE_DIR`; a repository-local profile path is rejected. Visible mode is the default. Headless mode is explicit and reserved for noninteractive diagnostics.

Context:

Amazon returned an interstitial to an unsigned transient headless browser. A user-managed session is required to permit legitimate sign-in or challenge completion without trying to evade Amazon protections.

Consequences:

- `amazon.py` remains the sole owner of browser lifecycle, session reuse, selectors, and public-result extraction.
- The application never logs, parses, copies, or persists browser-profile contents, credentials, cookies, cart data, or account details.
- Manual sign-in is performed through a dedicated visible script; the application still performs no cart, checkout, or order action.
- Search and browser-close waits are bounded. A profile lock, interstitial, timeout, or browser error fails safely without creating a workflow or substituting stale/mock products.

---

## ADR-040 — Every Workflow State Change Goes Through `workflow_store.transition()`

Status: Accepted and implemented

Decision:

`agent.py` may not assign `PurchaseWorkflow.state` directly. Every state change calls
`workflow_store.transition(workflow, state, pending_question=...)`, which is the only place that
increments `state_version`, sets the pending question, and records terminal completion status.

Context:

Only cancellation used `transition()`. Purchase start, refinement, and candidate selection assigned
`workflow.state` directly and set `pending_question` by hand, so `state_version` stayed at 1 for the
entire life of a workflow. ADR-030 describes versioned state and ADR-026 requires a confirmation that
is invalidated when the prepared order changes; both depend on a version that actually advances.

Reasoning:

- A version that never changes cannot detect a stale confirmation, which is the core control in ADR-026.
- One transition function is a single auditable place to add future guards on illegal transitions.
- Setting `pending_question` beside the state change keeps the persisted question consistent with the state.

Tradeoffs:

- Callers must express a state change as a transition rather than an assignment.
- `transition()` still does not reject illegal transitions; it only records them consistently.

Consequences:

- `state_version` is now a reliable monotonic counter per workflow and is covered by a regression test.
- Future cart, checkout, and confirmation work inherits a single place to enforce legal transitions and
  version invalidation; it must not reintroduce direct state assignment.

---

## ADR-041 — Persisted Workflow Records Are Deserialized Field-Tolerantly

Status: Accepted and implemented

Decision:

`PurchaseWorkflow.from_record()` and `Candidate` reconstruction ignore keys a stored payload contains
that the current model does not define, instead of raising. Required fields are still required.

Context:

Deserialization mapped every key by hand and called `Candidate(**candidate)`. Removing or renaming any
persisted field would make existing rows raise `TypeError`/`KeyError` inside
`workflow_store.get_active_workflow()`, which is called on the first line of ordinary message handling.
A model change would therefore have broken every incoming Telegram message for a user with a saved
workflow, in a code path with no error handling around it.

Reasoning:

- Persisted workflows outlive the code that wrote them, so the schema will change while rows exist.
- Failing to read one stored workflow should never be able to break unrelated conversation handling.
- Tolerant reads remove the hand-written field mapping, so adding a field no longer requires editing
  two places that can silently drift apart.

Tradeoffs:

- A genuinely mistyped field name is dropped silently instead of raising during development.
- Field removal is safe, but adding a *required* field to an existing model still needs a default.

Consequences:

- Workflow model fields can be added or retired without a migration script or a stale-row crash.
- Legacy-candidate invalidation (ADR-038) remains the mechanism for rejecting semantically stale data;
  tolerant deserialization is about shape, not trust.

---

## ADR-042 — Candidate References Resolve by Position, Comparison, or Described Words

Status: Accepted and implemented; refines ADR-031

Decision:

Deterministic candidate resolution tries, in order: an explicit comparison (`cheapest`,
`highest rated`), an explicit position (`option 5`, `#5`, a message that is only a number, an ordinal
word through fifth, and `last`), a description scored by the significant words the user typed, and
finally a bare number that appears anywhere in a longer message. Zero or ambiguous matches still
produce clarification and never guess. Resolution against labels the live Amazon path does not populate
was removed.

Context:

ADR-031 established the boundary but its implementation matched a description only when the *entire*
message was a substring of a candidate title, and it handled no digits at all. In manual Telegram
testing, `5` and `lets do the duracell` both failed against five real AA battery results. It also
branched on `option_label` values such as `"fastest delivery"` and `"best value"` that no code path
ever assigns, so those branches could only ever produce a misleading no-match.

Reasoning:

- An option number the agent itself displayed must always be selectable; that is the primary affordance.
- Scoring the overlap between the user's significant words and a candidate's title/brand handles natural
  references without a phrase list, and reports ambiguity when several candidates fit equally.
- Numeric tokens are kept regardless of length because pack counts (`10 count`, `3 pack`) are how users
  identify a variant.
- An explicit position must outrank a description so `option 2` is never reread as a product word, while a
  bare number inside a sentence is the weakest signal and is tried last.
- A branch that can never match is worse than no branch: it produces a confident-sounding failure.

Tradeoffs:

- Word-overlap scoring can call a reference ambiguous where a human would infer one product; it asks
  rather than guessing, which is the intended failure direction.
- The stop-word list is English and hand-maintained.
- `Candidate.option_label` and `delivery_label` are retained as unpopulated fields for Milestone 2
  ranking labels and delivery extraction; nothing reads them today.

Consequences:

- Resolution stays deterministic, stays inside `candidate_resolver.py`, and still selects only among
  already-persisted candidates.
- Regression coverage in `tests/test_candidate_resolution.py` pins the previously failing references,
  ambiguity behavior, out-of-range numbers, absent candidates, and comparisons with missing facts.
- When ranking labels and delivery estimates are populated, label-aware matching may return, but it must
  be added together with the data that makes it reachable.

---

## ADR-043 — A Question the Agent Asked Is Answered Deterministically and Statefully

Status: Accepted and implemented

Decision:

When the agent asks a question, it persists the question as workflow state. When a reply
arrives while a workflow is active, `workflow_reply.py` reads it deterministically first.
Only unambiguous replies — confirmation, refusal, cancellation, and an explicitly stated
option number — are acted on without the model. Anything else falls through to semantic
interpretation unchanged. A clarifying question now creates a workflow in
`AWAITING_REQUEST_CLARIFICATION`, and the next message answers it.

Context:

The shopping-signal fallback (ADR-037) asked "Are you looking for the cheapest matching
option on Amazon?" and stored nothing, so the answer arrived with no context and was
handled as an unrelated message. Separately, replies like `5` and `yea` had to survive a
router classification and a specialist classification before reaching the workflow; either
step returning `general_chat` or low confidence silently dropped the reply into ordinary
chat. The most common message in the conversation was also the least reliable, and it paid
full local-model latency.

Reasoning:

- A question with no persisted context cannot have a meaningful answer; the state machine
  already had `AWAITING_REQUEST_CLARIFICATION` for exactly this.
- "3" or "cancel" is not a language-understanding problem. Deterministic routing before
  probabilistic reasoning is the existing rule for explicit input (ADR-029).
- Removing a model round trip from the most frequent reply is a large, free latency win.
- ADR-033 removed deterministic *routing* phrase lists; it explicitly kept deterministic
  resolution once a workflow has stored candidates. This extends that, and does not revive
  a general phrase router.

Tradeoffs:

- The affirmative/refusal vocabulary is English and hand-maintained.
- The fast path is intentionally strict: every significant word must belong to one
  vocabulary, so "no, do you have anything cheaper" defers to the model rather than
  reading as a refusal. Strictness costs coverage and buys correctness.
- `workflow_reply` and `candidate_resolver` need different strictness for the same words.
  They share `candidate_resolver.explicit_position()` so they cannot disagree about what
  "option 3" means, but `workflow_reply` additionally requires the message to say nothing
  else. `candidate_resolver` may stay lenient because the model has already classified the
  message as a selection.

Consequences:

- An unclassified reply while a question is pending re-asks that question instead of
  answering something unrelated.
- A reply to a pending clarification is used as the search query even when the model
  returns no confident classification.
- A purchase request no longer collides with a pending clarification; it fulfils it.
- Storage, transitions, and Amazon access remain agent-owned. `workflow_reply` reads text
  and returns an intent; it touches nothing else.

---

## ADR-044 — Ordering and Hard Filtering Are Deterministic and Inspectable

Status: Accepted and implemented

Decision:

`ranking.py` owns hard constraint filtering and candidate ordering as pure functions. The
requested ordering is read from the user's own words. Price ordering uses price per item
when every candidate states a pack size, and total price otherwise. Every ordering reports
the basis it used and any limitation, and both are shown to the user.

Context:

`Find me cheap AA batteries.` returned Amazon's extraction order, and extracted constraints
such as `{"max_price": 20}` were stored on the workflow and never applied — the user's
stated budget was silently ignored. PRODUCT_REQUIREMENTS specifies two deterministic stages
(hard filtering, then inspectable ranking) and neither existed.

Reasoning:

- Ordering and budget checks are arithmetic and policy. The playbook and ADR-023 both place
  those in deterministic code, not in a prompt.
- A ranking that cannot state its basis cannot be trusted or debugged; returning the basis
  and caveat with the ordering makes the claim checkable.
- Comparing a 4-pack to a 48-pack by total price is misleading, so unit price is preferred
  whenever the facts support it and the fallback is stated out loud.
- A missing fact is not a violation: a candidate with no price cannot prove it exceeds a
  budget, so it is kept and listed last rather than dropped.

Tradeoffs:

- Pack size is read from title text, so an unusual title yields no unit price and the
  comparison falls back to total price.
- Reading the sort preference from words is a deterministic keyword check. It is a policy
  check on an already-routed purchase request, not a return to phrase-based routing.
- Only `max_price`, `min_rating`, and `prime` are enforced; other extracted constraints are
  stored and ignored rather than guessed at.

Consequences:

- Candidates are persisted in displayed order, so a reply of "3" always means the third
  line shown.
- A refinement re-filters and re-orders results already retrieved instead of requiring a
  new search, and a refinement matching nothing is reported and not persisted.
- Category-aware weighting from PRODUCT_REQUIREMENTS remains future work; this is the
  price/rating subset with honest reporting.

---

## ADR-045 — Conversation About Candidates Receives the Candidate Facts

Status: Accepted and implemented

Decision:

When a workflow with stored candidates is active, ordinary conversation is sent to the model
with those candidates serialized as structured context. Presentation of candidates to the
user lives in `product_display.py`; serialization of candidates for the model lives in
`product_evaluator.candidate_context()`.

Context:

The user asking "what's the difference between the first two?" reached `_general_response()`,
which sent only the raw message. The purchasing system prompt tells the model to summarize
only facts supplied by the application, and the application supplied none — so the model
could only refuse or invent. The safety prompt was correct and the plumbing defeated it.

Reasoning:

- ADR-023 makes the model an evaluator of supplied facts. A question about products already
  retrieved is exactly that, and the facts were already stored.
- Supplying facts is a stronger anti-hallucination control than instructing the model not to
  invent them.
- One path handles both cases: an unrelated question is still answered normally, so no
  additional routing decision or classifier field is required.

Tradeoffs:

- Every conversational turn during an active workflow carries a larger prompt.
- The model can still misread supplied facts; the prompt boundary remains the only control
  on its prose, and no deterministic claim checker exists yet.

Consequences:

- Presentation and model-facing serialization stay in separate modules with separate formats:
  Telegram text for the user, compact JSON for the model.
- Conversation with no active workflow is unchanged and carries no product context.

---

## ADR-046 — An Abandoned Workflow Expires Instead of Blocking Every Later Request

Status: Accepted and implemented; refines ADR-030

Decision:

`workflow_store.get_active_workflow()` treats a workflow untouched for longer than
`MAX_ACTIVE_WORKFLOW_AGE` (24 hours) as inactive. The stored row is kept for inspection; an
unparseable or missing timestamp is treated as fresh, never as expired.

Context:

ADR-030 requires the user to cancel an active workflow before starting another. Combined with
a workflow that is never completed — the normal outcome, since checkout does not exist — an
abandoned search from days earlier permanently rejected every new purchase request. Recovery
required knowing to type "cancel".

Reasoning:

- A conversational agent must not have a state the user can enter and not know how to leave.
- Expiry is a lifecycle policy over persisted state, so it belongs with persistence.
- Failing toward "still active" on a bad timestamp preserves the ADR-030 guarantee when the
  age cannot be established.

Tradeoffs:

- A genuinely long-running deliberation is dropped after a day.
- The threshold is a fixed constant rather than configuration.

Consequences:

- ADR-030's one-active-workflow rule still holds; it is now bounded in time.
- When cart and checkout are implemented, an expiring workflow must not be able to strand a
  prepared order: expiry policy has to be revisited alongside the ADR-026 confirmation gate.

---

## ADR-047 — Amazon Searches Run in the Background by Default

Status: Accepted and implemented; refines ADR-039

Decision:

`AMAZON_BROWSER_HEADLESS` defaults to true, so an ordinary search opens no window.
`AMAZON_BROWSER_HEADLESS=false` restores a visible browser for debugging.
`open_profile_for_manual_sign_in()` forces a visible browser regardless of the setting.

Context:

ADR-039 chose a visible browser as the safe default while read-only search was being
proven. In use that means a "Google Chrome for Testing" window pops open and closes on
every Telegram message, which is intrusive enough to make the agent unpleasant to use.

Reasoning:

- Headless is a display choice, not an evasion: the same persistent profile, the same
  signed-in session, and the same public pages are used. Nothing about ADR-039's rule
  against bypassing CAPTCHAs or protections changes.
- The one step that genuinely needs a window — manual sign-in — now asks for one
  explicitly rather than depending on a global default.

Tradeoffs:

- A challenge or interstitial is no longer visible as it happens; it surfaces as a
  failed search instead. The remedy is to set the variable to false and re-run sign-in.
- Headless sessions can be treated differently by some sites. This has not been verified
  live against Amazon with the signed-in profile, and that verification is outstanding.

Consequences:

- `_persistent_browser_context()` takes an explicit `headless` override.
- If searches begin failing after this change, the first diagnostic step is
  `AMAZON_BROWSER_HEADLESS=false` plus `scripts/amazon_profile_login.py`.

---

## ADR-048 — The List Is the Agent's Own, Not Amazon's Cart

Status: Accepted and implemented; refines ADR-030

Decision:

Adding an item records a `CartLine` in the agent's own SQLite workflow. Nothing is sent
to Amazon, and no Amazon cart is modified. Every message that shows the list states this.
A second purchase request searches again and keeps the list, so one conversation can
gather several products.

Context:

The workflow ended at "selected", and a request to add something to a cart produced
"Updated the workflow quantity", which reads as though a cart action succeeded. Separately,
ADR-030 required cancelling before starting another search, so asking for a second product
was refused outright — the worst moment in a role-played conversation.

Reasoning:

- A basket is the natural centre of a shopping conversation; without one the agent
  cannot express quantity, multiple items, or a total.
- Writing to a real Amazon cart requires browser automation whose selectors cannot be
  verified without a live signed-in session. On a page where "Add to Cart" sits beside
  "Buy Now", an unverified selector is an unacceptable risk. The local list delivers the
  entire product experience with none of that exposure.
- Saying "nothing has been added to your Amazon cart" on every list message prevents the
  single most damaging misunderstanding this agent could create.
- Shopping is iterative. Keeping the list across searches turns ADR-030's one-workflow
  rule from a restriction into a container.

Tradeoffs:

- The user must still buy on Amazon themselves; the agent stops one step short.
- Prices are copied at the time of the search and can go stale.
- A real Amazon cart adapter remains future work and needs its own ADR, live selector
  verification, and the ADR-026 controls before it may write anything.

Consequences:

- `cart.py` holds pure operations over stored candidate facts; a line can only ever show
  what a read-only search returned.
- An unknown price makes the whole subtotal unknown rather than quietly smaller.
- Restating "add it" for something already listed reports the existing line instead of
  doubling the quantity.

---

## ADR-049 — Order Placement Is Refused Deterministically, Before the Model

Status: Accepted and implemented

Decision:

Phrasing that asks to buy — "place the order", "confirm", "order it now", "buy it now" —
is matched deterministically in `workflow_reply.py` and routed to the agent's own
confirmation gate. The gate records the approval and refuses, because ordering is not
implemented. `checkout.place_order()` exists solely to raise `OrderPlacementDisabled`.

Context:

In a role-played conversation, "yes place the order" was classified as general chat and
answered by the language model. With a real model that reply could have been "Your order
has been placed" — a false confirmation of a financial action, produced by the component
least equipped to make that claim.

Reasoning:

- The most consequential sentence a user can type must not depend on a probabilistic
  classifier being in a good mood.
- A named function that always raises makes the absence of ordering assertable by a test
  rather than assumed from the absence of code.
- Recording the confirmation before refusing keeps ADR-026's gate real: when ordering is
  eventually implemented, the approval step already exists and is already versioned.

Tradeoffs:

- The keyword set is English and will miss unusual phrasing; those fall through to
  semantic interpretation, where the system prompt still forbids claiming an order.
- "confirm" is claimed by the gate even in states where nothing is pending, so the reply
  explains what to do instead.

Consequences:

- Tests assert that no module outside `checkout.py` mentions `place_order`, that
  `amazon.py` exposes no cart or ordering function, and that `agent.py` never references
  `PLACING_ORDER` or `COMPLETED`.
- Every confirmed summary states plainly that no order was submitted.

---

## ADR-050 — A Candidate Is Identified by Its Amazon Product, Not Its Position

Status: Accepted and implemented

Decision:

`candidate_id` is derived from the ASIN in the product URL, falling back to a stable hash
of that URL. It is no longer the result's position in a search.

Context:

Ids were `amazon-result-{index}`. In a conversation with two searches, the first result of
the second search reused `amazon-result-1` and merged into the unrelated line already on
the list: adding a t-shirt silently increased the quantity of a shampoo. This was found by
role-playing a multi-item conversation, not by the unit tests, which only ever searched once.

Reasoning:

- Identity must come from the thing itself. Amazon already provides one in the ASIN.
- A stable id makes "the same product found twice" merge correctly, which is the behaviour
  a cart should have.
- A URL hash keeps ids stable across restarts for listings without a `/dp/` path.

Consequences:

- Cart lines, selection, and removal all key off Amazon's product identity.
- Persisted workflows written before this change carry position-based ids; they remain
  readable (ADR-041) and are replaced on the next search.

---

## ADR-051 — The Model Is Removed From the Shopping Path

Status: Accepted and implemented; supersedes the shopping parts of ADR-023, ADR-033 and ADR-037

Decision:

No language model participates in shopping. Menus, references, state questions, and
searching are deterministic. Every user-visible word is generated by Python from stored
Amazon data or is a fixed string. The model's only remaining job is extracting a
natural-language memory request, and it is reached only when the message matches a
memory pattern.

Any message that is not a menu choice, a control word, a reference to something on
screen, or a question about stored state is sent to Amazon as a search.

Context:

Five UAT failures in one session were all the same failure: each landed on the terminal
`_general_response()` fallback, which handed the message to the model and returned
whatever came back. None reached the search, menus, ranking, cart, or order gate.

The worst produced invented products — "Garden Bug Spray, 16oz, organic formula" —
presented as Amazon results, with markdown asterisks and no prices. The system prompt
said, in those words, "Never invent product facts, prices, ratings, review counts,
sellers". **A prompt is not a control.**

Reasoning:

- Amazon's own search already does the natural-language understanding. Verified live:
  the raw sentence "alright, i need a new iphone 17 charger" returns iPhone 17 chargers,
  while the model answered "I don't have information about an iPhone 17 charger yet."
  Removing the model produced better results than tuning it could.
- An invariant that is structural can be tested. "The model cannot write to the user" is
  checkable; "the model should not invent facts" is not.
- Everything the user valued in testing — the result lists, the order summary, the menus
  — was already generated by Python. The model contributed none of it.
- Latency collapses. Menu actions now return in ~0.0s against 20-120s semantic timeouts.
- The agent works with LM Studio switched off.

Tradeoffs:

- No conversation. A non-shopping message gets a fixed reply and the menu.
- Questions about products are answered from templates over stored titles, and say
  plainly that a title is all the evidence there is.
- Natural memory phrasing still needs the model; colon commands always work.

Consequences:

- Deleted: `product_evaluator.py`, `request_context.py`, `response_policy.py`,
  `examples.py` and its corpus, and the semantic router, purchase and workflow
  specialists in `intent_classifier.py`.
- `state_answer.py` answers cart, total, results and attribute questions from stored
  state.
- Tests assert that no module outside `intent_classifier` mentions `generate_response`,
  that no shopping message reaches the model, and that a whole purchase completes
  without it.
- ADR-026's order gate is unchanged: ordering is still not implemented and still refused.

---

## ADR-052 — The Conversation Is a Numbered Menu

Status: Accepted and implemented

Note on ordering: this decision was implemented before ADR-051 but recorded after it.
The log is append-only, so it is added here rather than inserted in sequence.

Decision:

Every reply the agent sends ends with numbered choices, and the menu is persisted with
the workflow. A numeric reply resolves against exactly the menu the user read, is
executed before anything else, and never reaches a language model.

Context:

Three separate UAT failures had one shape: the same free text meant different things in
different places.

- The results footer offered `a brand, like "Employee"` as a way to *narrow*. Typing
  `employee` *selected* a product and added it to the list.
- `i prefer the runner up` became an Amazon search for "i prefer the runner up",
  returning marathon t-shirts.
- The user's own list rendered identically to search results, so items added earlier
  read as fresh suggestions.

Each was patched individually and another appeared, because the words the user typed
genuinely did not distinguish the intents. The agent had to guess, and no amount of
better guessing removes the ambiguity.

Reasoning:

- A number cannot be misread. Removing the ambiguity beats resolving it better.
- Persisting the menu means the number shown is the number resolved, even across a
  restart, so a stale menu cannot select the wrong product.
- It makes the common path instant: menu actions return in ~0.0s because no model,
  network, or search is involved.
- It is the smallest interface that still supports the whole flow: pick, narrow, search
  again, view list, remove, check out, confirm, start over.

Tradeoffs:

- Less conversational. The user picks from what is offered rather than saying anything.
- Menus must be rebuilt whenever state changes. Forgetting to do so leaves a stale menu:
  exactly that bug let "confirm" run twice and push to the real Amazon cart twice, which
  is now covered by a regression test.
- Free text still has to work for the opening request, which is why searching is the
  default for anything unrecognised (ADR-051).

Consequences:

- `menu.py` owns the numbered choices and reading one back; `flow.py` builds the menu for
  each point in the conversation; `PurchaseWorkflow.pending_menu` persists it.
- Results are headed with a magnifier and the list with a basket, because rendering them
  alike is what made a list read as suggestions.
- Output is Telegram HTML with `parse_mode` set, and every value from Amazon or the user
  is escaped. Before this, markdown was sent verbatim and the user saw literal
  `**Pick one:**`.

---

## ADR-053 — Relevance to the Query, Not Ad Markers, Removes Junk Results

Status: Accepted and implemented

Decision:

A search result that shares no significant word with the query is dropped before the
user sees it. Sponsored/ad markers are **not** used. If the filter would remove every
result, it removes none.

Context:

"melatonin 10mg" offered `1 · One Medical Membership: Get 24/7 on-demand care for 50+
conditions and more — $99.00` as a selectable product. Typing `1` would have put a $99
subscription on the list.

A live DOM probe of that results page settled how to fix it, and inverted the obvious
approach. The One Medical card carried **no** sponsored marker of any kind — not
`AdHolder`, not `s-sponsored-label`, not `puis-sponsored`, not the word "sponsored".
The three genuine melatonin products **did** carry one. Filtering on markers would have
deleted the good results and kept the placement.

Reasoning:

- The discriminator has to be a fact about the product, and the only one available is
  whether it has anything to do with what was asked for.
- Token comparison is deterministic, cheap, and explainable, and it needs no
  maintenance as Amazon changes its ad markup.
- Requiring only *one* shared word keeps the filter conservative: it removes the
  obviously unrelated without second-guessing Amazon's relevance ranking.
- Never emptying the list means a strict filter can annoy but cannot break a search.

Tradeoffs:

- A genuinely relevant product described entirely in synonyms would be dropped. Not
  observed live across the queries tested.
- A placement for a product in the same category still passes, because it is relevant.
  This filter removes junk, not advertising.

Consequences:

- `ranking.relevance()` is the filter; `agent.py` applies it to every new search and
  re-search. `amazon.py` remains a pure extractor and knows nothing about queries.

---

## ADR-054 — Cheapest Per Item Is the Default Ordering

Status: Accepted and implemented; refines ADR-044

Decision:

When the user states no preference, results are ordered by price ascending — per item
where every candidate states a pack size, total price otherwise. An explicit request
for rating or delivery speed still wins.

Context:

ADR-044 made an unstated preference mean "Amazon's own order". Amazon's order leads
with placements, so the first numbered option was regularly the least useful line on
the page.

Reasoning:

- Once results are relevance-filtered, price is what a shopper is actually comparing.
- A predictable ordering makes the numbers mean something across searches.

Tradeoffs:

- The most relevant product may not be option 1. Mitigated by relevance filtering
  running first, so everything in the list is at least on-topic.

Consequences:

- `ranking.default_sort()` maps an unstated preference to price; `requested_sort()` is
  unchanged, so the difference between "the user asked" and "we chose" stays visible.

---

## ADR-055 — A Reference Is Short; a Description Is a Search

Status: Accepted and implemented; refines ADR-042

Decision:

Candidate resolution by description applies only to messages of three significant words
or fewer, and never when every candidate matches equally well. Longer messages are
searched on Amazon.

Context:

Three UAT failures came from one line: any message arriving with stored candidates was
matched against them first. With five results all titled "Oral-B" (ADR-006 title bug),
"oral b branded toothpaste 4 pack" tied across all five and produced "More than one
option matches that description. Which option do you mean?" — a question with no
sensible answer. Worse, "oral-B toothbrushes 6 pack" matched one stale result on four
words and was **added to the list without Amazon ever being queried for it**.

Reasoning:

- A reference points at something visible and is therefore short: "the duracell",
  "natrol gummies". Naming a product in full is a request, not a pointer.
- Every candidate matching equally is evidence the words discriminate nothing, which is
  the opposite of an ambiguous reference. Treating it as ambiguity produced a question
  the user could not answer.
- Searching is the safe failure direction: it is visible, reversible, and cheap.
  Silently adding the wrong item to a list that later writes to a real cart is not.

Tradeoffs:

- A long, deliberate reference now becomes a search. The user sees results rather than
  an addition, which is recoverable in one turn.

Consequences:

- Explicit positions, ordinals, and comparisons ("cheapest") are unaffected and remain
  the unambiguous way to pick.

---

## ADR-056 — A Menu Outlives the Turn That Printed It

Status: Accepted and implemented; refines ADR-052

Decision:

Choosing "Search for something else" or "Narrow these results" keeps the current
results menu live. Menus without product choices — cart, checkout — are still cleared.
No reply that asks the user to choose is sent without a menu attached.

Context:

ADR-052 guarantees that the number shown is the number resolved. Clearing the menu
broke it: the results were still visible in the chat, so `3` still looked valid, but
the agent had forgotten the menu and answered "Which product should I search for on
Amazon?". Separately, "More than one option matches that description" was sent as a
bare sentence with nothing to pick from.

Reasoning:

- The user's screen, not the agent's state, decides what looks selectable.
- A cart menu must still be dropped: reusing "1 · Check out" after moving on would act
  on an intent the user has left behind.

Consequences:

- `agent._keep_only_product_menu()` retains a menu only when it offers products, and
  `agent._with_menu()` attaches the pending menu to every choose-one question.

---

## ADR-057 — The Real Amazon Cart Is Reconciled Against the List at Confirmation

Status: Accepted and implemented

Decision:

After the approved list is pushed to the real Amazon cart, the cart is read back and
anything in it that this conversation did not add is named in the reply. A failed read
is reported to the log and never breaks the confirmation.

Context:

A confirmed summary read "2 item(s), items subtotal $22.95" while the real Amazon cart
held a $1.98 pack of coffee filters left from an earlier session and one of the two
items had silently failed to add. The summary described the agent's list; the order the
user would place is the whole cart. Those are different things and nothing said so.

Reasoning:

- ADR-026 requires the user to review what they are approving. Reviewing a list that is
  not the thing that would be bought does not satisfy that.
- This is tolerable only while ordering is refused (ADR-049). It has to exist *before*
  ordering is implemented, not alongside it.
- `amazon.read_cart()` already existed and was unused.

Tradeoffs:

- One extra page read per confirmation.
- Items the user deliberately keeps in their cart are reported every time.

Consequences:

- The ADR-046 expiry question and ISSUE-017 (removal not reaching the real cart) are
  now the remaining gaps between the agent's list and the real cart.

---

## ADR-058 — A Variation Listing Is Resolved to a Child ASIN Before Anything Is Added

Status: Accepted and implemented

Decision:

When a chosen search result is a variation parent, the agent reads its children and
asks which one, as a numbered menu. What reaches the cart is always the child ASIN the
user picked, priced from that child's own product page.

Context:

A search result for a variation parent has no fixed identity: scent, size, and pack are
chosen on the product page, and the page can raise the quantity further. The agent
stored the parent ASIN, `add_many_to_cart` correctly refused it ("may need a size or
colour chosen first"), and the user saw an item silently fail to add. Safe, and useless.

Separately, such a result displays no pack count, so the user compared a price against
an unknown quantity.

Reasoning:

- Amazon ships the whole variation map inline as
  `"dimensionValuesDisplayData":{"<ASIN>":["Swagger","3.8 Ounce (Pack of 3)"],...}`.
  Reading it is exact; clicking swatches to discover children would be guesswork.
  Live-verified against four listings, which reported 2-4 real children each.
- Resolving before adding turns a silent failure into a choice, and makes the thing
  that arrives the thing that was picked.
- The price is read from the child's page rather than inherited from the parent,
  because a parent's "from $9.40" is not the price of the pack of six.

Tradeoffs:

- One extra page read per selection (~2s) and one extra turn, but only for listings
  that genuinely have a choice to make: a single-variant listing is added directly.
- A failed variant read falls back to adding what was picked, so a lookup problem
  never blocks a selection.

Consequences:

- Every result line now states its pack count, or says "count not stated" when Amazon
  did not, which is the cue that the product page will ask.
- `ranking.pack_count()` still reads a count from a title; nothing reads one from the
  query (ISSUE-034, closed WONTFIX at the user's direction).

---

## ADR-059 — Checkout Writes the Cart in One Step; the Order Screen Is a Labelled Mock

Status: Accepted and implemented; refines ADR-026 and ADR-049

Decision:

"Check out" shows the summary and writes to the real Amazon cart in the same step.
A "Place the order" option opens a screen shaped like Amazon's confirmation page whose
first line reads `DEMO SCREEN — NO ORDER WAS PLACED`. Placing clears the list.

Context:

The user reviewed a list, chose "Check out", and was then asked to approve the same
list again. The second gate reviewed nothing new. Separately, the "ordering is not
implemented" copy appeared on every terminal screen and was asked to be removed.

Reasoning:

- The list *is* the review. A second approval of unchanged contents is friction, not
  a control, and the cart is not an order — everything in it stays removable.
- A visible destination screen makes the finished flow reviewable before any of it is
  real. Shaping it like the real thing is the point; that is exactly why the banner
  has to be the first thing read.
- The ordering function in `checkout.py` is still called by nothing. The invariant
  test was rewritten to assert no *call* rather than no mention, because the menu now
  legitimately carries a `PLACE_ORDER` action.

Tradeoffs:

- An item reaches the real Amazon cart one tap earlier than before. This is a genuine
  loosening of ADR-026's two-step gate, recorded deliberately rather than silently.
- A screen that looks like a confirmation could mislead if the banner were ever
  removed. It must not be.

Consequences:

- Both terminal screens are built from the whole Amazon cart, not the agent's list:
  the order a user would place is every line in the cart, and showing only the agent's
  two items understated a six-item cart.
- Checking out twice is idempotent; a regression test asserts one cart write.

---

## ADR-060 — A Stored Menu Never Makes a Workflow Unreadable

Status: Accepted and implemented; extends ADR-041

Decision:

`MenuOption.from_record()` returns `None` for an action the current code does not
define, and those entries are dropped when a workflow is restored.

Context:

ADR-041 made workflow *fields* tolerant, but menu actions stayed strict:
`MenuAction(record["action"])` raises `ValueError` on an unknown value. That happens
inside `get_active_workflow()`, which runs on the first line of every message, so
retiring any menu action would have broken every incoming message for any user
holding a saved workflow. This session retired two actions, which is what surfaced it.

Consequences:

- Menu actions can now be renamed or removed without a migration.
- The dropped option is simply absent; the menu is rebuilt on the next reply.

---

## ADR-061 — Ordering Is Implemented, Behind a Kill Switch, a Ceiling, and an Audit Log

Status: Accepted and implemented; supersedes ADR-049 and completes ADR-010 stage 6

Decision:

`amazon.place_order()` submits a real Amazon order. It is the only function that knows
how to do so, and it refuses unless every one of these holds:

- `AMAZON_ENABLE_ORDERING` is exactly `true`.
- The cart total is readable and at or under `AMAZON_MAX_ORDER_TOTAL`.
- The order total Amazon states on its own review page is also at or under that
  ceiling — checked separately, because a total that grew between the two reads is a
  reason to stop, not to pay.

Every attempt is appended to an audit log. `checkout.place_order()` and
`OrderPlacementDisabled` are removed: ordering exists now, so a function whose only
job was to refuse is no longer the truth.

Context:

ADR-049 refused ordering because it was not implemented and a false confirmation was
the worst thing the agent could produce. The user has since asked for real ordering,
which is ADR-010's stage 6 and the project's stated goal. `AGENTS.md` requires price
limits, audit logs, and confirmation rules before any financial action, so those are
built with it rather than after it.

Reasoning:

- The ready-to-order screen already shows the whole cart, the total, the address and
  the card. That is ADR-026's reviewable pause, so the gate is satisfied by connecting
  it to a real action rather than by adding another approval.
- A kill switch that defaults to off means the dangerous path cannot be reached by
  accident — by a stale `.env`, a copied config, or a test.
- Checking the ceiling twice matters because the two numbers come from different pages
  at different times. Trusting the first would let a shipping charge or a price change
  push an order past a limit the user set.
- Amazon requires a fresh sign-in before checkout (`max_auth_age=900`, verified live).
  This application never authenticates, so that is surfaced as the user's step. It is
  not a bug to route around; it is Amazon's control and it stays.

Tradeoffs:

- The agent can now spend money. That is the point, and it is why the controls above
  are structural rather than advisory.
- Ordering cannot succeed unless the user has signed in to Amazon recently, so the
  sign-in failure is the common path rather than an edge case.

Consequences:

- A test asserts that only `amazon.py` contains the checkout selectors, so ordering
  cannot spread across modules.
- The fuzz suite asserts the kill switch is the only thing between a menu tap and a
  purchase, and that it reads strictly.

---

## ADR-062 — A Failed Order Changes Nothing; a Placed Order Clears Everything

Status: Accepted and implemented

Decision:

The two outcomes are deliberately asymmetric. A placed order clears the list and
offers one choice: start shopping again. A failed order transitions nothing, clears
nothing, and offers view / add / remove / start-over — with the last labelled as the
only thing that clears the list.

Reasoning:

- Items that were bought must leave the list, or the next session invites ordering
  them a second time.
- Items that were *not* bought must stay, or a declined card costs the user the whole
  basket they had assembled. Recovery should be fixing the card, not rebuilding the
  list.
- The failure headline names the cause — declined, sign-in required, or unknown —
  because "something went wrong" gives the user nothing to act on.
- An unconfirmed outcome is reported as unknown, never as success. Amazon not showing
  a confirmation page is not the same as an order failing, and the user is told to
  check their orders rather than being told either story.

---

## ADR-063 — Ordering Drives a Visible Browser Through a Mapped Checkout Pipeline

Status: Accepted and implemented; corrects the conclusion recorded in ADR-061

Decision:

`place_order()` runs a **visible** browser and walks Amazon's checkout as a bounded
state machine rather than a fixed sequence: on each pass, click the order control if
it is present and enabled, otherwise decline an upsell, otherwise advance one step,
otherwise stop. Capped at six steps.

Context — the headless finding was wrong:

ADR-061 recorded that Amazon requires re-authentication before checkout
(`max_auth_age=900`) and treated that as an inherent ceiling on unattended ordering.
That conclusion came from probes that were **all headless**, and it was wrong. The
same profile, the same cookies and the same session reach
`Place Your Order - Amazon Checkout` cleanly in a visible browser. The user pushed
back on the conclusion; testing the one variable I had not varied disproved it.

What the live mapping found:

- **Amazon buttons are a `<span>` label with an `<input type="submit">` laid over
  them.** Clicking the label times out with "input intercepts pointer events", which
  is why the first selectors matched nothing.
- **A Prime free-trial offer is injected mid-checkout for non-members.** Its prominent
  button enrols the user in a paid subscription. Only the "No thanks" control may be
  clicked, and `NEVER_CLICK` refuses the rest.
- **The pipeline's shape varies.** With an unverified card there is a payment step;
  with a working default card, checkout goes straight to the review page. A fixed
  sequence would have broken on both.
- **The review page renders several inputs sharing `id="placeOrder"`,** one of them a
  disabled twin (`SPC_animatedDisabledPlaceOrderTop`). Matching the id and taking the
  first hit can resolve to a control that submits nothing while appearing to work, so
  being enabled is part of what makes a match a match.

Reasoning:

- A visible browser is a real browser with a genuinely authenticated session. Nothing
  about it spoofs a fingerprint or defeats a bot check; it is the display mode that
  ADR-047 chose for convenience, and the choice turned out to have a functional
  consequence at checkout.
- A state machine survives Amazon adding, removing or reordering steps. A recorded
  click sequence would not.
- Refusing to click an unknown control is the correct failure: the reply says the
  order was not submitted, which is true and checkable.

Consequences:

- Card verification, an expired session, a paid-offer trap, a stalled step and a
  missing control are each reported distinctly and logged distinctly.
- The agent still never types a card number or a password. Both walls are handed back
  to the user with what to do about them.

---

## ADR-064 — Tests Are Hermetic Against the Developer's `.env`

Status: Accepted and implemented

Decision:

`conftest.py` forces `AMAZON_ENABLE_ORDERING=false` and a default price ceiling for
every test, regardless of what the real `.env` contains.

Context:

`load_dotenv()` loads the developer's real `.env` into the test process. Switching
ordering on for live testing therefore switched it on inside pytest too, and twenty
tests changed behaviour. Nothing placed an order — the `async_playwright` blocker
holds — but a suite whose behaviour depends on a config flag is one missing mock away
from being able to spend money.

Consequences:

- The browser blocker is no longer the only thing standing between the test suite and
  a real order; the kill switch is independently forced off.

---

## ADR-065 — Only Prime-Eligible Products Are Ever Suggested

Status: Accepted and implemented

Decision:

Every product the agent offers must carry Amazon's Prime badge. Non-eligible results
are dropped from search and from re-search before ranking. If nothing eligible is
found, the agent says so and suggests nothing rather than offering a product that
cannot ship free.

Two exceptions, both deliberate: items already sitting in the real Amazon cart are
reported exactly as they are, because hiding one would misrepresent what an order
would buy; and a future re-buy workflow will need the same latitude for something the
user has already chosen.

Reasoning:

- A product that cannot ship free is not worth the user's attention, so this belongs
  in code as a rule rather than as a preference they restate every search.
- Eligibility is never inferred. `prime_eligible` is True only where the result card
  carried the badge; no badge means dropped, not guessed.

Related fix — the badge is about the product, not the account:

`_result_metadata_from_html()` previously cleared the badge whenever a "Join Prime"
upsell appeared on the same card. Those are different facts: the badge says *this
listing ships under Prime*, the upsell says *this account is not a member*. Conflating
them was cosmetic while Prime was only displayed, and silently drops genuinely
eligible results the moment it becomes a filter.

---

## ADR-066 — Delivery Is Always the Free Option That Arrives Soonest

Status: Accepted and implemented

Decision:

When Amazon's checkout offers a choice of delivery speed, the agent selects the
fastest option whose own label says FREE and states no price. If no label can be read,
nothing is touched and Amazon's default stands.

Reasoning:

- Amazon's preselected speed is not always the free one, so accepting the default
  could spend money on shipping that the user never chose and never saw.
- Only the option's own text is trusted: an option is eligible when its label says
  "FREE" and contains no price, and "soonest" is decided by the date it states.
- Doing nothing is the correct fallback. A guess between delivery options is a guess
  about a charge.

---

## ADR-067 — Removing From the List Removes From the Amazon Cart

Status: Accepted and implemented; closes ISSUE-017

Decision:

Removing an item from the agent's list also removes it from the real Amazon cart when
checkout has already put it there. A failed removal is reported and the item is not
shown as gone.

Context:

Checkout writes the list into the real cart (ADR-059). Removal only ever touched the
agent's own copy, so an item the user had just deleted stayed in the Amazon cart and
would still have been bought. The list and the cart have to mean the same thing once
checkout has run.
