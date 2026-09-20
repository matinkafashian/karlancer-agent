"""Inspect one Karlancer project without changing the page or submitting a bid."""

import asyncio
import json

from bs4 import BeautifulSoup

from karlancer_agent.browser.selectors import PROJECT_ROOT, VISIBLE_SUBMIT_CONTROL
from karlancer_agent.browser.session import BrowserSession
from karlancer_agent.config import Settings

PROJECT_URL = (
    "https://www.karlancer.com/projects/"
    "%D8%B7%D8%B1%D8%A7%D8%AD%DB%8C-%D9%88-%D9%BE%DB%8C%D8%A7%D8%AF%D9%87%E2%80%8C"
    "%D8%B3%D8%A7%D8%B2%DB%8C-%D8%B1%D8%A8%D8%A7%D8%AA-self-%D8%A8%D8%B1%D8%A7%DB%8C-"
    "%D9%85%D8%AF%DB%8C%D8%B1%DB%8C%D8%AA-%D8%A7%DA%A9%D8%A7%D9%86%D8%AA-%D8%B4%D8%AE"
    "%D8%B5%DB%8C-2m6m2ew3r850"
)


async def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    browser = BrowserSession(
        profile_dir=settings.browser_profile_dir,
        headless=settings.browser_headless,
        proxy=settings.browser_proxy,
    )
    await browser.start()
    try:
        async with browser.new_page() as page:
            await page.goto(PROJECT_URL, wait_until="commit", timeout=60_000)
            await page.wait_for_selector(PROJECT_ROOT, state="visible", timeout=30_000)
            await page.wait_for_timeout(15_000)
            html = await page.content()
            await page.screenshot(path="/data/evidence/ip-access-check.png", full_page=True)
            text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
            markers = [
                marker
                for marker in ("پیشنهاد شما", "قبلاً پیشنهاد", "پیشنهاد ثبت شده")
                if marker in text
            ]
            result = {
                "url": page.url,
                "text_preview": text[:500],
                "project_root_count": await page.locator(PROJECT_ROOT).count(),
                "markers": markers,
                "visible_submit_controls": await page.locator(VISIBLE_SUBMIT_CONTROL).count(),
            }
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    finally:
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
