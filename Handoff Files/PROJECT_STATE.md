# Amazon AI Purchasing Agent — Project State

Last updated: 2026-07-24

Status: Active development

Current milestone: The complete-response flow has a minimal, mocked pytest baseline. Amazon automation has not started.

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
    ↓
llm_client.py
    ↓
LM Studio OpenAI-compatible local API
    ↓
Qwen3.5-4B-MLX-4bit
```

LM Studio successfully loads the selected model. Its OpenAI-compatible local API runs at the configured `LLM_BASE_URL`. Telegram-to-local-model-to-Telegram inference has been verified end to end.

The OpenAI Python SDK is used only as a client for LM Studio's OpenAI-compatible local API. This project does not use it as an OpenAI cloud-model dependency.

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
- Delegates the model request to `generate_response()`.
- Logs underlying model/server errors to the terminal.
- Returns a friendly user-facing error message when the local model cannot be reached or fails.

### `src/llm_client.py` — language-model communication boundary

- Loads `LLM_BASE_URL` and `LLM_MODEL` from environment configuration instead of hardcoding them.
- Creates the existing `AsyncOpenAI` client with LM Studio as its base URL and a 60-second timeout.
- Sends a normal, non-streaming chat completion request.
- Returns stripped visible `message.content` only; reasoning fields are not shown to Telegram users.
- Raises a clear error for missing or whitespace-only visible model output.

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
│   └── llm_client.py
├── config/                    # Present; no tracked project files verified inside
├── data/                      # Present; no tracked project files verified inside
└── tests/
    └── test_complete_response_flow.py
```

`requirements-dev.txt` is the current development dependency manifest. No production dependency manifest was found (`requirements.txt`, `pyproject.toml`, `Pipfile`, `poetry.lock`, and `uv.lock` are absent).

### Planned but not implemented

- Persistent memory and preference storage.
- Amazon search, product evaluation, browser automation, and purchase execution.
- Configuration validation at startup.
- A production dependency manifest.
- Production reliability features such as health checks, structured logs, and automatic restart.

## 6. Installed Dependencies That Were Verified

The following packages were verified with `.venv/bin/python -m pip show` on 2026-07-24:

- `openai` 2.47.0 — OpenAI-compatible client used to call LM Studio locally.
- `python-telegram-bot` 22.8 — Telegram integration.
- `python-dotenv` 1.2.2 — loads local environment configuration.
- `pydantic` 2.13.4 — installed dependency; not yet used by the current source modules.
- `playwright` 1.61.0 — installed for future browser automation; not yet used by the current source modules.
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
3. `agent.py` requests one complete response through `llm_client.py`.
4. `llm_client.py` waits for the non-streaming LM Studio completion and returns visible content.
5. `main.py` replaces `Thinking…` with the final response.
6. If the completed response is longer than Telegram's 4,096-character text limit, `main.py` places the first section in the placeholder and sends the remaining complete sections as additional messages.

Streaming is not part of the current implementation. Temporary streaming diagnostics have been removed.

## 9. Verification Completed

- LM Studio loaded `Qwen3.5-4B-MLX-4bit` and served the configured local OpenAI-compatible API.
- The model identifier `qwen3.5-4b-mlx` was verified.
- Telegram-to-local-model-to-Telegram inference was verified end to end.
- The complete-response Telegram behavior was tested successfully through Telegram on 2026-07-24.
- The current source was inspected on 2026-07-24.
- `tests/test_complete_response_flow.py` was added and verified with five mocked tests on 2026-07-24. It covers visible completed content, empty/whitespace model output, the friendly agent error, and long completed-response sectioning.
- `.venv/bin/python -m pytest` passed: 5 passed in 0.47s on 2026-07-24.
- `.venv/bin/python -m py_compile src/main.py src/agent.py src/llm_client.py` passed on 2026-07-24.
- `PYTHONPATH=src .venv/bin/python -c 'import main, agent, llm_client'` passed on 2026-07-24.
- The virtual environment and installed package versions listed above were inspected on 2026-07-24.

## 10. Known Limitations and Risks

- The Mac, LM Studio, and loaded model must remain available for replies to work.
- 8 GB unified memory limits model size and may create pressure once Chromium/Playwright is introduced.
- The test baseline covers unit behavior only; it does not replace manual Telegram or LM Studio integration testing.
- `requirements-dev.txt` pins pytest only; production dependency installation is not yet reproducible from a manifest.
- Missing or malformed environment variables do not yet receive explicit startup validation.
- Telegram/network/API failures around placeholder edits are not yet given dedicated retry handling.
- The current model response path has no conversation memory, tool use, product search, or purchase capability.
- Any future purchasing workflow is financially consequential and requires explicit safety, confirmation, audit, and authorization design before implementation.

## 11. Current Roadmap

1. Add configuration validation and reproducible startup hardening.
2. Create a production dependency manifest after the startup configuration contract is defined.
3. Add a minimal memory/preferences design.
4. Build read-only Amazon search and recommendation capabilities.
5. Add confirmation, audit, price-limit, and duplicate-prevention safeguards.
6. Consider browser automation and purchase execution only after the earlier safety stages are verified.

Do not begin Amazon automation until the current milestone is documented and the next checkpoint is selected deliberately.

## 12. Immediate Next Checkpoint

Deliberately select and complete configuration validation and reproducible startup hardening before any Amazon work. The checkpoint should validate required environment-variable names without printing values, provide clear startup errors, and document the supported setup command.

After that checkpoint, update this document with the command used and the result before selecting the next milestone.

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
