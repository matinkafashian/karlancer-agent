"""Async engine and session factory construction."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from karlancer_agent.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """Create the production async engine without exposing its URL in logs."""

    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_recycle=1_800,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create non-expiring sessions suitable for repository return values."""

    return async_sessionmaker(engine, expire_on_commit=False)
