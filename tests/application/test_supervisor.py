"""Graph edge dispatch (docs/decisions.md #16): a test asserts only that
the router's conditional edge reaches the *expected* node — never that
that node produces a working result. Docs, Web Search, and Escalation
Agent are all real as of Stage 3 (docs/decisions.md #20); their tests mock
only `call_docs_agent`/`call_web_search`/`run_escalation_agent` (the
external boundary each node crosses), never a real network/LLM call.
"""

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from langchain_core.callbacks.base import BaseCallbackHandler

from src.application import graph_nodes, supervisor
from src.application.escalation_agent import EscalationAgentResult
from src.application.router_agent import RouterResult
from src.domain.schemas import (
    ClassificationOutput,
    DocsResponse,
    EscalationOutput,
    Source,
    WebSearchResponse,
)
from src.infrastructure.docs_client import DocsCallResult, DocsUnavailableError
from src.infrastructure.web_search_client import (
    WebSearchCallResult,
    WebSearchUnavailableError,
)


def _fake_escalation_result() -> EscalationAgentResult:
    return EscalationAgentResult(
        output=EscalationOutput(
            summary="Тестовий випадок",
            category="critical",
            customer_message="Оператор зв'яжеться з вами найближчим часом.",
            attempted_resolution="Класифіковано, передано оператору.",
        ),
        written=True,
        sent=False,
        deduplicated=False,
        capped=False,
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
        graph_nodes, "run_router", lambda *a, **kw: _fake_router_result("product")
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
    monkeypatch.setattr(graph_nodes, "call_docs_agent", lambda *a, **kw: fake_result)

    result = supervisor.handle_request(
        "Чи є у вас безлактозне молоко?", "r1", "s1", "0123456789abcdef0123456789abcdef"
    )

    assert result["next_action"] == "respond"
    assert result["answer"] == "Так, є безлактозне молоко."


def test_product_classification_with_unavailable_docs_escalates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        graph_nodes, "run_router", lambda *a, **kw: _fake_router_result("product")
    )

    def _raise(*_a, **_kw):
        raise DocsUnavailableError("DocsInvalidOutputError: refused to answer")

    monkeypatch.setattr(graph_nodes, "call_docs_agent", _raise)
    monkeypatch.setattr(
        graph_nodes, "run_escalation_agent", lambda *a, **kw: _fake_escalation_result()
    )

    result = supervisor.handle_request(
        "Чи є у вас безлактозне молоко?", "r1", "s1", "0123456789abcdef0123456789abcdef"
    )

    assert result["next_action"] == "escalate"
    assert result["escalation_output"] is not None
    assert result["answer"] == "Оператор зв'яжеться з вами найближчим часом."


def test_general_classification_with_confident_answer_responds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        graph_nodes, "run_router", lambda *a, **kw: _fake_router_result("general")
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
    monkeypatch.setattr(graph_nodes, "call_web_search", lambda *a, **kw: fake_result)

    result = supervisor.handle_request(
        "Коли у вас відкривається новий магазин?",
        "r1",
        "s1",
        "0123456789abcdef0123456789abcdef",
    )

    assert result["next_action"] == "respond"
    assert result["answer"] == "Новий магазин відкривається у грудні."
    assert result["retrieval_context"] == ["магазин відкривається у грудні"]


def test_general_classification_with_low_confidence_escalates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        graph_nodes, "run_router", lambda *a, **kw: _fake_router_result("general")
    )
    fake_result = WebSearchCallResult(
        response=WebSearchResponse(answer="Не впевнений.", sources=[], confidence=0.2),
        retrieval_context=[],
    )
    monkeypatch.setattr(graph_nodes, "call_web_search", lambda *a, **kw: fake_result)
    monkeypatch.setattr(
        graph_nodes, "run_escalation_agent", lambda *a, **kw: _fake_escalation_result()
    )

    # Low confidence routes to Escalation (task §7 step 6) — the
    # conditional edge dispatches there and the real node now handles it.
    result = supervisor.handle_request(
        "Загальне питання", "r1", "s1", "0123456789abcdef0123456789abcdef"
    )

    assert result["next_action"] == "escalate"
    assert result["escalation_output"] is not None


def test_general_classification_with_unavailable_search_escalates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        graph_nodes, "run_router", lambda *a, **kw: _fake_router_result("general")
    )

    def _raise(*_a, **_kw):
        raise WebSearchUnavailableError("SearchUnavailableError: both providers down")

    monkeypatch.setattr(graph_nodes, "call_web_search", _raise)
    monkeypatch.setattr(
        graph_nodes, "run_escalation_agent", lambda *a, **kw: _fake_escalation_result()
    )

    result = supervisor.handle_request(
        "Загальне питання", "r1", "s1", "0123456789abcdef0123456789abcdef"
    )

    assert result["next_action"] == "escalate"
    assert result["escalation_output"] is not None


def test_critical_classification_dispatches_to_escalate_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        graph_nodes,
        "run_router",
        lambda *a, **kw: _fake_router_result("critical", "critical"),
    )
    monkeypatch.setattr(
        graph_nodes, "run_escalation_agent", lambda *a, **kw: _fake_escalation_result()
    )

    result = supervisor.handle_request(
        "У мене алергічна реакція на ваш продукт!",
        "r1",
        "s1",
        "0123456789abcdef0123456789abcdef",
    )

    assert result["next_action"] == "escalate"
    assert result["escalation_output"] is not None


def test_router_exhaustion_also_dispatches_to_escalate_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # docs/decisions.md #12: Router's own failure fails closed to
    # Escalation, exercised through the graph rather than router_agent
    # directly.
    monkeypatch.setattr(
        graph_nodes,
        "run_router",
        lambda *a, **kw: RouterResult(
            classification=None,
            prompt_version=None,
            errors=["router_retries_exhausted"],
            retry_count=1,
        ),
    )
    monkeypatch.setattr(
        graph_nodes, "run_escalation_agent", lambda *a, **kw: _fake_escalation_result()
    )

    result = supervisor.handle_request(
        "Питання про товар", "r1", "s1", "0123456789abcdef0123456789abcdef"
    )

    assert result["next_action"] == "escalate"
    assert result["escalation_output"] is not None


def test_empty_input_is_rejected_before_the_graph_is_invoked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = {"n": 0}
    monkeypatch.setattr(supervisor, "build_graph", lambda: called.update(n=1))

    result = supervisor.handle_request(
        "   ", "r1", "s1", "0123456789abcdef0123456789abcdef"
    )

    assert result["next_action"] == "reject"
    assert result["errors"] == ["empty_input"]
    assert called["n"] == 0  # the graph was never built, let alone invoked


class _FakeSpan:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    def update(self, **kwargs) -> None:
        self.updates.append(kwargs)


class _FakeLangfuseClient:
    """Stands in for `get_langfuse_client()`'s return value — enough
    surface to exercise the guardrail/routing spans and
    `_current_observation_id()` without a real Langfuse client.
    """

    def __init__(self) -> None:
        self.opened_spans: list[tuple[str, str]] = []
        self.last_span = _FakeSpan()

    @contextmanager
    def start_as_current_observation(self, *, name: str, as_type: str, **_kw):
        self.opened_spans.append((name, as_type))
        yield self.last_span

    def get_current_observation_id(self) -> str:
        return "deadbeefdeadbeef"


def test_graph_invoke_passes_callbacks_when_tracing_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeLangfuseClient()
    fake_handler = BaseCallbackHandler()
    monkeypatch.setattr(supervisor, "get_langfuse_client", lambda: fake_client)
    monkeypatch.setattr(graph_nodes, "get_langfuse_client", lambda: fake_client)
    monkeypatch.setattr(
        supervisor, "build_callback_handler", lambda **_kw: fake_handler
    )
    monkeypatch.setattr(
        graph_nodes,
        "run_router",
        lambda *a, **kw: _fake_router_result("critical", "critical"),
    )
    monkeypatch.setattr(
        graph_nodes, "run_escalation_agent", lambda *a, **kw: _fake_escalation_result()
    )

    captured_config = {}
    real_build_graph = supervisor.build_graph

    def spying_build_graph():
        graph = real_build_graph()
        real_invoke = graph.invoke

        def spying_invoke(state, config=None, **kw):
            captured_config.update(config or {})
            return real_invoke(state, config=config, **kw)

        graph.invoke = spying_invoke
        return graph

    monkeypatch.setattr(supervisor, "build_graph", spying_build_graph)

    supervisor.handle_request(
        "Термінова проблема!", "r1", "s1", "0123456789abcdef0123456789abcdef"
    )

    assert captured_config["callbacks"] == [fake_handler]


def test_graph_invoke_passes_empty_callbacks_when_tracing_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(supervisor, "get_langfuse_client", lambda: None)
    monkeypatch.setattr(graph_nodes, "get_langfuse_client", lambda: None)
    monkeypatch.setattr(
        graph_nodes,
        "run_router",
        lambda *a, **kw: _fake_router_result("critical", "critical"),
    )
    monkeypatch.setattr(
        graph_nodes, "run_escalation_agent", lambda *a, **kw: _fake_escalation_result()
    )

    result = supervisor.handle_request(
        "Термінова проблема!", "r1", "s1", "0123456789abcdef0123456789abcdef"
    )

    assert result["next_action"] == "escalate"  # no crash with tracing disabled


def test_docs_node_passes_parent_span_id_when_tracing_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeLangfuseClient()
    monkeypatch.setattr(supervisor, "get_langfuse_client", lambda: fake_client)
    monkeypatch.setattr(graph_nodes, "get_langfuse_client", lambda: fake_client)
    monkeypatch.setattr(
        supervisor, "build_callback_handler", lambda **_kw: BaseCallbackHandler()
    )
    monkeypatch.setattr(
        graph_nodes, "run_router", lambda *a, **kw: _fake_router_result("product")
    )
    captured = {}

    def fake_call_docs_agent(*_a, **kw):
        captured.update(kw)
        return DocsCallResult(
            response=DocsResponse(answer="x", sources=[], confidence=0.9),
            retrieval_context=[],
        )

    monkeypatch.setattr(graph_nodes, "call_docs_agent", fake_call_docs_agent)

    supervisor.handle_request(
        "Питання про товар", "r1", "s1", "0123456789abcdef0123456789abcdef"
    )

    assert captured["parent_span_id"] == "deadbeefdeadbeef"


def test_guardrail_span_records_triggered(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _FakeLangfuseClient()
    monkeypatch.setattr(supervisor, "get_langfuse_client", lambda: fake_client)

    supervisor.handle_request(
        "   ", "r1", "s1", "0123456789abcdef0123456789abcdef"
    )  # empty input -> rejected

    assert ("input_filter.run", "guardrail") in fake_client.opened_spans
    assert fake_client.last_span.updates[-1] == {"metadata": {"triggered": True}}


def test_unsupported_language_is_rejected_before_the_graph_is_invoked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = {"n": 0}
    monkeypatch.setattr(supervisor, "build_graph", lambda: called.update(n=1))

    result = supervisor.handle_request(
        "这个牛奶不含乳糖吗？", "r1", "s1", "0123456789abcdef0123456789abcdef"
    )

    assert result["next_action"] == "reject"
    assert result["errors"] == ["unsupported_language"]
    assert called["n"] == 0
