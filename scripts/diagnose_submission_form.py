"""Read back Karlancer proposal fields without clicking the final submit button."""

import asyncio
import json

from karlancer_agent.browser.selectors import (
    BID_MODAL,
    CONTINUE_TO_BID_CONTROL,
    PROPOSAL_BODY_INPUT,
    PROPOSAL_BUDGET_INPUT,
    PROPOSAL_DURATION_INPUT,
    VISIBLE_SUBMIT_CONTROL,
)
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
            await page.wait_for_selector(VISIBLE_SUBMIT_CONTROL, state="visible", timeout=20_000)
            await page.locator(VISIBLE_SUBMIT_CONTROL).click()
            await page.wait_for_selector(BID_MODAL, state="visible", timeout=20_000)
            continue_control = page.locator(CONTINUE_TO_BID_CONTROL)
            if await continue_control.is_visible():
                await continue_control.click()

            expected = {
                PROPOSAL_BODY_INPUT: "diagnostic-line-1\ndiagnostic-line-2",
                PROPOSAL_BUDGET_INPUT: "7000000",
                PROPOSAL_DURATION_INPUT: "6",
            }
            observed = {}
            for selector, value in expected.items():
                locator = page.locator(selector)
                await locator.fill(value)
                observed[selector] = {
                    "expected": value,
                    "input_value": await locator.input_value(),
                    "value_property": await locator.evaluate("element => element.value"),
                }
            print(json.dumps(observed, ensure_ascii=False, sort_keys=True))
    finally:
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
