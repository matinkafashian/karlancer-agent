"""Assisted review, signed one-time decisions, and validated human edits."""

import base64
import hashlib
import hmac
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

from karlancer_agent.db.repositories import ApprovalAction, ProjectRepository
from karlancer_agent.domain.enums import ProjectState
from karlancer_agent.domain.models import ExtractedProject
from karlancer_agent.notifications.telegram import AssistedReport, TelegramReceipt
from karlancer_agent.proposal.validator import ProposalValidation, ProposalValidator


class ApprovalError(RuntimeError):
    """Base class mapped to stable API errors."""


class InvalidApproval(ApprovalError):
    pass


class ExpiredApproval(ApprovalError):
    pass


class UsedApproval(ApprovalError):
    pass


class ApprovalConflict(ApprovalError):
    pass


class InvalidEditedDraft(ApprovalError):
    def __init__(self, validation: ProposalValidation) -> None:
        super().__init__("edited proposal failed validation")
        self.validation = validation


class TelegramReporter(Protocol):
    async def send_assisted_report(
        self, report: AssistedReport, *, notification_key: str
    ) -> TelegramReceipt: ...


class ApprovalCommandResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: UUID
    state: ProjectState
    notification_key: str | None = None
    telegram_message_id: int | None = None
    skipped: bool = False


class ApprovalTokenSigner:
    """Create compact HMAC capabilities that fit Telegram's 64-byte limit."""

    def __init__(self, key: bytes) -> None:
        if len(key) < 32:
            raise ValueError("approval signing key must contain at least 32 bytes")
        self._key = key

    def sign(self, approval_id: UUID) -> str:
        identifier = _encode(approval_id.bytes)
        signature = _encode(hmac.digest(self._key, approval_id.bytes, "sha256")[:12])
        return f"{identifier}.{signature}"

    def verify(self, token: str) -> UUID:
        try:
            identifier, supplied_signature = token.split(".", maxsplit=1)
            raw_id = _decode(identifier)
            approval_id = UUID(bytes=raw_id)
        except (ValueError, TypeError) as error:
            raise InvalidApproval("approval token is malformed") from error
        expected = _encode(hmac.digest(self._key, approval_id.bytes, "sha256")[:12])
        if not hmac.compare_digest(supplied_signature, expected):
            raise InvalidApproval("approval token signature is invalid")
        return approval_id


class ApprovalService:
    """Coordinate DB state and Telegram without granting submission capability."""

    def __init__(
        self,
        *,
        repository: ProjectRepository,
        telegram: TelegramReporter,
        signer: ApprovalTokenSigner,
        validator: ProposalValidator,
        token_ttl: timedelta = timedelta(minutes=15),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._telegram = telegram
        self._signer = signer
        self._validator = validator
        self._token_ttl = token_ttl
        self._clock = clock

    async def request_review(
        self, project_id: UUID, *, notification_key: str
    ) -> ApprovalCommandResult:
        approval_id = uuid4()
        token = self._signer.sign(approval_id)
        now = self._clock()
        try:
            created = await self._repository.prepare_assisted_review(
                project_id,
                approval_id=approval_id,
                token_hash=_token_hash(token),
                expires_at=now + self._token_ttl,
                notification_key=notification_key,
                issued_at=now,
            )
        except ValueError as error:
            raise ApprovalConflict(str(error)) from error
        return await self._dispatch(
            project_id, notification_key=notification_key, skipped=not created
        )

    async def decide(
        self, project_id: UUID, *, callback_data: str
    ) -> ApprovalCommandResult:
        action, token = _parse_callback(callback_data)
        approval_id = self._signer.verify(token)
        result = await self._repository.consume_approval(
            project_id,
            approval_id=approval_id,
            token_hash=_token_hash(token),
            action=action,
            now=self._clock(),
        )
        if result == "expired":
            raise ExpiredApproval("approval token has expired")
        if result == "used":
            raise UsedApproval("approval token was already used")
        if result in {"wrong_project", "state_conflict"}:
            raise ApprovalConflict("approval does not match the current project state")
        if result == "invalid":
            raise InvalidApproval("approval token is invalid")
        state = (
            ProjectState.SUBMIT_PENDING if result == "approved" else ProjectState.REJECTED
        )
        return ApprovalCommandResult(project_id=project_id, state=state)

    async def edit(
        self, project_id: UUID, *, body: str, notification_key: str
    ) -> ApprovalCommandResult:
        view = await self._repository.project_view(project_id)
        if view is None:
            raise LookupError(f"project {project_id} was not found")
        extracted_data = view.get("extracted")
        if not isinstance(extracted_data, dict):
            raise ApprovalConflict("project extraction is missing")
        project = ExtractedProject.model_validate(extracted_data)
        validation = self._validator.validate(body=body, project=project)
        if not validation.is_valid:
            raise InvalidEditedDraft(validation)

        approval_id = uuid4()
        token = self._signer.sign(approval_id)
        now = self._clock()
        try:
            created = await self._repository.replace_assisted_draft(
                project_id,
                body=body,
                validation=validation,
                approval_id=approval_id,
                token_hash=_token_hash(token),
                expires_at=now + self._token_ttl,
                notification_key=notification_key,
                now=now,
            )
        except ValueError as error:
            raise ApprovalConflict(str(error)) from error
        return await self._dispatch(
            project_id, notification_key=notification_key, skipped=not created
        )

    async def _dispatch(
        self, project_id: UUID, *, notification_key: str, skipped: bool
    ) -> ApprovalCommandResult:
        data = await self._repository.assisted_report_data(notification_key)
        if data is None:
            raise RuntimeError("approval report was not durably queued")
        if bool(data["delivered"]):
            return ApprovalCommandResult(
                project_id=project_id,
                state=ProjectState(cast(str, data["state"])),
                notification_key=notification_key,
                skipped=True,
            )
        approval_id = cast(UUID, data["approval_id"])
        token = self._signer.sign(approval_id)
        report = AssistedReport(
            title=cast(str, data["title"]),
            selection_reason=cast(str, data["reason"]),
            budget=cast(str, data["budget"]),
            duration=cast(str, data["duration"]),
            draft_body=cast(str, data["draft_body"]),
            project_url=cast(str, data["project_url"]),
            approve_callback=f"approve:{token}",
            reject_callback=f"reject:{token}",
        )
        receipt = await self._telegram.send_assisted_report(
            report, notification_key=notification_key
        )
        await self._repository.mark_notification_delivered(
            notification_key, delivered_at=self._clock()
        )
        return ApprovalCommandResult(
            project_id=project_id,
            state=ProjectState.AWAITING_APPROVAL,
            notification_key=notification_key,
            telegram_message_id=receipt.message_id,
            skipped=skipped,
        )


def _parse_callback(value: str) -> tuple[ApprovalAction, str]:
    try:
        raw_action, token = value.split(":", maxsplit=1)
    except ValueError as error:
        raise InvalidApproval("approval callback is malformed") from error
    if raw_action not in {"approve", "reject"} or not token:
        raise InvalidApproval("approval callback action is invalid")
    return cast(ApprovalAction, raw_action), token


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
