"""Graph edge dispatch (docs/decisions.md #16): a test asserts only that
the router's conditional edge reaches the *expected* node — never that
that node produces a working result. Docs and Web Search Agent are both
real as of Stage 2 (docs/decisions.md #20); their tests mock only
`call_docs_agent`/`call_web_search` (the A2A hop), the one boundary a
real network/LLM call would otherwise cross. Escalation still raises
`NotImplementedError` until Stage 3 builds it.
"""

from datetime import datetime, timezone

import pytest

from src.application import supervisor
from src.application.router_agent import RouterResult
from src.domain.schemas import (
    ClassificationOutput,
    DocsResponse,
    Source,
    WebSearchResponse,
)
from src.infrastructure.docs_client import DocsCallResult, DocsUnavailableError
from src.infrastructure.web_search_client import (
    WebSearchCallResult,
    WebSearchUnavailableError,
)


def _fake_router_result(category: str, urgency: str = "low") -> RouterResult:
    classification = ClassificationOutput(  # type: ignore[arg-type]
        category=category, urgency=urgency, language="uk"
    )
    return RouterResult(
        classification=classification,
        prompt_version=1,
        errors=[],
        retry_count=0,
    )


def test_product_classification_with_confident_answer_responds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        supervisor, "run_router", lambda *a, **kw: _fake_router_result("product")
    )
    fake_result = DocsCallResult(
        response=DocsResponse(
            answer="Так, є безлактозне молоко.",
            sources=[
                Source(
                    ref="internal_policy/assortment_v1.md",
                    retrieved_at=datetime.now(timezone.utc),
                )
            ],
            confidence=0.9,
        ),
        retrieval_context=["безлактозне молоко в наявності"],
    )
    monkeypatch.setattr(supervisor, "call_docs_agent", lambda *a, **kw: fake_result)

    result = supervisor.handle_request(
        "Чи є у вас безлактозне молоко?", "r1", "s1", "t1"
    )

    assert result["next_action"] == "respond"
    assert result["answer"] == "Так, є безлактозне молоко."


def test_product_classification_with_unavailable_docs_escalates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        supervisor, "run_router", lambda *a, **kw: _fake_router_result("product")
    )

    def _raise(*_a, **_kw):
        raise DocsUnavailableError("DocsInvalidOutputError: refused to answer")

    monkeypatch.setattr(supervisor, "call_docs_agent", _raise)

    with pytest.raises(NotImplementedError, match="Stage 3"):
        supervisor.handle_request("Чи є у вас безлактозне молоко?", "r1", "s1", "t1")


def test_general_classification_with_confident_answer_responds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        supervisor, "run_router", lambda *a, **kw: _fake_router_result("general")
    )
    fake_result = WebSearchCallResult(
        response=WebSearchResponse(
            answer="Новий магазин відкривається у грудні.",
            sources=[
                Source(
                    ref="https://example.com", retrieved_at=datetime.now(timezone.utc)
                )
            ],
            confidence=0.9,
        ),
        retrieval_context=["магазин відкривається у грудні"],
    )
    monkeypatch.setattr(supervisor, "call_web_search", lambda *a, **kw: fake_result)

    result = supervisor.handle_request(
        "Коли у вас відкривається новий магазин?", "r1", "s1", "t1"
    )

    assert result["next_action"] == "respond"
    assert result["answer"] == "Новий магазин відкривається у грудні."
    assert result["retrieval_context"] == ["магазин відкривається у грудні"]


def test_general_classification_with_low_confidence_escalates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        supervisor, "run_router", lambda *a, **kw: _fake_router_result("general")
    )
    fake_result = WebSearchCallResult(
        response=WebSearchResponse(answer="Не впевнений.", sources=[], confidence=0.2),
        retrieval_context=[],
    )
    monkeypatch.setattr(supervisor, "call_web_search", lambda *a, **kw: fake_result)

    # Low confidence routes to Escalation (task §7 step 6), which is still
    # Stage 3 scope (docs/decisions.md #16) — the conditional edge itself
    # is what this test proves, same pattern as the critical-classification
    # test below.
    with pytest.raises(NotImplementedError, match="Stage 3"):
        supervisor.handle_request("Загальне питання", "r1", "s1", "t1")


def test_general_classification_with_unavailable_search_escalates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        supervisor, "run_router", lambda *a, **kw: _fake_router_result("general")
    )

    def _raise(*_a, **_kw):
        raise WebSearchUnavailableError("SearchUnavailableError: both providers down")

    monkeypatch.setattr(supervisor, "call_web_search", _raise)

    with pytest.raises(NotImplementedError, match="Stage 3"):
        supervisor.handle_request("Загальне питання", "r1", "s1", "t1")


def test_critical_classification_dispatches_to_escalate_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        supervisor,
        "run_router",
        lambda *a, **kw: _fake_router_result("critical", "critical"),
    )
    with pytest.raises(NotImplementedError, match="Stage 3"):
        supervisor.handle_request(
            "У мене алергічна реакція на ваш продукт!", "r1", "s1", "t1"
        )


def test_router_exhaustion_also_dispatches_to_escalate_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # docs/decisions.md #12: Router's own failure fails closed to
    # Escalation, exercised through the graph rather than router_agent
    # directly.
    monkeypatch.setattr(
        supervisor,
        "run_router",
        lambda *a, **kw: RouterResult(
            classification=None,
            prompt_version=None,
            errors=["router_retries_exhausted"],
            retry_count=1,
        ),
    )
    with pytest.raises(NotImplementedError, match="Stage 3"):
        supervisor.handle_request("Питання про товар", "r1", "s1", "t1")


def test_empty_input_is_rejected_before_the_graph_is_invoked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = {"n": 0}
    monkeypatch.setattr(supervisor, "build_graph", lambda: called.update(n=1))

    result = supervisor.handle_request("   ", "r1", "s1", "t1")

    assert result["next_action"] == "reject"
    assert result["errors"] == ["empty_input"]
    assert called["n"] == 0  # the graph was never built, let alone invoked


def test_unsupported_language_is_rejected_before_the_graph_is_invoked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = {"n": 0}
    monkeypatch.setattr(supervisor, "build_graph", lambda: called.update(n=1))

    result = supervisor.handle_request("这个牛奶不含乳糖吗？", "r1", "s1", "t1")

    assert result["next_action"] == "reject"
    assert result["errors"] == ["unsupported_language"]
    assert called["n"] == 0
