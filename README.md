# Amazon AI Purchasing Agent

A personal, local-first purchasing assistant controlled through Telegram. A message goes to a
Telegram bot, a locally hosted language model interprets it, and deterministic Python code
executes memory and read-only Amazon work.

**Status: preview only.** The agent can remember explicit facts, search Amazon read-only, and
present product candidates. It cannot add to cart, check out, or place an order, and it is not
reliable enough for daily use yet. See `Handoff Files/PROJECT_STATE.md` for the current
implementation checkpoint and known issues.

## Documentation map

| File | Role |
| --- | --- |
| `AGENTS.md` | Stable engineering rules, boundaries, and safety constraints |
| `Handoff Files/PROJECT_STATE.md` | What exists now, what is verified, next checkpoint |
| `Handoff Files/DECISIONS.md` | Append-only architecture decision records |
| `docs/ARCHITECTURE.md` | Module boundaries and workflow state machine |
| `docs/PRODUCT_REQUIREMENTS.md` | Intended product behavior (largely future) |
| `docs/IMPLEMENTATION_PLAN.md` | Milestone roadmap |

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/playwright install chromium
cp .env.example .env   # then fill in the values
```

Required in `.env`: `TELEGRAM_BOT_TOKEN`, `AUTHORIZED_TELEGRAM_USER_ID`, `LLM_BASE_URL`,
`LLM_MODEL`. Optional: `AMAZON_BROWSER_PROFILE_DIR` (defaults to a directory under
`~/Library/Application Support/Amazon Agent`), `AMAZON_BROWSER_HEADLESS` (defaults to visible).

LM Studio must be running and serving the model named in `LLM_MODEL`.

## Running

```bash
PYTHONPATH=src .venv/bin/python src/main.py
```

Only the configured Telegram user ID is served; every other sender is ignored. Do not start a
second instance — Telegram rejects duplicate polling.

## Amazon browser profile

Amazon searches reuse a persistent Chromium profile stored **outside** the repository so a
manual sign-in survives between runs. Sign in once:

```bash
PYTHONPATH=src .venv/bin/python scripts/amazon_profile_login.py
```

The application never reads, copies, or logs profile contents, and never bypasses a CAPTCHA or
other protection. A repository-local profile path is rejected.

## Verification

```bash
.venv/bin/python -m pytest -q
```

Tests mock Telegram, LM Studio, and Amazon; they use temporary SQLite databases and never touch
`data/`. Passing tests do **not** prove live integration — see the verification taxonomy in
`Handoff Files/PROJECT_STATE.md`.

Live checks are deliberate and manual:

```bash
PYTHONPATH=src .venv/bin/python -u scripts/amazon_live_probe.py "AA batteries"
PYTHONPATH=src .venv/bin/python scripts/live_semantic_probe.py
```

## Safety

Purchasing is staged: search → recommend → cart → checkout preparation → explicit confirmation →
narrowly preapproved automation. Only the first two stages exist. No code path can place an
order, and the model never executes tools — it classifies and explains, while Python validates
and acts.
