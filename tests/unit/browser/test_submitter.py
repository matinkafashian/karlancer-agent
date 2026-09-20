# ruff: noqa: RUF001

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from karlancer_agent.browser.selectors import (
    FINAL_SUBMIT_CONTROL,
    PROPOSAL_BODY_INPUT,
    PROPOSAL_BUDGET_INPUT,
    PROPOSAL_DURATION_INPUT,
    VISIBLE_SUBMIT_CONTROL,
)
from karlancer_agent.browser.submitter import (
    ProposalSubmitter,
    SubmissionCommand,
    SubmissionGateInput,
    SubmissionGateOutcome,
    _browser_preflight,
    evaluate_submission_gate,
)
from karlancer_agent.config import RunMode, Settings
from karlancer_agent.domain.enums import ProjectState
from karlancer_agent.proposal.templates import FIXED_INTRO, FIXED_OUTRO, PRICE_SENTENCE
from karlancer_agent.proposal.validator import ProposalValidator

NOW = datetime(2026, 9, 8, 13, 0, tzinfo=UTC)
BID_MODAL_SELECTOR = "#setBidModal"
CONTINUE_TO_BID_SELECTOR = (
    "#setBidModal button:has-text('ادامه و ثبت پیشنهاد')"
)
BASIC_SUBMIT_CONFIRM_SELECTOR = (
    "#setBidModal div.clickable:text-is('ثبت پیشنهاد بدون ارتقا')"
)
SUBMISSION_SUCCESS_SELECTOR = (
    "#setBidModal :text-is('پیشنهاد شما با موفقیت ثبت شد')"
)


def settings(
    tmp_path: Path, *, mode: RunMode = RunMode.ASSISTED, enabled: bool = True
) -> Settings:
    return Settings(
        _env_file=None,
        run_mode=mode,
        submission_enabled=enabled,
        database_url="sqlite+aiosqlite:///:memory:",
        telegram_bot_token="secret",
        telegram_chat_id=1,
        internal_api_key="internal-secret-key",
        approval_signing_key="approval-signing-key-with-32-characters",
        evidence_dir=tmp_path,
    )


def valid_body() -> str:
    understanding = (
        "برای این ربات تلگرام، پیام‌ها با وب‌هوک دریافت و اطلاعات کاربران در PostgreSQL "
        "ذخیره می‌شود تا پس از راه‌اندازی دوباره داده‌ای از بین نرود."
    )
    approach = (
        "کار را با پایتون و ساختار ماژولار انجام می‌دهم؛ پردازش پیام و پایگاه داده جدا "
        "خواهند بود. مسیرهای تست و راهنمای استقرار همراه کد تحویل می‌شود."
    )
    return f"{FIXED_INTRO}\n\n{understanding}\n\n{approach}\n\n{PRICE_SENTENCE}\n\n{FIXED_OUTRO}"


def test_submit_selector_targets_only_the_visible_responsive_copy() -> None:
    from karlancer_agent.browser.selectors import VISIBLE_SUBMIT_CONTROL

    assert VISIBLE_SUBMIT_CONTROL.endswith(":visible")


def material(*, state=ProjectState.SUBMIT_PENDING, approved=True, body=None):
    return {
        "project_id": "322263",
        "project_url": "https://www.karlancer.com/projects/322263/bot",
        "proposal_form_url": "https://www.karlancer.com/projects/322263/bot#proposal",
        "title": "ساخت ربات تلگرام با پایتون",
        "description": "دریافت پیام با وب‌هوک و ذخیره در PostgreSQL",
        "skills": ["Python", "Telegram Bot", "PostgreSQL"],
        "budget_text": "۱ تا ۳ میلیون تومان",
        "budget_min": "1000000",
        "budget_max": "3000000",
        "currency": "TOMAN",
        "duration_text": "۳ روز",
        "duration_value": "3",
        "duration_unit": "day",
        "is_open": True,
        "already_submitted": False,
        "employer_display_id": None,
        "body": body or valid_body(),
        "validator_result": {"is_valid": True},
        "approved_at": NOW if approved else None,
        "state": state.value,
    }


@pytest.mark.parametrize(
    ("gate", "expected", "reason"),
    (
        (
            SubmissionGateInput(
                run_mode=RunMode.SHADOW,
                submission_enabled=True,
                project_state=ProjectState.DRAFTED,
                approval_granted=False,
                validation_passed=True,
                budget_max=Decimal("3"),
                duration_value=Decimal("1"),
                duration_unit="day",
                daily_submissions=30,
                daily_limit=30,
            ),
            SubmissionGateOutcome.PREVIEW,
            None,
        ),
        (
            SubmissionGateInput(
                run_mode=RunMode.ASSISTED,
                submission_enabled=False,
                project_state=ProjectState.SUBMIT_PENDING,
                approval_granted=True,
                validation_passed=True,
                budget_max=Decimal("3"),
                duration_value=Decimal("1"),
                duration_unit="day",
                daily_submissions=0,
                daily_limit=30,
            ),
            SubmissionGateOutcome.BLOCKED,
            "submission_disabled",
        ),
        (
            SubmissionGateInput(
                run_mode=RunMode.ASSISTED,
                submission_enabled=True,
                project_state=ProjectState.SUBMIT_PENDING,
                approval_granted=False,
                validation_passed=True,
                budget_max=Decimal("3"),
                duration_value=Decimal("1"),
                duration_unit="day",
                daily_submissions=0,
                daily_limit=30,
            ),
            SubmissionGateOutcome.BLOCKED,
            "approval_required",
        ),
        (
            SubmissionGateInput(
                run_mode=RunMode.AUTO,
                submission_enabled=True,
                project_state=ProjectState.DRAFTED,
                approval_granted=False,
                validation_passed=True,
                budget_max=Decimal("3"),
                duration_value=Decimal("1"),
                duration_unit="day",
                daily_submissions=0,
                daily_limit=30,
            ),
            SubmissionGateOutcome.ALLOWED,
            None,
        ),
    ),
)
def test_mode_flag_and_approval_gate_matrix(gate, expected, reason) -> None:
    decision = evaluate_submission_gate(gate)
    assert decision.outcome is expected
    assert reason is None or reason in decision.reasons


@pytest.mark.parametrize(
    ("changes", "reason"),
    (
        ({"validation_passed": False}, "invalid_proposal"),
        ({"budget_max": None}, "missing_budget"),
        ({"duration_value": None}, "missing_duration"),
        ({"duration_unit": None}, "missing_duration"),
        ({"duration_unit": "week"}, "unsupported_duration_unit"),
        ({"daily_submissions": 30}, "daily_limit_reached"),
        ({"project_state": ProjectState.REJECTED}, "invalid_project_state"),
    ),
)
def test_hard_submission_gates_block(changes, reason) -> None:
    gate = SubmissionGateInput(
        run_mode=RunMode.ASSISTED,
        submission_enabled=True,
        project_state=ProjectState.SUBMIT_PENDING,
        approval_granted=True,
        validation_passed=True,
        budget_max=Decimal("3"),
        duration_value=Decimal("1"),
        duration_unit="day",
        daily_submissions=0,
        daily_limit=30,
    ).model_copy(update=changes)
    decision = evaluate_submission_gate(gate)
    assert decision.outcome is SubmissionGateOutcome.BLOCKED
    assert reason in decision.reasons


class FakeRepository:
    def __init__(
        self, *, material_data=None, reservation="reserved", recent_submissions=0
    ) -> None:
        self.material_data = material_data or material()
        self.reservation = reservation
        self.recent_submissions = recent_submissions
        self.events: list[tuple[str, object]] = []

    async def submission_material(self, project_id):
        self.events.append(("material", project_id))
        return self.material_data

    async def count_recent_submissions(self, *, since):
        self.events.append(("count", since))
        return self.recent_submissions

    async def reserve_ready_submission(self, project_id, **kwargs):
        self.events.append(("reserve", project_id))
        return self.reservation

    async def record_shadow_preview(self, project_id, **kwargs):
        self.events.append(("preview", project_id))

    async def complete_submission(self, project_id, **kwargs):
        self.events.append(("complete", project_id))
        return True

    async def pause_submission(self, project_id, **kwargs):
        self.events.append(("pause", project_id))
        return True


class FakeLocator:
    def __init__(self, page, selector: str) -> None:
        self.page = page
        self.selector = selector

    async def fill(self, value: str) -> None:
        self.page.events.append(("fill", self.selector, value))
        self.page.values[self.selector] = value

    async def input_value(self) -> str:
        if self.selector in self.page.readback_overrides:
            return self.page.readback_overrides[self.selector]
        return self.page.values.get(self.selector, "")

    async def click(self) -> None:
        self.page.events.append(("click", self.selector))
        if self.selector == VISIBLE_SUBMIT_CONTROL:
            visible_clicks = sum(
                event[:2] == ("click", VISIBLE_SUBMIT_CONTROL)
                for event in self.page.events
            )
            if visible_clicks >= 2:
                self.page.bid_modal_open = True
        if self.selector == CONTINUE_TO_BID_SELECTOR:
            self.page.warning_visible = False
        if self.selector == "#setBidModal #submitBid":
            self.page.final_clicked = True

    async def is_visible(self) -> bool:
        self.page.events.append(("visible", self.selector))
        if self.selector == SUBMISSION_SUCCESS_SELECTOR:
            return self.page.success_visible
        if self.selector == CONTINUE_TO_BID_SELECTOR:
            return self.page.warning_visible
        if self.selector == BASIC_SUBMIT_CONFIRM_SELECTOR:
            return self.page.upsell_visible
        return True


class FakePage:
    url = "https://www.karlancer.com/projects/322263/bot"

    def __init__(
        self,
        *,
        confirms=True,
        html='<html><p>safe</p></html>',
        screenshot_fails=False,
        warning_visible=True,
        upsell_visible=False,
        modal_hides=True,
        success_visible=False,
        success_html='<html><p>پیشنهاد شما ثبت شده است</p></html>',
        bid_modal_open=True,
        public_status=None,
    ) -> None:
        self.confirms = confirms
        self.html = html
        self.screenshot_fails = screenshot_fails
        self.warning_visible = warning_visible
        self.upsell_visible = upsell_visible
        self.modal_hides = modal_hides
        self.success_visible = success_visible
        self.final_clicked = False
        self.success_html = success_html
        self.bid_modal_open = bid_modal_open
        self.public_status = public_status
        self.events: list[tuple] = []
        self.values: dict[str, str] = {}
        self.readback_overrides: dict[str, str] = {}

    async def goto(self, url: str, **kwargs) -> None:
        self.events.append(("goto", url))
        self.url = url
        if self.final_clicked and self.confirms and self.success_html is not None:
            self.html = self.success_html

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    async def wait_for_selector(self, selector: str, **kwargs) -> None:
        self.events.append(("wait", selector, kwargs.get("state")))
        if (
            selector == BID_MODAL_SELECTOR
            and kwargs.get("state") == "visible"
            and not self.bid_modal_open
        ):
            raise PlaywrightTimeoutError("bid modal did not open")
        if selector == SUBMISSION_SUCCESS_SELECTOR and kwargs.get("state") == "visible":
            if self.success_visible:
                return
            raise PlaywrightTimeoutError("success message missing")
        if (
            (not self.confirms or not self.modal_hides)
            and self.final_clicked
            and kwargs.get("state") == "hidden"
        ):
            raise PlaywrightTimeoutError("confirmation missing")

    async def screenshot(self, **kwargs) -> bytes:
        self.events.append(("screenshot", kwargs.get("full_page")))
        if self.screenshot_fails:
            raise OSError("evidence disk unavailable")
        return b"fake-png-evidence"

    async def content(self) -> str:
        return self.html

    async def evaluate(self, expression: str) -> object:
        self.events.append(("page_evaluate", expression))
        return self.public_status


@pytest.mark.asyncio
async def test_submission_retries_visible_control_when_first_click_does_not_open(
    tmp_path,
):
    repository = FakeRepository()
    page = FakePage(bid_modal_open=False)
    submitter = ProposalSubmitter(
        settings=settings(tmp_path, mode=RunMode.AUTO),
        repository=repository,
        validator=ProposalValidator(),
        clock=lambda: NOW,
    )

    result = await submitter.submit(
        page,
        SubmissionCommand(
            project_id=uuid4(), idempotency_key="submit:fallback-trigger"
        ),
    )

    assert result.status == "submitted"
    assert sum(
        event[:2] == ("click", VISIBLE_SUBMIT_CONTROL) for event in page.events
    ) == 2
    assert not any(event[0] == "evaluate" for event in page.events)


class FakeLimitAlerts:
    def __init__(self) -> None:
        self.alerts: list[dict[str, object]] = []

    async def notify(self, **kwargs) -> bool:
        self.alerts.append(kwargs)
        return True


@pytest.mark.asyncio
async def test_real_submit_reserves_before_browser_and_uses_maximum_budget(
    tmp_path: Path,
) -> None:
    repository = FakeRepository()
    page = FakePage(html='<html><input name="csrf" value="private-token"><p>safe</p></html>')
    result = await ProposalSubmitter(
        settings=settings(tmp_path),
        repository=repository,
        validator=ProposalValidator(),
        clock=lambda: NOW,
    ).submit(page, SubmissionCommand(project_id=uuid4(), idempotency_key="submit:322263:v1"))

    assert result.status == "submitted"
    assert repository.events[0][0] == "material"
    assert [event[0] for event in repository.events][:3] == ["material", "count", "reserve"]
    assert repository.events[-1][0] == "complete"
    assert page.events[0][0] == "goto"
    assert ("fill", PROPOSAL_BODY_INPUT, valid_body()) in page.events
    assert ("fill", PROPOSAL_BUDGET_INPUT, "3000000") in page.events
    assert ("fill", PROPOSAL_DURATION_INPUT, "3") in page.events
    assert ("click", CONTINUE_TO_BID_SELECTOR) in page.events
    assert ("click", FINAL_SUBMIT_CONTROL) in page.events
    assert ("wait", BID_MODAL_SELECTOR, "hidden") in page.events
    assert page.events.index(("click", FINAL_SUBMIT_CONTROL)) < next(
        index
        for index, event in enumerate(page.events)
        if event[0] == "goto" and index > 0
    )


@pytest.mark.asyncio
async def test_form_filling_works_when_one_time_warning_is_already_disabled(
    tmp_path: Path,
) -> None:
    repository = FakeRepository()
    page = FakePage(warning_visible=False)
    result = await ProposalSubmitter(
        settings=settings(tmp_path),
        repository=repository,
        validator=ProposalValidator(),
        clock=lambda: NOW,
    ).submit(
        page,
        SubmissionCommand(project_id=uuid4(), idempotency_key="submit:no-warning"),
    )

    assert result.status == "submitted"
    assert ("visible", CONTINUE_TO_BID_SELECTOR) in page.events
    assert ("click", CONTINUE_TO_BID_SELECTOR) not in page.events


@pytest.mark.asyncio
async def test_duplicate_reservation_causes_no_browser_action(tmp_path: Path) -> None:
    repository = FakeRepository(reservation="duplicate")
    page = FakePage(html='<html><input name="csrf" value="private-token"><p>safe</p></html>')
    result = await ProposalSubmitter(
        settings=settings(tmp_path),
        repository=repository,
        validator=ProposalValidator(),
        clock=lambda: NOW,
    ).submit(page, SubmissionCommand(project_id=uuid4(), idempotency_key="submit:duplicate"))
    assert result.status == "blocked"
    assert result.reason == "duplicate_submission"
    assert page.events == []


@pytest.mark.asyncio
async def test_daily_submission_limit_reports_once_without_browser_action(
    tmp_path: Path,
) -> None:
    alerts = FakeLimitAlerts()
    repository = FakeRepository(recent_submissions=30)
    page = FakePage()

    result = await ProposalSubmitter(
        settings=settings(tmp_path),
        repository=repository,
        validator=ProposalValidator(),
        limit_alerts=alerts,
        clock=lambda: NOW,
    ).submit(
        page,
        SubmissionCommand(project_id=uuid4(), idempotency_key="submit:daily-limit"),
    )

    assert result.status == "blocked"
    assert result.reason == "daily_limit_reached"
    assert page.events == []
    assert [alert["reason"] for alert in alerts.alerts] == ["daily_limit_reached"]


@pytest.mark.asyncio
async def test_karlancer_quota_message_pauses_before_final_click_and_reports(
    tmp_path: Path,
) -> None:
    alerts = FakeLimitAlerts()
    repository = FakeRepository()
    page = FakePage(
        html="<div>سهمیه ارسال پیشنهاد شما به پایان رسیده است؛ پلن خود را ارتقا دهید.</div>"
    )

    result = await ProposalSubmitter(
        settings=settings(tmp_path),
        repository=repository,
        validator=ProposalValidator(),
        limit_alerts=alerts,
        clock=lambda: NOW,
    ).submit(
        page,
        SubmissionCommand(project_id=uuid4(), idempotency_key="submit:site-limit"),
    )

    assert result.status == "paused_error"
    assert result.reason == "platform_submission_limit"
    assert ("click", FINAL_SUBMIT_CONTROL) not in page.events
    assert repository.events[-1][0] == "pause"
    assert [alert["reason"] for alert in alerts.alerts] == [
        "platform_submission_limit"
    ]


@pytest.mark.asyncio
async def test_shadow_writes_sanitized_evidence_and_never_clicks_submit(
    tmp_path: Path,
) -> None:
    repository = FakeRepository(
        material_data=material(state=ProjectState.DRAFTED, approved=False)
    )
    page = FakePage(html='<html><input name="csrf" value="private-token"><p>safe</p></html>')
    result = await ProposalSubmitter(
        settings=settings(tmp_path, mode=RunMode.SHADOW, enabled=True),
        repository=repository,
        validator=ProposalValidator(),
        clock=lambda: NOW,
    ).submit(page, SubmissionCommand(project_id=uuid4(), idempotency_key="shadow:322263"))

    assert result.status == "would_submit"
    assert ("fill", PROPOSAL_BODY_INPUT, valid_body()) in page.events
    assert ("fill", PROPOSAL_BUDGET_INPUT, "3000000") in page.events
    assert not any(event == ("click", FINAL_SUBMIT_CONTROL) for event in page.events)
    assert not any(event[0] == "reserve" for event in repository.events)
    assert Path(result.html_path).is_relative_to(tmp_path)
    assert "private-token" not in Path(result.html_path).read_text(encoding="utf-8")
    assert any(event[0] == "preview" for event in repository.events)


@pytest.mark.asyncio
async def test_formatted_numeric_readback_is_accepted_before_final_click(
    tmp_path: Path,
) -> None:
    repository = FakeRepository()
    page = FakePage()
    page.readback_overrides[PROPOSAL_BUDGET_INPUT] = "3,000,000 تومان"
    page.readback_overrides[PROPOSAL_DURATION_INPUT] = "3 روز"

    result = await ProposalSubmitter(
        settings=settings(tmp_path),
        repository=repository,
        validator=ProposalValidator(),
        clock=lambda: NOW,
    ).submit(
        page,
        SubmissionCommand(project_id=uuid4(), idempotency_key="submit:formatted-readback"),
    )

    assert result.status == "submitted"
    assert ("click", FINAL_SUBMIT_CONTROL) in page.events


@pytest.mark.asyncio
async def test_basic_submission_confirms_the_optional_upgrade_prompt(
    tmp_path: Path,
) -> None:
    repository = FakeRepository()
    page = FakePage(upsell_visible=True)

    result = await ProposalSubmitter(
        settings=settings(tmp_path),
        repository=repository,
        validator=ProposalValidator(),
        clock=lambda: NOW,
    ).submit(
        page,
        SubmissionCommand(project_id=uuid4(), idempotency_key="submit:no-upgrade"),
    )

    assert result.status == "submitted"
    assert ("click", BASIC_SUBMIT_CONFIRM_SELECTOR) in page.events


@pytest.mark.asyncio
async def test_visible_success_message_confirms_when_modal_remains_open(
    tmp_path: Path,
) -> None:
    repository = FakeRepository()
    page = FakePage(
        upsell_visible=True,
        modal_hides=False,
        success_visible=True,
    )

    result = await ProposalSubmitter(
        settings=settings(tmp_path),
        repository=repository,
        validator=ProposalValidator(),
        clock=lambda: NOW,
    ).submit(
        page,
        SubmissionCommand(project_id=uuid4(), idempotency_key="submit:success-modal"),
    )

    assert result.status == "submitted"
    assert ("wait", SUBMISSION_SUCCESS_SELECTOR, "visible") in page.events


@pytest.mark.asyncio
async def test_late_visible_success_after_wait_timeout_is_not_paused(tmp_path):
    class LateSuccessPage(FakePage):
        async def wait_for_selector(self, selector, **kwargs):
            if selector == BID_MODAL_SELECTOR and kwargs.get("state") == "hidden":
                self.success_visible = True
                raise PlaywrightTimeoutError("success modal stayed open")
            return await super().wait_for_selector(selector, **kwargs)

    repository = FakeRepository()
    page = LateSuccessPage(confirms=False, modal_hides=False)
    result = await ProposalSubmitter(
        settings=settings(tmp_path), repository=repository,
        validator=ProposalValidator(), clock=lambda: NOW,
    ).submit(page, SubmissionCommand(project_id=uuid4(), idempotency_key="late-success"))
    assert result.status == "submitted"
    assert page.events.count(("click", FINAL_SUBMIT_CONTROL)) == 1
    assert repository.events[-1][0] == "complete"


@pytest.mark.asyncio
async def test_readback_mismatch_stops_before_final_click(tmp_path: Path) -> None:
    repository = FakeRepository()
    page = FakePage()
    page.readback_overrides[PROPOSAL_BUDGET_INPUT] = "2999999"
    result = await ProposalSubmitter(
        settings=settings(tmp_path),
        repository=repository,
        validator=ProposalValidator(),
        clock=lambda: NOW,
    ).submit(page, SubmissionCommand(project_id=uuid4(), idempotency_key="submit:mismatch"))
    assert result.status == "paused_error"
    assert result.reason == "form_readback_mismatch"
    assert ("click", FINAL_SUBMIT_CONTROL) not in page.events
    assert repository.events[-1][0] == "pause"


@pytest.mark.asyncio
async def test_timeout_after_final_click_is_ambiguous_paused_and_not_retried(
    tmp_path: Path,
) -> None:
    repository = FakeRepository()
    page = FakePage(confirms=False)
    result = await ProposalSubmitter(
        settings=settings(tmp_path),
        repository=repository,
        validator=ProposalValidator(),
        clock=lambda: NOW,
    ).submit(page, SubmissionCommand(project_id=uuid4(), idempotency_key="submit:timeout"))
    assert result.status == "paused_error"
    assert result.reason == "ambiguous_after_click"
    assert page.events.count(("click", FINAL_SUBMIT_CONTROL)) == 1
    assert repository.events[-1][0] == "pause"


@pytest.mark.asyncio
async def test_success_accepts_karlancer_confirmation_with_intervening_words(
    tmp_path: Path,
) -> None:
    repository = FakeRepository()
    page = FakePage(
        success_html='<html><p>پیشنهاد شما با موفقیت ثبت شد</p></html>'
    )
    result = await ProposalSubmitter(
        settings=settings(tmp_path),
        repository=repository,
        validator=ProposalValidator(),
        clock=lambda: NOW,
    ).submit(
        page,
        SubmissionCommand(project_id=uuid4(), idempotency_key="submit:success-text"),
    )

    assert result.status == "submitted"
    assert repository.events[-1][0] == "complete"


@pytest.mark.asyncio
async def test_hidden_modal_without_confirmation_marker_is_ambiguous(
    tmp_path: Path,
) -> None:
    repository = FakeRepository()
    page = FakePage(success_html=None)
    result = await ProposalSubmitter(
        settings=settings(tmp_path),
        repository=repository,
        validator=ProposalValidator(),
        clock=lambda: NOW,
    ).submit(
        page,
        SubmissionCommand(project_id=uuid4(), idempotency_key="submit:no-marker"),
    )

    assert result.status == "paused_error"
    assert result.reason == "ambiguous_after_click"
    assert not any(event[0] == "complete" for event in repository.events)


@pytest.mark.asyncio
async def test_success_without_evidence_is_paused_and_never_marked_submitted(
    tmp_path: Path,
) -> None:
    repository = FakeRepository()
    page = FakePage(screenshot_fails=True)
    result = await ProposalSubmitter(
        settings=settings(tmp_path),
        repository=repository,
        validator=ProposalValidator(),
        clock=lambda: NOW,
    ).submit(page, SubmissionCommand(project_id=uuid4(), idempotency_key="submit:no-proof"))

    assert result.status == "paused_error"
    assert result.reason == "success_evidence_failed"
    assert not any(event[0] == "complete" for event in repository.events)
    assert repository.events[-1][0] == "pause"


@pytest.mark.asyncio
async def test_evidence_retention_removes_only_expired_agent_files(
    tmp_path: Path,
) -> None:
    expired = tmp_path / f"{uuid4()}-20260101T000000Z-old.html"
    unrelated = tmp_path / "keep-me.html"
    expired.write_text("old", encoding="utf-8")
    unrelated.write_text("user-owned", encoding="utf-8")
    old_timestamp = (NOW - timedelta(days=8)).timestamp()
    os.utime(expired, (old_timestamp, old_timestamp))

    repository = FakeRepository(
        material_data=material(state=ProjectState.DRAFTED, approved=False)
    )
    await ProposalSubmitter(
        settings=settings(tmp_path, mode=RunMode.SHADOW),
        repository=repository,
        validator=ProposalValidator(),
        clock=lambda: NOW,
    ).submit(
        FakePage(),
        SubmissionCommand(project_id=uuid4(), idempotency_key="shadow:retention"),
    )

    assert not expired.exists()
    assert unrelated.read_text(encoding="utf-8") == "user-owned"


@pytest.mark.asyncio
async def test_invalid_current_body_blocks_before_reservation_or_browser(
    tmp_path: Path,
) -> None:
    repository = FakeRepository(material_data=material(body="سلام"))
    page = FakePage()
    result = await ProposalSubmitter(
        settings=settings(tmp_path),
        repository=repository,
        validator=ProposalValidator(),
        clock=lambda: NOW,
    ).submit(page, SubmissionCommand(project_id=uuid4(), idempotency_key="submit:invalid"))
    assert result.status == "blocked"
    assert "invalid_proposal" in result.reason
    assert [event[0] for event in repository.events] == ["material", "count"]
    assert page.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("material_changes", "html", "reason"),
    (
        ({"is_open": False}, "<html></html>", "project_closed"),
        ({"already_submitted": True}, "<html></html>", "already_submitted"),
        ({}, '<form><input type="password"></form>', "login_required"),
        ({}, '<div class="g-recaptcha">challenge</div>', "browser_challenge"),
    ),
)
async def test_preflight_blocks_closed_duplicate_login_and_challenge(
    tmp_path: Path, material_changes, html: str, reason: str
) -> None:
    data = material()
    data.update(material_changes)
    repository = FakeRepository(material_data=data)
    page = FakePage(html=html)
    result = await ProposalSubmitter(
        settings=settings(tmp_path),
        repository=repository,
        validator=ProposalValidator(),
        clock=lambda: NOW,
    ).submit(page, SubmissionCommand(project_id=uuid4(), idempotency_key=f"submit:{reason}"))

    assert result.status in {"blocked", "paused_error"}
    assert result.reason == reason
    assert ("click", FINAL_SUBMIT_CONTROL) not in page.events


@pytest.mark.asyncio
async def test_preflight_ignores_hidden_login_form_for_authenticated_page() -> None:
    page = FakePage(
        html=(
            '<nav><a href="/panel/dashboard">داشبورد</a></nav>'
            '<div hidden><input type="password"></div>'
        )
    )

    assert await _browser_preflight(page) is None


@pytest.mark.asyncio
async def test_preflight_detects_completed_public_project() -> None:
    page = FakePage(public_status="completed")

    assert await _browser_preflight(page) == "project_closed"
