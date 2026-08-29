"""Escalation Agent orchestration: compose an operator report, mask any
PII in it, gate it behind human confirmation (or an explicit bypass), then
write it to a run-scoped file and optionally send it to the real Telegram
test channel.

Runs in-process with the Supervisor — unlike Docs/Web Search Agent,
there is no A2A hop here; the fallback for "tool unavailable" must not
itself depend on a network call to reach.
"""

import hashlib
import logging
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any

from src.domain.filters import mask_pii
from src.domain.schemas import (
    ClassificationOutput,
    DocsResponse,
    EscalationOutput,
    WebSearchResponse,
)
from src.domain.state import ErrorType
from src.infrastructure.llm import get_chat_model
from src.infrastructure.observability import get_langfuse_client
from src.infrastructure.prompts import get_prompt, get_prompt_client
from src.infrastructure.report_writer import write_escalation_report
from src.infrastructure.telegram_client import send_telegram_message
from src.kernel.constants import (
    MAX_ESCALATION_SENDS_PER_PROCESS,
    MAX_ESCALATION_SENDS_PER_SESSION,
)
from src.kernel.settings import settings

# `capped=True` used to be invisible past this module — recorded on
# `EscalationAgentResult` but never logged, never reaching a Langfuse
# span, and never surfacing in `ChatResponse`. The chat UI showed
# "Telegram не надсилався", indistinguishable from real sending being
# switched off entirely. Found live 2026-08-29.
logger = logging.getLogger(__name__)


class EscalationInvalidOutputError(Exception):
    """The model call succeeded but did not produce a valid
    `EscalationOutput` — refusal, prose, or an out-of-schema value.
    """


@dataclass(frozen=True)
class EscalationContext:
    """Everything `escalate_node` already has in `SupportFlowState` at the
    point Escalation is reached — enough for the report to say what was
    already tried, which is what `attempted_resolution` must carry so an
    operator never re-derives it.

    Parameters
    ----------
    masked_text : str
        Already PII-masked.
    classification : ClassificationOutput or None
        `None` when Router itself failed.
    confidence : float or None
    errors : list[ErrorType]
    docs_response, web_search_response : ... or None
        Whichever agent was tried before escalating, if any.
    """

    masked_text: str
    classification: ClassificationOutput | None
    confidence: float | None
    errors: list[ErrorType]
    docs_response: DocsResponse | None = None
    web_search_response: WebSearchResponse | None = None


@dataclass(frozen=True)
class EscalationAgentResult:
    """Parameters
    ----------
    output : EscalationOutput
    written : bool
        A report file was actually written.
    sent : bool
        A real Telegram message was actually sent.
    deduplicated : bool
        This session already escalated an identical masked message —
        write/send were both skipped.
    capped : bool
        The session or process send cap was reached — the send (not the
        write) was skipped.
    """

    output: EscalationOutput
    written: bool
    sent: bool
    deduplicated: bool
    capped: bool


@dataclass
class _SessionEscalations:
    count: int = 0
    seen_hashes: set[str] = field(default_factory=set)


# ponytail: module-level, single-process, process-lifetime store — correct
# for this project's single-process demo topology, not for a multi-worker
# deployment. Upgrade to a shared store (Redis, a DB row) if that changes.
_session_store: dict[str, _SessionEscalations] = {}
_process_send_count = 0


def _render_context(context: EscalationContext) -> str:
    lines = []
    if context.classification is not None:
        lines.append(
            f"category={context.classification.category} "
            f"urgency={context.classification.urgency} "
            f"language={context.classification.language}"
        )
    else:
        lines.append("classification: unavailable (Router failed)")
    if context.confidence is not None:
        lines.append(f"confidence={context.confidence:.2f}")
    if context.errors:
        lines.append(f"errors={context.errors}")
    if context.docs_response is not None:
        lines.append(f"docs_agent tried, answered: {context.docs_response.answer}")
    if context.web_search_response is not None:
        lines.append(
            f"web_search_agent tried, answered: {context.web_search_response.answer}"
        )
    return "\n".join(lines)


def _compose_escalation_output(
    compiled_prompt: str, masked_text: str
) -> EscalationOutput:
    model = get_chat_model("escalation")
    structured_model = model.with_structured_output(EscalationOutput, include_raw=True)
    client = get_langfuse_client()
    span_cm = (
        client.start_as_current_observation(
            name="escalation_agent.compose",
            as_type="generation",
            prompt=get_prompt_client("supportflow/escalation"),
            model=model.model_name,
        )
        if client is not None
        else nullcontext()
    )
    try:
        with span_cm as generation:
            raw = structured_model.invoke(compiled_prompt)
            result = raw.get("parsed")
            # See docs_agent.py's identical fix — validated inside the
            # `with` so a parse failure is marked `level="ERROR"` instead
            # of leaving a `level=DEFAULT` span the judge evaluator would
            # otherwise score.
            if generation is not None:
                usage = getattr(raw.get("raw"), "usage_metadata", None) or {}
                update_kwargs: dict[str, Any] = dict(
                    usage_details=dict(usage),
                    input=compiled_prompt,
                    output=str(result),
                )
                if isinstance(result, EscalationOutput):
                    update_kwargs["metadata"] = {
                        "customer_message": masked_text,
                        "agent_answer": result.customer_message,
                    }
                else:
                    update_kwargs["level"] = "ERROR"
                generation.update(**update_kwargs)
    except Exception as exc:  # noqa: BLE001 — provider errors vary
        raise EscalationInvalidOutputError(str(exc)) from exc
    if not isinstance(result, EscalationOutput):
        raise EscalationInvalidOutputError(
            f"model returned no valid EscalationOutput: {raw.get('parsing_error')}"
        )
    return result


def _default_confirm(output: EscalationOutput) -> bool:
    """Interactive/demo-mode HITL gate — a CLI prompt. Blocks the calling
    process until answered; acceptable only because Escalation runs
    in-process with the Supervisor in this project's single-process demo
    topology, never inside an unattended web request handler.
    """
    print("\n--- Escalation report awaiting confirmation ---")
    print(f"summary: {output.summary}")
    print(f"category: {output.category}")
    print(f"customer_message: {output.customer_message}")
    print(f"attempted_resolution: {output.attempted_resolution}")
    answer = input("Write report and send to Telegram? [y/N] ").strip().lower()
    return answer == "y"


def _mask_output(output: EscalationOutput) -> EscalationOutput:
    """F18: the seeded prompt already forbids full address/phone/email/
    payment data, but a prompt instruction is bypassable by the same
    injection it would defend against — the deterministic filter is the
    actual control.
    """
    return output.model_copy(
        update={
            "customer_message": mask_pii(output.customer_message),
            "summary": mask_pii(output.summary),
        }
    )


def run_escalation_agent(
    context: EscalationContext,
    *,
    request_id: str,
    session_id: str,
    confirm_fn: Any = _default_confirm,
    send_fn: Any = send_telegram_message,
    write_fn: Any = write_escalation_report,
    compose_fn: Any = _compose_escalation_output,
    prompt_fn: Any = get_prompt,
) -> EscalationAgentResult:
    """Compose, mask, confirm, write, and (conditionally) send an
    escalation report.

    Parameters
    ----------
    context : EscalationContext
    request_id, session_id : str
    confirm_fn, send_fn, write_fn, compose_fn, prompt_fn : Any, optional
        Injected for testing without a real CLI prompt, network call, file
        write, or LLM call; production callers use the defaults.

    Returns
    -------
    EscalationAgentResult

    Raises
    ------
    EscalationInvalidOutputError
        The model's output failed `EscalationOutput` validation.
    """
    prompt_text, _prompt_version = prompt_fn("supportflow/escalation")
    compiled_prompt = prompt_text.replace(
        "{{customer_message}}", context.masked_text
    ).replace("{{context}}", _render_context(context))

    raw_output = compose_fn(compiled_prompt, context.masked_text)
    output = _mask_output(raw_output)

    if not settings.bypass_hitl and not confirm_fn(output):
        return EscalationAgentResult(
            output=output, written=False, sent=False, deduplicated=False, capped=False
        )

    message_hash = hashlib.sha256(context.masked_text.encode("utf-8")).hexdigest()
    session = _session_store.setdefault(session_id, _SessionEscalations())
    if message_hash in session.seen_hashes:
        return EscalationAgentResult(
            output=output, written=False, sent=False, deduplicated=True, capped=False
        )
    session.seen_hashes.add(message_hash)
    session.count += 1

    write_fn(
        output.model_dump(mode="json"),
        session_id=session_id,
        request_id=request_id,
    )

    global _process_send_count
    capped = (
        session.count > MAX_ESCALATION_SENDS_PER_SESSION
        or _process_send_count >= MAX_ESCALATION_SENDS_PER_PROCESS
    )
    if capped:
        logger.warning(
            "escalation send capped for session=%s: session_count=%d "
            "(limit %d), process_count=%d (limit %d) — report written, "
            "no real Telegram send",
            session_id,
            session.count,
            MAX_ESCALATION_SENDS_PER_SESSION,
            _process_send_count,
            MAX_ESCALATION_SENDS_PER_PROCESS,
        )
    # F17's "assert the target equals the configured test channel": this
    # agent never accepts an externally supplied chat_id (nothing in
    # `EscalationOutput` carries one), so the only thing to assert is that
    # a test channel is actually configured — there is no other value a
    # send could target.
    channel_ok = bool(settings.telegram_chat_id)
    sent = False
    if settings.allow_real_send and channel_ok and not capped:
        text = (
            f"[{output.category}] {output.summary}\n\n"
            f"{output.customer_message}\n\n"
            f"Already tried: {output.attempted_resolution}"
        )
        send_fn(
            text,
            chat_id=settings.telegram_chat_id,
            bot_token=settings.telegram_bot_token,
            timeout=10.0,
        )
        _process_send_count += 1
        sent = True

    return EscalationAgentResult(
        output=output, written=True, sent=sent, deduplicated=False, capped=capped
    )
