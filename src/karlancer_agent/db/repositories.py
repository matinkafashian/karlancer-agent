"""Transactional repository operations that guard external side effects."""

from typing import cast
from uuid import UUID

from sqlalchemy import CursorResult, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from karlancer_agent.db.models import ProjectRow, ReplyEventRow, SubmissionRow
from karlancer_agent.domain.enums import ProjectState
from karlancer_agent.domain.models import ChannelProject, FirstReply


class ProjectRepository:
    """Persistence boundary for discovery, claims, submissions, and replies."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def upsert_discovered(self, project: ChannelProject) -> tuple[ProjectRow, bool]:
        """Create one project or return the existing message/URL match."""

        canonical_url = str(project.project_url)
        async with self._sessions() as session, session.begin():
            existing = await self._find_project(
                session, project.channel_message_id, canonical_url
            )
            if existing is not None:
                return existing, False

            row = ProjectRow(
                channel_message_id=project.channel_message_id,
                canonical_project_url=canonical_url,
                title=project.title,
                raw_channel_data=project.model_dump(mode="json"),
                state=ProjectState.DISCOVERED.value,
            )
            savepoint = await session.begin_nested()
            try:
                session.add(row)
                await session.flush()
            except IntegrityError:
                await savepoint.rollback()
                existing = await self._find_project(
                    session, project.channel_message_id, canonical_url
                )
                if existing is None:
                    raise
                return existing, False
            else:
                await savepoint.commit()
                return row, True

    async def claim_for_processing(self, project_id: UUID) -> bool:
        """Atomically claim a discovered project for extraction exactly once."""

        statement = (
            update(ProjectRow)
            .where(
                ProjectRow.id == project_id,
                ProjectRow.state == ProjectState.DISCOVERED.value,
            )
            .values(state=ProjectState.EXTRACT_PENDING.value)
        )
        async with self._sessions() as session, session.begin():
            result = cast(CursorResult[object], await session.execute(statement))
            return result.rowcount == 1

    async def reserve_submission(self, project_id: UUID, key: str) -> bool:
        """Reserve the sole submission slot before any browser side effect."""

        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(SubmissionRow.id).where(SubmissionRow.project_id == project_id)
            )
            if existing is not None:
                return False

            savepoint = await session.begin_nested()
            try:
                session.add(
                    SubmissionRow(
                        project_id=project_id,
                        idempotency_key=key,
                        status="reserved",
                    )
                )
                await session.flush()
            except IntegrityError:
                await savepoint.rollback()
                return False
            else:
                await savepoint.commit()
                return True

    async def record_first_reply_once(self, reply: FirstReply) -> bool:
        """Persist no more than one employer reply event for each project."""

        project_id = UUID(reply.project_id)
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(ReplyEventRow.id).where(ReplyEventRow.project_id == project_id)
            )
            if existing is not None:
                return False

            savepoint = await session.begin_nested()
            try:
                session.add(
                    ReplyEventRow(
                        project_id=project_id,
                        employer_message_id=reply.employer_message_id,
                        reply_text=reply.text,
                        received_at=reply.received_at,
                        conversation_url=str(reply.conversation_url),
                    )
                )
                await session.flush()
            except IntegrityError:
                await savepoint.rollback()
                return False
            else:
                await savepoint.commit()
                return True

    @staticmethod
    async def _find_project(
        session: AsyncSession, channel_message_id: int, canonical_url: str
    ) -> ProjectRow | None:
        statement = select(ProjectRow).where(
            or_(
                ProjectRow.channel_message_id == channel_message_id,
                ProjectRow.canonical_project_url == canonical_url,
            )
        )
        result = await session.scalar(statement)
        return result
