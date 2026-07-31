# Amazon AI Purchasing Agent

A personal, local-first purchasing assistant controlled through Telegram. You send a message,
it searches Amazon, and you pick from numbered options until your list is ready.

**Status: preview.** Shopping is entirely deterministic — no language model takes part, and
none can write anything you see (ADR-051). It searches, ranks, presents numbered results,
answers questions from stored facts, builds a list, and prepares an order summary.

**Confirming puts your list into your real Amazon cart.** That is the one action that changes
anything in your account. **It stops there: no code path can place an order** — you complete
the purchase on Amazon yourself. See `Handoff Files/PROJECT_STATE.md` for the current
checkpoint and `Handoff Files/OPEN_ISSUES.md` for known gaps.

## How it works

Every reply ends with numbered choices, so you mostly answer with a number:

```
🔎 Results for iphone 17 charger
1 · iPhone 17 Charger Fast Charging Type C
    $9.99 · arrives Mon, Aug 3
...
Or:
6 · Narrow these results
7 · Search for something else
8 · Start over
```

Anything the agent doesn't recognise as a choice, a command, or a question about your list
is sent to Amazon as a search — Amazon's own search understands ordinary phrasing, so
"alright, i need a new iphone 17 charger" works as typed.

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
`~/Library/Application Support/Amazon Agent`), `AMAZON_BROWSER_HEADLESS` (defaults to `true`,
so searches run in the background with no window; set `false` to watch while debugging).

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

```

## Safety

Purchasing is staged: search → list → checkout summary → explicit confirmation → **ordering
is not implemented**. Confirmation writes to your real Amazon cart; nothing submits an order.

Four things enforce that, rather than one:

- `amazon.py` exposes search and cart add/remove only. There is no order function to call, and
  `_refuse_ordering_url()` blocks navigation to any checkout URL.
- Cart writes click `#add-to-cart-button` by exact id, so the control can never resolve to
  "Buy Now", which sits beside it on the product page. Success is confirmed by reading
  Amazon's own cart badge rather than assumed.
- `checkout.place_order()` exists only to raise, and tests assert nothing calls it.
- **No language model can write to you**, so none can claim an order was placed. A test asserts
  no module outside `intent_classifier` even mentions `generate_response`.

Set `AMAZON_ENABLE_CART=false` to disable cart writes entirely.

Prices are copied from search results rather than recomputed, so a subtotal is the sum of what
Amazon showed — with shipping, tax and delivery reported as unknown rather than estimated.
