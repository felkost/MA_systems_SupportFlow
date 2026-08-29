"""`run_web_search`'s judge-facing metadata and error-level fix — same
defect class as `_compose_docs_response`'s, see that test's docstring.
"""

from contextlib import contextmanager

import pytest

from src.application import web_search_agent
from src.domain.schemas import WebSearchResponse
from src.infrastructure.web_search import SearchOutcome, SearchResult


class _FakeSpan:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    def update(self, **kwargs) -> None:
        self.updates.append(kwargs)


class _FakeLangfuseClient:
    def __init__(self) -> None:
        self.last_span = _FakeSpan()

    @contextmanager
    def start_as_current_observation(self, *, name: str, as_type: str, **_kw):
        yield self.last_span


class _FakeStructuredModel:
    def __init__(self, raw: dict) -> None:
        self._raw = raw

    def invoke(self, _prompt: str) -> dict:
        return self._raw


class _FakeChatModel:
    model_name = "deepseek/deepseek-v4-flash"

    def __init__(self, raw: dict) -> None:
        self._raw = raw

    def with_structured_output(
        self, _schema: type, include_raw: bool = False
    ) -> _FakeStructuredModel:
        assert include_raw is True
        return _FakeStructuredModel(self._raw)


def _fake_search(_query: str) -> SearchOutcome:
    return SearchOutcome(
        results=[SearchResult(title="t", url="https://x", content="uno")],
        provider="tavily",
    )


def _patch_common(monkeypatch: pytest.MonkeyPatch, fake_model, fake_client) -> None:
    monkeypatch.setattr(web_search_agent, "get_chat_model", lambda _role: fake_model)
    monkeypatch.setattr(web_search_agent, "get_langfuse_client", lambda: fake_client)
    monkeypatch.setattr(
        web_search_agent, "get_prompt", lambda _name: ("{{customer_message}}", 1)
    )
    monkeypatch.setattr(web_search_agent, "get_prompt_client", lambda _name: object())


def test_successful_parse_carries_clean_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeLangfuseClient()
    parsed = WebSearchResponse(answer="Ось відповідь.", confidence=0.8)
    fake_model = _FakeChatModel({"raw": object(), "parsed": parsed})
    _patch_common(monkeypatch, fake_model, fake_client)

    web_search_agent.run_web_search("Яка погода?", search_fn=_fake_search)

    update = fake_client.last_span.updates[0]
    assert update["metadata"] == {
        "customer_message": "Яка погода?",
        "agent_answer": "Ось відповідь.",
    }


def test_parse_failure_marks_the_span_level_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeLangfuseClient()
    fake_model = _FakeChatModel({"raw": object(), "parsed": None, "parsing_error": "x"})
    _patch_common(monkeypatch, fake_model, fake_client)

    with pytest.raises(web_search_agent.WebSearchInvalidOutputError):
        web_search_agent.run_web_search("Яка погода?", search_fn=_fake_search)

    update = fake_client.last_span.updates[0]
    assert update["level"] == "ERROR"
    assert "metadata" not in update
