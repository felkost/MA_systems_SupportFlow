"""FastAPI API for the React chat frontend — the only public HTTP
entry point in this project. CORS is restricted to `settings.frontend_origin`
so only the local frontend may call it.

Every `/chat` call runs `handle_request` inside `_bypass_hitl()`: Escalation's
confirmation step (`escalation_agent.py::_default_confirm`) blocks on
`input()`, which is only safe when a human is at this process's own console
— never true for a uvicorn worker thread serving a browser request. This is
the same "automated run" case `tests/evaluation/harness.py`'s own
`_bypass_hitl()` and `scripts/golden_dataset_smoke.py` already handle
— an explicit bypass flag for automated runs — scoped with
try/finally, never a permanent module-level mutation, since `settings` is a
process-wide singleton other code (and, in the test process, other test
modules) also reads. `ALLOW_REAL_SEND` is untouched, so this alone never
triggers a real Telegram send.
"""

import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import src.application.supervisor as supervisor
import src.infrastructure.judge_stats as judge_stats
import src.infrastructure.live_case_log as live_case_log
from src.domain.schemas import Source
from src.domain.state import ErrorType, SupportFlowState
from src.infrastructure.observability import new_trace_id
from src.kernel.constants import MAX_INPUT_CHARS
from src.kernel.settings import PROJECT_ROOT, settings

_EVAL_LIVE_SCRIPT = PROJECT_ROOT / "scripts" / "eval_live_batch.py"


@contextmanager
def _bypass_hitl() -> Iterator[None]:
    original = settings.bypass_hitl
    settings.bypass_hitl = True
    try:
        yield
    finally:
        settings.bypass_hitl = original


_REJECTION_MESSAGES: dict[ErrorType, str] = {
    "empty_input": "Будь ласка, напишіть ваше запитання.",
    "input_too_long": "Повідомлення занадто довге — скоротіть його, будь ласка.",
    "unsupported_language": (
        "Наразі ми відповідаємо українською, російською та англійською мовами."
    ),
    "out_of_domain": "Це питання виходить за межі того, з чим ми можемо допомогти.",
    "forbidden_content": (
        "Не вдалося обробити це повідомлення — спробуйте, будь ласка, переформулювати."
    ),
}
_DEFAULT_REJECTION_MESSAGE = _REJECTION_MESSAGES["forbidden_content"]

app = FastAPI(title="SupportFlow API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str = Field(max_length=MAX_INPUT_CHARS)
    session_id: str | None = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source] = Field(default_factory=list)
    confidence: float | None = None
    category: str | None = None
    escalated: bool
    report_written: bool = False
    telegram_sent: bool = False
    elapsed_ms: int
    session_id: str
    # Returned so a given answer can be correlated with its own Langfuse
    # trace — without it, reading back the trace for one specific request
    # means guessing by timestamp.
    trace_id: str


class RealSendToggleRequest(BaseModel):
    enabled: bool


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True, "allow_real_send": settings.allow_real_send}


@app.post("/admin/real-send")
def set_real_send(payload: RealSendToggleRequest) -> dict[str, bool]:
    """A settings-page runtime toggle for `ALLOW_REAL_SEND`, so a demo
    can turn a real Telegram send on and
    off from the UI without restarting the process. Mutates the shared
    `settings` singleton directly (unlike `_bypass_hitl`'s per-request
    scoping) — the whole point here is that the state persists until
    toggled again, matching the author's own request ("turn it on, it
    works with Telegram; turn it off, it doesn't").

    Carries the same risk `/chat`'s own no-auth design already accepts:
    this endpoint has no auth either, only CORS restricting
    it to the local frontend origin — acceptable for a local demo, not a
    hosted deployment.
    """
    settings.allow_real_send = payload.enabled
    return {"allow_real_send": settings.allow_real_send}


def _answer_route(state: SupportFlowState) -> str:
    """Which agent actually produced the answer shown to the customer.

    Mirrors `tests/evaluation/harness.py::_actual_route`'s ordering
    problem: a low-confidence Docs answer leaves `docs_response` set even
    though the case escalated, so the escalate branch is decided by the
    caller before this function is reached.
    """
    if state["docs_response"] is not None:
        return "docs"
    if state["web_search_response"] is not None:
        return "web_search"
    return "unknown"


@app.get("/stats/quality")
def quality_stats() -> dict[str, object]:
    """Judge-scored answer quality for the footer: the live sample for
    the configured experiment, and the frozen golden-dataset line.

    Declared `def`, not `async def`, like every other endpoint here: the
    Langfuse read is blocking I/O, and FastAPI runs a sync endpoint in a
    threadpool instead of on the event loop.

    Each figure ships the name of the metric and the judge that produced
    it. They are two different instruments measuring a similar idea, so a
    bare number would invite a comparison neither one supports.
    """
    return {
        "live": judge_stats.live_quality(),
        "live_deepeval": judge_stats.live_deepeval(),
        "baseline": judge_stats.golden_baseline(),
    }


@app.post("/stats/eval-live")
def run_live_eval() -> dict[str, object]:
    """Grade the requests recorded since the last run, then return the
    refreshed block.

    Runs `scripts/eval_live_batch.py` as a **subprocess**, never in
    process: DeepEval brings its own OpenTelemetry instrumentation, and
    this process already owns a `TracerProvider` configured for Langfuse
    — loading both into one process is what splits a single request's
    trace in two. A separate interpreter keeps that boundary intact.

    Costs money for each new case, so the batch is capped per call; what
    is left over is reported as `pending` and picked up next time. Only
    unscored cases are graded, so pressing this with nothing new is free.

    Carries the same no-auth exposure `/admin/real-send` already accepts:
    CORS to the local frontend is the only gate, which is acceptable for
    a local demo and not for a hosted deployment.
    """
    completed = subprocess.run(  # noqa: S603 — fixed argv, no shell, no user input
        [sys.executable, str(_EVAL_LIVE_SCRIPT), "--yes", "--limit", "10"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=900,
    )
    return {
        "ok": completed.returncode == 0,
        "log": (completed.stdout or completed.stderr).strip()[-400:],
        "stats": judge_stats.live_deepeval(),
    }


@app.post("/chat")
def chat(payload: ChatRequest) -> ChatResponse:
    request_id = uuid.uuid4().hex
    session_id = payload.session_id or uuid.uuid4().hex
    trace_id = new_trace_id()

    started = time.monotonic()
    with _bypass_hitl():
        state = supervisor.handle_request(
            payload.message, request_id, session_id, trace_id
        )
    elapsed_ms = round((time.monotonic() - started) * 1000)

    if state["next_action"] == "reject":
        error = state["errors"][0] if state["errors"] else "forbidden_content"
        answer = _REJECTION_MESSAGES.get(error, _DEFAULT_REJECTION_MESSAGE)
    else:
        answer = state["answer"] or _DEFAULT_REJECTION_MESSAGE

    response_source = state["docs_response"] or state["web_search_response"]
    classification = state["classification"]

    # Recorded for the offline batch scorer, never scored inline: running
    # DeepEval inside the request would attach its own OTel
    # instrumentation alongside Langfuse's and split this trace in two.
    # A rejected request has no agent answer to grade, so it is not kept.
    if state["next_action"] != "reject":
        live_case_log.append_case(
            masked_text=state["original_request_masked"],
            answer=answer,
            retrieval_context=state["retrieval_context"],
            tools_called=state["tools_called"],
            route=(
                "escalate"
                if state["next_action"] == "escalate"
                else _answer_route(state)
            ),
            trace_id=trace_id,
            answer_prompt_version=state["answer_prompt_version"],
            experiment=settings.experiment or None,
        )

    return ChatResponse(
        answer=answer,
        sources=response_source.sources if response_source is not None else [],
        confidence=state["confidence"],
        category=classification.category if classification is not None else None,
        escalated=state["next_action"] == "escalate",
        report_written=state["report_written"],
        telegram_sent=state["telegram_sent"],
        elapsed_ms=elapsed_ms,
        session_id=session_id,
        trace_id=trace_id,
    )
