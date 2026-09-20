"""Report this real acceptance failure once; preserve the global stop."""

import asyncio
import json

import httpx
from sqlalchemy import text

from karlancer_agent.config import Settings
from karlancer_agent.db.repositories import ProjectRepository
from karlancer_agent.db.session import create_engine, create_session_factory
from karlancer_agent.notifications.telegram import TelegramBotClient
from karlancer_agent.services.notifications import OperationalNotifier, SystemAlert


async def main():
    settings = Settings()
    engine = create_engine(settings)
    try:
        repository = ProjectRepository(create_session_factory(engine))
        await repository.pause_system(reason="Acceptance: message-page navigation timed out")
        async with httpx.AsyncClient() as http:
            notifier = OperationalNotifier(
                repository=repository,
                telegram=TelegramBotClient(
                    token=settings.telegram_bot_token.get_secret_value(),
                    chat_id=settings.telegram_chat_id,
                    http=http,
                ),
            )
            alert = SystemAlert(
                notification_key="acceptance-message-timeout-20260918",
                title="کارلنسر: سیستم متوقف است",
                body="در تست واقعی، صفحه پیام‌های کارلنسر از مرورگر VPS به‌طور ناپایدار باز شد "
                "و چند بار پس از ۳۰ ثانیه پاسخ نداد. پایش پاسخ کارفرما فعلاً قابل اعتماد نیست. "
                "هیچ پیشنهاد جدیدی در این تست ارسال نشد. کل اتوماسیون تا بررسی و "
                "فعال‌سازی دستی متوقف می‌ماند؛ تلاش و گزارش تکراری انجام نمی‌شود.",
            )
            delivered = await notifier.queue_and_dispatch(alert)
            print(
                json.dumps(
                    {
                        "error_alert_delivered": delivered,
                        "system_paused": await repository.is_system_paused(),
                    }
                )
            )
        async with engine.connect() as connection:
            rows = await connection.execute(
                text("SELECT count(*) AS replies, count(notified_at) AS notified FROM reply_events")
            )
            print(json.dumps(dict(rows.mappings().one())))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
