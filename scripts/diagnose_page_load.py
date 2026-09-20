"""Observe one navigation and application readiness, without posting anything."""

import asyncio
import json
from urllib.parse import urlsplit

from karlancer_agent.browser.session import BrowserSession
from karlancer_agent.config import Settings


async def main():
    settings = Settings()
    browser = BrowserSession(
        profile_dir=settings.browser_profile_dir,
        headless=True,
        proxy=settings.browser_proxy,
    )
    await browser.start()
    try:
        async with browser.new_page() as page:
            failed = []
            page.on(
                "requestfailed",
                lambda req: failed.append(
                    {
                        "host": urlsplit(req.url).hostname,
                        "type": req.resource_type,
                        "failure": req.failure,
                    }
                ),
            )
            response = await page.goto(
                "https://www.karlancer.com/panel/messages", wait_until="commit", timeout=20000
            )
            print(json.dumps({"document_status": response.status}, ensure_ascii=False), flush=True)
            try:
                await page.wait_for_selector(".room-item", timeout=20000)
            except Exception as error:
                print(type(error).__name__, flush=True)
            print(
                json.dumps(
                    {
                        "url": page.url,
                        "ready_state": await page.evaluate("document.readyState"),
                        "rooms": await page.locator(".room-item").count(),
                        "failed_requests": failed[:20],
                        "excerpt": (await page.locator("body").inner_text())[:400],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    finally:
        await browser.close()


asyncio.run(main())
