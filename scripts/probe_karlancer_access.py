"""Bounded read-only browser check with no AI calls or notifications."""

import asyncio
import json

from karlancer_agent.browser.session import BrowserSession
from karlancer_agent.config import Settings


async def main():
    settings = Settings()
    browser = BrowserSession(
        profile_dir=settings.browser_profile_dir,
        headless=True,
        proxy=settings.browser_proxy,
    )
    try:
        await browser.start()
        async with browser.new_page() as page:
            response = await page.goto(
                "https://www.karlancer.com/panel/messages", wait_until="domcontentloaded"
            )
            await page.wait_for_timeout(4000)
            body = await page.locator("body").inner_text()
            print(
                json.dumps(
                    {
                        "url": page.url,
                        "http_status": response.status if response else None,
                        "title": await page.title(),
                        "rooms": await page.locator(".room-item").count(),
                        "restriction_markers": [
                            word
                            for word in (
                                "حساب شما محدود",
                                "مسدود",
                                "دسترسی شما",
                                "ورود به حساب",
                                "کد امنیتی",
                            )
                            if word in body
                        ],
                        "page_excerpt": body[:650],
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
