"""Schema bounds and shape (task §6)."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.domain.schemas import (
    ClassificationOutput,
    DocsResponse,
    EscalationOutput,
    Source,
    WebSearchResponse,
)


def test_classification_output_rejects_invalid_category() -> None:
    with pytest.raises(ValidationError):
        ClassificationOutput(category="billing", urgency="low", language="uk")


def test_classification_output_rejects_invalid_urgency() -> None:
    with pytest.raises(ValidationError):
        ClassificationOutput(category="product", urgency="extreme", language="uk")


def test_classification_output_accepts_valid_values() -> None:
    out = ClassificationOutput(category="critical", urgency="critical", language="uk")
    assert out.category == "critical"
    assert out.urgency == "critical"
    assert out.language == "uk"


@pytest.mark.parametrize("bad_confidence", [-0.01, 1.01, 5.0, -5.0])
def test_docs_response_rejects_out_of_bounds_confidence(bad_confidence: float) -> None:
    # Unbounded confidence fails open — a hallucinated 5.0 would always
    # pass a 0.70 threshold.
    with pytest.raises(ValidationError):
        DocsResponse(answer="x", confidence=bad_confidence)


@pytest.mark.parametrize("bad_confidence", [-0.01, 1.01])
def test_web_search_response_rejects_out_of_bounds_confidence(
    bad_confidence: float,
) -> None:
    with pytest.raises(ValidationError):
        WebSearchResponse(answer="x", confidence=bad_confidence)


def test_docs_response_defaults_to_no_sources() -> None:
    out = DocsResponse(answer="x", confidence=0.5)
    assert out.sources == []


def test_docs_response_carries_structured_sources_with_retrieval_time() -> None:
    # Sources are structured, not list[str], so a retrieval timestamp
    # travels with each one.
    out = DocsResponse(
        answer="x",
        confidence=0.9,
        sources=[
            Source(ref="silpo:products/123", retrieved_at=datetime.now(timezone.utc))
        ],
    )
    assert out.sources[0].ref == "silpo:products/123"
    assert out.sources[0].version == ""


def test_escalation_output_requires_all_four_fields() -> None:
    out = EscalationOutput(
        summary="s",
        category="critical",
        customer_message="m",
        attempted_resolution="a",
    )
    assert out.category == "critical"
