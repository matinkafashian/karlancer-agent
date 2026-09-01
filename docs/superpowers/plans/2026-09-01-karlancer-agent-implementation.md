# Karlancer Automation Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to execute this plan task by task.

**Goal:** ساخت و اعتبارسنجی یک ایجنت ۲۴ساعته و قابل‌استقرار روی VPS که پروژه‌های کارلنسر را از کانال عمومی تلگرام دریافت کند، پروژه‌های مناسب را تشخیص دهد، پیشنهاد انسانی و منطبق با قواعد کاربر بسازد، با گیت‌های ایمنی ارسال کند و فقط اولین پاسخ کارفرما را به کاربر گزارش دهد.

**Architecture:** n8n زمان‌بندی و هماهنگی workflowها را انجام می‌دهد؛ یک سرویس Python/FastAPI منطق دامنه، طبقه‌بندی، تولید متن و APIهای کنترلی را ارائه می‌کند؛ Playwright با پروفایل دائمی Chromium تمام تعامل‌های احراز هویت‌شده با کارلنسر را انجام می‌دهد؛ PostgreSQL منبع حقیقت و ضامن idempotency است؛ Telegram Bot اعلان‌ها و تأییدهای حالت assisted را منتقل می‌کند. همه اجزا با Docker Compose روی یک VPS اجرا می‌شوند و provider مدل از طریق interface قابل تعویض است.

**Tech Stack:** Python 3.12، FastAPI، Pydantic، SQLAlchemy async، Alembic، PostgreSQL 16، Playwright/Chromium، httpx، BeautifulSoup/lxml، PyYAML، n8n، Docker Compose، pytest/pytest-asyncio، Ruff، mypy.

**Spec:** `docs/superpowers/specs/2026-09-01-karlancer-agent-design.md`

## Global Constraints

- حالت پیش‌فرض `shadow` و مقدار پیش‌فرض `SUBMISSION_ENABLED=false` است.
- ارسال واقعی در `shadow` همیشه ممنوع است. در `assisted` به `SUBMISSION_ENABLED=true` و approval یکتای همان پروژه نیاز دارد؛ در `auto` هر دو شرط `RUN_MODE=auto` و `SUBMISSION_ENABLED=true` لازم‌اند.
- اگر نتیجه ارسال مبهم باشد، retry انجام نمی‌شود و رکورد به `paused_error` می‌رود.
- پیشنهاد اولیه هرگز نباید شماره تلفن، آیدی تلگرام، `t.me` یا درخواست انتقال گفتگو خارج از کارلنسر داشته باشد.
- مبلغ فرم برابر حداکثر بودجه استخراج‌شده و زمان برابر زمان خود پروژه است؛ نبود یا ابهام هرکدام ارسال را متوقف می‌کند.
- پروژه‌های آموزشی مرتبط پذیرفته می‌شوند؛ کارهای صرفاً استفاده از ابزارهای AI برای محتوا، تصویر، ویدئو یا کاراکتر رد می‌شوند.
- پس از اولین پاسخ کارفرما، وضعیت `human_handoff` نهایی است و سیستم هیچ پاسخی در گفتگو نمی‌فرستد.
- سقف ایمنی ارسال در نسخه اول ۳۰ پیشنهاد در ۲۴ ساعت است و تغییر آن نیازمند تنظیم صریح است.
- هیچ secret، cookie، Telegram token یا پروفایل مرورگر وارد Git، image یا log نمی‌شود.
- هر task با تست شکست‌خورده شروع و فقط پس از عبور تست‌ها commit می‌شود.

## Target File Map

```text
.
├── .env.example
├── .gitignore
├── .python-version
├── Dockerfile
├── README.md
├── alembic.ini
├── compose.yaml
├── compose.production.yaml
├── pyproject.toml
├── migrations/
│   ├── env.py
│   └── versions/0001_initial.py
├── src/karlancer_agent/
│   ├── api/{dependencies.py,main.py,routes/approvals.py,routes/health.py,routes/projects.py}
│   ├── browser/{extractor.py,replies.py,selectors.py,session.py,submitter.py}
│   ├── classification/{glossary.yaml,rules.py,service.py}
│   ├── db/{base.py,models.py,repositories.py,session.py}
│   ├── domain/{enums.py,models.py,state_machine.py}
│   ├── ingestion/{base.py,parser.py,telegram_preview.py}
│   ├── llm/{base.py,ollama.py,openai_compatible.py,schemas.py}
│   ├── notifications/telegram.py
│   ├── observability/{health.py,logging.py}
│   ├── proposal/{generator.py,templates.py,validator.py}
│   ├── services/{approval.py,pipeline.py,reply_watcher.py}
│   └── config.py
├── tests/
│   ├── e2e/test_shadow_pipeline.py
│   ├── fixtures/{channel_page.html,project_ai_programming.html,project_tool_only.html,reply_page.html}
│   ├── integration/{test_api.py,test_postgres_repository.py}
│   └── unit/...
├── workflows/n8n/{assisted.json,ingest.json,reply-watch.json,shadow.json}
├── scripts/{backup.sh,login-session.sh,restore.sh,smoke.sh}
└── docs/operations/{acceptance.md,deployment.md,model-evaluation.md,recovery.md,session-login.md}
```

---

## Task 1: Bootstrap Repository and Safe Configuration

**Files:** `.gitignore`, `.python-version`, `pyproject.toml`, `uv.lock`, `.env.example`, `src/karlancer_agent/config.py`, `tests/unit/test_config.py`, `README.md`

Public interface:

```python
class Settings(BaseSettings):
    run_mode: Literal["shadow", "assisted", "auto"] = "shadow"
    submission_enabled: bool = False
    daily_submission_limit: int = 30
    database_url: SecretStr
    telegram_bot_token: SecretStr
    telegram_chat_id: int
    browser_profile_dir: Path = Path("/data/browser-profile")

    @property
    def real_submission_allowed(self) -> bool: ...
```

- [ ] `git init` اجرا شود و `.gitignore` شامل `.env`، `.venv`، `browser-profile/`، `playwright/.auth/`، dumps و cacheها باشد.
- [ ] `tests/unit/test_config.py` نوشته شود تا defaultها، رد config ناقص، ممنوع‌بودن ارسال در shadow و نیاز assisted/auto به kill switch را بررسی کند؛ approval پروژه در submitter جداگانه کنترل شود.
- [ ] `uv run pytest tests/unit/test_config.py -q` اجرا و شکست به علت نبود پیاده‌سازی ثبت شود.
- [ ] پروژه Python 3.12 و dependency groupهای `dev`/`test` در `pyproject.toml` ساخته و با `uv lock` قفل شوند.
- [ ] `Settings` با `pydantic-settings` پیاده‌سازی شود؛ secretها در repr و log مخفی بمانند.
- [ ] تست config، سپس `uv run ruff check .` و `uv run mypy src` اجرا شوند.
- [ ] commit: `chore: bootstrap safe agent configuration`

## Task 2: Domain Models and Explicit State Machine

**Files:** `src/karlancer_agent/domain/enums.py`, `src/karlancer_agent/domain/models.py`, `src/karlancer_agent/domain/state_machine.py`, `tests/unit/domain/test_state_machine.py`

Required types:

```python
class ProjectState(StrEnum):
    DISCOVERED = "discovered"
    EXTRACT_PENDING = "extract_pending"
    EXTRACTED = "extracted"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"
    ELIGIBLE = "eligible"
    DRAFTED = "drafted"
    AWAITING_APPROVAL = "awaiting_approval"
    SUBMIT_PENDING = "submit_pending"
    SUBMITTED = "submitted"
    REPLY_WATCH = "reply_watch"
    REPLY_NOTIFIED = "reply_notified"
    HUMAN_HANDOFF = "human_handoff"
    RETRYABLE_ERROR = "retryable_error"
    PAUSED_ERROR = "paused_error"

def transition(current: ProjectState, target: ProjectState) -> ProjectState: ...
```

- [ ] تست تمام transitionهای مجاز و نمونه‌های ممنوع، مخصوصاً خروج از `human_handoff`، نوشته شود.
- [ ] تست fail اجرا شود.
- [ ] مدل‌های immutable برای `ChannelProject`, `ExtractedProject`, `AnalysisResult`, `ProposalDraft`, `SubmissionReceipt` و `FirstReply` ساخته شوند.
- [ ] state machine با جدول صریح transitionها پیاده‌سازی شود؛ transition ضمنی ممنوع باشد.
- [ ] تست‌ها و type check اجرا شوند.
- [ ] commit: `feat: define project domain and state machine`

## Task 3: PostgreSQL Schema, Migrations, and Idempotent Repositories

**Files:** `src/karlancer_agent/db/base.py`, `db/models.py`, `db/session.py`, `db/repositories.py`, `alembic.ini`, `migrations/env.py`, `migrations/versions/0001_initial.py`, `tests/integration/test_postgres_repository.py`

Schema requirements:

- `projects`: channel message ID، Karlancer project ID، canonical URL، raw/extracted data، state، timestamps.
- `analyses`: rule score، LLM decision، reasons، provider/model/version.
- `proposal_drafts`: body، validator result، version، approved timestamp.
- `submissions`: unique project ID، idempotency key، status، form values، evidence URL/hash.
- `reply_events`: unique `(project_id, employer_message_id)` و notification timestamp.
- `audit_events`: append-only event type، actor، payload sanitized، timestamp.

Repository contracts:

```python
async def upsert_discovered(project: ChannelProject) -> tuple[ProjectRow, bool]: ...
async def claim_for_processing(project_id: UUID) -> bool: ...
async def reserve_submission(project_id: UUID, key: str) -> bool: ...
async def record_first_reply_once(reply: FirstReply) -> bool: ...
```

- [ ] integration test با PostgreSQL واقعی در Compose نوشته شود: duplicate channel message و URL فقط یک project بسازند.
- [ ] تست atomic claim، unique submission و first-reply-once نوشته شود و fail اجرا شود.
- [ ] migration اولیه با indexها، foreign keyها، check constraintها و enum/state stringهای سازگار ساخته شود.
- [ ] repositoryها با transaction و `SELECT ... FOR UPDATE SKIP LOCKED` برای claim پیاده شوند.
- [ ] `alembic upgrade head` و integration test اجرا شوند.
- [ ] commit: `feat: add durable idempotent persistence`

## Task 4: Telegram Channel Preview Ingestion

**Files:** `src/karlancer_agent/ingestion/base.py`, `ingestion/parser.py`, `ingestion/telegram_preview.py`, `tests/fixtures/channel_page.html`, `tests/unit/ingestion/test_parser.py`, `tests/unit/ingestion/test_preview_source.py`

Interface:

```python
class ProjectSource(Protocol):
    async def poll(self, after_message_id: int | None) -> list[ChannelProject]: ...
```

- [ ] یک fixture واقعیِ sanitized از `t.me/s/karlancer_projects` با چند پیام، دکمه مشاهده پروژه، hashtag فارسی/انگلیسی و یک پیام غیرپروژه ذخیره شود.
- [ ] تست parser برای message ID، عنوان، budget text، duration text، hashtags و URL canonical نوشته و fail اجرا شود.
- [ ] parser مستقل از شبکه با BeautifulSoup/lxml پیاده شود.
- [ ] `TelegramPreviewSource` با timeout، User-Agent مشخص، backoff شبکه ۱، ۵ و ۱۵ دقیقه فقط برای GET و ترتیب صعودی message ID پیاده شود.
- [ ] هر poll چند پیام قبل از cursor را نیز بخواند تا پیام مرزی از دست نرود؛ unique constraintها تکرار را حذف کنند.
- [ ] پاسخ ناقص یا تغییر DOM باید alertable error بدهد و cursor را جلو نبرد.
- [ ] unit tests با `respx` و سپس suite اجرا شوند.
- [ ] commit: `feat: ingest Karlancer channel projects`

## Task 5: Project Page Extraction and Budget/Duration Normalization

**Files:** `src/karlancer_agent/browser/selectors.py`, `browser/extractor.py`, `tests/fixtures/project_ai_programming.html`, `tests/fixtures/project_tool_only.html`, `tests/unit/browser/test_extractor.py`

Interface:

```python
class ProjectExtractor:
    async def extract(self, page: Page, url: str) -> ExtractedProject: ...
```

- [ ] fixtures برای پروژه برنامه‌نویسی AI، پروژه آموزشی و کار صرفاً ابزاری آماده شود.
- [ ] تست استخراج عنوان، شرح کامل، مهارت‌ها، حداقل/حداکثر بودجه، واحد پول، عدد/واحد مدت، وضعیت باز/بسته، شناسه کارفرما، URL فرم و project ID نوشته و fail اجرا شود.
- [ ] selectorها بر مبنای role/text/stable attributes در یک فایل مرکزی تعریف شوند؛ CSS شکننده fallback آخر باشد.
- [ ] parser مبلغ ارقام فارسی/انگلیسی، جداکننده‌ها و بازه را normalize کند و فقط سقف را برگرداند.
- [ ] parser زمان همان مقدار/واحد نمایش‌داده‌شده را برگرداند و هر ابهام را `ExtractionError` کند.
- [ ] preflight مشخص کند پروژه هنوز باز است، قبلاً از این حساب پیشنهاد نگرفته، نشست معتبر است و CAPTCHA وجود ندارد؛ شکست هرکدام به `PAUSED_ERROR` برود.
- [ ] تست fixtureها اجرا شود.
- [ ] commit: `feat: extract normalized Karlancer project data`

## Task 6: Persistent Browser Session and Login Health

**Files:** `src/karlancer_agent/browser/session.py`, `src/karlancer_agent/observability/health.py`, `tests/unit/browser/test_session.py`, `scripts/login-session.sh`, `docs/operations/session-login.md`

Interface:

```python
class BrowserSession:
    async def start(self) -> None: ...
    async def new_page(self) -> AsyncContextManager[Page]: ...
    async def login_status(self) -> LoginStatus: ...
    async def close(self) -> None: ...
```

- [ ] تست شود که `launch_persistent_context` دقیقاً یک بار با directory اختصاصی اجرا می‌شود و دو instance همزمان رد می‌شوند.
- [ ] تست login health برای signed-in، login-expired و challenge/2FA نوشته و fail اجرا شود.
- [ ] قفل process/file برای single-owner profile و shutdown تمیز پیاده شود.
- [ ] `scripts/login-session.sh` برای ورود دستی از طریق noVNC و ذخیره پروفایل نوشته شود.
- [ ] runbook شامل تجدید session، مالکیت volume و ممنوعیت کپی cookie در Git نوشته شود.
- [ ] تست‌ها اجرا شوند.
- [ ] commit: `feat: manage persistent Karlancer browser session`

## Task 7: Deterministic Skill Rules and Exclusion Filters

**Files:** `src/karlancer_agent/classification/glossary.yaml`, `classification/rules.py`, `tests/unit/classification/test_rules.py`

Output:

```python
class RuleAssessment(BaseModel):
    positive_categories: set[str]
    matched_terms: set[str]
    excluded_terms: set[str]
    score: int
    decision: Literal["accept", "reject", "needs_llm"]
```

- [ ] glossary دو‌زبانه برای Python، ML، DL، CV، NLP، LLM، RAG، AI Engineer، AI Agent، API و ربات‌های پیام‌رسان تدوین شود.
- [ ] exclusionهای tool-only مثل تدوین/ادیت ویدئو، ساخت تصویر/کاراکتر و کار صرفاً با ابزار AI اضافه شود، بدون رد پروژه‌های برنامه‌نویسی‌ای که همین رسانه‌ها را پردازش می‌کنند.
- [ ] حداقل ۳۰ case جدولی شامل مثبت، منفی و ambiguous نوشته و fail اجرا شود.
- [ ] scoring بر title، tags و description وزن متفاوت داشته باشد؛ تطابق substring خام برای واژه‌های کوتاه مثل `AI` ممنوع باشد.
- [ ] `AI/هوش مصنوعی` به‌تنهایی موجب پذیرش نشود؛ API integration مرتبط مجاز باشد.
- [ ] تست‌ها اجرا و glossary version در نتیجه ثبت شود.
- [ ] commit: `feat: classify projects with bilingual deterministic rules`

## Task 8: Swappable LLM Providers and Structured Analysis

**Files:** `src/karlancer_agent/llm/base.py`, `llm/schemas.py`, `llm/openai_compatible.py`, `llm/ollama.py`, `tests/unit/llm/test_providers.py`

Contracts:

```python
class LLMProvider(Protocol):
    async def analyze(self, project: ExtractedProject, rules: RuleAssessment) -> AnalysisResult: ...
    async def draft(self, project: ExtractedProject, analysis: AnalysisResult) -> ProposalCore: ...
```

- [ ] JSON schema برای category، accept/reject، confidence، reasons، technical approach و حداکثر دو سؤال ضروری نوشته شود.
- [ ] contract test مشترک برای providerها، invalid JSON، timeout و پاسخ ناقص نوشته و fail اجرا شود.
- [ ] adapter OpenAI-compatible با endpoint/key/model تنظیم‌پذیر و بدون وابستگی به اشتراک تعاملی Codex پیاده شود.
- [ ] adapter Ollama با همان contract پیاده شود.
- [ ] یک retry فقط برای خطای transport/parse پیش از هر side effect مجاز باشد؛ fallback به `NEEDS_REVIEW` صریح باشد.
- [ ] تصمیم خودکار فقط با `decision=eligible`، `confidence >= 0.85`، `hard_negative=null` و extractor/validator معتبر مجاز باشد؛ سایر موارد `NEEDS_REVIEW` شوند.
- [ ] تست‌ها اجرا شوند.
- [ ] commit: `feat: add swappable structured LLM providers`

## Task 9: Human Proposal Generation and Hard Validation

**Files:** `src/karlancer_agent/proposal/templates.py`, `proposal/generator.py`, `proposal/validator.py`, `tests/unit/proposal/test_generator.py`, `tests/unit/proposal/test_validator.py`

- [ ] متن شروع و پایان تأییدشده کاربر به‌صورت ثابت و test snapshot ذخیره شود؛ لینک‌های YouTube/GitHub/LinkedIn دقیقاً حفظ شوند.
- [ ] تست تولید بدنه برای پروژه روشن، پروژه مبهم با یک سؤال و پروژه با حداکثر دو سؤال نوشته و fail اجرا شود.
- [ ] generator فقط بخش میانی را از provider بگیرد و intro/outro را در کد اضافه کند.
- [ ] validator وجود intro/outro، طول میانه، ۰ تا ۲ سؤال، اشاره مشخص به نیاز پروژه و نبود clichéهای ممنوع را بررسی کند.
- [ ] validator regex قطعی برای شماره تلفن، Telegram ID، `t.me`، WhatsApp و دعوت به ارتباط خارج سایت داشته باشد.
- [ ] جمله «مبلغ و زمان دقیق پس از بررسی جزئیات نهایی اعلام می‌شود» فقط در متن باشد؛ مقدار فرم همچنان از پروژه گرفته شود.
- [ ] property/parameterized tests برای دور زدن regex با فاصله و ارقام فارسی اجرا شوند.
- [ ] commit: `feat: generate and validate human proposals`

## Task 10: Shadow Pipeline and FastAPI Control Surface

**Files:** `src/karlancer_agent/services/pipeline.py`, `src/karlancer_agent/api/main.py`, `api/dependencies.py`, `api/routes/health.py`, `api/routes/projects.py`, `tests/integration/test_api.py`, `tests/e2e/test_shadow_pipeline.py`

Endpoints:

- `POST /v1/jobs/ingest`
- `POST /v1/projects/{id}/analyze`
- `POST /v1/projects/{id}/draft`
- `GET /v1/projects/{id}`
- `GET /health/live`
- `GET /health/ready`

- [ ] E2E shadow test با channel fixture → extraction fixture → classification → draft → DB نوشته شود و تضمین کند submitter فراخوانی نشده است.
- [ ] test API برای idempotency key، auth داخلی و status codeهای پایدار نوشته و fail اجرا شود.
- [ ] pipeline به شکل resume-safe پیاده شود: هر stage وضعیت فعلی را می‌خواند و stage تکمیل‌شده را تکرار نمی‌کند.
- [ ] readiness وابستگی DB، browser session و provider را جدا گزارش دهد؛ liveness به وابستگی خارجی متکی نباشد.
- [ ] structured logging با project ID و event، بدون متن secret/cookie، اضافه شود.
- [ ] تست‌ها اجرا شوند.
- [ ] commit: `feat: run end-to-end shadow pipeline`

## Task 11: Telegram Reports and Assisted Approval

**Files:** `src/karlancer_agent/notifications/telegram.py`, `src/karlancer_agent/services/approval.py`, `src/karlancer_agent/api/routes/approvals.py`, `tests/unit/notifications/test_telegram.py`, `tests/integration/test_approval_api.py`

- [ ] تست گزارش assisted شامل عنوان، دلیل انتخاب، budget/duration، متن کامل draft، لینک پروژه و دکمه‌های approve/reject نوشته شود.
- [ ] تست callback امضاشده، منقضی‌شده، دوباره‌استفاده‌شده و متعلق به project دیگر نوشته و fail اجرا شود.
- [ ] Telegram Bot API client با timeout و idempotent notification key پیاده شود.
- [ ] approval token یک‌بارمصرف با TTL و hash ذخیره‌شده در DB پیاده شود.
- [ ] approve فقط `AWAITING_APPROVAL → SUBMIT_PENDING` و reject فقط `AWAITING_APPROVAL → REJECTED` را مجاز کند؛ فرمان edit نسخه جدید draft را validate و دوباره `AWAITING_APPROVAL` کند.
- [ ] تست‌ها اجرا شوند.
- [ ] commit: `feat: add Telegram-assisted approval flow`

## Task 12: Submission Safety, Form Filling, and Evidence

**Files:** `src/karlancer_agent/browser/submitter.py`, `tests/unit/browser/test_submitter.py`, `tests/e2e/test_submission_guard.py`

Interface:

```python
class ProposalSubmitter:
    async def submit(self, page: Page, command: SubmissionCommand) -> SubmissionReceipt: ...
```

- [ ] test matrix گیت ارسال برای تمام ترکیب‌های mode/flag/approval/validation/budget/duration/daily-limit نوشته و fail اجرا شود.
- [ ] قبل از browser action، `reserve_submission` اتمیک فراخوانی شود؛ reservation تکراری side effect نداشته باشد.
- [ ] فرم با متن validate‌شده، سقف بودجه و زمان پروژه پر شود؛ مقادیر read-back قبل از کلیک نهایی assert شوند.
- [ ] analyzer و generator هیچ reference به Playwright/Page نگیرند؛ فقط submitter مجاز به کلیک نهایی باشد و AI هرگز selector/مختصات تولید نکند.
- [ ] در shadow فقط screenshot/HTML sanitized و `would_submit` ثبت شود و هیچ کلیک نهایی انجام نشود.
- [ ] success فقط با evidence قطعی صفحه/record ثبت شود؛ timeout بعد از کلیک نهایی به `PAUSED_ERROR` برود و retry نشود.
- [ ] screenshot failure/success خارج از profile و با retention محدود ذخیره شود.
- [ ] tests با fake Playwright page اجرا شوند.
- [ ] commit: `feat: submit proposals behind strict safety gates`

## Task 13: First Employer Reply Detection and Terminal Handoff

**Files:** `src/karlancer_agent/browser/replies.py`, `src/karlancer_agent/services/reply_watcher.py`, `tests/fixtures/reply_page.html`, `tests/unit/browser/test_replies.py`, `tests/e2e/test_first_reply_handoff.py`

- [ ] fixture گفتگو با پیام خود کاربر، اولین پیام کارفرما و پیام‌های بعدی ساخته شود.
- [ ] تست تشخیص نقش sender، message ID/time/text excerpt و conversation URL نوشته و fail اجرا شود.
- [ ] watcher فقط پروژه‌های `SUBMITTED` را بررسی کند و اولین employer message جدید را انتخاب کند.
- [ ] `record_first_reply_once` پیش از اعلان فراخوانی شود؛ crash بین ثبت و ارسال با outbox/audit قابل بازیابی باشد.
- [ ] گزارش تلگرام شامل عنوان، خلاصه پروژه، خلاصه پاسخ و لینک مستقیم گفتگو باشد.
- [ ] بعد از اعلان موفق state به `HUMAN_HANDOFF` برسد؛ تست ثابت کند هیچ API یا browser path برای پاسخ خودکار وجود ندارد.
- [ ] اجرای دوباره watcher نباید اعلان دوم تولید کند.
- [ ] commit: `feat: notify once on first employer reply`

## Task 14: n8n Workflows as Thin Orchestrator

**Files:** `workflows/n8n/ingest.json`, `shadow.json`, `assisted.json`, `reply-watch.json`, `tests/integration/test_n8n_exports.py`

- [ ] schema test نوشته شود که exportها JSON معتبر، بدون credential و دارای timeout/error branch باشند.
- [ ] `ingest`: schedule → worker ingest endpoint → alert on failure.
- [ ] `shadow`: poll claimed projects → analyze/draft → Telegram shadow report؛ بدون endpoint ارسال.
- [ ] `assisted`: draft report/callback → approval endpoint → submission endpoint؛ auto route به‌صورت جدا و با env gate.
- [ ] `reply-watch`: schedule → watcher endpoint → Telegram report/outbox recovery.
- [ ] credentialها فقط با n8n credential store/env reference و نه داخل JSON باشند.
- [ ] exportها با یک n8n container موقت import و activate شوند.
- [ ] commit: `feat: orchestrate agent workflows with n8n`

## Task 15: Containerization, VPS Operations, Backup, and Recovery

**Files:** `Dockerfile`, `compose.yaml`, `compose.production.yaml`, `scripts/backup.sh`, `scripts/restore.sh`, `scripts/smoke.sh`, `docs/operations/deployment.md`, `docs/operations/recovery.md`

- [ ] `Dockerfile` non-root، multi-stage و مبتنی بر Python image رسمی ساخته شود؛ Playwright browser version با lock/build هماهنگ باشد.
- [ ] Compose شامل `postgres`, `worker`, `n8n`, `browser/noVNC` با volumeهای صریح، healthcheck، resource limit و restart policy باشد.
- [ ] `depends_on: condition: service_healthy` فقط برای startup ordering استفاده شود؛ خود سرویس‌ها نیز reconnect کنند.
- [ ] پورت PostgreSQL، n8n و noVNC عمومی نشوند؛ دسترسی عملیاتی با firewall و SSH tunnel و در صورت انتشار n8n با TLS/authentication مستند شود.
- [ ] backup روزانه از PostgreSQL و n8n data نسخه timestamped بگیرد و حداقل هفت نسخه نگه دارد؛ browser profile فقط با توقف owner snapshot شود.
- [ ] sizing مستند شود: یک stack حداقل ۲ vCPU/۴GB RAM/۳۰GB SSD؛ سه یا چهار stack مستقل حداقل ۴ vCPU/۸GB RAM با Compose project، volume، secret و resource limit جدا.
- [ ] restore script target directory/database را validate کند و ابتدا در محیط disposable آزمایش شود.
- [ ] smoke script Compose config، migration، health endpoints، browser login status و `n8n audit` را بررسی کند.
- [ ] `docker compose -f compose.yaml -f compose.production.yaml config` و smoke test اجرا شوند.
- [ ] commit: `ops: package agent for resilient VPS deployment`

## Task 16: Model Evaluation and Shadow Acceptance Gate

**Files:** `tests/fixtures/evaluation_cases.yaml`, `scripts/evaluate-model.py`, `docs/operations/model-evaluation.md`, `docs/operations/acceptance.md`

- [ ] حداقل ۵۰ نمونه sanitized و برچسب‌خورده شامل همه دسته‌های مثبت، آموزشی، API، tool-only و ambiguous تهیه شود.
- [ ] evaluation script precision/recall پذیرش، false positiveهای tool-only، schema validity و proposal validator pass rate را برای هر provider/model گزارش کند.
- [ ] گیت پیشنهادی نسخه اول ثبت شود: صفر leakage اطلاعات تماس، صفر tool-only false positive در مجموعه بحرانی، ۱۰۰٪ schema-valid و حداقل ۹۰٪ تصمیم صحیح کلی.
- [ ] ۳–۴ چرخه بازبینی متن که کاربر خواسته در acceptance log مستند شود؛ تغییر prompt/version قابل ردیابی باشد.
- [ ] ابتدا معیار رسمی shadow روی حداقل ۲۰ پروژه اخیر اجرا شود: حداقل ۹۰٪ انطباق تصمیم، صفر hard-negative پذیرفته‌شده و تأیید حداقل شش draft از سه گروه ساده/مبهم/آموزشی.
- [ ] سپس shadow soak حداقل ۴۸ ساعت یا ۱۰۰ پروژه (هرکدام دیرتر) بدون side effect واقعی اجرا و duplicate rate، extraction failures و selector failures گزارش شود.
- [ ] assisted فقط پس از تأیید shadow فعال و حداقل سه پیشنهاد متوالی با مبلغ/زمان دقیق، بدون تکرار و بدون اصلاح اساسی ارسال شود.
- [ ] auto فقط با اقدام صریح کاربر فعال و سه ارسال اول audit شود؛ اولین پاسخ آزمایشی/واقعی دقیقاً یک اعلان معتبر بدهد و هیچ پاسخ خودکاری ایجاد نشود.
- [ ] commit: `test: add model and shadow acceptance gates`

## Task 17: Final Verification and Release Checklist

**Files:** `README.md`, `docs/operations/acceptance.md`

- [ ] `uv sync --all-extras --frozen` اجرا شود.
- [ ] `uv run ruff check .`، `uv run mypy src` و `uv run pytest -q` همگی سبز باشند.
- [ ] migration روی DB خالی و DB ارتقایافته آزمایش شود.
- [ ] `docker compose ... up -d --build`، healthcheckها و restart آزمایشی worker/n8n/Postgres اجرا شوند.
- [ ] expiration session، provider outage، Telegram outage، DOM change و ambiguous submission شبیه‌سازی و مسیر alert/manual review تأیید شود.
- [ ] backup/restore روی stack disposable اجرا و checksum/row counts مقایسه شود.
- [ ] `README` شامل quick start، modeها، گیت ارسال، provider selection و لینک runbookها تکمیل شود.
- [ ] release checklist با نام model، prompt/glossary version، selector fixture version و timestamp تکمیل شود.
- [ ] commit: `docs: finalize verified Karlancer agent release`

## Requirement Traceability

- دریافت همه پروژه‌ها و حذف تکرار: Tasks 3–4.
- خواندن صفحه و استخراج مبلغ/زمان: Tasks 5–6.
- مهارت‌های دو‌زبانه، آموزشی و رد tool-only: Task 7 و ارزیابی Task 16.
- provider قابل‌تعویض و عدم اتکا به usage تعاملی Codex: Task 8.
- متن ثابت، لحن انسانی، حداکثر دو سؤال و منع اطلاعات تماس: Task 9.
- shadow → assisted → auto و ارسال بدون تکرار: Tasks 10–12 و 16.
- اولین پاسخ، گزارش یک‌باره و توقف کامل پاسخ‌گویی: Task 13.
- اجرای ۲۴ساعته روی VPS و امکان اجرای پروژه‌های جداگانه دیگر: Tasks 14–15؛ هر agent باید Compose project name، شبکه، volume، DB/schema و resource limit مستقل داشته باشد.
- تست، بازیابی و معیار پذیرش: Tasks 15–17.

## Official Implementation References

- Playwright persistent browser context: <https://playwright.dev/python/docs/api/class-browsertype>
- Playwright locators: <https://playwright.dev/python/docs/locators>
- FastAPI container deployment: <https://fastapi.tiangolo.com/deployment/docker/>
- Docker Compose startup ordering and healthchecks: <https://docs.docker.com/compose/how-tos/startup-order/>
- Docker Compose production guidance: <https://docs.docker.com/compose/how-tos/production/>
- n8n security audit: <https://docs.n8n.io/hosting/securing/security-audit/>

## Execution Order and Stop Points

Tasks 1–10 محیط shadow کامل را می‌سازند. پس از Task 10، اجرای نمونه‌های واقعی فقط خواندنی آغاز می‌شود. Tasks 11–12 قابلیت assisted را می‌سازند، ولی ارسال واقعی تا عبور Task 16 فعال نمی‌شود. Task 13 مستقل از تولید پاسخ است و فقط notification/handoff دارد. Task 16 یک توقف اجباری برای تأیید کاربر است؛ بدون آن `RUN_MODE=auto` و `SUBMISSION_ENABLED=true` همزمان تنظیم نمی‌شوند.
