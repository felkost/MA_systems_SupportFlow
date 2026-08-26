"""Langfuse/OTel tracing setup — the one place every process configures its
own span exporter (CLAUDE.md invariant), composes `should_export_span`
rather than replacing it, and applies the second, export-time PII barrier
(docs/decisions.md #14, amended by this stage's own decision 34 — see
`docs/decisions.md`'s dated addendum under #14).

Confirmed against the installed `langfuse==4.14.4` SDK by direct source
inspection this stage (not assumed from v2/v3-era docs): the LangChain
integration lives at `langfuse.langchain.CallbackHandler`, not
`langfuse.callback.CallbackHandler`; cross-process span parenting uses
Langfuse's own `TraceContext` (`trace_id`+`parent_span_id`), not OTel
`baggage`; `mask_otel_spans` is batch-shaped
(`MaskOtelSpansParams.spans -> MaskOtelSpansResult.span_patches`), not a
single-span callback.
"""

import uuid
from typing import Any

from langfuse import Langfuse, is_default_export_span
from langfuse.langchain import CallbackHandler
from langfuse.types import (
    MaskOtelSpansParams,
    MaskOtelSpansResult,
    OtelSpanPatch,
    TraceContext,
)

from src.domain.filters import mask_pii
from src.kernel.settings import settings

_client: Langfuse | None = None
_configured = False


def new_trace_id() -> str:
    """32-char lowercase hex — the only format Langfuse's `TraceContext`
    accepts for cross-process span parenting (`int(trace_id, 16)` is run
    unconditionally on it; a hyphenated `uuid.uuid4()` string raises
    `ValueError` there).
    """
    return uuid.uuid4().hex


def tag_trace(trace_id: str, tags: list[str]) -> None:
    """Attach `tags` to an already-created trace, so separate runs over
    the same golden dataset (a baseline before/after a fix, a
    production/candidate prompt comparison) show up as distinct, visually
    comparable groups in Langfuse's own Trace Tags filter (Scores >
    Analytics) instead of one undifferentiated pool — the author's own
    request, 2026-08-26.

    A thin wrapper over `Langfuse._create_trace_tags_via_ingestion`
    (private in the installed `langfuse==4.14.4` SDK, confirmed by
    reading its source: it enqueues a partial `trace-create` ingestion
    event carrying only `id`+`tags`, which Langfuse merges into the
    existing trace rather than overwriting it) — no public equivalent
    exists in this SDK version. A no-op when tracing is disabled.
    """
    client = get_langfuse_client()
    if client is None:
        return
    client._create_trace_tags_via_ingestion(trace_id=trace_id, tags=tags)


# Every literal `name=` this project passes to `start_as_current_observation`
# (grep-verified against src/, 2026-08-26 live-trace audit). An *exact* set,
# not a prefix — a prefix like `"a2a."` was tried first and confirmed live to
# also match a2a-sdk's own auto-instrumented internal spans
# (`a2a.client.transports.jsonrpc.JsonRpcTransport.send_message`,
# `a2a.server.events.event_queue_v2.EventQueueSource.dequeue_event`, ~1400
# of them across one short smoke-test session), none of which this project
# asked to trace.
_OWN_SPAN_NAMES = frozenset(
    {
        "input_filter.run",
        "supervisor.routing",
        "acp.call_router",
        "silpo_mcp.call_tool",
        "docs_agent.compose",
        "docs_agent.a2a_request",
        "web_search_agent.compose",
        "web_search_agent.a2a_request",
        "escalation_agent.compose",
        "a2a.send_message",
        "telegram.send_message",
        "report_writer.write",
    }
)


def should_export_span(span: Any) -> bool:
    """Compose onto Langfuse's own default filter, never replace it
    (CLAUDE.md invariant). Confirmed live (Stage 4 Wave A's own
    observability smoke test) that every span this project opens manually
    via `client.start_as_current_observation(...)` already carries
    `is_langfuse_span`'s `scope.name == "langfuse-sdk"` stamp, so
    `is_default_export_span` alone already keeps every one of them —
    `_OWN_SPAN_NAMES` is a closed, exact-match set kept only as a
    documented, provably-harmless extension point (never a prefix, which
    a live run proved lets unrelated third-party instrumentation through
    too).
    """
    return is_default_export_span(span) or span.name in _OWN_SPAN_NAMES


def mask_otel_spans(*, params: MaskOtelSpansParams) -> MaskOtelSpansResult | None:
    """Second, independent PII barrier at actual export time
    (docs/decisions.md #14, decision 34's amendment) — reuses
    `src.domain.filters.mask_pii`'s email/phone/card patterns. A raising
    hook drops the *entire* export batch (SDK docstring), so every
    non-`str` attribute value (a legal `bool`/`int`/`float` per
    `AttributeValue`) passes through untouched rather than reaching
    `mask_pii`'s `unicodedata.normalize`, which raises on non-`str` input.
    """
    patches: dict[Any, OtelSpanPatch] = {}
    for span_id, span_data in params.spans.items():
        redacted = {
            key: (mask_pii(value) if isinstance(value, str) else value)
            for key, value in span_data.attributes.items()
        }
        if redacted != dict(span_data.attributes):
            patches[span_id] = OtelSpanPatch(set_attributes=redacted)
    return MaskOtelSpansResult(span_patches=patches) if patches else None


def configure_tracing() -> None:
    """Build this process's Langfuse client, once, eagerly — never lazily
    inside a request path (docs/decisions.md's Stage 4 decision 33): a
    misconfigured key raising mid-request collides with
    `src.infrastructure.acp.call_router`'s broad `except Exception`,
    silently relabeling itself as a router-output failure.

    No-ops if `settings.tracing_enabled` is `False`. Raises if `True` and
    either Langfuse key is empty — the same "fails loudly at startup,
    never silently" precedent as a cold-cache prompt fetch
    (docs/decisions.md #13).
    """
    global _client, _configured
    if _configured:
        return
    if not settings.tracing_enabled:
        _configured = True
        return
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        raise RuntimeError(
            "TRACING_ENABLED=true but langfuse_public_key/langfuse_secret_key "
            "is empty — refusing to start with tracing half-configured."
        )
    _client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_base_url,
        should_export_span=should_export_span,
        mask_otel_spans=mask_otel_spans,
    )
    _configured = True


def get_langfuse_client() -> Langfuse | None:
    """The configured singleton, or `None` if tracing was never enabled.
    Never raises about configuration — idempotently self-configuring
    (calls `configure_tracing()` if this process never called it
    explicitly) so a process that forgot the explicit call at its own
    entrypoint still ends up correctly configured, or correctly raises,
    on first real use rather than silently tracing nothing.
    """
    if not _configured:
        configure_tracing()
    return _client


def build_callback_handler(
    trace_context: TraceContext | None = None,
) -> CallbackHandler | None:
    """For `graph.invoke(state, config={"callbacks": [...]})`. `None` when
    tracing is disabled — callers pass an empty list in that case.
    """
    if get_langfuse_client() is None:
        return None
    return CallbackHandler(trace_context=trace_context)


def flush_and_shutdown() -> None:
    """`.shutdown()`, not merely `.flush()` — the SDK's `flush()` alone
    leaves background consumer threads running; `.shutdown()` flushes and
    joins them. No-ops if tracing was never enabled.
    """
    if _client is not None:
        _client.shutdown()
