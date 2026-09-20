"""Resume Karlancer automation after the blocking condition is verified resolved."""

import asyncio
from datetime import UTC, datetime

from karlancer_agent.config import Settings
from karlancer_agent.db.repositories import ProjectRepository
from karlancer_agent.db.session import create_engine, create_session_factory


async def main() -> None:
    engine = create_engine(Settings())
    try:
        repository = ProjectRepository(create_session_factory(engine))
        await repository.close_provider_circuit("system", updated_at=datetime.now(UTC))
        print({"system_paused": await repository.is_system_paused()})
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
