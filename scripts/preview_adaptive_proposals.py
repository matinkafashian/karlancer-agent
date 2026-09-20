"""Read stored projects and preview new drafts without submitting or saving them."""

import asyncio
import json
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from karlancer_agent.config import Settings
from karlancer_agent.db.models import ProjectRow
from karlancer_agent.domain.enums import AnalysisDecision
from karlancer_agent.domain.models import AnalysisResult, ExtractedProject
from karlancer_agent.llm.factory import build_model_provider
from karlancer_agent.proposal.generator import ProposalGenerator
from karlancer_agent.proposal.validator import ProposalValidator


async def main():
    settings = Settings()
    engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        async with async_sessionmaker(engine)() as session:
            rows = (
                await session.scalars(
                    select(ProjectRow).where(ProjectRow.channel_message_id.in_([245303, 245399]))
                )
            ).all()
            projects = [ExtractedProject.model_validate(row.extracted_data) for row in rows]
        # Include a clear task to test that the model does not force questions.
        if projects:
            projects.append(
                projects[0].model_copy(
                    update={
                        "title": "اسکریپت پایتون حذف ردیف تکراری CSV",
                        "description": (
                            "اسکریپت Python می‌خواهم که فایل CSV با UTF-8 را بگیرد، "
                            "ردیف‌های کاملاً یکسان را حذف کند، اولین ردیف را حفظ کند و "
                            "خروجی را در فایل جدید ذخیره کند. فقط کتابخانه استاندارد، "
                            "اجرای خط فرمان، فایل کوچک و بدون رابط گرافیکی. "
                            "فایل اصلی تغییر نکند."
                        ),
                        "skills": ("Python",),
                    }
                )
            )
        async with httpx.AsyncClient() as http:
            bundle = build_model_provider(settings, http)
            for project in projects:
                analysis = AnalysisResult(
                    decision=AnalysisDecision.ELIGIBLE,
                    programming_deliverable=None,
                    hard_negative=None,
                    confidence=0.95,
                    reason="Preview only; evaluate original description.",
                )
                core = await bundle.provider.draft(project, analysis)
                draft = ProposalGenerator().build(
                    project=project,
                    core=core,
                    provider_name=bundle.provider_name,
                    model_name=bundle.model_name,
                    prompt_version="proposal-core-v2-adaptive",
                    generated_at=datetime.now(UTC),
                )
                validation = ProposalValidator().validate(body=draft.body, project=project)
                print(
                    json.dumps(
                        {
                            "title": project.title,
                            "body": draft.body,
                            "questions": core.questions,
                            "validation": validation.model_dump(),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
