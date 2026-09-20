"""Manual, bounded reply-only acceptance check while the scheduler is stopped.

Intentionally bypasses the global pause for this explicitly requested diagnostic.
Never invokes a model, drafts a proposal, or submits anything to Karlancer.
"""

import asyncio
import json

import httpx
from sqlalchemy import text

from karlancer_agent.browser.replies import KarlancerReplyReader
from karlancer_agent.browser.session import BrowserSession
from karlancer_agent.config import Settings
from karlancer_agent.db.repositories import ProjectRepository
from karlancer_agent.db.session import create_engine, create_session_factory
from karlancer_agent.notifications.telegram import TelegramBotClient
from karlancer_agent.services.reply_watcher import ReplyWatcher


async def main():
    settings = Settings()
    engine = create_engine(settings)
    browser = BrowserSession(
        profile_dir=settings.browser_profile_dir,
        headless=True,
        proxy=settings.browser_proxy,
    )
    try:
        async with httpx.AsyncClient() as http:
            repository = ProjectRepository(create_session_factory(engine))
            telegram = TelegramBotClient(
                token=settings.telegram_bot_token.get_secret_value(),
                chat_id=settings.telegram_chat_id,
                http=http,
            )
            watcher = ReplyWatcher(
                repository=repository,
                reader=KarlancerReplyReader(),
                telegram=telegram,
            )
            await browser.start()
            async with browser.new_page() as page:
                result = await asyncio.wait_for(watcher.run(page=page), timeout=180)
                print(json.dumps({"scan": result.model_dump()}, ensure_ascii=False), flush=True)
            async with engine.connect() as connection:
                rows = await connection.execute(
                    text(
                        "SELECT p.title, r.notified_at, r.conversation_url "
                        "FROM reply_events r JOIN projects p ON p.id=r.project_id "
                        "ORDER BY r.created_at DESC"
                    )
                )
                print(
                    json.dumps(
                        {"reply_delivery": [dict(row) for row in rows.mappings()]},
                        default=str,
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    except Exception as error:
        print(json.dumps({"failed": type(error).__name__, "detail": str(error)[:400]}))
        raise SystemExit(1) from None
    finally:
        await browser.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
