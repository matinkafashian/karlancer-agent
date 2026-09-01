"""Bilingual deterministic scoring before semantic model analysis."""

import re
import unicodedata
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

FIELD_WEIGHTS = {"title": 3, "tags": 2, "description": 1}


class RuleDecision(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    NEEDS_LLM = "needs_llm"


class TermGroup(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strong: tuple[str, ...] = ()
    standard: tuple[str, ...] = ()


class Glossary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    positive: dict[str, TermGroup]
    hard_negative: dict[str, tuple[str, ...]]
    programming_markers: tuple[str, ...]


class RuleAssessment(BaseModel):
    """Auditable output retained beside later LLM analysis."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    positive_categories: frozenset[str] = frozenset()
    matched_terms: frozenset[str] = frozenset()
    excluded_terms: frozenset[str] = frozenset()
    score: int = Field(ge=0)
    decision: RuleDecision
    glossary_version: str


class RulesClassifier:
    """Apply exact phrase boundaries with stronger title/tag weights."""

    def __init__(self, glossary: Glossary) -> None:
        self._glossary = glossary

    @classmethod
    def default(cls) -> "RulesClassifier":
        path = Path(__file__).with_name("glossary.yaml")
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(Glossary.model_validate(raw))

    def assess(
        self, *, title: str, tags: tuple[str, ...], description: str
    ) -> RuleAssessment:
        fields = {
            "title": normalize_text(title),
            "tags": normalize_text(" ".join(tags)),
            "description": normalize_text(description),
        }
        matched_terms: set[str] = set()
        categories: set[str] = set()
        strong_match = False
        score = 0

        for category, group in self._glossary.positive.items():
            category_matched = False
            for strength, terms in (("strong", group.strong), ("standard", group.standard)):
                for term in terms:
                    normalized_term = normalize_text(term)
                    field_hits = [
                        field_name
                        for field_name, field_text in fields.items()
                        if contains_term(field_text, normalized_term)
                    ]
                    if not field_hits:
                        continue
                    category_matched = True
                    matched_terms.add(normalized_term)
                    score += sum(FIELD_WEIGHTS[field_name] for field_name in field_hits)
                    strong_match = strong_match or strength == "strong"
            if category_matched:
                categories.add(category)

        combined = " ".join(fields.values())
        excluded_terms = {
            normalize_text(term)
            for terms in self._glossary.hard_negative.values()
            for term in terms
            if contains_term(combined, normalize_text(term))
        }
        programming_markers = {
            normalize_text(term)
            for term in self._glossary.programming_markers
            if contains_term(combined, normalize_text(term))
        }

        if excluded_terms:
            decision = (
                RuleDecision.NEEDS_LLM
                if programming_markers and matched_terms
                else RuleDecision.REJECT
            )
        elif strong_match or score >= 4:
            decision = RuleDecision.ACCEPT
        elif matched_terms:
            decision = RuleDecision.NEEDS_LLM
        else:
            decision = RuleDecision.REJECT

        return RuleAssessment(
            positive_categories=frozenset(categories),
            matched_terms=frozenset(matched_terms),
            excluded_terms=frozenset(excluded_terms),
            score=score,
            decision=decision,
            glossary_version=self._glossary.version,
        )


def normalize_text(value: str) -> str:
    """Normalize Persian/Arabic variants without turning substrings into matches."""

    normalized = unicodedata.normalize("NFKC", value).lower()
    normalized = normalized.translate(
        str.maketrans("\u064a\u0643\u06c0", "\u06cc\u06a9\u0647")
    )
    normalized = re.sub(r"[_\-]+", " ", normalized)
    normalized = re.sub(r"[^\w+#.]+", " ", normalized, flags=re.UNICODE)
    return re.sub(r"\s+", " ", normalized).strip()


def contains_term(normalized_text: str, normalized_term: str) -> bool:
    """Match a complete normalized term, never a raw substring such as AI in email."""

    if not normalized_term:
        return False
    pattern = rf"(?<!\w){re.escape(normalized_term)}(?!\w)"
    return re.search(pattern, normalized_text, flags=re.UNICODE) is not None
