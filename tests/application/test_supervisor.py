"""Graph edge dispatch (docs/decisions.md #16): a test asserts only that
the router's conditional edge reaches the *expected* node — never that
that node produces a working result, since Docs/Web Search/Escalation
raise `NotImplementedError` until their own stages build them. Nothing
green here can be mistaken for a working end-to-end route.
"""

import pytest

from src.application import supervisor
from src.application.router_agent import RouterResult
from src.domain.schemas import ClassificationOutput


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


def test_product_classification_dispatches_to_docs_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        supervisor, "run_router", lambda *a, **kw: _fake_router_result("product")
    )
    with pytest.raises(NotImplementedError, match="Stage 2"):
        supervisor.handle_request("Чи є у вас безлактозне молоко?", "r1", "s1", "t1")


def test_general_classification_dispatches_to_web_search_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        supervisor, "run_router", lambda *a, **kw: _fake_router_result("general")
    )
    with pytest.raises(NotImplementedError, match="Stage 2"):
        supervisor.handle_request(
            "Коли у вас відкривається новий магазин?", "r1", "s1", "t1"
        )


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
