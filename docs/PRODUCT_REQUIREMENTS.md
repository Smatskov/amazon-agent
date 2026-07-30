# Product Requirements

## Purpose and mission

This repository builds a personal conversational purchasing agent: **Telegram message → conversational agent → Amazon product discovery or repurchase lookup → product selection → cart and checkout preparation → exact final summary → explicit user confirmation → order placement → Telegram confirmation**. This is the intended product, not a claim that all stages are implemented today.

Users communicate naturally rather than through rigid commands. The agent must handle new and repeat purchases, vague requests, refinements, corrections, comparisons, questions, option selections, quantity/address changes, denials, cancellation, confirmation, unrelated side questions, and references to earlier messages or presented products. Examples are regression cases, not a closed phrase list.

## Intended purchase flow

1. Interpret the request and current conversation.
2. Check local verified purchase history and Amazon order history when appropriate.
3. Offer a credible prior item for repurchase confirmation, otherwise search Amazon.
4. Apply hard constraints and rank valid candidates.
5. Present up to three meaningful options, answer questions, and accept refinements.
6. Select a product, prepare cart and checkout, and read exact checkout facts.
7. Send the authenticated user an exact final summary and require explicit confirmation.
8. Place the order only after valid confirmation, verify success, and then record completed purchase history.

## Product selection policy

Selection has two deterministic stages.

### Stage 1: hard filtering

Reject a product that has a wrong category/model/compatibility/size/variation/pack count; is unavailable; exceeds a hard budget; lacks mandatory Prime; arrives too late; has a prohibited seller or excluded brand; is subscription-only when one-time purchase is required; duplicates an ASIN or variation; or has insufficient facts for a safe comparison.

### Stage 2: inspectable ranking

Rank remaining products using request relevance, constraint satisfaction, Prime and delivery speed, total and unit price, rating and review-count confidence, seller and ships-from reliability, availability, variation accuracy, verified history similarity, explicit preferences, value, returnability, compatibility confidence, and penalties for sponsored placement, missing data, subscriptions, and duplicates. Category profiles may adjust weights: consumables emphasize unit price and repeat fit; electronics compatibility/returnability; replacement parts model fit; clothing size/variation/returns; home goods dimensions/value; health-related consumer products compatibility and safety information.

Comparative terms mean: **cheapest** lowest valid total/unit price; **best value** strongest relevant value score; **fastest** earliest verified delivery; **highest rated** strongest rating with review confidence; **most popular** strongest review-volume evidence; **safest choice** strongest compatibility/reliability facts; **best overall** strongest weighted score; **same as last time/usual** verified order history, never inferred preference alone.

Present fewer than three if fewer trustworthy candidates exist. When possible, candidates should expose real tradeoffs such as best overall, best value, and fastest delivery; diversity must never force an invalid choice.

## Safety and modes

Preview mode is the default. Live mode is permitted only through the confirmation gate.

No product may be ordered without a Telegram confirmation request containing exact final checkout facts and a subsequent explicit confirmation from the authenticated user. Any change to product, variation, quantity, seller, price, shipping, tax, total, destination, shipping method, delivery estimate, or subscription status invalidates prior confirmation.

The system must safely clarify ambiguous requests; report model timeout/invalid output; handle login, MFA, CAPTCHA, no candidates/no Prime, unavailability, changed price/delivery/cart/checkout, expired confirmation, duplicate or uncertain submission; and record a purchase only after verified order success.
