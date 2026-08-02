# Open Issues

> **UAT session 4 (2026-07-31) closed all five reported failures** by removing the
> language model from the shopping path entirely (ADR-051). They shared one cause: each
> landed on the `_general_response()` fallback, which let the model answer. That
> fallback no longer exists.


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


---

## UAT session 4 — 2026-07-31, the model answering instead of shopping

All five reported failures had one cause and one fix.

### ISSUE-018 — "is there anything in my cart?" answered "I don't have access" — **FIXED**
Answered from stored state by `state_answer.py`, instantly, and it says plainly that the
real Amazon cart is not being checked.

### ISSUE-019 — "i need a new iphone 17 charger" claimed no knowledge — **FIXED**
Searching is now the default for any unrecognised message. Verified live: that exact
sentence returns iPhone 17 chargers from $7.99.

### ISSUE-020 — "iphone 16 charger" claimed no results — **FIXED**
Same cause, same fix.

### ISSUE-021 — "bug spray" returned invented products — **FIXED, was the most serious**
The model produced "Garden Bug Spray: 16oz, organic formula" with markdown asterisks and
no prices. The prompt forbidding exactly that did not work. The model can no longer write
anything the user sees, so invention is structurally impossible rather than discouraged.

### ISSUE-022 — "the larger size" lost the context — **FIXED**
A short phrase pointing at what is on screen now re-shows the numbered menu and asks for
a number, instead of searching Amazon for those words.

### ISSUE-023 — Quantity can no longer be changed — **OPEN, introduced by this change**
`_change_quantity` was reachable only through the deleted semantic path. Adding more than
one of an item is currently not possible. Needs a "Change quantity" menu option.


---

## UAT session 5 — 2026-07-31, melatonin, Oral-B, and the shared Amazon cart

Fourteen observations were reported. They reduced to six causes. Findings marked
**live-verified** were confirmed with a read-only DOM probe against real Amazon
results, and in one case the probe reversed the intended fix.

### ISSUE-024 — An unrelated $99 placement was option 1 — **FIXED** (ADR-053)

**Seen:** `melatonin 10mg` → `1 · One Medical Membership: Get 24/7 on-demand care for
50+ conditions and more — $99.00`. Typing `1` would have listed a subscription.

**Root cause, live-verified:** the card carries a real ASIN, a real price, and a `/dp/`
link, so the extractor could not tell it from a product. **The probe also showed it
carries no sponsored marker of any kind, while the three genuine melatonin products
did** — marker-based ad filtering would have deleted the good results and kept the ad.

**Fix:** drop results sharing no significant word with the query; never empty the list.
**Live-verified after the fix:** `dropped as unrelated: 1`, One Medical gone.

### ISSUE-025 — Results were not ordered by price — **FIXED** (ADR-054)

Amazon's own order was passed through unchanged, which leads with placements. Now
cheapest-per-item first unless the user asks for rating or speed.
**Live-verified:** `oral b toothbrush 4 pack` now leads with `$6.42 ($1.60 ea)`.

### ISSUE-006 — Brand-only titles — **FIXED** (was OPEN since session 1)

**Seen:** five results all reading `Oral-B`, one of which the user reasonably concluded
the agent had invented. It had not — the products were real, the titles were not read.

**Root cause, live-verified:** on that layout `h2` holds only the brand and the anchor
text is empty, so taking the longer of the two still yields the brand. **The result
image's `alt` holds the complete name.**

**Fix:** `img.s-image[alt]` is now a first-class title source. Replacement characters
(`U+FFFD`) in titles are cleaned.
**Live-verified:** `Oral-B` → `Oral-B Complete Deep Clean Soft Bristles Toothbrush 4 Count`.

### ISSUE-026 — Narrowing by brand found nothing — **FIXED** (ADR-053, agent `_search_again`)

**Seen:** the agent offered "a brand" as a way to narrow; `natures bounty` replied
"Nothing I found matches that" and reprinted the same unfiltered list.

**Root cause:** three bugs. The filter did a raw substring test, so `natures` did not
match `nature's` or `nature`. The re-search re-ran the *original* query and re-applied
the same filter, so a brand outside the first five could never be found. The zero-match
reply reprinted the old list with nothing marking it stale.

**Fix:** token matching with possessive/plural folding and one-edit typo tolerance; the
narrowing is folded into the Amazon query (`natures bounty melatonin 10mg`); the
zero-match reply now says the results are the earlier ones, unchanged.

### ISSUE-027 — The answer to "What should I look for?" was rejected — **FIXED** (ADR-055)

**Seen:** the agent asked what to look for; the reply `oral b branded toothpaste 4 pack`
was answered with "More than one option matches that description."

**Root cause:** "Search for something else" cleared the *menu* but left
`workflow.candidates` populated, so the answer fell into the stale-candidate resolver.

### ISSUE-028 — A number stopped working while still on screen — **FIXED** (ADR-056)

**Seen:** `3` → "Which product should I search for on Amazon?" while numbered results
were still visible in the chat.

**Root cause:** the menu was cleared server-side while its numbers remained on the
user's screen, breaking ADR-052's core guarantee.

### ISSUE-029 — Naming a product silently added a stale result — **FIXED** (ADR-055)

**Seen:** `oral-B toothbrushes 6 pack` was intended as a search and instead added an
item to the list.

**Root cause:** it matched a previous result on four words. **Amazon was never queried.**
The product it picked happened to be sensible, which made the bug look like a feature.

### ISSUE-030 — "Which option do you mean?" arrived with no options — **FIXED** (ADR-056)

### ISSUE-031 — The remove menu showed no prices — **FIXED**

Dropping an item is a money decision; each line now shows its price.

### ISSUE-032 — The Amazon cart held an item the agent never added — **FIXED** (ADR-057)

**Seen:** confirmed summary read "2 item(s), items subtotal $22.95"; the real Amazon
cart showed a $1.98 pack of coffee filters from an earlier session and a subtotal of
$6.94. The agent's list and the real cart are different things and nothing said so.

**Why it matters:** harmless while ordering is refused (ADR-049). Once ordering exists,
confirming would buy the stranger. The reconciliation had to land first.

### ISSUE-033 — "No Add to Cart control on this page" for an addable item — **FIXED, live-confirmed**

**Seen:** the Oral-B 6-pack failed to add during a confirmation that otherwise worked.

**Root cause, live-verified — two mechanisms, and the second was the real one:**

1. The buybox is attached *after* `DOMContentLoaded`, so counting the button straight
   after navigation sees nothing.
2. **Product pages redirect to a variation URL after load** — `/dp/B00CC6XSSQ` becomes
   `/dp/B00CC6XSSQ?th=1`. A locator wait started before that navigation times out even
   though the button appears moments later. Three of four probed products redirected.
   A first fix that only waited on the locator still failed 3 of 4; polling after
   settling the page found the control on 4 of 4, in 1.5-2.5 seconds.

**Fix:** settle the page (`wait_for_load_state("load")`), then poll for the control,
re-checking the URL against the ordering-refusal list on every pass.

**Live-confirmed end to end** with the user's authorisation: `add_many_to_cart` on the
exact item that had failed returned `added=True`, the cart went 2 -> 3, `read_cart()`
showed the item, and `remove_from_cart()` returned it to 2. Nothing was left behind.

**Also confirmed by that run:** `read_cart()` correctly identifies a pre-existing item
(the coffee filters, ASIN B0C4Z6SKCS) as one the agent did not add (ISSUE-032), and
`remove_from_cart()` works — which is the capability ISSUE-017 needs.

### ISSUE-039 — A failed narrowing still displayed the products it rejected — **FIXED**

**Seen:** "Nothing matched" followed immediately by the five results that had just
failed the filter, which read as a successful narrowing and invited the user to pick
one of the excluded items.

**Fix:** nothing is shown. A menu offers to show the previous results, narrow
differently, search again, or start over.

### ISSUE-040 — A price range was parsed as a keyword — **FIXED**

**Seen:** `between 10 and 20 dollars` returned "nothing matched" when a $10.97 and a
$17.99 result were both on screen.

**Root cause:** `MAX_PRICE_PHRASE` matches only "under/below/less than/max". A range
matched nothing, so the leftover words "between" and "dollars" became a **keyword the
title had to contain** — which excluded every product. Separately, `under 10` reported
nothing matched while Amazon had six Dove body washes from $5.47, because the
re-search asked Amazon for the same unfiltered page.

**Fix:** ranges are parsed in every written form ("between 10 and 20 dollars",
"$10-$20", "from 10 to 20"), `min_price` is enforced alongside `max_price`, money words
can never become a keyword, and both bounds are sent to Amazon as `low-price`/
`high-price`. **Live-verified:** those parameters are honoured; `rh=p_36:...` and
`s=price-asc-rank` both returned "Sorry! Something went wrong!" and are not used.

### ISSUE-034 — A stated pack size is not enforced — **WONTFIX**

Closed at the user's direction: whatever Amazon returns for the query is acceptable,
whether or not it honours a stated count.

### ISSUE-041 — Repeated automated probing trips an Amazon interstitial — **OPEN**

During diagnosis, one product page returned `title: "Amazon.com"` with no buybox after
a burst of automated page loads; a later run with pauses returned the real page. Live
diagnosis should be spaced out. This is a testing hazard, not a user-facing bug.

### ISSUE-034 — A stated pack size is not enforced — **OPEN**

`oral b toothbrush 4 pack` still returns 6-packs among the 4-packs. Amazon returns
them and relevance filtering keeps them, because they are genuinely toothbrushes. Price
ordering now surfaces a 4-pack first, so the impact is reduced but not removed.
`ranking.pack_count()` already reads a pack size from a title; nothing reads one from
the *query*.

### ISSUE-035 — Auto-add cannot be justified from evidence — **OPEN, needs a decision**

The user asked that an item only be added without showing options when the agent has
found a specific reason to believe it (for example the product appearing in Amazon
order history), and that it say what that reason was. **No such evidence source exists
today:** order history is unverifiable (the test account has zero orders) and the agent
has no order history of its own because ordering is not implemented. Building the
justification without the evidence would mean the agent asserting a reason it does not
have, which is the failure ADR-051 exists to prevent. Until an evidence source exists,
the honest options are to keep command-mode's single recommendation (which the user
approves before it is added) or to never auto-add at all.

### ISSUE-036 — Shortened titles lost or mangled the identifying detail — **FIXED**

Once titles were extracted properly (ISSUE-006) they became long enough for the
shortener to matter, and two defects surfaced:

- `Dove Body Wash with Pump 3 Count Deep Moisture for…` — the cut landed on a function
  word, reading as a damaged name rather than a shortened one.
- `Dove Body Wash 2-Pack` — a pack count in the head returned early and dropped
  `15.2 Oz Ea`, the only thing separating it from a different 2-pack.

**Fix:** trimming never ends on a dangling word, and a size segment is kept even when
the head already names a pack.

### ISSUE-037 — Five Dove listings looked like the same product repeated — **FIXED by ISSUE-006**

**Seen:** five results all reading `Dove`, four at `$10.97`, indistinguishable.

**Root cause:** not duplicates. Live-verified as five distinct ASINs — Deep Moisture,
Sensitive Skin with Pump, 3 Count with Pump, 2-Pack, Antibacterial with Pump — that all
rendered as the brand. True duplicates are already removed by ASIN (ISSUE-024 work).

**Note for testing:** this transcript was produced by a bot process started before the
title fix landed. **The Telegram bot must be restarted to pick up source changes.**

### ISSUE-038 — "Narrow these results" spent a turn explaining itself — **FIXED**

The menu said only "Narrow these results"; what it accepted was revealed only after the
user chose it. The label now reads "Narrow these — by brand, budget, or keyword".

---

## UAT session 6 — 2026-08-01, variations, the final screens, and a cleanup pass

### ISSUE-043 — A variation listing could not be added — **FIXED** (ADR-058)

A search result for a variation parent (Old Spice deodorant: scent and pack chosen on
the product page) was stored as the parent ASIN. `add_many_to_cart` correctly refused
it, so the item silently failed to add. The agent now reads the children and asks
which one. **Live-verified**: four listings reported 2-4 real children each.

### ISSUE-044 — A result showed no pack count — **FIXED**

Every line now states the count, or "⚠️ count not stated" when Amazon did not — the
cue that the product page will ask, and that the price is not per unit.

### ISSUE-023 — Quantity could not be changed — **FIXED** (open since session 4)

"Change a quantity" is now a cart-menu option. Changing one invalidates a previous
confirmation (ADR-026).

### ISSUE-045 — Titles collapsed to a brand for sponsored listings — **FIXED**

`display_title` split on "|", which Amazon uses as its separator, leaving the brand as
the head and every descriptive segment too long to keep. Five results all read
"Dollar Shave Club, 2 Pack". A short leading segment now absorbs the next until the
name identifies the product.

### ISSUE-046 — Both final screens described the list, not the cart — **FIXED** (ADR-059)

"2 item(s), $35.23" while six items sat in the real Amazon cart. Both terminal screens
are now built from the whole cart, split into what this conversation added and what was
already there.

### ISSUE-047 — A placed order left the list populated — **FIXED**

The next screen still offered "View your list (2 items)" for items just ordered.

### ISSUE-048 — A stored menu could make a workflow unreadable — **FIXED** (ADR-060)

Found during the cleanup sweep, not user-reported. An unknown menu action raised inside
`get_active_workflow()`, which runs on the first line of every message.

### ISSUE-049 — Cart writes opened a browser before validating URLs — **FIXED**

Found during the cleanup sweep. A list made entirely of unusable URLs still launched
Chromium. URLs are now validated first, and a list with nothing writable opens nothing.

### ISSUE-050 — A corrupt quantity payload could raise — **FIXED**

Found during the cleanup sweep. `_set_quantity` did `int()` on an unvalidated stored
payload. A stored menu is untrusted input and must not be able to raise inside message
handling.

### ISSUE-051 — The shipping address cannot be changed from the agent — **WONTFIX (blocked)**

The default delivery location and the card's last four digits are read and displayed
read-only on both final screens. **Changing them is not possible here:** Amazon puts
`/a/addresses` behind a fresh sign-in even with a valid session (probe returned
`Amazon Sign-In`), and this application never authenticates or handles payment details.
Both are labelled as Amazon defaults, shown read-only, with a note to change them on
Amazon before ordering.

### ISSUE-052 — Amazon interstitials under rapid automated probing — **OPEN**

Unchanged from session 5. Space live diagnosis out; a burst of page loads returned one
bot-check page.

### ISSUE-058 — A live order stalled on the "Need anything else?" carousel — **FIXED**

**Seen (2026-08-02, first real order attempt):** the pipeline reached
`/checkout/byg/…?tangoIngressUrl=…`, titled **"Need anything else?"**, recognised
nothing on it, looped its six steps in ~20 seconds and returned
`REFUSED no-place-order-control`. Nothing was bought — the safe failure worked.

**Root cause:** an interstitial not previously seen. It is an add-on carousel
("Frequently bought with items in your cart", "Don't forget these essentials"), and
the way past it is a **"Continue to checkout"** button in the top-right corner. The
captured controls were all hidden add-to-cart forms for the suggested products, which
is why the earlier selector sweep found nothing usable.

**Fix:** `CHECKOUT_ADVANCE_TEXT` matches "Continue to checkout" / "Proceed to
checkout" / "Continue" / "No thanks" on the whole label, and the pipeline tries it
alongside the Prime decline.

**Second, larger risk found in the same screenshot:** that page is a wall of
**"Add to cart"** buttons, one per suggested product. An automation pressing the most
prominent-looking control would have added Ziploc bags or aluminium foil to the order
about to be placed. `NEVER_CLICK` now refuses `add to cart`, `add both to cart`,
`subscribe`, `buy now` and every Prime signup wording, and the advance label is matched
whole so "Continue to checkout" can never be confused with an "Add to cart" beside it.

### ISSUE-059 — Ordering always opens a visible browser window — **BY DESIGN, not yet optional**

Searches, cart reads and cart writes remain headless: no window appears during ordinary
use. **`place_order()` is different — it forces `headless=False` every time, including
in production.** That is not a leftover from testing: a headless browser is redirected
to `/ap/signin` at checkout while a visible one is not (ADR-063), so the window is
currently what makes ordering work at all. Only two code paths force a window:
`place_order()` and `open_profile_for_manual_sign_in()`.

Worth revisiting: whether a non-headless-but-offscreen mode, or a separate virtual
display, gives the same result without a window appearing on the user's desktop. Not
attempted yet.

### ISSUE-060 — A placed order was reported as "nothing was bought" — **FIXED**

**The worst failure this system can produce, and it happened on the first successful
order.**

**Seen (2026-08-02, order 112-3910624-2541021, $12.61):** the pipeline worked — it
cleared the add-on carousel, reached `/checkout/.../spc`, found the order control, and
clicked it (`PLACING subtotal=11.9 stated_total=12.61`). Amazon then showed
**"Verify payment"** at `/cpe/executions?...pageType=CPEFront` — a 3-D Secure step
from the card issuer. The code read "no confirmation page" as "no order" and told the
user:

> ❌ THE ORDER DID NOT GO THROUGH … Nothing was bought and nothing was charged.

The order had in fact been placed and shipped. Verified against order history:
`ORDER PLACED August 2, 2026 · TOTAL $12.61 · Arriving Tuesday`.

**Two independent defects:**

1. **A payment-authorisation page was treated as a terminal failure.** It is an
   intermediate step that resolves by itself. `_await_order_outcome()` now waits
   through it for up to 45 seconds, polling for a confirmation, a decline, or a real
   order number.
2. **An unknown outcome asserted a known one.** The failure screen's guidance said
   "Nothing was bought and nothing was charged" for *every* non-success, including the
   case where the outcome was explicitly undetermined. The user was told the opposite
   of the truth about their own money.

**Fixes:**

- `OrderResult.unknown` is a distinct outcome from a failure, with its own screen:
  "I COULD NOT TELL WHETHER THE ORDER WENT THROUGH … Do not assume it failed … check
  Your Orders before trying again — ordering a second time could buy the same thing
  twice." It never claims nothing was bought.
- Before reporting an unknown outcome at all, `find_recent_order()` reads Amazon's own
  order list and matches on the total. The order list is the last word on whether an
  order exists; whichever page the browser happened to be showing is not.

**Lesson for the build:** every other failure path in this system was written to fail
closed. This one failed *open* — it made a confident negative claim from an absence of
evidence. "I don't know" has to be a first-class outcome anywhere money is involved.
