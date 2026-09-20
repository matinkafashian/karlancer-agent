"""Read-only operational snapshot; never print credentials or proposal bodies."""

import asyncio
import json

from sqlalchemy import text

from karlancer_agent.config import Settings
from karlancer_agent.db.repositories import ProjectRepository
from karlancer_agent.db.session import create_engine, create_session_factory


async def main():
    settings = Settings()
    engine = create_engine(settings)
    try:
        repository = ProjectRepository(create_session_factory(engine))
        result = {
            "settings": {
                key: getattr(settings, key)
                for key in (
                    "run_mode",
                    "submission_enabled",
                    "daily_submission_limit",
                    "live_reply_monitor_enabled",
                    "model_provider",
                    "model_name",
                )
            },
            "system_paused": await repository.is_system_paused(),
        }
        async with engine.connect() as connection:
            for name, query in {
                "circuits": "SELECT provider, status, retry_at FROM provider_circuits",
                "projects": "SELECT state, count(*) FROM projects GROUP BY state",
                "submissions": (
                    "SELECT status, count(*), max(created_at) "
                    "FROM submissions GROUP BY status"
                ),
                "attempts_24h": (
                    "SELECT count(*) FROM submissions "
                    "WHERE created_at >= now() - interval '24 hours'"
                ),
                "replies": (
                    "SELECT count(*) AS total, count(notified_at) AS notified "
                    "FROM reply_events"
                ),
                "recent_audit": (
                    "SELECT event_type, created_at FROM audit_events "
                    "ORDER BY created_at DESC LIMIT 8"
                ),
            }.items():
                rows = await connection.execute(text(query))
                result[name] = [dict(row) for row in rows.mappings()]
        print(json.dumps(result, default=str, ensure_ascii=False))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
