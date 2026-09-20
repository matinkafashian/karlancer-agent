"""Print a compact, read-only Karlancer runtime snapshot."""

import asyncio
import json

from sqlalchemy import text

from karlancer_agent.config import Settings
from karlancer_agent.db.session import create_engine


async def main() -> None:
    engine = create_engine(Settings())
    try:
        async with engine.connect() as connection:
            states = (
                (
                    await connection.execute(
                        text(
                            "SELECT state, count(*) AS count FROM projects "
                            "GROUP BY state ORDER BY state"
                        )
                    )
                )
                .mappings()
                .all()
            )
            drafts = (
                (
                    await connection.execute(
                        text(
                            "SELECT id, title, created_at FROM projects "
                            "WHERE state = 'drafted' ORDER BY created_at DESC LIMIT 10"
                        )
                    )
                )
                .mappings()
                .all()
            )
            submissions = (
                (
                    await connection.execute(
                        text(
                            "SELECT p.title, s.status, s.created_at, s.submitted_at "
                            "FROM submissions s JOIN projects p ON p.id = s.project_id "
                            "ORDER BY s.created_at DESC LIMIT 6"
                        )
                    )
                )
                .mappings()
                .all()
            )
            circuits = (
                (
                    await connection.execute(
                        text(
                            "SELECT provider, status, retry_at FROM provider_circuits "
                            "ORDER BY provider"
                        )
                    )
                )
                .mappings()
                .all()
            )
        print(
            json.dumps(
                {
                    "states": [dict(row) for row in states],
                    "drafts": [dict(row) for row in drafts],
                    "submissions": [dict(row) for row in submissions],
                    "circuits": [dict(row) for row in circuits],
                },
                ensure_ascii=False,
                default=str,
            )
        )
    finally:
        await engine.dispose()


asyncio.run(main())
