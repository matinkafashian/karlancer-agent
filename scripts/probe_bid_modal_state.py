"""Open a Karlancer bid dialog and report which modal appears, without submitting."""

import asyncio
import json
import sys

from karlancer_agent.browser.selectors import VISIBLE_SUBMIT_CONTROL
from karlancer_agent.browser.session import BrowserSession
from karlancer_agent.config import Settings


async def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: probe_bid_modal_state.py PROJECT_URL")

    settings = Settings()  # type: ignore[call-arg]
    browser = BrowserSession(
        profile_dir=settings.browser_profile_dir,
        headless=settings.browser_headless,
        proxy=settings.browser_proxy,
    )
    await browser.start()
    try:
        async with browser.new_page() as page:
            network = []
            page.on(
                "response",
                lambda r: (
                    network.append({"url": r.url.split("?")[0], "status": r.status})
                    if "/api/" in r.url or r.status >= 400
                    else None
                ),
            )
            page.on(
                "requestfailed",
                lambda r: network.append({"url": r.url.split("?")[0], "failure": r.failure}),
            )
            await page.goto(sys.argv[1], wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_selector(VISIBLE_SUBMIT_CONTROL, state="visible", timeout=30_000)
            # Karlancer replaces the server-rendered project card during Angular
            # hydration.  Wait for the stable client-rendered control.
            await page.wait_for_timeout(5_000)
            await page.wait_for_selector(VISIBLE_SUBMIT_CONTROL, state="visible", timeout=30_000)
            controls = page.locator(VISIBLE_SUBMIT_CONTROL)
            control_info = []
            for index in range(await controls.count()):
                item = controls.nth(index)
                control_info.append(
                    {
                        "index": index,
                        "html": (await item.evaluate("element => element.outerHTML"))[:800],
                        "box": await item.bounding_box(),
                    }
                )
            console_errors = []
            page.on(
                "console",
                lambda message: (
                    console_errors.append(message.text) if message.type == "error" else None
                ),
            )
            await controls.click()
            await page.wait_for_timeout(3_000)
            fallback_used = False
            if not await page.locator("#setBidModal").is_visible():
                trigger = page.locator('app-setbid-modal button[data-target="#setBidModal"]')
                if await trigger.count():
                    await page.locator(VISIBLE_SUBMIT_CONTROL).click()
                    fallback_used = True
                    await page.wait_for_timeout(5_000)

            async def modal_state(selector: str) -> dict[str, object]:
                modal = page.locator(selector)
                count = await modal.count()
                if count == 0:
                    return {"count": 0, "visible": False}
                return {
                    "count": count,
                    "visible": await modal.is_visible(),
                    "class": await modal.get_attribute("class"),
                    "aria_hidden": await modal.get_attribute("aria-hidden"),
                }

            print(
                json.dumps(
                    {
                        "url": page.url,
                        "controls": control_info,
                        "console_errors": console_errors[-10:],
                        "network": network[-20:],
                        "fallback_used": fallback_used,
                        "modal_text": await page.locator("#setBidModal").inner_text()
                        if await page.locator("#setBidModal").count()
                        else "",
                        "bid_modal": await modal_state("#setBidModal"),
                        "buy_bid_modal": await modal_state("#buyBidDialog"),
                        "alert_modal": await modal_state("#alertDialog"),
                        "body_preview": " ".join((await page.locator("body").inner_text()).split())[
                            :1_000
                        ],
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
