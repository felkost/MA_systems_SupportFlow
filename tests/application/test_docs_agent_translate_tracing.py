"""`_translate_to_ukrainian_search_term`'s tracing.

Regression guard: this call used to make a real paid LLM request with no
span at all, so its tokens never appeared in any cost figure. It also used
a hardcoded instruction string, which `CLAUDE.md` forbids. Both are fixed
by copying the `_compose_docs_response`/`_compose_escalation_output`
observability shape — this test asserts the span actually gets opened with
the right name and carries real `usage_details`.
"""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from src.application import docs_agent


class _FakeSpan:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    def update(self, **kwargs) -> None:
        self.updates.append(kwargs)


class _FakeLangfuseClient:
    def __init__(self) -> None:
        self.opened_spans: list[tuple[str, str]] = []
        self.last_span = _FakeSpan()

    @contextmanager
    def start_as_current_observation(self, *, name: str, as_type: str, **_kw):
        self.opened_spans.append((name, as_type))
        yield self.last_span


class _FakeStructuredModel:
    def __init__(self, raw: dict) -> None:
        self._raw = raw

    def invoke(self, _prompt: str) -> dict:
        return self._raw


class _FakeChatModel:
    model_name = "openai/gpt-5.6-luna"

    def __init__(self, raw: dict) -> None:
        self._raw = raw

    def with_structured_output(
        self, _schema: type, include_raw: bool = False
    ) -> _FakeStructuredModel:
        assert include_raw is True
        return _FakeStructuredModel(self._raw)


def test_translate_opens_a_named_generation_span_with_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeLangfuseClient()
    parsed = docs_agent._SearchTerm(search_term_uk="молоко")
    raw_message = SimpleNamespace(
        usage_metadata={"input_tokens": 12, "output_tokens": 3}
    )
    fake_model = _FakeChatModel({"raw": raw_message, "parsed": parsed})

    monkeypatch.setattr(docs_agent, "get_chat_model", lambda _role: fake_model)
    monkeypatch.setattr(docs_agent, "get_langfuse_client", lambda: fake_client)
    monkeypatch.setattr(
        docs_agent, "get_prompt", lambda _name: ("{{customer_message}}", 1)
    )
    monkeypatch.setattr(docs_agent, "get_prompt_client", lambda _name: object())

    result = docs_agent._translate_to_ukrainian_search_term("Чи є молоко?")

    assert result == "молоко"
    assert fake_client.opened_spans == [("docs_agent.translate", "generation")]
    assert fake_client.last_span.updates[0]["usage_details"] == {
        "input_tokens": 12,
        "output_tokens": 3,
    }
