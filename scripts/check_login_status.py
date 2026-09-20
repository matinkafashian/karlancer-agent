"""Print the authenticated Karlancer session state without changing it."""

import asyncio

from karlancer_agent.browser.session import BrowserSession
from karlancer_agent.config import Settings


async def main() -> None:
    settings = Settings()
    browser = BrowserSession(
        profile_dir=settings.browser_profile_dir,
        headless=True,
        proxy=settings.browser_proxy,
    )
    try:
        await browser.start()
        print((await browser.login_status()).value)
    finally:
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
