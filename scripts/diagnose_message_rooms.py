"""Read Karlancer message rooms without sending messages or proposals."""

import asyncio
import json
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from karlancer_agent.browser.session import BrowserSession
from karlancer_agent.config import Settings


async def main() -> None:
    settings = Settings()
    browser = BrowserSession(
        profile_dir=settings.browser_profile_dir,
        headless=settings.browser_headless,
        proxy=settings.browser_proxy,
    )
    await browser.start()
    try:
        async with browser.new_page() as page:
            await page.goto(
                "https://www.karlancer.com/panel/messages",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            await page.wait_for_timeout(5_000)
            room_count = min(await page.locator(".room-item").count(), 50)
            rooms: list[dict[str, object]] = []
            for index in range(room_count):
                await page.locator(".room-item").nth(index).click()
                await page.wait_for_timeout(1_000)
                soup = BeautifulSoup(await page.content(), "lxml")
                project_links = []
                for link in soup.select("#messageBox a[href*='/projects/']"):
                    href = str(link.get("href", ""))
                    if href:
                        project_links.append(urljoin("https://www.karlancer.com", href))
                incoming = soup.select("#messageBox [id^='message_'].flex-row-reverse")
                rooms.append(
                    {
                        "index": index,
                        "conversation_url": page.url,
                        "project_links": list(dict.fromkeys(project_links)),
                        "incoming_message_ids": [str(item.get("id", "")) for item in incoming],
                    }
                )
            print(json.dumps({"room_count": room_count, "rooms": rooms}, ensure_ascii=False))
    finally:
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
