"""`_compose_docs_response`'s judge-facing metadata and error-level fix.

Two regressions guarded here: (1) the judge evaluator used to receive the
entire compiled prompt as `input`, ~1500 tokens of mostly-fixed template,
instead of the customer's actual ~10-30 token question — `metadata` now
carries the clean pair alongside the existing `input`/`output`. (2) a
parse failure used to leave the span at `level=DEFAULT` with `output` the
string "None", indistinguishable to the judge from a real bad answer —
it must now be marked `level="ERROR"`, which the evaluator's filter
already excludes.
"""

from contextlib import contextmanager

import pytest

from src.application import docs_agent
from src.domain.schemas import DocsResponse


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
    model_name = "openai/gpt-5.6-luna"

    def __init__(self, raw: dict) -> None:
        self._raw = raw

    def with_structured_output(
        self, _schema: type, include_raw: bool = False
    ) -> _FakeStructuredModel:
        assert include_raw is True
        return _FakeStructuredModel(self._raw)


def _patch_common(monkeypatch: pytest.MonkeyPatch, fake_model, fake_client) -> None:
    monkeypatch.setattr(docs_agent, "get_chat_model", lambda _role: fake_model)
    monkeypatch.setattr(docs_agent, "get_langfuse_client", lambda: fake_client)
    monkeypatch.setattr(docs_agent, "get_prompt_client", lambda _name: object())


def test_successful_parse_carries_clean_metadata_alongside_full_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeLangfuseClient()
    parsed = DocsResponse(answer="Так, є в наявності.", confidence=0.9)
    fake_model = _FakeChatModel({"raw": object(), "parsed": parsed})
    _patch_common(monkeypatch, fake_model, fake_client)

    result = docs_agent._compose_docs_response(
        "full compiled prompt text...", "Чи є молоко?"
    )

    assert result is parsed
    update = fake_client.last_span.updates[0]
    assert update["input"] == "full compiled prompt text..."  # unchanged, still full
    assert update["metadata"] == {
        "customer_message": "Чи є молоко?",
        "agent_answer": "Так, є в наявності.",
    }
    assert "level" not in update


def test_parse_failure_marks_the_span_level_error_instead_of_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeLangfuseClient()
    fake_model = _FakeChatModel({"raw": object(), "parsed": None, "parsing_error": "x"})
    _patch_common(monkeypatch, fake_model, fake_client)

    with pytest.raises(docs_agent.DocsInvalidOutputError):
        docs_agent._compose_docs_response("prompt", "Чи є молоко?")

    update = fake_client.last_span.updates[0]
    assert update["level"] == "ERROR"
    assert "metadata" not in update
