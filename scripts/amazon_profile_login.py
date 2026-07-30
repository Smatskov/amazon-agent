"""Open the persistent Amazon browser profile for one manual sign-in session."""

import asyncio

import amazon


if __name__ == "__main__":
    asyncio.run(amazon.open_profile_for_manual_sign_in())
