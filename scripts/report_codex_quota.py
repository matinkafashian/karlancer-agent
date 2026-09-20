"""Deliver one durable quota alert while preserving the global pause."""

import asyncio
import json

import httpx

from karlancer_agent.config import Settings
from karlancer_agent.db.repositories import ProjectRepository
from karlancer_agent.db.session import create_engine, create_session_factory
from karlancer_agent.notifications.telegram import TelegramBotClient
from karlancer_agent.services.notifications import OperationalNotifier, SystemAlert


async def main() -> None:
    settings = Settings()
    engine = create_engine(settings)
    try:
        repository = ProjectRepository(create_session_factory(engine))
        await repository.pause_system(reason="codex_usage_limit_until_2026-09-20_23:09")
        async with httpx.AsyncClient() as http:
            notifier = OperationalNotifier(
                repository=repository,
                telegram=TelegramBotClient(
                    token=settings.telegram_bot_token.get_secret_value(),
                    chat_id=settings.telegram_chat_id,
                    http=http,
                ),
            )
            delivered = await notifier.queue_and_dispatch(
                SystemAlert(
                    notification_key="codex-quota-until-20260920-2309",
                    title="کارلنسر: توقف به‌دلیل پایان سهمیه Codex",
                    body="تست مستقیم Codex نشان داد سهمیه حساب متصل به VPS تمام شده است. "
                    "زمان اعلام‌شده برای تلاش بعدی: ۲۰ سپتامبر ۲۰۲۶ ساعت ۲۳:۰۹. "
                    "این محدودیت مربوط به پیشنهادهای کارلنسر نیست. کل اتوماسیون متوقف "
                    "مانده و تلاش تکراری انجام نمی‌شود.",
                )
            )
            print(
                json.dumps(
                    {"delivered": delivered, "system_paused": await repository.is_system_paused()}
                )
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
