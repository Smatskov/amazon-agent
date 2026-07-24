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
