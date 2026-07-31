# Open Issues

Running log from live UAT. Newest session at the top. Add findings here as you test;
each entry records what you saw, the root cause once diagnosed, and its status.

Status values: **OPEN** · **FIXED** (with commit) · **WONTFIX** (with reason)

---

## DESIGN-001 — Deciding what a message is about — **IMPLEMENTED as a numbered menu**

Requested: a reliable way to tell a new search, a new purchase, an addition to the
cart, and a reply about what is already on screen apart from one another.

The failures in session 2 share one cause: **the agent decides what a message means
without weighing what it just said.** "i prefer the runner up" is only ambiguous if you
ignore that the previous turn presented a pick and a runner-up.

Proposed rule, evaluated in order and entirely deterministic:

1. **Reference to what is on screen.** If the previous turn presented options or a
   recommendation, and the message resolves against them — a number, a brand shown, a
   comparison, "runner up", "the other one" — it is a reply. This wins over everything
   else, because a reply arriving straight after a question is the most likely message
   in the conversation.
2. **Control words.** `cancel`, `reset`, `checkout`, `confirm`, buy phrasing. Already
   deterministic today.
3. **Refinement.** A constraint with no new product noun — "under $16", "only Prime",
   "cheaper" — applies to the current search.
4. **New product.** A message naming something not on screen. Split further:
   - *specific* (brand or model named) → add and confirm in one step;
   - *broad* (category only) → search and present options.
5. **Question.** Answerable from stored facts, workflow untouched.
6. **Anything else** → general conversation.

The local model is consulted only where the rule above is genuinely ambiguous, and its
answer is never allowed to override steps 1 or 2. Today the order is effectively
inverted: the model routes first and deterministic resolution is a fallback, which is
how "cheapest" and "the runner up" both got lost.

Open question for the user, carried over from the last session: **when a new product
request arrives while items are already listed, keep adding to the same list or start
a new one?** Currently it always keeps adding. Correct for "shampoo and body wash",
wrong after moving on to something unrelated.

---

## UAT session 2 — 2026-07-30, coffee filters and french press

Nothing below is implemented yet. Logged and awaiting the go-ahead.

### ISSUE-008 — The happy path takes too many turns — **OPEN**

**Seen:** `order coffee filters` → recommendation → `yes` → `checkout` → `confirm`.
Four turns to buy one cheap, obvious thing.

**Wanted:**

- **Brand named** ("order Melitta coffee filters") → add straight to the cart and show
  one confirmation. No option list, no separate checkout step.
- **Broad request** ("order coffee filters") → search and present a clean, scannable
  list of options.

**Root cause:** `request_mode.COMMAND` currently always produces a single
recommendation and then still routes through the full `checkout` → `confirm` gate. The
gate was designed when the end of the flow was a refusal; now that `confirm` writes to
the real Amazon cart, an extra approval step for a $1.98 item is friction with no
safety value.

**Plan:** add a third mode between browse and command — a *specific* request (brand or
model named) short-circuits to add-and-confirm. Collapse `checkout`+`confirm` into one
approval for single-item lists, keeping the two-step gate only when the list has
several items or exceeds a value threshold. The order refusal itself does not change.

### ISSUE-009 — Result formatting is dense and shows noise — **FIXED**

**Seen:**

```
Amazon Basics Basket Coffee Filters for 8-12 Cup Coffee Makers, White, Packaging May Vary, 200 Count
$1.98 — $0.01 each — 4.8/5 (24,037 reviews) — arrives Mon, Aug 3
```

**Problems:** review counts are noise once the user trusts the ranking; every fact is
crammed into one em-dash-separated run-on; the title still carries marketing filler
("Packaging May Vary").

**Plan:** drop review counts from display (keep them for ranking). Give each option a
short bolded name line and a compact facts line with price, unit price, and arrival
only. Trim residual filler phrases from titles. Keep the whole message no longer than
the current longest reply — a table is acceptable only if it does not add height.

### ISSUE-010 — Raw markdown is shown to the user — **FIXED**

**Seen:** `**Pick one:**` appears literally in Telegram.

**Root cause:** `main.py` sends messages with no `parse_mode`, so Telegram renders the
asterisks as text. The formatting was written as if it would render and never did.

**Plan:** send with HTML parse mode and escape every value that comes from Amazon or
the user, since product titles can contain characters that would otherwise break
parsing. Then bold is genuinely bold and no markup is visible. This also delivers the
bolded headers asked for in ISSUE-009.

### ISSUE-011 — "i prefer the runner up" started a new search — **FIXED**

**Seen:** after a recommendation with a named runner-up, replying `i prefer the runner
up` searched Amazon for "i prefer the runner up" and returned marathon t-shirts.

**Root cause:** three failures stacked.

1. The recommendation names a runner-up in the text but stores nothing about it. Only
   the top pick is written to `selected_candidate_id`, so "runner up" refers to
   something the agent did not persist.
2. `candidate_resolver` has no concept of "runner up", "the other one", or "the
   alternative", so resolution failed.
3. `_new_product_query()` then treated the leftover words as a product and searched.
   Its `NON_PRODUCT_WORDS` list does not contain `prefer`, `runner`, `up`, or
   `alternative`.

**Plan:** persist the runner-up alongside the pick; teach the resolver the
runner-up/other/alternative references; and make the new-search fallback far more
conservative — a reply arriving immediately after a recommendation should be resolved
against the presented options first, and only become a search when it clearly names a
product that is not on screen. Regression tests will cover the exact transcript.

### ISSUE-012 — Refining re-filters instead of searching again — **FIXED**

**Seen:** `under $16` narrowed the existing five results to two, one of which was
irrelevant, rather than finding five options under $16.

**Root cause:** `_refine_candidates()` deliberately re-filters results already
retrieved and never re-queries Amazon. That is right for "only the Prime ones" but
wrong for a budget, where the user wants a fresh set of options that satisfy it.

**Plan:** when a refinement adds a constraint that shrinks the list below a useful
number, re-run the Amazon search with the constraint applied and present a full set.
Keep in-place filtering when the current results still yield enough options.

---

## UAT session 1 — 2026-07-30, first end-to-end Telegram test

Transcript:

```
> i need a new iphone case
  Got it. Do you have a specific iPhone model in mind, or should I search for the latest one?
> search
  Search usage: search: <query>.
> I was asking you to go ahead and search for the iphone case
  I can search Amazon for you. Which product should I search for on Amazon?
> iphone case
  Amazon did not return usable search results right now. I have not started a purchase workflow; please try again later.
```

### ISSUE-001 — "iphone case" search failed completely — **FIXED**

**Seen:** "Amazon did not return usable search results right now."

**Root cause:** Two compounding faults, both confirmed live.

1. The product selector required `a.a-link-normal.s-line-clamp-3` or `h2 a`. Amazon
   serves **different result layouts per query**. On the phone-case layout the anchor
   *wraps* the `h2` and the clamp class is `s-line-clamp-2`, so the selector matched
   **zero** elements. "AA batteries" matched, which is why earlier testing passed.
2. `BROWSER_RESULTS_TIMEOUT_MS` was 6s and navigation used `wait_until="commit"`. The
   phone-case results page is ~1.5 MB and was not ready in that budget.

There was no CAPTCHA and no interstitial. Headless mode was not the cause.

**Fix:** Select on `div[data-asin]` cards plus any `/dp/` link, which is layout
independent; take the fuller of the heading text and the link text; raise the results
budget to 15s and navigate on `domcontentloaded`.

**Verified live** across `iphone case`, `AA batteries`, `head and shoulders shampoo`,
`organic body wash`, `jockey white t shirts medium` — 5 results each.

### ISSUE-002 — A bare word "search" triggered developer usage text — **FIXED**

**Seen:** typing `search` returned `Search usage: search: <query>.`

**Root cause:** `_search_query()` treats any message whose first token is `search` as
the colon alias, even with no colon, and returns the usage string. `_memory_instruction()`
has the identical fault: a bare `remember`, `recall` or `forget` returns memory usage
text. These colon forms are developer/debug affordances (ADR-027, ADR-033) and are
hijacking ordinary English.

**Fix planned:** require the colon to be present; otherwise treat the message as
ordinary language and let it route normally.

### ISSUE-003 — "i need a new iphone case" was not treated as a shopping request — **OPEN**

**Seen:** the agent replied conversationally and asked which iPhone model, instead of
searching.

**Root cause:** two layers both missed it.

1. The local model routed it to `general_chat`, so no purchase workflow started.
2. The deterministic safety net did not catch it either: `SHOPPING_FALLBACK_MARKERS`
   contains only `buy, find, search, shop, cheap, cheapest, best option(s), price(s),
   deal(s)`. The phrase "**i need** a new iphone case" contains none of them.

**Fix planned:** add intent verbs that express wanting rather than searching — `need`,
`want`, `looking for`, `get me`, `order`, `pick up`, `grab`, `restock`, `running low`,
`out of` — and treat a confident product noun phrase as a search rather than chat.

### ISSUE-004 — The agent asked what to search for when the message already said — **OPEN**

**Seen:** "go ahead and search for the iphone case" → "Which product should I search
for on Amazon?"

**Root cause:** the shopping-signal fallback asks for clarification without first
trying to extract a product from the message it already has. The stripping logic needed
here already exists (`REQUEST_PREFIX` / `_new_product_query` in `agent.py`) but is only
applied to replies *inside* an active workflow, not to the first request.

**Fix planned:** before asking, strip the request prefix and search if anything
substantive remains. Ask only when the message genuinely names no product.

### ISSUE-005 — Repeating an already-established intent restarts the conversation — **OPEN**

**Seen:** by turn 3 it was unambiguous that a search was wanted, yet the agent
re-announced "I can search Amazon for you."

**Root cause:** the clarification workflow stores the pending question but not the fact
that the user has already asked to search twice. Each unrouted shopping message produces
the same opening line.

**Fix planned:** if a clarification is already pending and another shopping-shaped
message arrives, treat that message as the answer rather than re-asking.

### ISSUE-006 — Some layouts yield a brand-only title — **OPEN**

**Seen (post-fix, live):** `head and shoulders shampoo` → title `Head & Shoulders`;
`jockey white t shirts medium` → title `Jockey`.

**Root cause:** on those layouts the `h2` holds only the brand and the anchor text is
empty, so the fuller-of-the-two rule still yields the brand.

**Impact:** the user cannot tell the options apart. Ranking and matching still work off
the same text, so accuracy scoring is degraded too.

**Fix planned:** fall back to the image `alt` text or the `aria-label` on the product
link, both of which carried the full title in DOM probes.

### ISSUE-007 — Price missing on the phone-case layout — **OPEN**

**Seen (post-fix, live):** `iphone case` results returned `price = None`.

**Root cause:** that layout shows "Click to see price" rather than an inline price for
some listings, and the offscreen price span is absent.

**Impact:** low. The subtotal correctly becomes unknown rather than wrong, and the item
is ranked last for price. But the user sees "price not shown" for common searches.

**Fix planned:** treat these as genuinely unpriced (current behaviour is honest), and
consider reading the price from the product page when a candidate is selected.

---

## How to add an issue

Record the transcript verbatim, then what you expected. Root cause can be filled in
later — an accurate observation is worth more than a guess at the cause.


---

## UAT session 3 — 2026-07-30, "employee" and the redesign

### ISSUE-013 — Typing a suggested brand added a random product — **FIXED**

**Seen:** the hint offered `a brand, like "Employee"` under *Narrow it*. Typing
`employee` selected a product and added it to the list.

**Root cause:** the same free text meant two different things. Brand words were routed
to the resolver, which selects; the hint promised they would filter. There was no way
for the agent to tell the two intents apart, because the user's words genuinely did not
distinguish them.

**Fix:** replaced by the numbered menu. Narrowing is now an explicit choice, and
choosing a product is a number. The same input can no longer mean two things.

### ISSUE-014 — The list was mistaken for search results — **FIXED**

**Seen:** after adding an item, the reply showed the list including coffee filters from
earlier in the conversation. The user read those as new suggestions.

**Root cause:** results and the list rendered identically. Nothing distinguished "what
Amazon returned" from "what you have chosen".

**Fix:** results are headed 🔎 *Results for X*; the list is headed 🧺 *Your list*, with
a subtotal and the not-in-your-Amazon-cart notice. A regression test asserts the two
never look alike.

### ISSUE-015 — Confirming twice pushed to the Amazon cart twice — **FIXED**

**Found while testing the redesign, not reported.** After confirming, the checkout menu
stayed pending, so replying "1" again re-ran the confirm and pushed the same items to
the real Amazon cart a second time. The menu is now replaced on confirm, and a test
asserts only one push happens.

### ISSUE-016 — First search after startup could time out — **FIXED**

**Seen live:** `order coffee filters` failed, then an immediate repeat succeeded.

**Root cause:** the first search pays browser cold-start cost and exceeded the budget.

**Fix:** one automatic retry before reporting failure. A retry only re-reads a public
results page, so it changes nothing. Verified live: "iphone case" failed attempt 1 and
succeeded on attempt 2 without the user seeing an error.

### ISSUE-017 — Removing an item only removes it locally — **OPEN**

**Not user-reported; found during the dead-code sweep.** If the user confirms (which
pushes to the real Amazon cart) and then removes an item from the list, Amazon still
has it. `amazon.remove_from_cart()` exists and is live-verified but nothing calls it.

**Plan:** after a confirmed push, removing an item should also remove it from the real
Amazon cart, or say plainly that it will not.
