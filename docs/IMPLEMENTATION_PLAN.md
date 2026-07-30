# Implementation Plan

> **Status note.** This roadmap was written before live Amazon search existed. Milestone 1 is
> complete and its mocked candidates were replaced by real read-only Amazon results (ADR-038),
> so Milestone 3 landed early and partially. The current work is not a numbered milestone below:
> it is repairing Telegram purchase-workflow UX and conversational continuity on top of verified
> live search. `Handoff Files/PROJECT_STATE.md` is the authoritative current checkpoint.

## Milestone 1 — Conversational router and persistent workflow foundation (complete)

**Goal:** state-aware purchase routing with SQLite persistence. **In scope:** structured interpretation, one active workflow/user, candidate reference resolution, clarification/cancellation/refinement/quantity behavior, and tests. **Out of scope:** order history, cart, checkout, confirmation, and placement. **Dependencies:** current Telegram, SQLite, and LM Studio boundaries. **Acceptance:** purchase wording starts a workflow; memory/general chat remain intact; state survives restart; no live side effects. **Automated tests:** routing, persistence, state/candidate behavior, regression routes. **Manual verification:** Telegram preview flow. **Files:** router/workflow/candidate modules, tests, handoff/docs.

Delivered differently than planned: candidates are real Amazon records rather than mocks, because fabricated candidates produced misleading results (ADR-038).

## Milestone 2 — Constraints, filtering, ranking, candidate resolution (not started)

Implement hard filters and inspectable category-aware ranking. No browser/cart/checkout. Tests cover constraints, scores, diversity, and ambiguity. No deterministic ranking module exists today; natural-purchase candidates are shown in Amazon extraction order.

## Milestone 3 — Read-only Amazon search and product-detail adapter (search complete, details not started)

Implement typed, read-only facts behind `amazon.py`; validate selectors manually. No login/cart/checkout. Search-result extraction is live-verified against a user-managed signed-in profile (ADR-039). Product-detail pages, delivery estimates, and availability are still not extracted.

## Milestone 4 — Order history and local completed-purchase history

Add verified history lookup and independent purchase-history storage. No inference from a purchase to preference.

## Milestone 5 — Cart and checkout-preview adapter

Prepare cart and inspect checkout facts only. Stop before irreversible placement.

## Milestone 6 — Versioned confirmation gate and duplicate prevention

Require exact checkout summary, authenticated explicit confirmation, version invalidation, idempotency, audit records.

## Milestone 7 — Authenticated browser validation

Validate login/MFA/CAPTCHA and browser state manually under preview controls.

## Milestone 8 — Full Telegram preview-mode validation

Validate conversational flow end-to-end without placement.

## Milestone 9 — Carefully controlled live-mode validation

Enable narrowly authorized purchases only after all preceding acceptance criteria, confirmation, audit, and duplicate safeguards are verified.

## Milestone checklist

Every milestone uses the same delivery contract: define the goal and scope; name dependencies; list expected files; specify automated and manual verification; stop before the next milestone; and reject unrelated work. The abbreviated plan above expands as follows.

| Milestone | In scope / expected files | Acceptance and tests | Manual verification / stop condition |
| --- | --- | --- | --- |
| 1 | Router, workflow models/store, semantic interpreter, candidate resolver, typed future gateway, docs | Routing, persistence, cancellation, reference-resolution and regression tests | Preview Telegram flow only; no cart or checkout |
| 2 | Constraint/profile and ranking modules, evaluator tests | Deterministic filters/scores and no invalid candidates | Inspect score explanations; stop before browser work |
| 3 | Read-only Amazon adapter/detail normalization | Mocked adapter tests plus designated live selector check | Validate public read-only facts; stop before login/cart |
| 4 | Purchase-history store and read-only order lookup | Verified-event lifecycle and independent deletion tests | Validate history lookup; stop before cart |
| 5 | Cart/checkout-preview adapter | Exact checkout-fact/version tests | Preview a checkout; stop before Place Order |
| 6 | Confirmation/version/idempotency/audit modules | Invalidated-confirmation and duplicate tests | Test Telegram confirmation; stop before live mode |
| 7 | Authenticated browser validation | Login/MFA/CAPTCHA failure handling | Controlled browser validation only |
| 8 | Telegram preview integration | End-to-end preview regression suite | Full preview flow; no orders |
| 9 | Narrow live-mode policy | Authorization, price-limit, audit, and verified-order tests | Controlled live validation; stop on uncertainty |
