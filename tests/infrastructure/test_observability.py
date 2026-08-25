"""`src.infrastructure.observability` — client lifecycle, span-filter
composition, and the export-time PII barrier. All offline: no live
Langfuse network call (`pytest --cov=src` never makes one).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.infrastructure import observability

# `tests/conftest.py`'s autouse `_tracing_disabled_by_default` fixture
# already resets `observability._client`/`_configured` and forces
# `tracing_enabled=False` before every test in the suite — no local
# fixture needed here.


def test_configure_tracing_noops_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(observability.settings, "tracing_enabled", False)
    observability.configure_tracing()
    assert observability.get_langfuse_client() is None


def test_configure_tracing_raises_when_enabled_with_missing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(observability.settings, "tracing_enabled", True)
    monkeypatch.setattr(observability.settings, "langfuse_public_key", "")
    monkeypatch.setattr(observability.settings, "langfuse_secret_key", "secret")
    with pytest.raises(RuntimeError, match="TRACING_ENABLED"):
        observability.configure_tracing()


def test_get_langfuse_client_never_raises_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(observability.settings, "tracing_enabled", False)
    assert observability.get_langfuse_client() is None


def test_get_langfuse_client_self_configures_when_never_called_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The idempotent safety net (decision 33): a process that never calls
    `configure_tracing()` itself still ends up correctly configured (here,
    correctly disabled) on first real use.
    """
    monkeypatch.setattr(observability.settings, "tracing_enabled", False)
    assert observability._configured is False
    client = observability.get_langfuse_client()
    assert client is None
    assert observability._configured is True


def test_new_trace_id_is_32_char_lowercase_hex() -> None:
    trace_id = observability.new_trace_id()
    assert len(trace_id) == 32
    assert trace_id == trace_id.lower()
    int(trace_id, 16)  # must not raise


def test_should_export_span_true_for_default_export_span() -> None:
    span = SimpleNamespace(
        name="ChatOpenAI",
        instrumentation_scope=None,
        attributes={"gen_ai.system": "openai"},
    )
    assert observability.should_export_span(span) is True


def test_should_export_span_true_for_own_named_span() -> None:
    span = SimpleNamespace(
        name="silpo_mcp.call_tool", instrumentation_scope=None, attributes=None
    )
    assert observability.should_export_span(span) is True


def test_should_export_span_false_for_unrelated_span() -> None:
    span = SimpleNamespace(
        name="GET /health", instrumentation_scope=None, attributes=None
    )
    assert observability.should_export_span(span) is False


def test_should_export_span_false_for_a2a_sdk_internal_instrumentation() -> None:
    """Regression: an earlier prefix-based predicate (`name.startswith("a2a.")`)
    was confirmed live to also match a2a-sdk's own auto-instrumented
    internal spans, exporting ~1400 of them in one smoke-test session.
    `should_export_span` must reject these by exact name, not prefix.
    """
    span = SimpleNamespace(
        name="a2a.server.events.event_queue_v2.EventQueueSource.dequeue_event",
        instrumentation_scope=None,
        attributes=None,
    )
    assert observability.should_export_span(span) is False


def test_mask_otel_spans_redacts_str_pii_attribute() -> None:
    params = SimpleNamespace(
        spans={
            "span-1": SimpleNamespace(
                attributes={"customer_email": "foo@bar.com", "status": "success"}
            )
        }
    )
    result = observability.mask_otel_spans(params=params)
    assert result is not None
    patch = result.span_patches["span-1"]
    assert patch.set_attributes["customer_email"] == "[REDACTED]"
    assert patch.set_attributes["status"] == "success"


def test_mask_otel_spans_passes_non_str_attribute_through_unmasked() -> None:
    """The `isinstance` guard (decision 34) — a bool/int attribute must
    never reach `mask_pii`'s `unicodedata.normalize`, which raises
    `TypeError` on non-`str` input and would drop the whole export batch.
    """
    params = SimpleNamespace(
        spans={
            "span-1": SimpleNamespace(
                attributes={"retry_count": 2, "triggered": True, "email": "a@b.com"}
            )
        }
    )
    result = observability.mask_otel_spans(params=params)
    assert result is not None
    patch = result.span_patches["span-1"]
    assert patch.set_attributes["retry_count"] == 2
    assert patch.set_attributes["triggered"] is True
    assert patch.set_attributes["email"] == "[REDACTED]"


def test_mask_otel_spans_returns_none_when_nothing_to_redact() -> None:
    params = SimpleNamespace(
        spans={"span-1": SimpleNamespace(attributes={"status": "success"})}
    )
    assert observability.mask_otel_spans(params=params) is None


def test_flush_and_shutdown_calls_shutdown_not_only_flush(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    monkeypatch.setattr(observability, "_client", fake_client)
    observability.flush_and_shutdown()
    fake_client.shutdown.assert_called_once()


def test_flush_and_shutdown_noops_when_disabled() -> None:
    observability.flush_and_shutdown()  # must not raise with no client
