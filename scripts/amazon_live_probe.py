"""Read-only Amazon profile probe that emits only public extracted product fields."""

import argparse
import asyncio
import json

import amazon


async def main(query: str) -> None:
    try:
        async with amazon._persistent_browser_context() as context:
            products = await amazon._search_in_context(context, query)
            print(
                json.dumps(
                    {
                        "query": query,
                        "count": len(products),
                        "products": [
                            {
                                "title": product.title,
                                "price": product.price,
                                "url_is_amazon": product.url.startswith(
                                    "https://www.amazon.com/"
                                ),
                                "rating": product.rating,
                                "review_count": product.review_count,
                                "prime_eligible": product.prime_eligible,
                            }
                            for product in products
                        ],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
    except amazon.AmazonSearchUnavailable as error:
        print(json.dumps({"query": query, "error": str(error)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    arguments = parser.parse_args()
    asyncio.run(main(arguments.query))
