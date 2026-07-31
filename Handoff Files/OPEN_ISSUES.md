# Open Issues

Running log from live UAT. Newest session at the top. Add findings here as you test;
each entry records what you saw, the root cause once diagnosed, and its status.

Status values: **OPEN** · **FIXED** (with commit) · **WONTFIX** (with reason)

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

### ISSUE-002 — A bare word "search" triggered developer usage text — **OPEN**

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
