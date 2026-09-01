import asyncio
import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from karlancer_agent.db.base import Base
from karlancer_agent.db.repositories import ProjectRepository
from karlancer_agent.domain.models import ChannelProject

POSTGRES_URL = os.getenv("TEST_POSTGRES_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="Set TEST_POSTGRES_DATABASE_URL to run real PostgreSQL concurrency tests.",
)


@pytest_asyncio.fixture
async def postgres_engine() -> AsyncIterator[AsyncEngine]:
    assert POSTGRES_URL is not None
    schema = f"karlancer_test_{uuid4().hex}"
    admin_engine = create_async_engine(POSTGRES_URL)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = create_async_engine(
        POSTGRES_URL,
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await admin_engine.dispose()


@pytest.mark.asyncio
async def test_postgres_allows_only_one_concurrent_discovery_and_claim(
    postgres_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(postgres_engine, expire_on_commit=False)
    repository = ProjectRepository(sessions)
    project = ChannelProject(
        channel_message_id=5001,
        title="Python RAG service",
        budget_text="10,000,000 to 20,000,000 IRR",
        duration_text="7 days",
        hashtags=("#Python", "#RAG"),
        project_url="https://www.karlancer.com/projects/5001/rag-service",
    )

    discoveries = await asyncio.gather(
        repository.upsert_discovered(project),
        repository.upsert_discovered(project),
    )
    rows = [row for row, _ in discoveries]
    assert sum(created for _, created in discoveries) == 1
    assert rows[0].id == rows[1].id

    claims = await asyncio.gather(
        repository.claim_for_processing(rows[0].id),
        repository.claim_for_processing(rows[0].id),
    )
    assert sorted(claims) == [False, True]
