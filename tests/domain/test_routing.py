"""`decide_route()` tested exhaustively as a pure function — this is where
route correctness lives, independent of any graph.
"""

import pytest

from src.domain.routing import confidence_below_threshold, decide_route
from src.domain.schemas import ClassificationOutput


def _classification(category: str, urgency: str) -> ClassificationOutput:
    return ClassificationOutput(  # type: ignore[arg-type]
        category=category, urgency=urgency, language="uk"
    )


def test_critical_category_routes_to_escalation_regardless_of_urgency() -> None:
    assert decide_route(_classification("critical", "low")) == "escalate"


def test_critical_urgency_routes_to_escalation_regardless_of_category() -> None:
    # The critical check fires on urgency too, not only category — a "product"
    # request with critical urgency must not fall through to Docs.
    assert decide_route(_classification("product", "critical")) == "escalate"
    assert decide_route(_classification("general", "critical")) == "escalate"


def test_product_category_routes_to_docs() -> None:
    assert decide_route(_classification("product", "low")) == "docs"
    assert decide_route(_classification("product", "medium")) == "docs"


def test_general_category_routes_to_web_search() -> None:
    assert decide_route(_classification("general", "low")) == "web_search"
    assert decide_route(_classification("general", "medium")) == "web_search"


@pytest.mark.parametrize("category", ["product", "general", "critical"])
@pytest.mark.parametrize("urgency", ["low", "medium", "critical"])
def test_decide_route_is_exhaustive_over_every_combination(
    category: str, urgency: str
) -> None:
    # every (category, urgency) pair must produce one of the three known
    # routes — no combination silently falls through.
    assert decide_route(_classification(category, urgency)) in {
        "escalate",
        "docs",
        "web_search",
    }


def test_confidence_below_threshold_with_no_threshold_never_escalates() -> None:
    assert confidence_below_threshold(0.0, None) is False


def test_confidence_below_threshold_compares_correctly() -> None:
    assert confidence_below_threshold(0.5, 0.70) is True
    assert confidence_below_threshold(0.70, 0.70) is False
    assert confidence_below_threshold(0.9, 0.70) is False
