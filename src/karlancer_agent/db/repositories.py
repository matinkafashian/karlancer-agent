"""Transactional repository operations that guard external side effects."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import CursorResult, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from karlancer_agent.classification.rules import RuleAssessment
from karlancer_agent.db.models import (
    AnalysisRow,
    ApprovalTokenRow,
    AuditEventRow,
    OutboxEventRow,
    ProjectRow,
    ProposalDraftRow,
    ReplyEventRow,
    SourceCursorRow,
    SubmissionRow,
)
from karlancer_agent.domain.enums import AnalysisDecision, ProjectState
from karlancer_agent.domain.models import (
    AnalysisResult,
    ChannelProject,
    ExtractedProject,
    FirstReply,
    ProposalDraft,
)
from karlancer_agent.proposal.validator import ProposalValidation

ApprovalAction = Literal["approve", "reject"]
ApprovalConsumeResult = Literal[
    "approved", "rejected", "invalid", "wrong_project", "expired", "used", "state_conflict"
]
SubmissionReservationResult = Literal[
    "reserved", "duplicate", "state_conflict", "daily_limit"
]


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

    async def get_project(self, project_id: UUID) -> ProjectRow | None:
        """Read one detached project record."""

        async with self._sessions() as session:
            return await session.get(ProjectRow, project_id)

    async def list_shadow_pending(self) -> tuple[UUID, ...]:
        """List unfinished read/analyze/draft work after a restart."""

        states = (
            ProjectState.DISCOVERED.value,
            ProjectState.EXTRACT_PENDING.value,
            ProjectState.EXTRACTED.value,
            ProjectState.ELIGIBLE.value,
        )
        async with self._sessions() as session:
            values = await session.scalars(
                select(ProjectRow.id)
                .where(ProjectRow.state.in_(states))
                .order_by(ProjectRow.created_at, ProjectRow.id)
            )
            return tuple(values)

    async def source_cursor(self, source: str) -> int | None:
        """Return the last safely observed source message."""

        async with self._sessions() as session:
            value = await session.scalar(
                select(SourceCursorRow.last_message_id).where(
                    SourceCursorRow.source == source
                )
            )
            return value

    async def advance_source_cursor(self, source: str, message_id: int) -> None:
        """Move a source cursor forward, never backward."""

        async with self._sessions() as session, session.begin():
            row = await session.get(SourceCursorRow, source)
            if row is None:
                session.add(SourceCursorRow(source=source, last_message_id=message_id))
            elif message_id > row.last_message_id:
                row.last_message_id = message_id

    async def record_extraction(
        self, project_id: UUID, extracted: ExtractedProject
    ) -> bool:
        """Persist trusted page data and advance the claimed project atomically."""

        statement = (
            update(ProjectRow)
            .where(
                ProjectRow.id == project_id,
                ProjectRow.state == ProjectState.EXTRACT_PENDING.value,
            )
            .values(
                karlancer_project_id=extracted.project_id,
                canonical_project_url=str(extracted.canonical_url),
                title=extracted.title,
                extracted_data=extracted.model_dump(mode="json"),
                state=ProjectState.EXTRACTED.value,
                last_error=None,
            )
        )
        async with self._sessions() as session, session.begin():
            result = cast(CursorResult[object], await session.execute(statement))
            return result.rowcount == 1

    async def record_analysis(
        self,
        project_id: UUID,
        *,
        rules: RuleAssessment,
        analysis: AnalysisResult,
        provider: str,
        model_name: str,
        analysis_version: str,
    ) -> bool:
        """Store one semantic decision and its rule evidence atomically."""

        target = {
            AnalysisDecision.ELIGIBLE: ProjectState.ELIGIBLE,
            AnalysisDecision.REJECTED: ProjectState.REJECTED,
            AnalysisDecision.NEEDS_REVIEW: ProjectState.NEEDS_REVIEW,
        }[analysis.decision]
        async with self._sessions() as session, session.begin():
            result = cast(
                CursorResult[object],
                await session.execute(
                    update(ProjectRow)
                    .where(
                        ProjectRow.id == project_id,
                        ProjectRow.state == ProjectState.EXTRACTED.value,
                    )
                    .values(state=target.value)
                ),
            )
            if result.rowcount != 1:
                return False
            session.add(
                AnalysisRow(
                    project_id=project_id,
                    rule_score=rules.score,
                    decision=analysis.decision.value,
                    confidence=Decimal(str(analysis.confidence)),
                    reasons={
                        "analysis": analysis.model_dump(mode="json"),
                        "rules": rules.model_dump(mode="json"),
                    },
                    provider=provider,
                    model_name=model_name,
                    analysis_version=analysis_version,
                )
            )
            return True

    async def latest_analysis(self, project_id: UUID) -> AnalysisResult | None:
        """Rehydrate the most recent structured analysis."""

        async with self._sessions() as session:
            row = await session.scalar(
                select(AnalysisRow)
                .where(AnalysisRow.project_id == project_id)
                .order_by(AnalysisRow.created_at.desc(), AnalysisRow.id.desc())
                .limit(1)
            )
        if row is None:
            return None
        payload = row.reasons.get("analysis")
        return AnalysisResult.model_validate(payload) if isinstance(payload, dict) else None

    async def record_draft(
        self,
        project_id: UUID,
        *,
        draft: ProposalDraft,
        validation: ProposalValidation,
    ) -> bool:
        """Persist an immutable draft and final shadow state in one transaction."""

        target = ProjectState.DRAFTED if validation.is_valid else ProjectState.NEEDS_REVIEW
        async with self._sessions() as session, session.begin():
            result = cast(
                CursorResult[object],
                await session.execute(
                    update(ProjectRow)
                    .where(
                        ProjectRow.id == project_id,
                        ProjectRow.state == ProjectState.ELIGIBLE.value,
                    )
                    .values(state=target.value)
                ),
            )
            if result.rowcount != 1:
                return False
            latest_version = await session.scalar(
                select(func.max(ProposalDraftRow.version)).where(
                    ProposalDraftRow.project_id == project_id
                )
            )
            session.add(
                ProposalDraftRow(
                    project_id=project_id,
                    body=draft.body,
                    validator_result=validation.model_dump(mode="json"),
                    version=(latest_version or 0) + 1,
                )
            )
            return True

    async def project_view(self, project_id: UUID) -> dict[str, Any] | None:
        """Return a sanitized API projection without secrets or browser state."""

        async with self._sessions() as session:
            row = await session.get(ProjectRow, project_id)
            if row is None:
                return None
            analysis = await session.scalar(
                select(AnalysisRow)
                .where(AnalysisRow.project_id == project_id)
                .order_by(AnalysisRow.created_at.desc(), AnalysisRow.id.desc())
                .limit(1)
            )
            draft = await session.scalar(
                select(ProposalDraftRow)
                .where(ProposalDraftRow.project_id == project_id)
                .order_by(ProposalDraftRow.version.desc())
                .limit(1)
            )
            return {
                "id": str(row.id),
                "state": row.state,
                "title": row.title,
                "project_url": row.canonical_project_url,
                "extracted": row.extracted_data,
                "analysis": analysis.reasons.get("analysis") if analysis else None,
                "draft": draft.body if draft else None,
            }

    async def ping(self) -> bool:
        """Check database connectivity for readiness without changing state."""

        async with self._sessions() as session:
            return bool(await session.scalar(select(1)))

    async def prepare_assisted_review(
        self,
        project_id: UUID,
        *,
        approval_id: UUID,
        token_hash: str,
        expires_at: datetime,
        notification_key: str,
        issued_at: datetime,
    ) -> bool:
        """Move a valid draft into assisted review and enqueue one report."""

        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(OutboxEventRow).where(
                    OutboxEventRow.deduplication_key == notification_key
                )
            )
            if existing is not None:
                payload_project = existing.payload.get("project_id")
                if payload_project != str(project_id):
                    raise ValueError("notification key belongs to another project")
                return False

            project = await session.get(ProjectRow, project_id)
            if project is None:
                raise LookupError(f"project {project_id} was not found")
            if project.state not in {
                ProjectState.DRAFTED.value,
                ProjectState.AWAITING_APPROVAL.value,
            }:
                raise ValueError("project is not ready for assisted review")

            draft = await session.scalar(
                select(ProposalDraftRow)
                .where(ProposalDraftRow.project_id == project_id)
                .order_by(ProposalDraftRow.version.desc())
                .limit(1)
            )
            if draft is None or not bool(draft.validator_result.get("is_valid")):
                raise ValueError("latest proposal draft is not valid")

            if project.state == ProjectState.AWAITING_APPROVAL.value:
                await session.execute(
                    update(ApprovalTokenRow)
                    .where(
                        ApprovalTokenRow.project_id == project_id,
                        ApprovalTokenRow.used_at.is_(None),
                    )
                    .values(used_at=issued_at)
                )
            else:
                project.state = ProjectState.AWAITING_APPROVAL.value
            session.add(
                ApprovalTokenRow(
                    id=approval_id,
                    project_id=project_id,
                    token_hash=token_hash,
                    expires_at=expires_at,
                )
            )
            session.add(
                OutboxEventRow(
                    event_type="assisted_review",
                    deduplication_key=notification_key,
                    payload={
                        "project_id": str(project_id),
                        "approval_id": str(approval_id),
                        "draft_version": draft.version,
                    },
                )
            )
            return True

    async def assisted_report_data(self, notification_key: str) -> dict[str, Any] | None:
        """Load a queued report; raw approval tokens are never persisted."""

        async with self._sessions() as session:
            event = await session.scalar(
                select(OutboxEventRow).where(
                    OutboxEventRow.deduplication_key == notification_key
                )
            )
            if event is None:
                return None
            project_id = UUID(cast(str, event.payload["project_id"]))
            approval_id = UUID(cast(str, event.payload["approval_id"]))
            draft_version = int(cast(int, event.payload["draft_version"]))
            project = await session.get(ProjectRow, project_id)
            approval = await session.get(ApprovalTokenRow, approval_id)
            draft = await session.scalar(
                select(ProposalDraftRow).where(
                    ProposalDraftRow.project_id == project_id,
                    ProposalDraftRow.version == draft_version,
                )
            )
            analysis = await session.scalar(
                select(AnalysisRow)
                .where(AnalysisRow.project_id == project_id)
                .order_by(AnalysisRow.created_at.desc(), AnalysisRow.id.desc())
                .limit(1)
            )
            if project is None or approval is None or draft is None:
                raise RuntimeError("assisted review outbox references missing data")
            extracted = project.extracted_data or {}
            analysis_payload = analysis.reasons.get("analysis", {}) if analysis else {}
            return {
                "project_id": project_id,
                "approval_id": approval_id,
                "title": project.title,
                "reason": analysis_payload.get(
                    "reason", "پیش‌نویس معتبر و آمادهٔ بررسی دستی است."
                ),
                "budget": extracted.get("budget_text", "نامشخص"),
                "duration": extracted.get("duration_text", "نامشخص"),
                "draft_body": draft.body,
                "project_url": project.canonical_project_url,
                "state": project.state,
                "delivered": event.delivered,
            }

    async def mark_notification_delivered(
        self, notification_key: str, *, delivered_at: datetime
    ) -> bool:
        """Complete one outbox record without allowing a second successful send."""

        statement = (
            update(OutboxEventRow)
            .where(
                OutboxEventRow.deduplication_key == notification_key,
                OutboxEventRow.delivered.is_(False),
            )
            .values(delivered=True, delivered_at=delivered_at)
        )
        async with self._sessions() as session, session.begin():
            result = cast(CursorResult[object], await session.execute(statement))
            return result.rowcount == 1

    async def consume_approval(
        self,
        project_id: UUID,
        *,
        approval_id: UUID,
        token_hash: str,
        action: ApprovalAction,
        now: datetime,
    ) -> ApprovalConsumeResult:
        """Consume one approval capability and apply its guarded transition."""

        async with self._sessions() as session, session.begin():
            token = await session.get(ApprovalTokenRow, approval_id)
            if token is None or token.token_hash != token_hash:
                return "invalid"
            if token.project_id != project_id:
                return "wrong_project"
            if token.used_at is not None:
                return "used"
            if _as_utc(token.expires_at) <= _as_utc(now):
                return "expired"
            project = await session.get(ProjectRow, project_id)
            if project is None or project.state != ProjectState.AWAITING_APPROVAL.value:
                return "state_conflict"

            if action == "approve":
                draft = await session.scalar(
                    select(ProposalDraftRow)
                    .where(ProposalDraftRow.project_id == project_id)
                    .order_by(ProposalDraftRow.version.desc())
                    .limit(1)
                )
                if draft is None:
                    return "state_conflict"
                token.used_at = now
                project.state = ProjectState.SUBMIT_PENDING.value
                draft.approved_at = now
                return "approved"
            token.used_at = now
            project.state = ProjectState.REJECTED.value
            return "rejected"

    async def replace_assisted_draft(
        self,
        project_id: UUID,
        *,
        body: str,
        validation: ProposalValidation,
        approval_id: UUID,
        token_hash: str,
        expires_at: datetime,
        notification_key: str,
        now: datetime,
    ) -> bool:
        """Store one validated human edit and issue a fresh assisted decision."""

        if not validation.is_valid:
            raise ValueError("edited proposal draft is not valid")
        async with self._sessions() as session, session.begin():
            project = await session.get(ProjectRow, project_id)
            if project is None:
                raise LookupError(f"project {project_id} was not found")
            if project.state != ProjectState.AWAITING_APPROVAL.value:
                raise ValueError("project is not awaiting approval")
            existing = await session.scalar(
                select(OutboxEventRow.id).where(
                    OutboxEventRow.deduplication_key == notification_key
                )
            )
            if existing is not None:
                return False

            latest_version = await session.scalar(
                select(func.max(ProposalDraftRow.version)).where(
                    ProposalDraftRow.project_id == project_id
                )
            )
            next_version = (latest_version or 0) + 1
            await session.execute(
                update(ApprovalTokenRow)
                .where(
                    ApprovalTokenRow.project_id == project_id,
                    ApprovalTokenRow.used_at.is_(None),
                )
                .values(used_at=now)
            )
            session.add(
                ProposalDraftRow(
                    project_id=project_id,
                    body=body,
                    validator_result=validation.model_dump(mode="json"),
                    version=next_version,
                )
            )
            session.add(
                ApprovalTokenRow(
                    id=approval_id,
                    project_id=project_id,
                    token_hash=token_hash,
                    expires_at=expires_at,
                )
            )
            session.add(
                OutboxEventRow(
                    event_type="assisted_review",
                    deduplication_key=notification_key,
                    payload={
                        "project_id": str(project_id),
                        "approval_id": str(approval_id),
                        "draft_version": next_version,
                    },
                )
            )
            return True

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

    async def submission_material(self, project_id: UUID) -> dict[str, Any] | None:
        """Load the sole persisted source for body, amount, duration, and approval."""

        async with self._sessions() as session:
            project = await session.get(ProjectRow, project_id)
            if project is None or project.extracted_data is None:
                return None
            draft = await session.scalar(
                select(ProposalDraftRow)
                .where(ProposalDraftRow.project_id == project_id)
                .order_by(ProposalDraftRow.version.desc())
                .limit(1)
            )
            if draft is None:
                return None
            material = dict(project.extracted_data)
            material.update(
                {
                    "project_url": project.canonical_project_url,
                    "body": draft.body,
                    "validator_result": draft.validator_result,
                    "approved_at": draft.approved_at,
                    "state": project.state,
                }
            )
            return material

    async def count_recent_submissions(self, *, since: datetime) -> int:
        """Count reservations too, preventing concurrent workers from exceeding the cap."""

        async with self._sessions() as session:
            value = await session.scalar(
                select(func.count(SubmissionRow.id)).where(
                    SubmissionRow.created_at >= since
                )
            )
            return int(value or 0)

    async def reserve_ready_submission(
        self,
        project_id: UUID,
        *,
        key: str,
        daily_limit: int,
        since: datetime,
        allowed_states: tuple[ProjectState, ...],
        form_budget: Decimal,
        duration_value: Decimal,
        duration_unit: str,
    ) -> SubmissionReservationResult:
        """Atomically enforce state, 24-hour cap, and exactly one external attempt."""

        async with self._sessions() as session, session.begin():
            if session.get_bind().dialect.name == "postgresql":
                # Serialize the rolling global quota check across different projects.
                await session.execute(select(func.pg_advisory_xact_lock(4_214_337)))
            project = await session.scalar(
                select(ProjectRow).where(ProjectRow.id == project_id).with_for_update()
            )
            if project is None or project.state not in {state.value for state in allowed_states}:
                return "state_conflict"
            if project.state == ProjectState.SUBMIT_PENDING.value:
                approved_at = await session.scalar(
                    select(ProposalDraftRow.approved_at)
                    .where(ProposalDraftRow.project_id == project_id)
                    .order_by(ProposalDraftRow.version.desc())
                    .limit(1)
                )
                if approved_at is None:
                    return "state_conflict"
            existing = await session.scalar(
                select(SubmissionRow.id).where(
                    or_(
                        SubmissionRow.project_id == project_id,
                        SubmissionRow.idempotency_key == key,
                    )
                )
            )
            if existing is not None:
                return "duplicate"
            recent = await session.scalar(
                select(func.count(SubmissionRow.id)).where(SubmissionRow.created_at >= since)
            )
            if int(recent or 0) >= daily_limit:
                return "daily_limit"

            savepoint = await session.begin_nested()
            try:
                session.add(
                    SubmissionRow(
                        project_id=project_id,
                        idempotency_key=key,
                        status="reserved",
                        form_budget=form_budget,
                        duration_value=duration_value,
                        duration_unit=duration_unit,
                    )
                )
                if project.state == ProjectState.DRAFTED.value:
                    project.state = ProjectState.SUBMIT_PENDING.value
                await session.flush()
            except IntegrityError:
                await savepoint.rollback()
                return "duplicate"
            else:
                await savepoint.commit()
                return "reserved"

    async def record_shadow_preview(
        self,
        project_id: UUID,
        *,
        evidence_hash: str,
        screenshot_path: str,
        html_path: str,
    ) -> None:
        """Audit a read-only preview without creating a submission reservation."""

        async with self._sessions() as session, session.begin():
            session.add(
                AuditEventRow(
                    project_id=project_id,
                    event_type="would_submit",
                    actor="submission_guard",
                    payload={
                        "evidence_hash": evidence_hash,
                        "screenshot_path": screenshot_path,
                        "html_path": html_path,
                    },
                )
            )

    async def complete_submission(
        self,
        project_id: UUID,
        *,
        submitted_at: datetime,
        evidence_url: str,
        evidence_hash: str,
    ) -> bool:
        """Record success only after the browser exposes deterministic evidence."""

        async with self._sessions() as session, session.begin():
            submission_result = cast(
                CursorResult[object],
                await session.execute(
                    update(SubmissionRow)
                    .where(
                        SubmissionRow.project_id == project_id,
                        SubmissionRow.status == "reserved",
                    )
                    .values(
                        status="submitted",
                        submitted_at=submitted_at,
                        evidence_url=evidence_url,
                        evidence_hash=evidence_hash,
                    )
                ),
            )
            project_result = cast(
                CursorResult[object],
                await session.execute(
                    update(ProjectRow)
                    .where(
                        ProjectRow.id == project_id,
                        ProjectRow.state == ProjectState.SUBMIT_PENDING.value,
                    )
                    .values(state=ProjectState.SUBMITTED.value, last_error=None)
                ),
            )
            complete = submission_result.rowcount == 1 and project_result.rowcount == 1
            if not complete:
                await session.rollback()
            return complete

    async def pause_submission(
        self,
        project_id: UUID,
        *,
        reason: str,
        evidence_url: str | None,
        evidence_hash: str | None,
    ) -> bool:
        """Permanently pause an attempted submit; no automatic retry is permitted."""

        async with self._sessions() as session, session.begin():
            await session.execute(
                update(SubmissionRow)
                .where(
                    SubmissionRow.project_id == project_id,
                    SubmissionRow.status == "reserved",
                )
                .values(
                    status="paused_error",
                    evidence_url=evidence_url,
                    evidence_hash=evidence_hash,
                )
            )
            result = cast(
                CursorResult[object],
                await session.execute(
                    update(ProjectRow)
                    .where(
                        ProjectRow.id == project_id,
                        ProjectRow.state == ProjectState.SUBMIT_PENDING.value,
                    )
                    .values(state=ProjectState.PAUSED_ERROR.value, last_error=reason)
                ),
            )
            return result.rowcount == 1

    async def claim_reply_watch_targets(self) -> tuple[dict[str, Any], ...]:
        """Move new submissions into read-only reply watch and return active targets."""

        watch_states = (
            ProjectState.SUBMITTED.value,
            ProjectState.REPLY_WATCH.value,
        )
        async with self._sessions() as session, session.begin():
            statement = (
                select(ProjectRow, SubmissionRow)
                .join(SubmissionRow, SubmissionRow.project_id == ProjectRow.id)
                .where(
                    ProjectRow.state.in_(watch_states),
                    SubmissionRow.status == "submitted",
                    SubmissionRow.submitted_at.is_not(None),
                )
                .order_by(SubmissionRow.submitted_at, ProjectRow.id)
                .with_for_update(skip_locked=True)
            )
            rows = (await session.execute(statement)).all()
            targets: list[dict[str, Any]] = []
            for project, submission in rows:
                if project.state == ProjectState.SUBMITTED.value:
                    project.state = ProjectState.REPLY_WATCH.value
                extracted = project.extracted_data or {}
                description = str(extracted.get("description", "")).strip()
                targets.append(
                    {
                        "project_id": project.id,
                        "title": project.title,
                        "project_url": project.canonical_project_url,
                        "project_summary": description[:500] or project.title,
                        "submitted_at": submission.submitted_at,
                        "conversation_url": submission.conversation_url,
                    }
                )
            return tuple(targets)

    async def set_submission_conversation_url(
        self, project_id: UUID, *, conversation_url: str
    ) -> bool:
        """Persist the discovered room once so later polls avoid scanning every room."""

        if not conversation_url.startswith(
            "https://www.karlancer.com/panel/messages?room="
        ):
            raise ValueError("conversation URL must be a Karlancer room")
        async with self._sessions() as session, session.begin():
            result = cast(
                CursorResult[object],
                await session.execute(
                    update(SubmissionRow)
                    .where(
                        SubmissionRow.project_id == project_id,
                        SubmissionRow.status == "submitted",
                    )
                    .values(conversation_url=conversation_url)
                ),
            )
            return result.rowcount == 1

    async def queue_first_reply_once(
        self, reply: FirstReply, *, notification_key: str
    ) -> bool:
        """Atomically persist the first reply and a recoverable Telegram outbox item."""

        project_id = UUID(reply.project_id)
        async with self._sessions() as session, session.begin():
            project = await session.scalar(
                select(ProjectRow).where(ProjectRow.id == project_id).with_for_update()
            )
            if project is None or project.state != ProjectState.REPLY_WATCH.value:
                return False
            existing = await session.scalar(
                select(ReplyEventRow.id).where(ReplyEventRow.project_id == project_id)
            )
            if existing is not None:
                return False

            reply_id = uuid4()
            savepoint = await session.begin_nested()
            try:
                session.add(
                    ReplyEventRow(
                        id=reply_id,
                        project_id=project_id,
                        employer_message_id=reply.employer_message_id,
                        reply_text=reply.text,
                        received_at=reply.received_at,
                        conversation_url=str(reply.conversation_url),
                    )
                )
                session.add(
                    OutboxEventRow(
                        event_type="first_reply",
                        deduplication_key=notification_key,
                        payload={
                            "project_id": str(project_id),
                            "reply_event_id": str(reply_id),
                        },
                    )
                )
                project.state = ProjectState.REPLY_NOTIFIED.value
                await session.flush()
            except IntegrityError:
                await savepoint.rollback()
                return False
            else:
                await savepoint.commit()
                return True

    async def pending_first_reply_notification_keys(self) -> tuple[str, ...]:
        """Return undelivered handoffs so a crash before Telegram send is recoverable."""

        async with self._sessions() as session:
            values = await session.scalars(
                select(OutboxEventRow.deduplication_key)
                .where(
                    OutboxEventRow.event_type == "first_reply",
                    OutboxEventRow.delivered.is_(False),
                )
                .order_by(OutboxEventRow.created_at, OutboxEventRow.id)
            )
            return tuple(values)

    async def first_reply_report_data(
        self, notification_key: str
    ) -> dict[str, Any] | None:
        """Load the immutable report payload represented by one outbox key."""

        async with self._sessions() as session:
            event = await session.scalar(
                select(OutboxEventRow).where(
                    OutboxEventRow.deduplication_key == notification_key,
                    OutboxEventRow.event_type == "first_reply",
                )
            )
            if event is None:
                return None
            project_id = UUID(cast(str, event.payload["project_id"]))
            reply_id = UUID(cast(str, event.payload["reply_event_id"]))
            project = await session.get(ProjectRow, project_id)
            reply = await session.get(ReplyEventRow, reply_id)
            if project is None or reply is None:
                raise RuntimeError("first-reply outbox references missing data")
            extracted = project.extracted_data or {}
            description = str(extracted.get("description", "")).strip()
            return {
                "project_id": project_id,
                "title": project.title,
                "project_summary": description[:500] or project.title,
                "reply_summary": reply.reply_text[:1000],
                "conversation_url": reply.conversation_url,
                "delivered": event.delivered,
            }

    async def complete_first_reply_notification(
        self, notification_key: str, *, notified_at: datetime
    ) -> bool:
        """Mark delivery and enter terminal human handoff in one transaction."""

        async with self._sessions() as session, session.begin():
            event = await session.scalar(
                select(OutboxEventRow)
                .where(
                    OutboxEventRow.deduplication_key == notification_key,
                    OutboxEventRow.event_type == "first_reply",
                )
                .with_for_update()
            )
            if event is None or event.delivered:
                return False
            project_id = UUID(cast(str, event.payload["project_id"]))
            reply_id = UUID(cast(str, event.payload["reply_event_id"]))
            project = await session.get(ProjectRow, project_id)
            reply = await session.get(ReplyEventRow, reply_id)
            if (
                project is None
                or reply is None
                or project.state != ProjectState.REPLY_NOTIFIED.value
            ):
                return False
            event.delivered = True
            event.delivered_at = notified_at
            reply.notified_at = notified_at
            project.state = ProjectState.HUMAN_HANDOFF.value
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


def _as_utc(value: datetime) -> datetime:
    """Normalize SQLite's naive DateTime round-trip to UTC."""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
