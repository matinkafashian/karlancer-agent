"""Persist a global Karlancer automation pause without stopping PostgreSQL."""

import asyncio

from karlancer_agent.config import Settings
from karlancer_agent.db.repositories import ProjectRepository
from karlancer_agent.db.session import create_engine, create_session_factory


async def main() -> None:
    engine = create_engine(Settings())
    try:
        repository = ProjectRepository(create_session_factory(engine))
        changed = await repository.pause_system(
            reason="Existing account restriction and ambiguous submissions require review"
        )
        print({"system_paused": True, "changed": changed})
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
