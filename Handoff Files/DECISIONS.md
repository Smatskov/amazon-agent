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
