"""Interactive one-time login entry point for the noVNC display."""

import asyncio
from contextlib import suppress

from karlancer_agent.browser.session import BrowserSession
from karlancer_agent.config import Settings


async def run_login() -> None:
    """Open Karlancer and keep the persistent context alive until interrupted."""

    settings = Settings()  # type: ignore[call-arg]  # Values are loaded from environment.
    session = BrowserSession(
        profile_dir=settings.browser_profile_dir,
        headless=False,
        proxy=settings.browser_proxy,
    )
    await session.start()
    try:
        async with session.new_page() as page:
            await page.goto("https://www.karlancer.com/login", wait_until="domcontentloaded")
            print("Karlancer login page is ready in the noVNC browser. Press Ctrl+C when done.")
            await asyncio.Event().wait()
    finally:
        await session.close()


def main() -> None:
    with suppress(KeyboardInterrupt):
        asyncio.run(run_login())


if __name__ == "__main__":
    main()
