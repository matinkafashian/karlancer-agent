"""Compose model-generated project details with immutable approved text."""

from datetime import datetime

from karlancer_agent.domain.models import ExtractedProject, ProposalDraft
from karlancer_agent.llm.schemas import ProposalCore
from karlancer_agent.proposal.templates import FIXED_INTRO, FIXED_OUTRO, PRICE_SENTENCE


class ProposalGenerator:
    """The model supplies only the middle; approved fixed text stays deterministic."""

    def build(
        self,
        *,
        project: ExtractedProject,
        core: ProposalCore,
        provider_name: str,
        model_name: str,
        prompt_version: str,
        generated_at: datetime,
    ) -> ProposalDraft:
        middle_parts = [
            core.project_understanding.strip(),
            core.technical_approach.strip(),
            PRICE_SENTENCE,
        ]
        # Missing justification is not permission to invent engagement questions.
        if (
            core.questions
            and len(core.question_reasons) == len(core.questions)
            and all(reason.strip() for reason in core.question_reasons)
            and all(question.strip() for question in core.questions)
        ):
            middle_parts.append("\n".join(_normalize_question(item) for item in core.questions))

        body = f"{FIXED_INTRO}\n\n{'\n\n'.join(middle_parts)}\n\n{FIXED_OUTRO}"
        return ProposalDraft(
            project_id=project.project_id,
            body=body,
            model_provider=provider_name,
            model_name=model_name,
            prompt_version=prompt_version,
            generated_at=generated_at,
        )


def _normalize_question(value: str) -> str:
    question = value.strip()
    if question.endswith(("؟", "?")):
        return question
    return f"{question}؟"
