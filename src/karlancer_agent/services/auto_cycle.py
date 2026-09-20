# ruff: noqa: RUF001

"""Idempotent automatic drafting, submission, and owner reporting."""

import hashlib
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from karlancer_agent.browser.extractor import ExtractionError
from karlancer_agent.browser.submitter import (
    SubmissionCommand,
    SubmissionPage,
    SubmissionResult,
)
from karlancer_agent.domain.enums import ProjectState
from karlancer_agent.notifications.telegram import TEHRAN
from karlancer_agent.services.notifications import SystemAlert
from karlancer_agent.services.pipeline import ProjectStageResult
from karlancer_agent.services.provider_circuit import (
    ProviderCoolingDown,
    ProviderHardPaused,
)


class AutoCycleResult(BaseModel):
    """Concise stable result returned to n8n and rendered for Telegram."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    discovered: int = Field(ge=0)
    related: int = Field(ge=0)
    submitted: int = Field(ge=0)
    rejected: int = Field(ge=0)
    needs_review: int = Field(ge=0)
    errors: int = Field(ge=0)
    submitted_titles: tuple[str, ...] = ()
    ai_paused_until: AwareDatetime | None = None
    ai_hard_paused: bool = False


class AutoPipeline(Protocol):
    async def analyze(self, project_id: UUID) -> ProjectStageResult: ...

    async def draft(self, project_id: UUID) -> ProjectStageResult: ...


class ProjectRecord(Protocol):
    id: UUID
    title: str
    canonical_project_url: str


class AutoRepository(Protocol):
    async def is_system_paused(self) -> bool: ...

    async def pause_system(self, *, reason: str) -> bool: ...

    async def list_shadow_pending(self) -> tuple[UUID, ...]: ...

    async def list_auto_submission_candidates(self) -> tuple[UUID, ...]: ...

    async def get_project(self, project_id: UUID) -> ProjectRecord | None: ...

    async def record_project_error(self, project_id: UUID, *, reason: str) -> bool: ...

    async def reject_unavailable_project(
        self, project_id: UUID, *, reason: str
    ) -> bool: ...


class SubmissionBrowser(Protocol):
    def new_page(self) -> AbstractAsyncContextManager[Any]: ...


class ProjectSubmitter(Protocol):
    async def submit(
        self, page: SubmissionPage, command: SubmissionCommand
    ) -> SubmissionResult: ...


class AlertNotifier(Protocol):
    async def queue_and_dispatch(self, alert: SystemAlert) -> bool: ...


class AutoCycleService:
    """Stop all work on the first failure; successful cycles remain silent."""

    def __init__(
        self,
        *,
        pipeline: AutoPipeline,
        repository: AutoRepository,
        browser: SubmissionBrowser,
        submitter: ProjectSubmitter,
        notifier: AlertNotifier,
        ready_check: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._repository = repository
        self._browser = browser
        self._submitter = submitter
        self._notifier = notifier
        self._ready_check = ready_check

    async def run_once(
        self, *, idempotency_key: str, bootstrap_limit: int | None
    ) -> AutoCycleResult:
        if bootstrap_limit is not None and not 1 <= bootstrap_limit <= 10:
            raise ValueError("bootstrap_limit must be between 1 and 10")

        if await self._repository.is_system_paused():
            return AutoCycleResult(
                discovered=0, related=0, submitted=0, rejected=0,
                needs_review=0, errors=0,
            )

        if self._ready_check is not None and not await self._ready_check():
            return AutoCycleResult(discovered=0, related=0, submitted=0,
                                   rejected=0, needs_review=0, errors=0)

        discovered = related = submitted = rejected = needs_review = errors = 0
        submitted_titles: list[str] = []
        ai_paused_until: datetime | None = None
        ai_hard_paused = False
        error_messages: set[str] = set()
        owns_pause = False

        # Discovery has its own 30-minute schedule. Reuse ready drafts first;
        # only prepare one new candidate per due submission window.
        ready_candidates = await self._repository.list_auto_submission_candidates()
        pending = () if ready_candidates else await self._repository.list_shadow_pending()
        for project_id in pending:
            if await self._repository.is_system_paused():
                break
            try:
                analysis = await self._pipeline.analyze(project_id)
                if analysis.state is ProjectState.REJECTED:
                    rejected += 1
                    continue
                if analysis.state is ProjectState.NEEDS_REVIEW:
                    needs_review += 1
                    continue
                if analysis.state is not ProjectState.ELIGIBLE:
                    continue
                related += 1
                if await self._repository.is_system_paused():
                    break
                drafted = await self._pipeline.draft(project_id)
                if drafted.state is ProjectState.NEEDS_REVIEW:
                    needs_review += 1
                    continue
                if drafted.state is ProjectState.DRAFTED:
                    break
            except ProviderCoolingDown as error:
                ai_paused_until = error.retry_at
                break
            except ProviderHardPaused:
                ai_hard_paused = True
                break
            except Exception as error:
                if _is_unavailable_project(error):
                    rejected += 1
                    await self._repository.reject_unavailable_project(
                        project_id, reason=_safe_reason(error)
                    )
                    continue
                errors += 1
                error_messages.add(_friendly_error(str(error)))
                await self._record_project_error(
                    project_id,
                    error=error,
                )
                owns_pause = await self._repository.pause_system(reason=_safe_reason(error))
                break

        candidates = (
            ()
            if ai_paused_until is not None or ai_hard_paused
            else await self._repository.list_auto_submission_candidates()
        )
        attempted = 0
        for project_id in candidates:
            if await self._repository.is_system_paused():
                break
            if bootstrap_limit is not None and attempted >= bootstrap_limit:
                break
            row = await self._repository.get_project(project_id)
            if row is None:
                errors += 1
                continue
            attempted += 1
            try:
                async with self._browser.new_page() as page:
                    outcome = await self._submitter.submit(
                        page,
                        SubmissionCommand(
                            project_id=project_id,
                            idempotency_key=_submission_key(
                                idempotency_key, project_id
                            ),
                        ),
                    )
                if outcome.status == "submitted":
                    submitted += 1
                    submitted_titles.append(row.title[:80])
                elif outcome.status in {"paused_error", "blocked"}:
                    if outcome.reason in {
                        "submission_interval", "daily_limit_reached", "system_paused",
                    }:
                        break
                    if outcome.reason in {
                        "already_submitted", "duplicate_submission", "project_closed",
                    }:
                        await self._repository.reject_unavailable_project(
                            project_id, reason=outcome.reason)
                        continue
                    errors += 1
                    error_messages.add(_friendly_error(outcome.reason or outcome.status))
                    await self._record_project_error(
                        project_id,
                        error=RuntimeError(outcome.reason or outcome.status),
                    )
                    owns_pause = await self._repository.pause_system(
                        reason=outcome.reason or outcome.status
                    )
                    break
            except Exception as error:
                errors += 1
                error_messages.add(_friendly_error(str(error)))
                await self._record_project_error(
                    project_id,
                    error=error,
                )
                owns_pause = await self._repository.pause_system(reason=_safe_reason(error))
                break

        result = AutoCycleResult(
            discovered=discovered,
            related=related,
            submitted=submitted,
            rejected=rejected,
            needs_review=needs_review,
            errors=errors,
            submitted_titles=tuple(submitted_titles),
            ai_paused_until=ai_paused_until,
            ai_hard_paused=ai_hard_paused,
        )
        alert = _cycle_summary(idempotency_key, result, error_messages)
        if alert is not None and owns_pause:
            await self._notify_safely(alert)
        return result

    async def _record_project_error(
        self,
        project_id: UUID,
        *,
        error: Exception,
    ) -> None:
        reason = _safe_reason(error)
        await self._repository.record_project_error(project_id, reason=reason)

    async def _notify_safely(self, alert: SystemAlert) -> None:
        try:
            await self._notifier.queue_and_dispatch(alert)
        except Exception:
            return


def _submission_key(cycle_key: str, project_id: UUID) -> str:
    digest = hashlib.sha256(cycle_key.encode()).hexdigest()[:24]
    return f"auto:{digest}:{project_id}"


def _safe_reason(error: Exception) -> str:
    message = " ".join(str(error).split())[:400]
    return f"{type(error).__name__}: {message or 'unknown error'}"


def _is_unavailable_project(error: Exception) -> bool:
    """Treat only the known missing-card response as stale rather than an outage."""

    return (
        isinstance(error, ExtractionError)
        and str(error) == "Karlancer project card was not found"
    )


def _cycle_summary(
    idempotency_key: str, result: AutoCycleResult, error_messages: set[str] | None = None,
) -> SystemAlert | None:
    if not (result.errors or result.ai_paused_until or result.ai_hard_paused):
        return None
    messages = sorted(error_messages or ())
    if result.errors and not messages:
        messages.append("بخشی از بررسی یا ارسال پیشنهاد انجام نشد؛ علت نیاز به بررسی دارد.")
    if result.ai_paused_until is not None:
        local_retry = result.ai_paused_until.astimezone(TEHRAN)
        messages.append(
            "سهمیه استفاده از Codex برای تحلیل و نوشتن متن فعلاً در دسترس نیست؛ "
            "این محدودیت مربوط به تعداد پیشنهادهای کارلنسر نیست. "
            f"زمان اعلام‌شده پایان محدودیت: {local_retry:%Y-%m-%d ساعت %H:%M} به وقت تهران."
        )
    elif result.ai_hard_paused:
        messages.append(
            "تولید متن پس از چند تلاش ناموفق متوقف شده است؛ "
            "اتصال و سهمیه Codex باید بررسی شود."
        )
    messages.append("کل اتوماسیون کارلنسر متوقف شد؛ ادامه فقط پس از بررسی و فعال‌سازی دستی است.")
    body = "\n\n".join(messages)
    digest = hashlib.sha256(body.encode()).hexdigest()[:24]
    day = datetime.now(TEHRAN).date().isoformat()
    return SystemAlert(
        notification_key=f"operation-error:{day}:{digest}",
        title="کارلنسر: مشکلی در اجرای خودکار پیش آمده",
        body=body[:2000],
    )


def _friendly_error(reason: str) -> str:
    if "login" in reason.lower():
        return (
            "ورود به حساب کارلنسر لازم است؛ ارسال مربوطه انجام نشد. "
            "لطفاً وضعیت ورود حساب را بررسی کنید."
        )
    if "ambiguous_after_click" in reason:
        return (
            "نتیجه ثبت یک پیشنهاد مشخص نشد؛ برای جلوگیری از ارسال تکراری، "
            "آن پروژه متوقف شده و باید در سایت بررسی شود."
        )
    if "project card" in reason:
        return (
            "اطلاعات بعضی پروژه‌ها از سایت خوانده نشد؛ علت دقیق هنوز "
            "مشخص نیست و باید بررسی شود."
        )
    if "limit" in reason.lower() or "quota" in reason.lower():
        return "ارسال پیشنهاد با محدودیت روبه‌رو شد؛ سهمیه ارسال در حساب کارلنسر را بررسی کنید."
    return (
        "پردازش یا ارسال بعضی پروژه‌ها ناموفق بود؛ جزئیات در سوابق سیستم "
        "ثبت شده و نیاز به بررسی دارد."
    )
