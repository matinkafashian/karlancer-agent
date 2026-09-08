"""Strictly gated Playwright form submission with durable evidence."""

import hashlib
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field

from karlancer_agent.browser.selectors import (
    BID_MODAL,
    CONTINUE_TO_BID_CONTROL,
    FINAL_SUBMIT_CONTROL,
    PROPOSAL_BODY_INPUT,
    PROPOSAL_BUDGET_INPUT,
    PROPOSAL_DURATION_INPUT,
    SUBMIT_CONTROL,
)
from karlancer_agent.config import RunMode, Settings
from karlancer_agent.db.repositories import ProjectRepository
from karlancer_agent.domain.enums import ProjectState
from karlancer_agent.domain.models import ExtractedProject
from karlancer_agent.proposal.validator import ProposalValidator


class SubmissionGateOutcome(StrEnum):
    PREVIEW = "preview"
    ALLOWED = "allowed"
    BLOCKED = "blocked"


class SubmissionGateInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_mode: RunMode
    submission_enabled: bool
    project_state: ProjectState
    approval_granted: bool
    validation_passed: bool
    budget_max: Decimal | None
    duration_value: Decimal | None
    duration_unit: str | None
    daily_submissions: int = Field(ge=0)
    daily_limit: int = Field(gt=0)


class SubmissionGateDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: SubmissionGateOutcome
    reasons: tuple[str, ...] = ()


class SubmissionCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: UUID
    idempotency_key: str = Field(min_length=8, max_length=255)


class SubmissionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: UUID
    status: Literal["blocked", "would_submit", "submitted", "paused_error"]
    state: ProjectState
    reason: str | None = None
    evidence_hash: str | None = None
    screenshot_path: str | None = None
    html_path: str | None = None
    evidence_url: str | None = None


class LocatorLike(Protocol):
    async def fill(self, value: str) -> None: ...

    async def input_value(self) -> str: ...

    async def click(self) -> None: ...

    async def is_visible(self) -> bool: ...


class SubmissionPage(Protocol):
    @property
    def url(self) -> str: ...

    async def goto(self, url: str, **kwargs: Any) -> object: ...

    def locator(self, selector: str) -> LocatorLike: ...

    async def wait_for_selector(self, selector: str, **kwargs: Any) -> object: ...

    async def screenshot(self, **kwargs: Any) -> bytes: ...

    async def content(self) -> str: ...


def evaluate_submission_gate(value: SubmissionGateInput) -> SubmissionGateDecision:
    """Evaluate every non-browser condition with deterministic precedence."""

    reasons: list[str] = []
    allowed_states = (
        {ProjectState.DRAFTED, ProjectState.AWAITING_APPROVAL}
        if value.run_mode is RunMode.SHADOW
        else {ProjectState.SUBMIT_PENDING}
        if value.run_mode is RunMode.ASSISTED
        else {ProjectState.DRAFTED, ProjectState.SUBMIT_PENDING}
    )
    if value.project_state not in allowed_states:
        reasons.append("invalid_project_state")
    if not value.validation_passed:
        reasons.append("invalid_proposal")
    if value.budget_max is None:
        reasons.append("missing_budget")
    if value.duration_value is None or not value.duration_unit:
        reasons.append("missing_duration")
    elif value.duration_unit != "day":
        reasons.append("unsupported_duration_unit")
    if reasons:
        return SubmissionGateDecision(
            outcome=SubmissionGateOutcome.BLOCKED, reasons=tuple(reasons)
        )
    if value.run_mode is RunMode.SHADOW:
        return SubmissionGateDecision(outcome=SubmissionGateOutcome.PREVIEW)
    if not value.submission_enabled:
        reasons.append("submission_disabled")
    if value.run_mode is RunMode.ASSISTED and not value.approval_granted:
        reasons.append("approval_required")
    if value.daily_submissions >= value.daily_limit:
        reasons.append("daily_limit_reached")
    if reasons:
        return SubmissionGateDecision(
            outcome=SubmissionGateOutcome.BLOCKED, reasons=tuple(reasons)
        )
    return SubmissionGateDecision(outcome=SubmissionGateOutcome.ALLOWED)


class ProposalSubmitter:
    """The only component allowed to click Karlancer's final submit control."""

    def __init__(
        self,
        *,
        settings: Settings,
        repository: ProjectRepository,
        validator: ProposalValidator,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        confirmation_timeout_ms: int = 15_000,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._validator = validator
        self._clock = clock
        self._confirmation_timeout_ms = confirmation_timeout_ms
        self._evidence_dir = settings.evidence_dir.resolve()
        profile = settings.browser_profile_dir.resolve()
        if self._evidence_dir.parent == self._evidence_dir:
            raise ValueError("evidence directory cannot be a filesystem root")
        if self._evidence_dir == profile or self._evidence_dir.is_relative_to(profile):
            raise ValueError("evidence directory must be outside the browser profile")

    async def submit(
        self, page: SubmissionPage, command: SubmissionCommand
    ) -> SubmissionResult:
        material = await self._repository.submission_material(command.project_id)
        if material is None:
            return self._blocked(command.project_id, "submission_material_missing")
        current_state = ProjectState(str(material["state"]))
        project = _project_from_material(material)
        body = str(material["body"])
        if not project.is_open:
            return self._blocked(command.project_id, "project_closed", current_state)
        if project.already_submitted:
            return self._blocked(command.project_id, "already_submitted", current_state)
        validation = self._validator.validate(body=body, project=project)
        stored_validation = material.get("validator_result")
        stored_valid = isinstance(stored_validation, dict) and bool(
            stored_validation.get("is_valid")
        )
        now = self._clock()
        since = now - timedelta(hours=24)
        recent = await self._repository.count_recent_submissions(since=since)
        gate = evaluate_submission_gate(
            SubmissionGateInput(
                run_mode=self._settings.run_mode,
                submission_enabled=self._settings.submission_enabled,
                project_state=current_state,
                approval_granted=material.get("approved_at") is not None,
                validation_passed=validation.is_valid and stored_valid,
                budget_max=project.budget_max,
                duration_value=project.duration_value,
                duration_unit=project.duration_unit,
                daily_submissions=recent,
                daily_limit=self._settings.daily_submission_limit,
            )
        )
        if gate.outcome is SubmissionGateOutcome.BLOCKED:
            return self._blocked(
                command.project_id, ",".join(gate.reasons), current_state
            )
        if gate.outcome is SubmissionGateOutcome.PREVIEW:
            await page.goto(str(project.proposal_form_url), wait_until="domcontentloaded")
            preflight_error = await _browser_preflight(page)
            if preflight_error:
                return self._blocked(command.project_id, preflight_error, current_state)
            readback_matches = await self._fill_and_verify(
                page, project=project, body=body
            )
            if not readback_matches:
                return self._blocked(
                    command.project_id, "form_readback_mismatch", current_state
                )
            evidence = await self._capture(page, command.project_id, "would-submit")
            await self._repository.record_shadow_preview(
                command.project_id,
                evidence_hash=evidence.digest,
                screenshot_path=str(evidence.screenshot_path),
                html_path=str(evidence.html_path),
            )
            return SubmissionResult(
                project_id=command.project_id,
                status="would_submit",
                state=current_state,
                evidence_hash=evidence.digest,
                screenshot_path=str(evidence.screenshot_path),
                html_path=str(evidence.html_path),
            )

        allowed_states = (
            (ProjectState.SUBMIT_PENDING,)
            if self._settings.run_mode is RunMode.ASSISTED
            else (ProjectState.DRAFTED, ProjectState.SUBMIT_PENDING)
        )
        reservation = await self._repository.reserve_ready_submission(
            command.project_id,
            key=command.idempotency_key,
            daily_limit=self._settings.daily_submission_limit,
            since=since,
            allowed_states=allowed_states,
            form_budget=project.budget_max,
            duration_value=project.duration_value,
            duration_unit=project.duration_unit,
        )
        if reservation != "reserved":
            reason = {
                "duplicate": "duplicate_submission",
                "daily_limit": "daily_limit_reached",
                "state_conflict": "invalid_project_state",
            }[reservation]
            return self._blocked(command.project_id, reason, current_state)

        try:
            await page.goto(str(project.proposal_form_url), wait_until="domcontentloaded")
            preflight_error = await _browser_preflight(page)
            if preflight_error:
                return await self._pause(
                    page,
                    command.project_id,
                    reason=preflight_error,
                    label="preflight",
                )
            readback_matches = await self._fill_and_verify(
                page, project=project, body=body
            )
        except Exception:
            return await self._pause(
                page,
                command.project_id,
                reason="form_preparation_failed",
                label="preparation-failed",
            )
        if not readback_matches:
            return await self._pause(
                page, command.project_id, reason="form_readback_mismatch", label="mismatch"
            )

        try:
            await page.locator(FINAL_SUBMIT_CONTROL).click()
            await page.wait_for_selector(
                BID_MODAL,
                state="hidden",
                timeout=self._confirmation_timeout_ms,
            )
            await page.goto(str(project.canonical_url), wait_until="domcontentloaded")
            if not _has_submission_confirmation(await page.content()):
                raise RuntimeError("submission confirmation marker missing")
        except Exception:
            return await self._pause(
                page,
                command.project_id,
                reason="ambiguous_after_click",
                label="ambiguous",
            )

        try:
            evidence = await self._capture(page, command.project_id, "submitted")
        except Exception:
            await self._repository.pause_submission(
                command.project_id,
                reason="success_evidence_failed",
                evidence_url=page.url,
                evidence_hash=None,
            )
            return SubmissionResult(
                project_id=command.project_id,
                status="paused_error",
                state=ProjectState.PAUSED_ERROR,
                reason="success_evidence_failed",
                evidence_url=page.url,
            )
        completed = await self._repository.complete_submission(
            command.project_id,
            submitted_at=self._clock(),
            evidence_url=page.url,
            evidence_hash=evidence.digest,
        )
        if not completed:
            return await self._pause(
                page,
                command.project_id,
                reason="success_persistence_conflict",
                label="persistence-conflict",
            )
        return SubmissionResult(
            project_id=command.project_id,
            status="submitted",
            state=ProjectState.SUBMITTED,
            evidence_hash=evidence.digest,
            screenshot_path=str(evidence.screenshot_path),
            html_path=str(evidence.html_path),
            evidence_url=page.url,
        )

    async def _fill_and_verify(
        self, page: SubmissionPage, *, project: ExtractedProject, body: str
    ) -> bool:
        await page.locator(SUBMIT_CONTROL).click()
        continue_control = page.locator(CONTINUE_TO_BID_CONTROL)
        if await continue_control.is_visible():
            await continue_control.click()
        await page.locator(PROPOSAL_BODY_INPUT).fill(body)
        await page.locator(PROPOSAL_BUDGET_INPUT).fill(_decimal_text(project.budget_max))
        await page.locator(PROPOSAL_DURATION_INPUT).fill(
            _decimal_text(project.duration_value)
        )
        return await self._readback_matches(page, project=project, body=body)

    async def _readback_matches(
        self, page: SubmissionPage, *, project: ExtractedProject, body: str
    ) -> bool:
        current_body = await page.locator(PROPOSAL_BODY_INPUT).input_value()
        current_budget = await page.locator(PROPOSAL_BUDGET_INPUT).input_value()
        current_duration = await page.locator(PROPOSAL_DURATION_INPUT).input_value()
        return (
            current_body == body
            and _normalized_number(current_budget) == project.budget_max
            and _normalized_number(current_duration) == project.duration_value
        )

    async def _pause(
        self, page: SubmissionPage, project_id: UUID, *, reason: str, label: str
    ) -> SubmissionResult:
        try:
            evidence = await self._capture(page, project_id, label)
        except Exception:
            await self._repository.pause_submission(
                project_id,
                reason=reason,
                evidence_url=page.url,
                evidence_hash=None,
            )
            return SubmissionResult(
                project_id=project_id,
                status="paused_error",
                state=ProjectState.PAUSED_ERROR,
                reason=reason,
                evidence_url=page.url,
            )
        await self._repository.pause_submission(
            project_id,
            reason=reason,
            evidence_url=page.url,
            evidence_hash=evidence.digest,
        )
        return SubmissionResult(
            project_id=project_id,
            status="paused_error",
            state=ProjectState.PAUSED_ERROR,
            reason=reason,
            evidence_hash=evidence.digest,
            screenshot_path=str(evidence.screenshot_path),
            html_path=str(evidence.html_path),
            evidence_url=page.url,
        )

    async def _capture(
        self, page: SubmissionPage, project_id: UUID, label: str
    ) -> "_Evidence":
        self._evidence_dir.mkdir(parents=True, exist_ok=True)
        _remove_expired_evidence(
            self._evidence_dir,
            before=self._clock() - timedelta(days=self._settings.evidence_retention_days),
        )
        stamp = self._clock().strftime("%Y%m%dT%H%M%SZ")
        base = f"{project_id}-{stamp}-{label}"
        screenshot_path = self._evidence_dir / f"{base}.png"
        html_path = self._evidence_dir / f"{base}.html"
        screenshot = await page.screenshot(full_page=True)
        sanitized_html = _sanitize_html(await page.content())
        screenshot_path.write_bytes(screenshot)
        html_path.write_text(sanitized_html, encoding="utf-8")
        digest = hashlib.sha256(screenshot + sanitized_html.encode()).hexdigest()
        return _Evidence(
            digest=digest,
            screenshot_path=screenshot_path,
            html_path=html_path,
        )

    @staticmethod
    def _blocked(
        project_id: UUID,
        reason: str,
        state: ProjectState = ProjectState.PAUSED_ERROR,
    ) -> SubmissionResult:
        return SubmissionResult(
            project_id=project_id,
            status="blocked",
            state=state,
            reason=reason,
        )


class _Evidence(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    digest: str
    screenshot_path: Path
    html_path: Path


def _project_from_material(material: dict[str, Any]) -> ExtractedProject:
    fields = {
        "project_id": material["project_id"],
        "canonical_url": material["project_url"],
        "title": material["title"],
        "description": material["description"],
        "skills": tuple(material.get("skills", ())),
        "budget_text": material["budget_text"],
        "budget_min": material["budget_min"],
        "budget_max": material["budget_max"],
        "currency": material["currency"],
        "duration_text": material["duration_text"],
        "duration_value": material["duration_value"],
        "duration_unit": material["duration_unit"],
        "is_open": material["is_open"],
        "already_submitted": material.get("already_submitted", False),
        "employer_display_id": material.get("employer_display_id"),
        "proposal_form_url": material["proposal_form_url"],
    }
    return ExtractedProject.model_validate(fields)


def _decimal_text(value: Decimal) -> str:
    return format(value, "f").rstrip("0").rstrip(".") if "." in format(value, "f") else str(value)


def _normalized_number(value: str) -> Decimal | None:
    normalized = value.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    normalized = re.sub(r"[,٬،\s]", "", normalized)
    try:
        return Decimal(normalized)
    except Exception:  # Invalid browser text is a safe mismatch, not an exception path.
        return None


def _sanitize_html(value: str) -> str:
    soup = BeautifulSoup(value, "lxml")
    for tag in soup.select("script, input[type='hidden'], meta[name*='csrf' i]"):
        tag.decompose()
    for tag in soup.select("input, textarea, select"):
        tag.attrs.pop("value", None)
        if tag.name == "textarea":
            tag.clear()
        for attribute in tuple(tag.attrs):
            if any(marker in attribute.lower() for marker in ("token", "secret", "csrf")):
                tag.attrs.pop(attribute, None)
    return str(soup)


def _has_submission_confirmation(value: str) -> bool:
    text = BeautifulSoup(value, "lxml").get_text(" ", strip=True)
    return bool(
        re.search(r"پیشنهاد شما.{0,40}ثبت (?:شده|شد)", text)
        or "پیشنهاد ارسال شده" in text
    )


async def _browser_preflight(page: SubmissionPage) -> str | None:
    """Detect obvious expired-login and anti-bot pages before touching the form."""

    html = (await page.content()).lower()
    url = page.url.lower()
    if "/login" in url or re.search(r"<input[^>]+type=[\"']password[\"']", html):
        return "login_required"
    challenge_markers = ("g-recaptcha", "h-captcha", "hcaptcha", "cf-chl-", "کپچا")
    if any(marker in html for marker in challenge_markers):
        return "browser_challenge"
    if any(marker in html for marker in ("پیشنهاد شما ثبت شده", "قبلاً پیشنهاد")):
        return "already_submitted"
    if any(marker in html for marker in ("پروژه بسته شده", "پایان یافته")):
        return "project_closed"
    return None


def _remove_expired_evidence(directory: Path, *, before: datetime) -> None:
    cutoff = before.timestamp()
    patterns = (
        "????????-????-????-????-????????????-*-*.png",
        "????????-????-????-????-????????????-*-*.html",
    )
    for pattern in patterns:
        for path in directory.glob(pattern):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
