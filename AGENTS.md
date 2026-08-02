# Project Mission

Build a production-quality personal AI purchasing agent controlled through Telegram. The eventual flow is:

`Telegram request → agent reasoning → memory/tools → Amazon workflow → confirmation through Telegram`

Introduce purchasing incrementally, with safeguards at every financially consequential step.

# Source of Truth

Before any implementation task, follow this order:

1. Read `Handoff Files/PROJECT_STATE.md`.
2. Read `Handoff Files/DECISIONS.md`.
3. Inspect the affected source files.
4. Only then begin implementation.

Never assume the project state without reading these files first. `PROJECT_STATE.md` is the current implementation checkpoint and verified state. `DECISIONS.md` is append-only architectural decision history. This file contains stable operating rules. Do not copy temporary project status into `AGENTS.md`.

# Permanent Constraints

- Use Python; Telegram is the user interface.
- Use LM Studio as the local model runtime on an Apple Silicon Mac with 8 GB unified memory.
- Keep the design local-first and near-zero recurring cost.
- Keep the model swappable behind an API boundary.
- Keep Amazon automation isolated from reasoning logic.
- The iPhone is a remote Telegram interface, not the compute host.
- Purchasing requires explicit safety and confirmation controls.

# Architecture Boundaries

- `src/main.py` owns Telegram behavior and application startup.
- `src/agent.py` owns orchestration and workflow decisions.
- `src/llm_client.py` owns model-server communication.
- Future memory modules own storage and retrieval.
- Future Amazon modules own browser and commerce behavior.

Modules may call each other through clear interfaces, but implementation details must not leak across boundaries. Do not move responsibilities or redesign architecture without explaining what changes, why, the problem solved, tradeoffs, and whether an earlier ADR is affected.

# Engineering Standards

- Inspect current code before editing; prefer small, incremental changes that preserve working behavior unless the task explicitly changes it.
- Prefer modular, testable code and explicit interfaces. Avoid giant scripts, premature abstraction, hidden global state, and unnecessary dependencies.
- Use environment variables for configuration and secrets. Never inspect, print, expose, document, or commit secret values from `.env`.
- Use structured, useful error handling. Keep comments concise and focused on boundaries or non-obvious logic.
- Keep local model, Telegram, memory, and Amazon components replaceable.

# Verification Rules

Distinguish file existence, syntax validation, import validation, automated test success, service startup, direct API verification, manual Telegram verification, and end-to-end behavior. Never claim a level that was not performed; mocked tests do not replace real integration verification.

Do not start LM Studio, Telegram polling, or browser automation unless required by the task, and avoid duplicate Telegram bot instances. Use the project virtual environment for commands. Report exact commands and summarized results.

# Testing Rules

- Use `pytest`; mock external systems for unit tests.
- Tests must not call Telegram, LM Studio, Amazon, or the public internet unless explicitly designated integration tests.
- SQLite tests must use temporary databases.
- Add regression coverage for bugs and keep test scope proportional to the change.

# Purchasing Safety

Do not implement unrestricted purchasing. Unless an ADR changes the order, build stages as follows:

1. Search
2. Recommend
3. Add to cart
4. Prepare checkout
5. Request explicit confirmation
6. Narrowly preapproved purchase automation

Before financial actions, require price limits, duplicate prevention, audit logs, authorization, and confirmation rules. Never store payment credentials, authentication secrets, or sensitive addresses casually in memory.

# Change Philosophy

- Prefer the smallest change that satisfies the requested checkpoint.
- Do not make broad architectural changes or improve/refactor unrelated code.
- Do not refactor working code unless requested or required.
- Preserve existing behavior unless the task intentionally changes it.
- Explain meaningful architectural changes in plain English: what changes, why, the problem solved, and where the pattern appears in real software engineering. Do not over-explain trivial commands or repetitive edits. When execution speed is requested, minimize explanation while still flagging major risks.

# Engineering Workflow

Follow this sequence: **Understand → Plan → Implement → Verify → Document**. Do not skip directly from reading a task to editing code.

# Scope Control

- If the task can be completed without modifying unrelated modules, do not modify them.
- Avoid opportunistic refactoring and “while I’m here” improvements.
- Stay tightly scoped to the requested checkpoint.
- Ask for broader scope only when the current task truly cannot be completed safely without it.

# Documentation Discipline

After significant verified changes, regenerate `Handoff Files/PROJECT_STATE.md` to match the exact repository state and preserve it as the current source of truth. Append ADRs to `Handoff Files/DECISIONS.md` only for durable architectural decisions; never rewrite or delete previous ADR history. Keep documented code, commands, files, dependencies, limitations, and verification results synchronized with the repository.

# Final Reporting Format

For implementation tasks, report:

1. Files inspected
2. Files changed
3. Behavior added or changed
4. Tests added or updated
5. Verification commands and results
6. Unresolved risks
7. Git status and diff summary
8. Single recommended next checkpoint
