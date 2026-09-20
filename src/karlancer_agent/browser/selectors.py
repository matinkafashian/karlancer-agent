"""Central selectors grounded in a real Karlancer project page."""

PROJECT_ROOT = "app-project-card[type='project-bids']"
PROJECT_TITLE = f"{PROJECT_ROOT} h1"
PROJECT_DESCRIPTION = f"{PROJECT_ROOT} .white-space-pre-line"
PROJECT_SKILLS = f"{PROJECT_ROOT} h3 a[href*='/search?q=']"
SUBMIT_CONTROL = f"{PROJECT_ROOT} [id^='setBidBtn']"
VISIBLE_SUBMIT_CONTROL = f"{SUBMIT_CONTROL}:visible"
CANONICAL_LINK = "link[rel='canonical']"

# Submission selectors stay centralized so one verified fixture update can repair a DOM change.
BID_MODAL = "#setBidModal"
BID_MODAL_TRIGGER = 'app-setbid-modal button[data-target="#setBidModal"]'
CONTINUE_TO_BID_CONTROL = "#setBidModal button:has-text('ادامه و ثبت پیشنهاد')"
PROPOSAL_BODY_INPUT = "#setBidModal textarea[placeholder*='روند انجام پروژه']"
PROPOSAL_BUDGET_INPUT = "#setBidModal input[placeholder='قیمت']"
PROPOSAL_DURATION_INPUT = "#setBidModal input[placeholder='تعداد روز']"
FINAL_SUBMIT_CONTROL = "#setBidModal #submitBid"
BASIC_SUBMIT_CONFIRM_CONTROL = (
    "#setBidModal div.clickable:text-is('ثبت پیشنهاد بدون ارتقا')"
)
SUBMISSION_SUCCESS_MESSAGE = (
    "#setBidModal :text-is('پیشنهاد شما با موفقیت ثبت شد')"
)
