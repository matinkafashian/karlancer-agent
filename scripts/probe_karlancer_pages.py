"""Bounded read-only navigation probe for several Karlancer pages."""

import asyncio
import json
import time

from sqlalchemy import text

from karlancer_agent.browser.session import BrowserSession
from karlancer_agent.config import Settings
from karlancer_agent.db.session import create_engine


async def main() -> None:
    settings = Settings()
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(
                text(
                    "SELECT canonical_project_url FROM projects "
                    "WHERE canonical_project_url LIKE 'https://www.karlancer.com/projects/%' "
                    "ORDER BY created_at DESC LIMIT 3"
                )
            )
            project_urls = [str(row[0]) for row in rows]
    finally:
        await engine.dispose()

    urls = [
        "https://www.karlancer.com/",
        "https://www.karlancer.com/projects",
        "https://www.karlancer.com/dashboard",
        "https://www.karlancer.com/panel/messages",
        *project_urls,
    ]
    browser = BrowserSession(
        profile_dir=settings.browser_profile_dir,
        headless=True,
        proxy=settings.browser_proxy,
    )
    results: list[dict[str, object]] = []
    await browser.start()
    try:
        async with browser.new_page() as page:
            for round_number in (1, 2):
                for url in urls:
                    started = time.monotonic()
                    record: dict[str, object] = {"round": round_number, "url": url}
                    try:
                        response = await page.goto(url, wait_until="commit", timeout=25_000)
                        await page.wait_for_timeout(2_000)
                        record.update(
                            {
                                "ok": True,
                                "status": response.status if response else None,
                                "seconds": round(time.monotonic() - started, 2),
                                "final_url": page.url,
                                "title": await page.title(),
                                "rooms": await page.locator(".room-item").count(),
                            }
                        )
                    except Exception as error:
                        record.update(
                            {
                                "ok": False,
                                "seconds": round(time.monotonic() - started, 2),
                                "error": f"{type(error).__name__}: {str(error).splitlines()[0]}",
                                "final_url": page.url,
                            }
                        )
                    results.append(record)
                    print(json.dumps(record, ensure_ascii=False), flush=True)
    finally:
        await browser.close()
    passed = sum(bool(item["ok"]) for item in results)
    print(
        json.dumps(
            {"summary": {"passed": passed, "total": len(results), "failed": len(results) - passed}},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
